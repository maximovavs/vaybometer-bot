#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline checks for Cyprus local earthquake monitoring."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


def _event(
    mag: float,
    *,
    place: str = "Cyprus region",
    lat: float = 34.60,
    lon: float = 32.95,
    depth: float = 12.0,
    minutes_ago: int = 30,
    source: str = "EMSC",
    event_id: str = "evt",
    status: str = "reviewed",
) -> dict:
    when = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    nearest_name, nearest_dist = earthquakes._nearest_city(lat, lon)
    return {
        "source": source,
        "sources": [source],
        "source_event_id": event_id,
        "mag": mag,
        "place": place,
        "time_utc": when.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "time_local": when.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "depth_km": depth,
        "lat": lat,
        "lon": lon,
        "distance_km": nearest_dist,
        "distance_from_center_km": earthquakes._haversine_km(
            earthquakes.CY_CENTER_LAT,
            earthquakes.CY_CENTER_LON,
            lat,
            lon,
        ),
        "nearest_city": nearest_name,
        "url": "https://example.test/quake",
        "status": status,
        "event_type": "earthquake",
    }


def _events(items, *, regional_ok: bool = True, usgs_ok: bool = True):
    return earthquakes.CyprusQuakeEvents(
        items,
        min_mag=0.9,
        hours=24,
        radius_km=350,
        source_status={
            "regional": {"ok": regional_ok, "count": len(items) if regional_ok else None},
            "usgs": {"ok": usgs_ok, "count": 0 if usgs_ok else None},
        },
    )


def test_m09_included_m08_excluded() -> None:
    line = earthquakes.build_cyprus_quake_line(
        _events([_event(0.8, event_id="m08"), _event(0.9, event_id="m09")])
    )
    assert_true("m09", "1 микрособытие" in line, line)
    assert_true("m09", "M0.9–1.9" in line, line)
    assert_true("m09", "M0.8" not in line, line)
    assert_true("m09", "балл" not in line.lower(), line)
    print("PASS m09_included_m08_excluded")


def test_micro_events_are_aggregated() -> None:
    items = [_event(0.9 + index * 0.2, event_id=f"micro{index}") for index in range(5)]
    line = earthquakes.build_cyprus_quake_line(_events(items))
    assert_true("micro", "5 микрособытий M0.9–1.9" in line, line)
    assert_true("micro", "M1.1" not in line and "M1.7" not in line, line)
    assert_true("micro", len(line.splitlines()) <= 2, line)
    print("PASS micro_events_are_aggregated")


def test_m22_weak_event_line() -> None:
    line = earthquakes.build_cyprus_quake_line(
        _events([
            _event(1.4, event_id="micro"),
            _event(2.2, place="Akrotiri, Limassol", lat=34.58, lon=32.98, event_id="m22"),
        ])
    )
    assert_true("m22", "1 микрособытие и 1 слабое событие" in line, line)
    assert_true("m22", "сильнейшее M2.2" in line, line)
    assert_true("m22", "Акротири" in line and "Лимассол" in line, line)
    assert_true("m22", "⚠️" not in line, line)
    print("PASS m22_weak_event_line")


def test_m25_outside_24h_excluded() -> None:
    old_event = _event(2.5, event_id="old", minutes_ago=25 * 60)
    fresh_event = _event(0.9, event_id="fresh", minutes_ago=10)
    filtered = earthquakes._filter_events(
        [old_event, fresh_event],
        min_mag=0.9,
        radius_km=350,
        hours=24,
        now=datetime.now(timezone.utc),
    )
    line = earthquakes.build_cyprus_quake_line(_events(filtered))
    assert_true("old", len(filtered) == 1, filtered)
    assert_true("old", "M2.5" not in line, line)
    assert_true("old", "1 микрособытие" in line, line)
    print("PASS m25_outside_24h_excluded")


def test_m34_clear_without_damage_claims() -> None:
    line = earthquakes.build_cyprus_quake_line(
        _events([_event(3.4, place="Cyprus region", lat=34.90, lon=32.10, event_id="m34")])
    )
    low = line.lower()
    assert_true("m34", "сильнейшее событие M3.4" in line, line)
    assert_true("m34", "⚠️" not in line, line)
    for forbidden in ("ущерб", "опасн", "разруш", "пострад"):
        assert_true("m34", forbidden not in low, line)
    print("PASS m34_clear_without_damage_claims")


def test_m42_warning_includes_depth() -> None:
    line = earthquakes.build_cyprus_quake_line(
        _events([_event(4.2, place="Paphos, Cyprus", lat=34.80, lon=32.20, depth=18.0, event_id="m42")])
    )
    assert_true("m42", "⚠️" in line, line)
    assert_true("m42", "M4.2" in line, line)
    assert_true("m42", "глубина 18 км" in line, line)
    print("PASS m42_warning_includes_depth")


def test_no_events_threshold_aware_not_absolute() -> None:
    line = earthquakes.build_cyprus_quake_line(_events([]))
    assert_true("empty", "событий M0.9+ рядом с Кипром не найдено" in line, line)
    assert_true("empty", "землетрясений не было" not in line, line)
    assert_true("empty", "полностью спокойно" not in line, line)
    print("PASS no_events_threshold_aware_not_absolute")


def test_complete_source_failure_is_not_calm_claim() -> None:
    line = earthquakes.build_cyprus_quake_line(None)
    assert_true("failure", "данные временно не обновились" in line, line)
    assert_true("failure", "спокойно" not in line, line)
    print("PASS complete_source_failure_is_not_calm_claim")


def test_regional_failure_does_not_claim_no_m09_events() -> None:
    line = earthquakes.build_cyprus_quake_line(_events([], regional_ok=False, usgs_ok=True))
    assert_true("regional_fail", "региональные данные" in line, line)
    assert_true("regional_fail", "M0.9+ рядом с Кипром не найдено" not in line, line)
    assert_true("regional_fail", "спокойно" not in line, line)
    assert_true("regional_fail", len(line.splitlines()) <= 2, line)
    print("PASS regional_failure_does_not_claim_no_m09_events")


def test_two_source_duplicate_counts_once() -> None:
    base = _event(1.4, event_id="emsc1", source="EMSC", minutes_ago=10)
    duplicate = _event(1.5, event_id="usgs1", source="USGS", minutes_ago=11, status="automatic")
    merged = earthquakes.deduplicate_events([base, duplicate])
    line = earthquakes.build_cyprus_quake_line(_events(merged))
    assert_true("dedup", len(merged) == 1, merged)
    assert_true("dedup", "1 микрособытие" in line, line)
    assert_true("dedup", set(merged[0]["sources"]) == {"EMSC", "USGS"}, merged)
    print("PASS two_source_duplicate_counts_once")


def test_two_distinct_events_count_separately() -> None:
    first = _event(1.4, event_id="one", lat=34.58, lon=32.98, minutes_ago=10)
    second = _event(2.2, event_id="two", lat=35.80, lon=34.00, minutes_ago=140)
    merged = earthquakes.deduplicate_events([first, second])
    line = earthquakes.build_cyprus_quake_line(_events(merged))
    assert_true("distinct", len(merged) == 2, merged)
    assert_true("distinct", "1 микрособытие и 1 слабое событие" in line, line)
    print("PASS two_distinct_events_count_separately")


def test_quarry_blast_explosion_excluded() -> None:
    feature = {
        "id": "blast",
        "properties": {
            "mag": 1.6,
            "place": "Cyprus quarry",
            "time": int(datetime.now(timezone.utc).timestamp() * 1000),
            "type": "quarry blast",
            "url": "https://example.test/blast",
        },
        "geometry": {"type": "Point", "coordinates": [33.0, 34.7, 3.0]},
    }
    assert_true("blast", earthquakes._normalize_usgs_feature(feature) is None)
    feature["properties"]["type"] = "explosion"
    assert_true("explosion", earthquakes._normalize_usgs_feature(feature) is None)
    print("PASS quarry_blast_explosion_excluded")


def test_default_fetch_uses_m09() -> None:
    old_regional = earthquakes.fetch_regional_events
    old_usgs = earthquakes.fetch_usgs_events
    seen: list[float] = []

    def fake_regional(**kwargs):
        seen.append(float(kwargs["min_mag"]))
        return []

    def fake_usgs(**kwargs):
        seen.append(float(kwargs["min_mag"]))
        return []

    earthquakes.fetch_regional_events = fake_regional
    earthquakes.fetch_usgs_events = fake_usgs
    try:
        events = earthquakes.get_recent_earthquakes_cyprus()
    finally:
        earthquakes.fetch_regional_events = old_regional
        earthquakes.fetch_usgs_events = old_usgs
    assert_true("default", isinstance(events, earthquakes.CyprusQuakeEvents))
    assert_true("default", seen == [0.9, 0.9], seen)
    print("PASS default_fetch_uses_m09")


def test_format_v2_preserves_quake_line() -> None:
    quake_line = "🌍 Сейсмика 24ч: 1 микрособытие M0.9–1.9; заметных событий M2.0+ не найдено."
    legacy = "\n".join(
        [
            "<b>Кипр: погода на сегодня (25.06.2026)</b>",
            "Доброе утро! Теплее всего — Никосия (37°), прохладнее — Пафос (28°).",
            "🏭 AQI 25 (низкий) • PM₂.₅ 8 / PM₁₀ 28",
            quake_line,
            "✅ Сегодня: вода и завтрак.",
            "#Кипр #погода #здоровье",
        ]
    )
    out = build_format_v2("Кипр", "morning", legacy)
    assert_true("format_v2", quake_line in out, out)
    print("PASS format_v2_preserves_quake_line")


def test_post_common_source_failure_line() -> None:
    sys.modules.setdefault("imghdr", types.SimpleNamespace(what=lambda *_args, **_kwargs: None))
    pendulum_stub = types.ModuleType("pendulum")
    pendulum_stub.DateTime = object
    pendulum_stub.now = lambda *_args, **_kwargs: None
    pendulum_stub.today = lambda *_args, **_kwargs: None
    sys.modules.setdefault("pendulum", pendulum_stub)
    telegram_stub = types.ModuleType("telegram")
    telegram_stub.Bot = object
    telegram_stub.constants = types.SimpleNamespace(ParseMode=types.SimpleNamespace(HTML="HTML"))
    sys.modules.setdefault("telegram", telegram_stub)
    import post_common

    old_env = post_common.os.environ.get("CY_QUAKES_24H")
    old_get = post_common.get_recent_earthquakes_cyprus
    post_common.os.environ["CY_QUAKES_24H"] = "1"
    post_common.get_recent_earthquakes_cyprus = lambda **_kwargs: None
    try:
        line = post_common._cyprus_quake_line_for_morning()
        assert_true("post_common_failure", line is not None)
        assert_true("post_common_failure", "данные временно не обновились" in line, line)
    finally:
        post_common.get_recent_earthquakes_cyprus = old_get
        if old_env is None:
            post_common.os.environ.pop("CY_QUAKES_24H", None)
        else:
            post_common.os.environ["CY_QUAKES_24H"] = old_env
    print("PASS post_common_source_failure_line")


TESTS = [
    test_m09_included_m08_excluded,
    test_micro_events_are_aggregated,
    test_m22_weak_event_line,
    test_m25_outside_24h_excluded,
    test_m34_clear_without_damage_claims,
    test_m42_warning_includes_depth,
    test_no_events_threshold_aware_not_absolute,
    test_complete_source_failure_is_not_calm_claim,
    test_regional_failure_does_not_claim_no_m09_events,
    test_two_source_duplicate_counts_once,
    test_two_distinct_events_count_separately,
    test_quarry_blast_explosion_excluded,
    test_default_fetch_uses_m09,
    test_format_v2_preserves_quake_line,
    test_post_common_source_failure_line,
]


def main() -> None:
    for test in TESTS:
        test()
    print(f"OK: {len(TESTS)} Cyprus earthquake offline checks passed")


if __name__ == "__main__":
    main()
