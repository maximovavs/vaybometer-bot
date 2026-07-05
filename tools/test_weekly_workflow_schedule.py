#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression checks for Cyprus weekly workflow scheduling guards."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "weekly_forecast.yml"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _scheduled_week_start(local_day: date) -> str:
    days_since_saturday = (local_day.weekday() - 5) % 7
    scheduled_saturday = local_day - timedelta(days=days_since_saturday)
    return (scheduled_saturday + timedelta(days=2)).isoformat()


def _recovery_guard_accepts(schedule_expr: str, local_dt: datetime) -> bool:
    return schedule_expr == "0 11 5 7 *" and local_dt.date().isoformat() == "2026-07-05"


def test_cyprus_weekly_workflow_has_recovery_cron_and_guard() -> None:
    text = _workflow_text()
    assert 'cron: "0 11 5 7 *"' in text
    assert "# One-time recovery trigger: 2026-07-05 14:00 Asia/Nicosia." in text
    assert "Protected by an explicit year/date guard" in text
    assert 'if [ "${SCHEDULE_EXPR:-}" = "0 11 5 7 *" ]; then' in text
    assert 'if [ "$local_date" = "2026-07-05" ]; then' in text
    assert 'echo "week_start=2026-07-06" >> "$GITHUB_OUTPUT"' in text
    assert "Recovery schedule requested for 2026-07-05 14:00 Asia/Nicosia" in text
    assert "Skipping expired one-time recovery cron." in text


def test_cyprus_recovery_run_is_production_only() -> None:
    text = _workflow_text()
    assert 'CHANNEL_ID_OVERRIDE=""' in text
    assert 'SEND_TO_TEST="false"' in text
    assert 'PUBLISH_TO_PROD="true"' in text
    assert 'DRY_RUN="false"' in text
    assert "Weekly forecast recovery target: production" in text
    assert "No Telegram destination selected. Choose test, prod, or channel_override." in text


def test_cyprus_weekly_workflow_accepts_delayed_seasonal_runs() -> None:
    text = _workflow_text()
    assert 'cron: "0 19 * * 6"' in text
    assert 'cron: "0 20 * * 6"' in text
    assert "SCHEDULE_EXPR: ${{ github.event.schedule }}" in text
    assert "Weekly schedule expression:" in text
    assert "Current Cyprus UTC offset:" in text
    assert '"0 19 * * 6|+0300"|"0 20 * * 6|+0200")' in text
    assert "Scheduled weekly run accepted despite runner delay." in text
    assert "Skipping inactive seasonal weekly cron." in text
    assert "date +%u" not in text
    assert "date +%H" not in text
    assert "outside Saturday 22:00" not in text


def test_cyprus_scheduled_week_start_uses_next_monday() -> None:
    assert _scheduled_week_start(date(2026, 7, 4)) == "2026-07-06"
    assert _scheduled_week_start(date(2026, 7, 5)) == "2026-07-06"
    text = _workflow_text()
    assert "days_since_saturday = (today.weekday() - 5) % 7" in text
    assert "scheduled_saturday = today - timedelta(days=days_since_saturday)" in text
    assert "week_start = scheduled_saturday + timedelta(days=2)" in text
    assert 'args+=("--date" "$WEEK_START_DATE")' in text


def test_cyprus_recovery_date_guard_is_one_time_but_delay_tolerant() -> None:
    assert _recovery_guard_accepts("0 11 5 7 *", datetime(2026, 7, 5, 14, 7))
    assert _recovery_guard_accepts("0 11 5 7 *", datetime(2026, 7, 5, 23, 59))
    assert not _recovery_guard_accepts("0 11 5 7 *", datetime(2026, 7, 6, 0, 1))
    assert not _recovery_guard_accepts("0 19 * * 6", datetime(2026, 7, 5, 14, 7))


def main() -> None:
    tests = (
        test_cyprus_weekly_workflow_has_recovery_cron_and_guard,
        test_cyprus_recovery_run_is_production_only,
        test_cyprus_weekly_workflow_accepts_delayed_seasonal_runs,
        test_cyprus_scheduled_week_start_uses_next_monday,
        test_cyprus_recovery_date_guard_is_one_time_but_delay_tolerant,
    )
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"OK: {len(tests)} Cyprus weekly workflow schedule checks passed")


if __name__ == "__main__":
    main()
