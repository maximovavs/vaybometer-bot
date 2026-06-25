#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline checks for Cyprus 24h earthquake summary."""
from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import earthquakes  # noqa: E402
from format_v2 import build_format_v2  # noqa: E402


def assert_true(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        raise AssertionError(f"{name}: {detail or 'assertion failed'}")


def _feature(mag: float, lon: float, lat: float, depth: float, ts_ms: int = 1782381600000) -> dict:
    return {
        "type": "Feature",
        "properties": {
            "mag": mag,
            "place": "Cyprus region",
            "time": ts_ms,
            "url": "https://earthquake.usgs.gov/example",
        },
        "geometry": {
            "type": "Point",
            "coordinates": [lon, lat, depth],
        },
    }


def test_no_events_line() -> None:
    line = earthquakes.build_cyprus_quake_line([])
    assert_true("no_events", "спокойно" in line)
    assert_true("no_events", "24ч" in line)
    assert_true("no_events", len(line) < 140)
    print("PASS no_events_line")


def test_weak_event_line() -> None:
    event = earthquakes._normalize_event(_feature(2.8, 32.45, 34.80, 12.0))
    line = earthquakes.build_cyprus_quake_line([event])
    assert_true("weak_event", "1 событие" in line)
    assert_true("weak_event", "M2.8" in line)
    assert_true("weak_event", "Пафос" in line)
    assert_true("weak_event", "⚠️" not in line)
    assert_true("weak_event", len(line) < 150)
    print("PASS weak_event_line")


def test_significant_event_line() -> None:
    event = earthquakes._normalize_event(_feature(4.2, 32.45, 34.80, 18.0))
    line = earthquakes.build_cyprus_quake_line([event])
    assert_true("significant_event", "⚠️" in line)
    assert_true("significant_event", "M4.2" in line)
    assert_true("significant_event", "глубина 18 км" in line)
    assert_true("significant_event", len(line) < 140)
    print("PASS significant_event_line")


def test_malformed_source_no_crash() -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"features": "not-a-list"}

    old_get = earthquakes.requests.get
    earthquakes.requests.get = lambda *_args, **_kwargs: FakeResponse()
    try:
        events = earthquakes.get_recent_earthquakes_cyprus()
        assert_true("malformed", events is None)
    finally:
        earthquakes.requests.get = old_get
    print("PASS malformed_source_no_crash")


def test_format_v2_preserves_quake_line() -> None:
    legacy = "\n".join(
        [
            "<b>Кипр: погода на сегодня (25.06.2026)</b>",
            "Доброе утро! Теплее всего — Никосия (37°), прохладнее — Пафос (28°).",
            "🏭 AQI 25 (низкий) • PM₂.₅ 8 / PM₁₀ 28",
            "🌍 Сейсмика 24ч: спокойно — заметных землетрясений рядом с Кипром не было.",
            "✅ Сегодня: вода и завтрак.",
            "#Кипр #погода #здоровье",
        ]
    )
    out = build_format_v2("Кипр", "morning", legacy)
    assert_true("format_v2", "🌍 Сейсмика 24ч:" in out)
    assert_true("format_v2", "спокойно" in out)
    print("PASS format_v2_preserves_quake_line")


def test_post_common_skip_on_source_failure() -> None:
    sys.modules.setdefault("imghdr", types.SimpleNamespace(what=lambda *_args, **_kwargs: None))
    import post_common

    old_env = post_common.os.environ.get("CY_QUAKES_24H")
    old_get = post_common.get_recent_earthquakes_cyprus
    post_common.os.environ["CY_QUAKES_24H"] = "1"
    post_common.get_recent_earthquakes_cyprus = lambda **_kwargs: None
    try:
        assert_true("post_common_skip", post_common._cyprus_quake_line_for_morning() is None)
    finally:
        post_common.get_recent_earthquakes_cyprus = old_get
        if old_env is None:
            post_common.os.environ.pop("CY_QUAKES_24H", None)
        else:
            post_common.os.environ["CY_QUAKES_24H"] = old_env
    print("PASS post_common_skip_on_source_failure")


def main() -> None:
    test_no_events_line()
    test_weak_event_line()
    test_significant_event_line()
    test_malformed_source_no_crash()
    test_format_v2_preserves_quake_line()
    test_post_common_skip_on_source_failure()
    print("OK: Cyprus earthquake offline checks passed")


if __name__ == "__main__":
    main()
