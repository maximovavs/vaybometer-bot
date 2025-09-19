name: collect-radiation

on:
  schedule:
    # каждые 30 минут (со сдвигом, чтобы не пересекаться с другими задачами)
    - cron: '4/30 * * * *'
  workflow_dispatch:

permissions:
  contents: write   # нужно для коммита JSON

concurrency:
  group: collect-radiation-cy
  cancel-in-progress: false

jobs:
  collect:
    runs-on: ubuntu-22.04
    timeout-minutes: 20

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install deps
        shell: bash
        run: |
          set -euo pipefail
          python -m pip install --upgrade pip
          if [ -f requirements.txt ]; then
            pip install -r requirements.txt
          else
            pip install requests
          fi

      - name: Run collector (robust search with retry; CRLF-safe)
        shell: bash
        run: |
          set -euo pipefail
          cd "${GITHUB_WORKSPACE:?}"
          echo "PWD=$(pwd)"
          ls -la

          # возможные пути скрипта
          scripts=(
            "radiation_collect.py"
            "collectors/radiation_collect.py"
            "scripts/collect_radiation.py"
          )

          # до 3 попыток запуска
          for attempt in 1 2 3; do
            echo "Attempt $attempt"
            for s in "${scripts[@]}"; do
              # срежем '\r' на случай CRLF
              p="${s%$'\r'}"
              if [[ -f "$p" ]]; then
                echo "Running: $p"
                if python "$p"; then
                  echo "Collector finished OK."
                  exit 0
                else
                  echo "Collector failed on attempt $attempt."
                fi
              fi
            done
            echo "Sleeping 10s before next attempt…"
            sleep 10
          done

          echo "No collector script found in known locations."
          exit 1

      - name: Commit data
        shell: bash
        run: |
          set -euo pipefail
          cd "${GITHUB_WORKSPACE:?}"

          git config --global --add safe.directory "$GITHUB_WORKSPACE"
          git config user.name  "rad-bot"
          git config user.email "bot@users.noreply.github.com"

          # добавляем артефакты сборки (если есть)
          git add radiation_hourly.json           2>/dev/null || true
          git add data/safecast_cy.json           2>/dev/null || true
          git add data/radiation_official.json    2>/dev/null || true

          git commit -m "radiation: hourly update [cy]" || echo "no changes"
          git push
