name: world-daily-en

on:
  schedule:
    - cron: "15 7 * * *"   # 07:15 UTC каждый день (по умолчанию публикуем только Daily)
  workflow_dispatch:
    inputs:
      publish_to:
        description: "Куда публиковать?"
        type: choice
        required: false
        default: both
        options:
          - both       # Telegram + Facebook
          - telegram   # только основной Телеграм
          - facebook   # только Facebook-страница
          - test       # тестовый Телеграм
      content:
        description: "Что публиковать?"
        type: choice
        required: false
        default: daily
        options:
          - daily
          - astro
          - both
      # для обратной совместимости
      send_to_test:
        type: boolean
        description: "(deprecated) Send to TEST channel instead of main"
        default: false

jobs:
  run:
    runs-on: ubuntu-latest
    env:
      TZ: UTC
      PYTHONPATH: ${{ github.workspace }}

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install deps
        run: pip install requests jinja2 pytz astral

      # ---------- DAILY SNAPSHOT ----------
      - name: Collect DAILY
        env:
          YT_API_KEY:            ${{ secrets.YT_API_KEY }}
          YT_CHANNEL_ID:         ${{ secrets.YT_CHANNEL_ID }}
          YOUTUBE_PLAYLIST_IDS:  ${{ secrets.YOUTUBE_PLAYLIST_IDS }}
          FALLBACK_NATURE_LIST:  ${{ secrets.FALLBACK_NATURE_LIST }}
        run: |
          set -euo pipefail
          python world_en/world_collect.py
          echo "---- ls world_en ----"; ls -la world_en || true
          test -f world_en/daily.json || { echo "ERROR: world_en/daily.json missing"; exit 1; }

      - name: Render DAILY message
        run: python world_en/render.py world_en/templates/daily_en.j2 world_en/daily.json > world_en/message.txt

      - name: Ensure daily.json exists
        run: test -f world_en/daily.json || { echo "daily.json missing"; exit 1; }

      # ---------- ASTRO SNAPSHOT ----------
      - name: Collect ASTRO
        run: |
          set -euo pipefail
          python world_en/world_astro_collect.py
          test -f world_en/astro.json || { echo "ERROR: world_en/astro.json missing"; exit 1; }

      - name: Render ASTRO message (with fallback)
        run: |
          set -Eeuo pipefail
          # Пытаемся рендерить через шаблон, если он есть
          if [ -f world_en/templates/astro_en.j2 ]; then
            python world_en/render.py world_en/templates/astro_en.j2 world_en/astro.json > world_en/astro_message.txt || true
          fi
          # Фолбэк: собрать текст питоном, если файла нет/пустой
          if [ ! -s world_en/astro_message.txt ]; then
            python - <<'PY'
            import json, pathlib
            d=json.load(open('world_en/astro.json',encoding='utf-8'))
            lines=[]
            lines.append(f"🌙 Astro Snapshot • {d.get('WEEKDAY','')} , {d.get('DATE','')}")
            ph = d.get('PHASE_EN') or d.get('MOON_PHASE','—')
            lines.append(f"- Moon phase: {ph} {d.get('PHASE_EMOJI','')}")
            pct = d.get('MOON_PERCENT')
            if pct is not None: lines.append(f"- Illumination: {pct}%")
            sign = d.get('MOON_SIGN')
            if sign and sign!='—': lines.append(f"- Moon in: {sign} {d.get('MOON_SIGN_EMOJI','')}")
            voc = d.get('VOC_TEXT') or d.get('VOC')
            if voc: lines.append(f"- VoC: {voc} {d.get('VOC_BADGE','')}")
            lines.append("")
            lines.append(f"Energy: {d.get('ENERGY_ICON','')} {d.get('ENERGY_LINE','')}")
            lines.append(f"Advice: {d.get('ADVICE_LINE','')}")
            out="\n".join(lines).strip()
            pathlib.Path('world_en/astro_message.txt').write_text(out,encoding='utf-8')
            PY
          fi
          echo "ASTRO text:"
          tail -n +1 world_en/astro_message.txt || true

      # ===================== TELEGRAM (MAIN) =====================
      # DAILY -> TG (main)
      - name: Send DAILY text (MAIN TG)
        if: ${{ github.event_name == 'schedule'
                || ((inputs.publish_to == 'both' || inputs.publish_to == 'telegram' || (inputs.publish_to == '' && inputs.send_to_test != 'true'))
                    && (inputs.content == '' || inputs.content == 'daily' || inputs.content == 'both')) }}
        env:
          TG_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN_EN }}
          TG_CHAT:  ${{ secrets.TELEGRAM_CHAT_ID_EN }}
        run: |
          set -euo pipefail
          curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
            -d chat_id="${TG_CHAT}" -d parse_mode="HTML" \
            --data-urlencode text@"world_en/message.txt"

      - name: Send DAILY Nature card (MAIN TG)
        if: ${{ github.event_name == 'schedule'
                || ((inputs.publish_to == 'both' || inputs.publish_to == 'telegram' || (inputs.publish_to == '' && inputs.send_to_test != 'true'))
                    && (inputs.content == '' || inputs.content == 'daily' || inputs.content == 'both')) }}
        env:
          TG_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN_EN }}
          TG_CHAT:  ${{ secrets.TELEGRAM_CHAT_ID_EN }}
        run: |
          set -euo pipefail
          readarray -t L < <(python - <<'PY'
          import json
          d=json.load(open('world_en/daily.json',encoding='utf-8'))
          print(d.get('NATURE_THUMB',''))
          print(d.get('NATURE_URL',''))
          title=d.get('NATURE_TITLE','Nature Break')
          snip =d.get('NATURE_SNIPPET','60 seconds of calm')
          print(f"🌊 <b>{title}</b>\n<i>{snip}</i>")
          PY
          )
          THUMB="${L[0]}"; URL="${L[1]}"; CAPTION="${L[*]:2}"
          if [ -n "$THUMB" ] && [ -n "$URL" ]; then
            curl -s "https://api.telegram.org/bot${TG_TOKEN}/sendPhoto" \
              -F chat_id="${TG_CHAT}" \
              -F photo="${THUMB}" \
              -F parse_mode="HTML" \
              -F caption="$CAPTION" \
              -F reply_markup='{"inline_keyboard":[[{"text":"▶️ Watch on YouTube","url":"'"$URL"'"}]]}'
          elif [ -n "$URL" ]; then
            curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
              -d chat_id="${TG_CHAT}" \
              -d text="$URL" \
              -d disable_web_page_preview=false
          else
            echo "No nature URL in daily.json — skipping card."
          fi

      # ASTRO -> TG (main)  (публикуем только при ручном запуске, если выбран astro/both)
      - name: Send ASTRO text (MAIN TG)
        if: ${{ github.event_name == 'workflow_dispatch'
                && (inputs.publish_to == 'both' || inputs.publish_to == 'telegram')
                && (inputs.content == 'astro' || inputs.content == 'both') }}
        env:
          TG_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN_EN }}
          TG_CHAT:  ${{ secrets.TELEGRAM_CHAT_ID_EN }}
        run: |
          set -euo pipefail
          curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
            -d chat_id="${TG_CHAT}" \
            --data-urlencode text@"world_en/astro_message.txt"

      # ===================== FACEBOOK =====================
      # DAILY -> FB (main message)
      - name: Build FB-safe DAILY text
        if: ${{ (github.event_name == 'schedule'
                 || ((inputs.publish_to == 'both' || inputs.publish_to == 'facebook')
                     && (inputs.content == '' || inputs.content == 'daily' || inputs.content == 'both')))
                && secrets.FB_PAGE_ID != '' && secrets.FB_PAGE_TOKEN != '' }}
        env:
          TG_URL: ${{ secrets.TELEGRAM_CHANNEL_URL }}
        run: |
          python - <<'PY'
          import re, html, pathlib, os
          p = pathlib.Path('world_en/message.txt').read_text('utf-8')
          plain = re.sub(r'<[^>]+>', '', p)
          plain = html.unescape(plain)
          plain = re.sub(r'[ \t]+\n', '\n', plain)
          plain = re.sub(r'\n{3,}', '\n\n', plain).strip()
          if len(plain) > 2500:
            plain = plain[:2490].rsplit('\n',1)[0] + '\n…'
          link = os.getenv('TG_URL') or 'https://t.me/WorldVibeMeter'
          out  = f"{plain}\n\nFollow daily on Telegram: {link}"
          pathlib.Path('world_en/facebook_message.txt').write_text(out, 'utf-8')
          print('FB text length:', len(out))
          PY

      - name: Post DAILY to Facebook Page
        if: ${{ (github.event_name == 'schedule'
                 || ((inputs.publish_to == 'both' || inputs.publish_to == 'facebook')
                     && (inputs.content == '' || inputs.content == 'daily' || inputs.content == 'both')))
                && secrets.FB_PAGE_ID != '' && secrets.FB_PAGE_TOKEN != '' }}
        env:
          FB_PAGE_ID:     ${{ secrets.FB_PAGE_ID }}
          FB_PAGE_TOKEN:  ${{ secrets.FB_PAGE_TOKEN }}
          TG_URL:         ${{ secrets.TELEGRAM_CHANNEL_URL }}
        run: |
          set -euo pipefail
          MSG="$(cat world_en/facebook_message.txt)"
          curl -s -X POST "https://graph.facebook.com/v19.0/${FB_PAGE_ID}/feed" \
            --data-urlencode "message=$MSG" \
            --data-urlencode "link=${TG_URL:-https://t.me/WorldVibeMeter}" \
            --data-urlencode "access_token=${FB_PAGE_TOKEN}" \
            > world_en/fb_response.json
          echo "Facebook response:"; cat world_en/fb_response.json
          grep -q '"id":' world_en/fb_response.json || { echo "Facebook post failed"; exit 1; }

      - name: Post DAILY Nature link to Facebook
        if: ${{ (github.event_name == 'schedule'
                 || ((inputs.publish_to == 'both' || inputs.publish_to == 'facebook')
                     && (inputs.content == '' || inputs.content == 'daily' || inputs.content == 'both')))
                && secrets.FB_PAGE_ID != '' && secrets.FB_PAGE_TOKEN != '' }}
        env:
          FB_PAGE_ID:     ${{ secrets.FB_PAGE_ID }}
          FB_PAGE_TOKEN:  ${{ secrets.FB_PAGE_TOKEN }}
        run: |
          set -euo pipefail
          readarray -t L < <(python - <<'PY'
          import json
          d=json.load(open('world_en/daily.json','r',encoding='utf-8'))
          print(d.get('NATURE_URL',''))
          title=d.get('NATURE_TITLE','Nature Break')
          snip=d.get('NATURE_SNIPPET','60 seconds of calm')
          print(f"🌊 {title}\n{snip}")
          PY
          )
          URL="${L[0]}"; CAP="${L[*]:1}"
          if [ -n "$URL" ]; then
            curl -s -X POST "https://graph.facebook.com/v19.0/${FB_PAGE_ID}/feed" \
              --data-urlencode "message=$CAP" \
              --data-urlencode "link=$URL" \
              --data-urlencode "access_token=${FB_PAGE_TOKEN}" \
              > world_en/fb_nature_response.json
            echo "Facebook nature response:"; cat world_en/fb_nature_response.json
          else
            echo "No NATURE_URL — skipping FB nature post."
          fi

      # ASTRO -> FB (только при ручном запуске и выборе astro/both)
      - name: Build FB-safe ASTRO text
        if: ${{ github.event_name == 'workflow_dispatch'
                && (inputs.publish_to == 'both' || inputs.publish_to == 'facebook')
                && (inputs.content == 'astro' || inputs.content == 'both')
                && secrets.FB_PAGE_ID != '' && secrets.FB_PAGE_TOKEN != '' }}
        run: |
          python - <<'PY'
          import re, html, pathlib
          p = pathlib.Path('world_en/astro_message.txt').read_text('utf-8')
          plain = re.sub(r'<[^>]+>', '', p)
          plain = html.unescape(plain)
          plain = re.sub(r'[ \t]+\n', '\n', plain)
          plain = re.sub(r'\n{3,}', '\n\n', plain).strip()
          if len(plain) > 2500:
            plain = plain[:2490].rsplit('\n',1)[0] + '\n…'
          pathlib.Path('world_en/facebook_astro.txt').write_text(plain, 'utf-8')
          print('FB astro length:', len(plain))
          PY

      - name: Post ASTRO to Facebook Page
        if: ${{ github.event_name == 'workflow_dispatch'
                && (inputs.publish_to == 'both' || inputs.publish_to == 'facebook')
                && (inputs.content == 'astro' || inputs.content == 'both')
                && secrets.FB_PAGE_ID != '' && secrets.FB_PAGE_TOKEN != '' }}
        env:
          FB_PAGE_ID:     ${{ secrets.FB_PAGE_ID }}
          FB_PAGE_TOKEN:  ${{ secrets.FB_PAGE_TOKEN }}
        run: |
          set -euo pipefail
          MSG="$(cat world_en/facebook_astro.txt)"
          curl -s -X POST "https://graph.facebook.com/v19.0/${FB_PAGE_ID}/feed" \
            --data-urlencode "message=$MSG" \
            --data-urlencode "access_token=${FB_PAGE_TOKEN}" \
            > world_en/fb_astro_response.json
          echo "Facebook astro response:"; cat world_en/fb_astro_response.json
          grep -q '"id":' world_en/fb_astro_response.json || { echo "Facebook astro post failed"; exit 1; }

      # ===================== TELEGRAM (TEST) =====================
      - name: Send DAILY text (TEST TG)
        if: ${{ inputs.publish_to == 'test'
                || (inputs.publish_to == '' && inputs.send_to_test == 'true') }}
        env:
          TG_TOKEN: ${{ secrets.TELEGRAM_TOKEN_TEST }}
          TG_CHAT:  ${{ secrets.CHANNEL_ID_TEST }}
        run: |
          set -euo pipefail
          if [ -z "${TG_CHAT}" ]; then echo "TEST chat id is empty"; exit 1; fi
          curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
            -d chat_id="${TG_CHAT}" -d parse_mode="HTML" \
            --data-urlencode text@"world_en/message.txt"

      - name: Send DAILY Nature card (TEST TG)
        if: ${{ inputs.publish_to == 'test'
                || (inputs.publish_to == '' && inputs.send_to_test == 'true') }}
        env:
          TG_TOKEN: ${{ secrets.TELEGRAM_TOKEN_TEST }}
          TG_CHAT:  ${{ secrets.CHANNEL_ID_TEST }}
        run: |
          set -euo pipefail
          readarray -t L < <(python - <<'PY'
          import json
          d=json.load(open('world_en/daily.json',encoding='utf-8'))
          print(d.get('NATURE_THUMB',''))
          print(d.get('NATURE_URL',''))
          title=d.get('NATURE_TITLE','Nature Break')
          snip =d.get('NATURE_SNIPPET','60 seconds of calm')
          print(f"🌊 <b>{title}</b>\n<i>{snip}</i>")
          PY
          )
          THUMB="${L[0]}"; URL="${L[1]}"; CAPTION="${L[*]:2}"
          if [ -z "${TG_CHAT}" ]; then echo "TEST chat id is empty"; exit 1; fi
          if [ -n "$THUMB" ] && [ -n "$URL" ]; then
            curl -s "https://api.telegram.org/bot${TG_TOKEN}/sendPhoto" \
              -F chat_id="${TG_CHAT}" \
              -F photo="${THUMB}" \
              -F parse_mode="HTML" \
              -F caption="$CAPTION" \
              -F reply_markup='{"inline_keyboard":[[{"text":"▶️ Watch on YouTube","url":"'"$URL"'"}]]}'
          elif [ -n "$URL" ]; then
            curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
              -d chat_id="${TG_CHAT}" \
              -d text="$URL" \
              -d disable_web_page_preview=false
          else
            echo "No nature URL in daily.json — skipping card."
          fi

      - name: Send ASTRO text (TEST TG)
        if: ${{ (inputs.publish_to == 'test'
                 || (inputs.publish_to == '' && inputs.send_to_test == 'true'))
                && (inputs.content == 'astro' || inputs.content == 'both') }}
        env:
          TG_TOKEN: ${{ secrets.TELEGRAM_TOKEN_TEST }}
          TG_CHAT:  ${{ secrets.CHANNEL_ID_TEST }}
        run: |
          set -euo pipefail
          if [ -z "${TG_CHAT}" ]; then echo "TEST chat id is empty"; exit 1; fi
          curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
            -d chat_id="${TG_CHAT}" \
            --data-urlencode text@"world_en/astro_message.txt"