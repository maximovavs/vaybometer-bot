#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic identity of a scheduled Cyprus evening publication cycle.

GitHub reports only the cron expression of a scheduled run
(``GITHUB_EVENT_SCHEDULE``), never the unique scheduled occurrence, and a
delayed run is created late rather than backdated.  The cycle identity is
therefore resolved as the most recent local occurrence of that cron at or
before the execution clock (``Asia/Nicosia``, the timezone the workflow
schedules are declared in).

The contract for one evening cycle:

* ``slot_local_date``            D (local calendar day of the slot)
* ``expected_forecast_date``     D + 1 (evening text "for tomorrow")
* ``expected_image_date``        D + 1 (same identity as the text)
* ``expected_quiz_date``         D

A cycle is publishable only while the execution clock is still on D.  A run
that executes after local midnight belongs to an expired slot and must not
send, reserve or write receipts, so it can never occupy the next cycle.

Known limitation: a run delayed by 24h or more that lands inside the *next*
day's publication window is indistinguishable from that day's on-time run;
it is resolved as the later occurrence (never as a guessed older date).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Mapping
from zoneinfo import ZoneInfo

CYCLE_TZ = "Asia/Nicosia"
EVENING_PRIMARY_SCHEDULE = "0 16 * * *"
EVENING_RECOVERY_SCHEDULES = ("45 16 * * *", "15 18 * * *")
EVENING_SCHEDULES = (EVENING_PRIMARY_SCHEDULE, *EVENING_RECOVERY_SCHEDULES)

STATUS_VALID = "valid"
STATUS_EXPIRED = "expired"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_NOT_SCHEDULED = "not_scheduled"

ENV_KEYS = (
    "CY_CYCLE_STATUS",
    "CY_CYCLE_REASON",
    "CY_CYCLE_EVENT_SCHEDULE",
    "CY_CYCLE_SLOT_LOCAL_DATETIME",
    "CY_CYCLE_SLOT_LOCAL_DATE",
    "CY_CYCLE_RESOLVED_AT_LOCAL",
    "CY_CYCLE_EXPECTED_FORECAST_DATE",
    "CY_CYCLE_EXPECTED_IMAGE_DATE",
    "CY_CYCLE_EXPECTED_QUIZ_DATE",
)


def parse_daily_cron(expr: str) -> tuple[int, int] | None:
    """Return (hour, minute) for a plain daily cron ``M H * * *``; otherwise None."""
    fields = str(expr or "").strip().split(" ")
    if len(fields) != 5 or fields[2:] != ["*", "*", "*"]:
        return None
    minute_s, hour_s = fields[0], fields[1]
    if not (minute_s.isdigit() and hour_s.isdigit()):
        return None
    minute, hour = int(minute_s), int(hour_s)
    if not (0 <= minute <= 59 and 0 <= hour <= 23):
        return None
    return hour, minute


def local_slot(day: date, hour: int, minute: int, tz: ZoneInfo) -> datetime | None:
    """Unique aware local datetime for ``day hour:minute``; None if skipped or repeated (DST)."""
    first = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz, fold=0)
    second = first.replace(fold=1)
    if first.utcoffset() != second.utcoffset():
        return None  # wall time occurs twice (fall-back) -> ambiguous
    round_trip = first.astimezone(timezone.utc).astimezone(tz)
    if (round_trip.hour, round_trip.minute) != (hour, minute) or round_trip.date() != day:
        return None  # wall time does not exist (spring-forward gap)
    return first


def latest_daily_occurrence(now_local: datetime, hour: int, minute: int, tz: ZoneInfo) -> datetime | None:
    """Most recent occurrence of ``hour:minute`` at or before ``now_local``; None if unresolvable."""
    today = local_slot(now_local.date(), hour, minute, tz)
    if today is None:
        return None
    if today <= now_local:
        return today
    return local_slot(now_local.date() - timedelta(days=1), hour, minute, tz)


@dataclass(frozen=True)
class EveningCycle:
    status: str
    reason: str
    event_name: str
    event_schedule: str
    execution_local_datetime: datetime
    slot_local_datetime: datetime | None = None

    @property
    def is_valid(self) -> bool:
        return self.status == STATUS_VALID

    @property
    def is_guarded(self) -> bool:
        """True when the scheduled-evening date contract applies to this run."""
        return self.status != STATUS_NOT_SCHEDULED

    @property
    def role(self) -> str:
        if self.event_schedule == EVENING_PRIMARY_SCHEDULE:
            return "primary"
        if self.event_schedule in EVENING_RECOVERY_SCHEDULES:
            return "recovery"
        return ""

    @property
    def slot_local_date(self) -> date | None:
        return self.slot_local_datetime.date() if self.slot_local_datetime else None

    @property
    def expected_forecast_date(self) -> date | None:
        slot = self.slot_local_date
        return slot + timedelta(days=1) if slot else None

    @property
    def expected_image_date(self) -> date | None:
        return self.expected_forecast_date

    @property
    def expected_quiz_date(self) -> date | None:
        return self.slot_local_date

    def at(self, now: datetime) -> "EveningCycle":
        """Re-evaluate the publication window for the *same* slot identity at ``now``."""
        if self.slot_local_datetime is None or self.status in (STATUS_NOT_SCHEDULED, STATUS_AMBIGUOUS):
            return self
        if now.tzinfo is None:
            return EveningCycle(STATUS_AMBIGUOUS, "naive_execution_clock", self.event_name,
                                self.event_schedule, now, self.slot_local_datetime)
        now_local = now.astimezone(self.slot_local_datetime.tzinfo)
        status, reason = _window_status(self.slot_local_datetime, now_local)
        return EveningCycle(status, reason, self.event_name, self.event_schedule,
                            now_local, self.slot_local_datetime)

    def as_record(self) -> dict[str, str]:
        def iso(value: object) -> str:
            return value.isoformat() if value is not None else ""

        return {
            "status": self.status,
            "reason": self.reason,
            "role": self.role,
            "event_schedule": self.event_schedule,
            "slot_local_datetime": iso(self.slot_local_datetime),
            "slot_local_date": iso(self.slot_local_date),
            "execution_local_datetime": iso(self.execution_local_datetime),
            "execution_local_date": iso(self.execution_local_datetime.date()),
            "expected_forecast_date": iso(self.expected_forecast_date),
            "expected_image_date": iso(self.expected_image_date),
            "expected_quiz_date": iso(self.expected_quiz_date),
        }

    def marker(self) -> str:
        label = {
            STATUS_VALID: "CY_EVENING_CYCLE_VALID",
            STATUS_EXPIRED: "CY_EVENING_SLOT_EXPIRED",
            STATUS_AMBIGUOUS: "CY_EVENING_CYCLE_AMBIGUOUS",
        }.get(self.status, "CY_EVENING_CYCLE_NOT_SCHEDULED")
        return f"{label}: {json.dumps(self.as_record(), ensure_ascii=False, sort_keys=True)}"

    def env(self) -> dict[str, str]:
        record = self.as_record()
        return {
            "CY_CYCLE_STATUS": self.status,
            "CY_CYCLE_REASON": self.reason,
            "CY_CYCLE_EVENT_SCHEDULE": self.event_schedule,
            "CY_CYCLE_SLOT_LOCAL_DATETIME": record["slot_local_datetime"],
            "CY_CYCLE_SLOT_LOCAL_DATE": record["slot_local_date"],
            "CY_CYCLE_RESOLVED_AT_LOCAL": record["execution_local_datetime"],
            "CY_CYCLE_EXPECTED_FORECAST_DATE": record["expected_forecast_date"],
            "CY_CYCLE_EXPECTED_IMAGE_DATE": record["expected_image_date"],
            "CY_CYCLE_EXPECTED_QUIZ_DATE": record["expected_quiz_date"],
        }


def _window_status(slot: datetime, now_local: datetime) -> tuple[str, str]:
    if now_local < slot:
        return STATUS_AMBIGUOUS, "execution_before_slot"
    if now_local.date() != slot.date():
        return STATUS_EXPIRED, "executed_after_local_midnight"
    return STATUS_VALID, "within_slot_local_day"


def resolve_evening_cycle(
    *,
    event_name: str,
    event_schedule: str,
    now: datetime | None = None,
    anchor: Mapping[str, str] | None = None,
    tz_name: str = CYCLE_TZ,
) -> EveningCycle:
    """Resolve the evening cycle for a run; fail closed on anything not provable."""
    tz = ZoneInfo(tz_name)
    clock = now if now is not None else datetime.now(timezone.utc)
    schedule = str(event_schedule or "").strip()
    name = str(event_name or "").strip()
    if clock.tzinfo is None:
        return EveningCycle(STATUS_AMBIGUOUS, "naive_execution_clock", name, schedule, clock)
    now_local = clock.astimezone(tz)
    if name != "schedule":
        return EveningCycle(STATUS_NOT_SCHEDULED, "not_a_scheduled_run", name, schedule, now_local)
    if not schedule:
        return EveningCycle(STATUS_AMBIGUOUS, "missing_event_schedule", name, schedule, now_local)
    if schedule not in EVENING_SCHEDULES:
        return EveningCycle(STATUS_AMBIGUOUS, "unknown_evening_schedule", name, schedule, now_local)
    parsed = parse_daily_cron(schedule)
    if parsed is None:
        return EveningCycle(STATUS_AMBIGUOUS, "unparseable_event_schedule", name, schedule, now_local)
    slot = latest_daily_occurrence(now_local, parsed[0], parsed[1], tz)
    if slot is None:
        return EveningCycle(STATUS_AMBIGUOUS, "unresolvable_local_slot", name, schedule, now_local)

    anchor_values = {key: str((anchor or {}).get(key) or "").strip() for key in ENV_KEYS}
    if any(anchor_values.values()):
        if anchor_values["CY_CYCLE_EVENT_SCHEDULE"] != schedule:
            return EveningCycle(STATUS_AMBIGUOUS, "anchor_schedule_mismatch", name, schedule, now_local, slot)
        try:
            anchored_slot = datetime.fromisoformat(anchor_values["CY_CYCLE_SLOT_LOCAL_DATETIME"])
        except ValueError:
            return EveningCycle(STATUS_AMBIGUOUS, "anchor_slot_unreadable", name, schedule, now_local, slot)
        if anchored_slot.tzinfo is None or anchored_slot != slot:
            return EveningCycle(STATUS_AMBIGUOUS, "anchor_slot_mismatch", name, schedule, now_local, slot)

    status, reason = _window_status(slot, now_local)
    return EveningCycle(status, reason, name, schedule, now_local, slot)


def evening_cycle_from_environment(
    env: Mapping[str, str] | None = None,
    *,
    now: datetime | None = None,
) -> EveningCycle:
    source = os.environ if env is None else env
    return resolve_evening_cycle(
        event_name=source.get("GITHUB_EVENT_NAME", ""),
        event_schedule=source.get("GITHUB_EVENT_SCHEDULE", ""),
        now=now,
        anchor={key: source.get(key, "") for key in ENV_KEYS},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve the Cyprus evening publication cycle identity.")
    parser.add_argument("--github-env", default="", help="Append CY_CYCLE_* identity to this GITHUB_ENV file.")
    args = parser.parse_args(argv)
    env = {key: value for key, value in os.environ.items() if key not in ENV_KEYS}
    cycle = evening_cycle_from_environment(env)
    print(cycle.marker())
    if args.github_env:
        with open(args.github_env, "a", encoding="utf-8") as fh:
            for key, value in cycle.env().items():
                fh.write(f"{key}={value}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
