#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))

import json
import datetime as dt

# Валюты
from world_en.fx_intl import fetch_rates, format_line

HERE = Path(__file__).parent
ASTRO_PATH = HERE / "astro.json"
DAILY_PATH = HERE / "daily.json"


def _read_json_safe(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _run_astro_and_get_data() -> dict:
    """
    Пытается запустить world_astro_collect.main().
    - Если main() вернул dict — используем его.
    - Если нет — читаем world_en/astro.json (который main мог записать сам).
    - Если и этого нет — возвращаем {}.
    """
    # пробуем импортировать и вызвать
    try:
        from world_en.world_astro_collect import main as astro_main  # type: ignore
        rv = astro_main()
        if isinstance(rv, dict) and rv:
            return rv
    except Exception:
        pass
    # пробуем прочитать файл
    return _read_json_safe(ASTRO_PATH)


def _fx_line() -> str:
    try:
        fx = fetch_rates("USD", ["EUR", "CNY", "JPY", "INR", "IDR"])
        return format_line(fx, order=["USD", "EUR", "CNY", "JPY", "INR", "IDR"])
    except Exception:
        return "—"


def main():
    astro = _run_astro_and_get_data()

    # базовые даты
    today = dt.date.today().isoformat()
    weekday = dt.datetime.utcnow().strftime("%a")

    # соберём daily из астроданных (с дефолтами, чтобы шаблон не падал)
    out = {
        "DATE": astro.get("DATE") or today,
        "WEEKDAY": astro.get("WEEKDAY") or weekday,

        # Луна
        "MOON_PHASE": astro.get("MOON_PHASE") or "—",
        "PHASE_EN": astro.get("PHASE_EN") or "—",
        "PHASE_EMOJI": astro.get("PHASE_EMOJI") or "",
        "MOON_PERCENT": astro.get("MOON_PERCENT"),            # None допустим — шаблон сам скрывает
        "MOON_SIGN": astro.get("MOON_SIGN") or "—",
        "MOON_SIGN_EMOJI": astro.get("MOON_SIGN_EMOJI") or "",

        # VoC
        "VOC": astro.get("VOC") or astro.get("VOC_TEXT") or "No VoC today UTC",
        "VOC_TEXT": astro.get("VOC_TEXT") or astro.get("VOC") or "No VoC today UTC",
        "VOC_LEN": astro.get("VOC_LEN") or "",
        "VOC_BADGE": astro.get("VOC_BADGE") or "",
        "VOC_IS_ACTIVE": bool(astro.get("VOC_IS_ACTIVE")),

        # Энергия / совет
        "ENERGY_ICON": astro.get("ENERGY_ICON") or "",
        "ENERGY_LINE": astro.get("ENERGY_LINE") or "",
        "ADVICE_LINE": astro.get("ADVICE_LINE") or "",

        # Деньги — быстрая строка
        "fx_line": _fx_line(),
    }

    # Если вдруг будущие шаблоны ожидают альтернативные ключи — подстрахуемся
    out.setdefault("WEEKDAY_EN", out["WEEKDAY"])
    out.setdefault("DATE_UTC", out["DATE"])

    DAILY_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[daily] wrote {DAILY_PATH} ({DAILY_PATH.stat().st_size} bytes)")


if __name__ == "__main__":
    main()