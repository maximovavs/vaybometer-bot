name: world-daily-en

on:
  schedule:
    - cron: "15 7 * * *"   # 07:15 UTC
  workflow_dispatch:
    inputs:
      publish_to:
        description: "Куда публиковать?"
        type: choice
        required: false
        default: both
        options: [both, telegram, facebook, test]
      content:
        description: "Что публиковать?"
        type: choice
        required: false
        default: daily
        options: [daily, astro, both]
      # для обратной совместимости (если пустой publish_to)
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

      # ---------- Вычисляем флаги публикации ----------
      - name: Prepare flags
        id: flags
        shell: bash
        run: |
          set -euo pipefail
          EVT="${{ github.event_name }}"
          PUB="${{ github.event.inputs.publish_to || '' }}"
          CON="${{ github.event.inputs.content || '' }}"
          SEND_TEST="${{ github.event.inputs.send_to_test || 'false' }}"

          PUBLISH_TG_MAIN=false
          PUBLISH_FB=false
          PUBLISH_TG_TEST=false
          DO_DAILY=false
          DO_ASTRO=false

          # Что публиковать
          if [[ "$CON" == "" || "$CON" == "daily" || "$CON" == "both" ]]; then DO_DAILY=true; fi
          if [[ "$CON" == "astro" || "$CON" == "both" ]]; then DO_ASTRO=true; fi

          # Куда публиковать
          if [[ "$EVT" == "schedule" ]]; then
            # по расписанию: Daily в TG+FB
            PUBLISH_TG_MAIN=true
            PUBLISH_FB=true
            DO_ASTRO=false
          else
            case "$PUB" in
              both|"") PUBLISH_TG_MAIN=true; PUBLISH_FB=true;;
              telegram) PUBLISH_TG_MAIN=true;;
              facebook) PUBLISH_FB=true;;
              test) PUBLISH_TG_TEST=true;;
            esac
            # если старый флаг send_to_test=true и publish_to не задан
            if [[ "$PUB" == "" && "$SEND_TEST" == "true" ]]; then
              PUBLISH_TG_MAIN=false; PUBLISH_FB=false; PUBLISH_TG_TEST=true
            fi
          fi

          echo "publish_tg_main=$PUBLISH_TG_MAIN"   >> $GITHUB_OUTPUT
          echo "publish_fb=$PUBLISH_FB"            >> $GITHUB_OUTPUT
          echo "publish_tg_test=$PUBLISH_TG_TEST"  >> $GITHUB_OUTPUT
          echo "do_daily=$DO_DAILY"                >> $GITHUB_OUTPUT
          echo "do_astro=$DO_ASTRO"                >> $GITHUB_OUTPUT

      # ---------- DAILY ----------
      - name: Collect DAILY
        if: steps.flags.outputs.do_daily == 'true'
        env:
          YT_API_KEY:            ${{ secrets.YT_API_KEY }}
          YT_CHANNEL_ID:         ${{ secrets.YT_CHANNEL_ID }}
          YOUTUBE_PLAYLIST_IDS:  ${{ secrets.YOUTUBE_PLAYLIST_IDS }}
          FALLBACK_NATURE_LIST:  ${{ secrets.FALLBACK_NATURE_LIST }}
        run: |
          set -euo pipefail
          python world_en/world_collect.py
          test -f world_en/daily.json

      - name: Render DAILY
        if: steps.flags.outputs.do_daily == 'true'
        run: python world_en/render.py world_en/templates/daily_en.j2 world_en/daily.json > world_en/message.txt

      # ---------- ASTRO ----------
      - name: Collect ASTRO
        if: steps.flags.outputs.do_astro == 'true'
        run: |
          set -euo pipefail
          python world_en/world_astro_collect.py
          test -f world_en/astro.json

      - name: Render ASTRO (with fallback)
        if: steps.flags.outputs.do_astro == 'true'
        run: |
          set -Eeuo pipefail
          if [ -f world_en/templates/astro_en.j2 ]; then
            python world_en/render.py world_en/templates/astro_en.j2 world_en/astro.json > world_en/astro_message.txt || true
          fi
          if [ ! -s world_en/astro_message.txt ]; then
            python - <<'PY'
            import json, pathlib
            d=json.load(open('world_en/astro.json',encoding='utf-8'))
            lines=[]
            lines.append(f"🌙 Astro Snapshot • {d.get('WEEKDAY','')}, {d.get('DATE','')}")
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
            pathlib.Path('world_en/astro_message.txt').write_text("\n".join(lines).strip(), encoding='utf-8')
            PY
          fi

      # ---------- TELEGRAM MAIN ----------
      - name: TG MAIN — send DAILY
        if: steps.flags.outputs.publish_tg_main == 'true' && steps.flags.outputs.do_daily == 'true'
        env:
          TG_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN_EN }}
          TG_CHAT:  ${{ secrets.TELEGRAM_CHAT_ID_EN }}
        run: |
          set -euo pipefail
          curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
            -d chat_id="${TG_CHAT}" -d parse_mode="HTML" \
            --data-urlencode text@"world_en/message.txt"

      - name: TG MAIN — send DAILY nature card
        if: steps.flags.outputs.publish_tg_main == 'true' && steps.flags.outputs.do_daily == 'true'
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
          fi

      - name: TG MAIN — send ASTRO
        if: steps.flags.outputs.publish_tg_main == 'true' && steps.flags.outputs.do_astro == 'true'
        env:
          TG_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN_EN }}
          TG_CHAT:  ${{ secrets.TELEGRAM_CHAT_ID_EN }}
        run: |
          set -euo pipefail
          curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
            -d chat_id="${TG_CHAT}" \
            --data-urlencode text@"world_en/astro_message.txt"

      # ---------- FACEBOOK ----------
      - name: FB — build DAILY text
        if: steps.flags.outputs.publish_fb == 'true' && steps.flags.outputs.do_daily == 'true' && secrets.FB_PAGE_ID != '' && secrets.FB_PAGE_TOKEN != ''
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
          PY

      - name: FB — post DAILY
        if: steps.flags.outputs.publish_fb == 'true' && steps.flags.outputs.do_daily == 'true' && secrets.FB_PAGE_ID != '' && secrets.FB_PAGE_TOKEN != ''
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
          cat world_en/fb_response.json

      - name: FB — post DAILY nature link
        if: steps.flags.outputs.publish_fb == 'true' && steps.flags.outputs.do_daily == 'true' && secrets.FB_PAGE_ID != '' && secrets.FB_PAGE_TOKEN != ''
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
            cat world_en/fb_nature_response.json
          fi

      - name: FB — build ASTRO text
        if: steps.flags.outputs.publish_fb == 'true' && steps.flags.outputs.do_astro == 'true' && secrets.FB_PAGE_ID != '' && secrets.FB_PAGE_TOKEN != ''
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
          PY

      - name: FB — post ASTRO
        if: steps.flags.outputs.publish_fb == 'true' && steps.flags.outputs.do_astro == 'true' && secrets.FB_PAGE_ID != '' && secrets.FB_PAGE_TOKEN != ''
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
          cat world_en/fb_astro_response.json

      # ---------- TELEGRAM TEST ----------
      - name: TG TEST — send DAILY
        if: steps.flags.outputs.publish_tg_test == 'true' && steps.flags.outputs.do_daily == 'true'
        env:
          TG_TOKEN: ${{ secrets.TELEGRAM_TOKEN_TEST }}
          TG_CHAT:  ${{ secrets.CHANNEL_ID_TEST }}
        run: |
          set -euo pipefail
          curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
            -d chat_id="${TG_CHAT}" -d parse_mode="HTML" \
            --data-urlencode text@"world_en/message.txt"

      - name: TG TEST — send DAILY nature card
        if: steps.flags.outputs.publish_tg_test == 'true' && steps.flags.outputs.do_daily == 'true'
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
          curl -s "https://api.telegram.org/bot${TG_TOKEN}/sendPhoto" \
            -F chat_id="${TG_CHAT}" \
            -F photo="${THUMB}" \
            -F parse_mode="HTML" \
            -F caption="$CAPTION" \
            -F reply_markup='{"inline_keyboard":[[{"text":"▶️ Watch on YouTube","url":"'"$URL"'"}]]}'

      - name: TG TEST — send ASTRO
        if: steps.flags.outputs.publish_tg_test == 'true' && steps.flags.outputs.do_astro == 'true'
        env:
          TG_TOKEN: ${{ secrets.TELEGRAM_TOKEN_TEST }}
          TG_CHAT:  ${{ secrets.CHANNEL_ID_TEST }}
        run: |
          set -euo pipefail
          curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
            -d chat_id="${TG_CHAT}" \
            --data-urlencode text@"world_en/astro_message.txt"