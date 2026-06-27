#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Focused tests for separate Cyprus FX + Market Pulse posts."""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

telegram = types.ModuleType("telegram")
telegram.Bot = object
telegram.constants = types.SimpleNamespace(ParseMode=types.SimpleNamespace(HTML="HTML"))
sys.modules.setdefault("telegram", telegram)

pendulum = types.ModuleType("pendulum")
pendulum.DateTime = object
pendulum.Timezone = object
sys.modules.setdefault("pendulum", pendulum)

post_common = types.ModuleType("post_common")
post_common.main_common = lambda *args, **kwargs: None
sys.modules.setdefault("post_common", post_common)

import post_cy  # noqa: E402
import post_cy_fx_market_pulse as pulse  # noqa: E402


class _Date:
    def to_date_string(self) -> str:
        return "2026-06-27"


def _build_fx_text_with_ruble_deltas(eur_delta: float, usd_delta: float) -> str:
    post_cy._fetch_intermarket_eur_with_prev = lambda _today, _path: (
        {"USD": 1.14, "GBP": 0.86, "TRY": 53.14, "ILS": 3.41},
        {"USD": 1.14, "GBP": 0.86, "TRY": 52.96, "ILS": 3.39},
    )
    post_cy._fetch_ecb_latest_and_prev = lambda: (
        {"USD": 1.13, "GBP": 0.85},
        {"USD": 1.12, "GBP": 0.84},
        "2026-06-27",
        "2026-06-26",
    )
    post_cy._load_cbr_rates = lambda _date, _tz: {
        "EUR": {"value": 87.40, "delta": eur_delta},
        "USD": {"value": 77.06, "delta": usd_delta},
    }
    text, _rates, _inter = post_cy._build_fx_message_eur(_Date(), None, Path("unused.json"))
    return text


def cy_fx_message_hides_ecb_when_intermarket_exists(tmp_path: Path | None = None) -> None:
    text = _build_fx_text_with_ruble_deltas(1.63, 1.43)
    assert "💱 <b>Курсы валют | 1 EUR</b>" in text
    assert "ECB official:" not in text
    assert "Межрынок: $1.14 · £0.86 · ₺53.14 ↑0.18 · ₪3.41 ↑0.02" in text
    assert "К рублю: €87.40 ₽ ↑1.63 · $77.06 ₽ ↑1.43" in text
    assert "🧭 EUR/USD к рублю выше; для поездок смотрим TRY и ILS." in text


def cy_fx_summary_positive_is_higher() -> None:
    text = _build_fx_text_with_ruble_deltas(1.63, 1.43)
    assert "🧭 EUR/USD к рублю выше; для поездок смотрим TRY и ILS." in text


def cy_fx_summary_negative_is_lower() -> None:
    text = _build_fx_text_with_ruble_deltas(-1.63, -1.43)
    assert "🧭 EUR/USD к рублю ниже; для поездок смотрим TRY и ILS." in text


def cy_fx_summary_mixed_is_mixed() -> None:
    text = _build_fx_text_with_ruble_deltas(1.63, -1.43)
    assert "🧭 Рублёвые пары смешанно; для поездок смотрим TRY и ILS." in text


def cy_market_pulse_is_compact() -> None:
    pulse._fetch_crypto = lambda: ["24ч: BTC $60.3K ↑1.2% · ETH $1.6K ↑2.0%"]
    pulse._fetch_gold = lambda: ["Gold/oz: $4.1K"]
    block = pulse.build_market_pulse_block()
    assert "📊 <b>Пульс рынков</b>" in block
    assert "24ч: BTC $60.3K ↑1.2% · ETH $1.6K ↑2.0%" in block
    assert "Gold/oz: $4.1K" in block
    assert "Инфо-ориентир, не инвестрекомендация." in block
    assert "(" not in block


def main() -> None:
    checks = (
        cy_fx_message_hides_ecb_when_intermarket_exists,
        cy_fx_summary_positive_is_higher,
        cy_fx_summary_negative_is_lower,
        cy_fx_summary_mixed_is_mixed,
        cy_market_pulse_is_compact,
    )
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    print(f"OK: {len(checks)} Cyprus FX/Market Pulse checks passed")


if __name__ == "__main__":
    main()
