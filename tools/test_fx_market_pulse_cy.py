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
import fx_market_pulse_test as safe_pulse  # noqa: E402


class _Date:
    def to_date_string(self) -> str:
        return "2026-06-27"


def _build_fx_text_with_ruble_deltas(eur_delta: float | None, usd_delta: float | None) -> tuple[str, dict]:
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
    text, rates, _inter = post_cy._build_fx_message_eur(_Date(), None, Path("unused.json"))
    return text, rates


def cy_fx_numeric_lines_stay_unchanged() -> None:
    text, _rates = _build_fx_text_with_ruble_deltas(1.63, -1.43)
    assert "💱 <b>Курсы валют | 1 EUR</b>" in text
    assert "ECB official:" not in text
    assert "EUR: USD 1.14 · GBP 0.86 · TRY 53.14 ↑0.18 · ILS 3.41 ↑0.02" in text
    assert "К ₽: EUR 87.40 ↑1.63 · USD 77.06 ↓1.43" in text
    assert "#Кипр #курсы_валют #рынки" in text


def cy_plain_eur_summary_explains_displayed_moves() -> None:
    raw, rates = _build_fx_text_with_ruble_deltas(1.63, -1.43)
    text = pulse.replace_ruble_summary(raw, rates)
    expected = (
        "🧭 К евро: доллар почти не изменился, фунт почти не изменился, "
        "турецкая лира подешевела, шекель подешевел."
    )
    assert expected in text
    assert text.count("🧭 К евро:") == 1


def cy_plain_eur_summary_inverts_quote_arrow_semantics() -> None:
    raw = (
        "💱 <b>Курсы валют | 1 EUR</b>\n"
        "EUR: USD 1.15 ↑0.01 · GBP 0.85 ↓0.01 · TRY 54.75 ↑0.05 · ILS 3.52 ↓0.02\n"
        "К ₽: EUR 91.19 ↑0.31 · USD 79.46 ↓0.39\n"
        "🧭 Рублёвые пары смешанно; для поездок по региону смотрим TRY/ILS.\n\n"
        "#Кипр #курсы_валют #рынки"
    )
    rates = {
        "EUR": {"value": 91.19, "delta": 0.31},
        "USD": {"value": 79.46, "delta": -0.39},
    }
    text = pulse.replace_ruble_summary(raw, rates)
    assert (
        "🧭 К евро: доллар подешевел, фунт подорожал, "
        "турецкая лира подешевела, шекель подорожал."
    ) in text
    eur_summary = pulse.build_plain_eur_summary(raw)
    assert "0.01" not in eur_summary and "0.05" not in eur_summary and "0.02" not in eur_summary


def cy_plain_eur_summary_handles_missing_daily_dynamics() -> None:
    raw = "💱 Курсы\nEUR: USD 1.15 · GBP 0.86 · TRY 54.75 · ILS 3.52"
    assert pulse.build_plain_eur_summary(raw) == "🧭 К евро: динамика за день пока недоступна."


def cy_two_human_summaries_are_in_reading_order() -> None:
    raw, rates = _build_fx_text_with_ruble_deltas(1.63, -1.43)
    text = pulse.replace_ruble_summary(raw, rates)
    assert text.index("EUR:") < text.index("🧭 К евро:")
    assert text.index("🧭 К евро:") < text.index("К ₽:")
    assert text.index("К ₽:") < text.index("🧭 К рублю:")


def cy_plain_summary_explains_mixed_ruble_moves_without_repeating_numbers() -> None:
    raw, rates = _build_fx_text_with_ruble_deltas(1.63, -1.43)
    text = pulse.replace_ruble_summary(raw, rates)
    assert "🧭 К рублю: евро подорожал, доллар подешевел." in text
    assert "Рублёвые пары смешанно" not in text
    assert "для поездок по региону смотрим TRY/ILS" not in text
    summary = pulse.build_plain_ruble_summary(rates)
    assert "1.63" not in summary and "1.43" not in summary


def cy_plain_summary_handles_both_up() -> None:
    _raw, rates = _build_fx_text_with_ruble_deltas(1.63, 1.43)
    assert pulse.build_plain_ruble_summary(rates) == "🧭 К рублю: евро подорожал, доллар подорожал."


def cy_plain_summary_handles_both_down() -> None:
    _raw, rates = _build_fx_text_with_ruble_deltas(-1.63, -1.43)
    assert pulse.build_plain_ruble_summary(rates) == "🧭 К рублю: евро подешевел, доллар подешевел."


def cy_plain_summary_handles_zero_and_missing() -> None:
    rates = {
        "EUR": {"value": 87.40, "delta": 0.0},
        "USD": {"value": 77.06, "delta": None},
    }
    assert pulse.build_plain_ruble_summary(rates) == "🧭 К рублю: евро почти не изменился."


def cy_missing_deltas_never_restore_old_jargon() -> None:
    raw, rates = _build_fx_text_with_ruble_deltas(None, None)
    text = pulse.replace_ruble_summary(raw, rates)
    assert "🧭 К рублю: динамика за день пока недоступна." in text
    assert "Рублёвые пары смешанно" not in text
    assert "для поездок по региону смотрим TRY/ILS" not in text


def cy_safe_runner_uses_same_summary_formatter() -> None:
    assert safe_pulse.replace_ruble_summary is pulse.replace_ruble_summary
    raw, rates = _build_fx_text_with_ruble_deltas(1.63, -1.43)
    text = safe_pulse.replace_ruble_summary(raw, rates)
    assert "🧭 К евро:" in text
    assert "🧭 К рублю: евро подорожал, доллар подешевел." in text


def cy_no_cbr_values_keep_eur_summary_but_skip_ruble_summary() -> None:
    raw = (
        "💱 <b>Курсы валют | 1 EUR</b>\n"
        "EUR: USD 1.14 · GBP 0.86 · TRY 53.14 ↑0.18 · ILS 3.41 ↑0.02\n\n"
        "#Кипр #курсы_валют #рынки"
    )
    rates = {
        "EUR": {"value": None, "delta": None},
        "USD": {"value": None, "delta": None},
    }
    text = pulse.replace_ruble_summary(raw, rates)
    assert "🧭 К евро:" in text
    assert "🧭 К рублю:" not in text
    assert text.rstrip().endswith("#Кипр #курсы_валют #рынки")


def cy_market_pulse_is_compact() -> None:
    pulse._fetch_crypto = lambda: ["24ч: BTC $60.3K ↑1.2% · ETH $1.6K ↑2.0%"]
    pulse._fetch_gold = lambda: ["Gold/oz $4.1K"]
    block = pulse.build_market_pulse_block()
    assert "📊 <b>Пульс рынков</b>" in block
    assert "24ч: BTC $60.3K ↑1.2% · ETH $1.6K ↑2.0%" in block
    assert "Gold/oz $4.1K" in block
    assert "Gold/oz:" not in block
    assert "Инфо-ориентир, не инвестрекомендация." in block
    assert "(" not in block
    text = pulse.inject_market_pulse("💱 <b>Курсы валют | 1 EUR</b>\n\n#Кипр #курсы_валют", block)
    assert "#Кипр #курсы_валют #рынки" in text
    assert text.rstrip().endswith("#Кипр #курсы_валют #рынки")


def cy_fx_market_hashtag_survives_empty_or_existing_pulse() -> None:
    base = "💱 <b>Курсы валют | 1 EUR</b>\nEUR: USD 1.14\n\n#Кипр #курсы_валют"
    assert pulse.inject_market_pulse(base, "").rstrip().endswith("#Кипр #курсы_валют #рынки")
    existing = base.replace("\n\n#", "\n\n📊 <b>Пульс рынков</b>\n24ч: BTC $60.3K ↑1.2%\n\n#")
    assert pulse.inject_market_pulse(existing, "ignored").rstrip().endswith("#Кипр #курсы_валют #рынки")


def main() -> None:
    checks = (
        cy_fx_numeric_lines_stay_unchanged,
        cy_plain_eur_summary_explains_displayed_moves,
        cy_plain_eur_summary_inverts_quote_arrow_semantics,
        cy_plain_eur_summary_handles_missing_daily_dynamics,
        cy_two_human_summaries_are_in_reading_order,
        cy_plain_summary_explains_mixed_ruble_moves_without_repeating_numbers,
        cy_plain_summary_handles_both_up,
        cy_plain_summary_handles_both_down,
        cy_plain_summary_handles_zero_and_missing,
        cy_missing_deltas_never_restore_old_jargon,
        cy_safe_runner_uses_same_summary_formatter,
        cy_no_cbr_values_keep_eur_summary_but_skip_ruble_summary,
        cy_market_pulse_is_compact,
        cy_fx_market_hashtag_survives_empty_or_existing_pulse,
    )
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    print(f"OK: {len(checks)} Cyprus FX/Market Pulse checks passed")


if __name__ == "__main__":
    main()
