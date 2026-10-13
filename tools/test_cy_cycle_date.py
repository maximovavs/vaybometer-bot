#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline checks for the Cyprus scheduled-evening date contract.

Drives the real ``cy_cycle_date``, ``safe_test_post.main`` (with the real
image pipeline, offline backends and a fake Telegram bot) and
``cyprus_culture_quiz.deliver_quiz`` under fixed clocks.  No network, no
Telegram, no providers.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import types
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pendulum  # noqa: E402

import cy_cycle_date  # noqa: E402
import cyprus_culture_quiz as quiz  # noqa: E402
import cyprus_provider_image_qa  # noqa: E402
import cyprus_visual_dedup  # noqa: E402
import safe_test_post as safe_module  # noqa: E402
import world_en.imagegen as imagegen  # noqa: E402

NICOSIA = ZoneInfo("Asia/Nicosia")
BANK = ROOT / "data/cyprus_culture/v2026-10-exam-core-v5/questions.jsonl"
BANK_VERSION = "v2026-10-exam-core-v5"
PRIMARY = "0 16 * * *"
RECOVERY_1645 = "45 16 * * *"
RECOVERY_1815 = "15 18 * * *"
PRIMARY_ARGV = [
    "safe_test_post.py", "--mode", "evening", "--chat-id", "777", "--send",
    "--no-test-label", "--generate-image", "--send-image-to-chat",
]
RECOVERY_ARGV = [
    "safe_test_post.py", "--mode", "evening", "--generate-image",
    "--send-image-to-chat", "--image-only-recovery", "--chat-id", "777",
]


def local(*args: int) -> datetime:
    return datetime(*args, tzinfo=NICOSIA)


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def set(self, value: datetime) -> None:
        self.now = value


def _write_dhash_fixture(path: Path) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (512, 512), (90, 140, 190))
    draw = ImageDraw.Draw(image)
    for row in range(8):
        for col in range(9):
            shade = 40 + ((row * 9 + col) * 23) % 200
            draw.rectangle([col * 56, row * 64, col * 56 + 55, row * 64 + 63], fill=(shade, 120, 255 - shade))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="JPEG", quality=92)


def _forecast_text(target: date) -> str:
    return (
        f"🌅 Кипр завтра ({target.strftime('%d.%m.%Y')})\n"
        "✨ VayboMeter завтра: 6.8/10 — с оговорками; жара и порывы у моря.\n"
        "🏙 Лимассол — 30/22 °C • переменная облачность • ветер 6 м/с, порывы 11 м/с • 🌊 26°C\n"
        "🏙 Ларнака — 31/22 °C • переменная облачность • ветер 5 м/с, порывы 10 м/с • 🌊 26°C\n"
        "✅ План завтра: море утром, днём тень и вода.\n"
        "#Кипр #погода #здоровье\n"
    )


def _morning_text(target: date) -> str:
    return (
        f"🌅 Кипр сегодня ({target.strftime('%d.%m.%Y')})\n"
        "✨ VayboMeter: 7.0/10 — хорошо.\n"
        "🏙 Лимассол — 30/22 °C • переменная облачность • ветер 5 м/с\n"
        "✅ План: море утром.\n"
        "#Кипр #погода #здоровье\n"
    )


def _fake_build_message(**kwargs) -> str:
    # Runs under safe_test_post._TodayPatch, so pendulum.today() is the anchored base date.
    today = pendulum.today(kwargs.get("tz") or "Asia/Nicosia").date()
    if str(kwargs.get("mode") or "").startswith("morning"):
        return _morning_text(today)
    return _forecast_text(today + timedelta(days=1))


class Harness:
    """Runs the real safe_test_post.main() with offline image backends and a fake bot."""

    ENV = (
        "CHANNEL_ID", "CHANNEL_ID_TEST", "CY_SAFE_IMAGE_DIR", "CY_IMG_MIN_BYTES", "CY_IMAGE_DELIVERY_DIR",
        "CY_TEXT_DELIVERY_DIR", "CY_IMAGE_DIAGNOSTICS_DIR", "CY_IMAGE_PROVIDER_HEALTH_DIR", "GITHUB_EVENT_NAME",
        "GITHUB_EVENT_SCHEDULE", "GITHUB_STEP_SUMMARY", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "FORMAT_V2",
        "WORK_DATE", "POST_MODE", "CY_MORNING_DELIVERY_DIR", *cy_cycle_date.ENV_KEYS,
    )

    def __init__(self, root: Path, clock: Clock) -> None:
        self.root = root
        self.clock = clock
        self.photos: list[dict] = []
        self.messages: list[dict] = []
        self.builds = 0
        self.on_image_generation = None
        self.on_photo_sent = None
        self._message_id = 5000

    def __enter__(self) -> "Harness":
        self._old_env = {name: os.environ.get(name) for name in self.ENV}
        self._old = {
            "TOKEN": safe_module.TOKEN,
            "Bot": safe_module.Bot,
            "build_message": safe_module.build_message,
            "clock": safe_module._cy_evening_clock,
            "outcome": imagegen.generate_astro_image_outcome_with_exclusions,
            "availability": imagegen.configured_image_backends,
            "qa": cyprus_provider_image_qa.evaluate_cyprus_provider_image_qa,
            "prod_history": cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH,
            "test_history": cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_TEST_PATH,
            "argv": list(sys.argv),
            "cwd": os.getcwd(),
        }
        for name in self.ENV:
            os.environ.pop(name, None)
        os.environ.update(
            {
                "CHANNEL_ID": "777",
                "CY_SAFE_IMAGE_DIR": str(self.root / "images"),
                "CY_IMG_MIN_BYTES": "1000",
                "CY_IMAGE_DELIVERY_DIR": str(self.root / "image-receipts"),
                "CY_TEXT_DELIVERY_DIR": str(self.root / "text-receipts"),
                "CY_IMAGE_DIAGNOSTICS_DIR": str(self.root / "diagnostics"),
                "CY_IMAGE_PROVIDER_HEALTH_DIR": str(self.root / "provider-health"),
                "CY_MORNING_DELIVERY_DIR": str(self.root / "morning-delivery"),
                "GITHUB_STEP_SUMMARY": str(self.root / "step-summary.md"),
                "GITHUB_RUN_ID": "2278",
                "GITHUB_RUN_ATTEMPT": "1",
                "FORMAT_V2": "0",
            }
        )
        history_prod = self.root / "history-prod.json"
        history_test = self.root / "history-test.json"
        history_prod.write_text("[]", "utf-8")
        history_test.write_text("[]", "utf-8")
        cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH = history_prod
        cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_TEST_PATH = history_test
        (self.root / "work").mkdir(parents=True, exist_ok=True)
        os.chdir(self.root / "work")

        harness = self

        class FakeBot:
            def __init__(self, token: str) -> None:
                assert token == "fixture-token"

            async def send_photo(self, **kwargs):
                harness._message_id += 1
                harness.photos.append({"chat_id": kwargs["chat_id"], "caption": kwargs.get("caption", ""),
                                       "at": harness.clock()})
                if harness.on_photo_sent:
                    harness.on_photo_sent()
                return types.SimpleNamespace(message_id=harness._message_id)

            async def send_message(self, **kwargs):
                harness._message_id += 1
                harness.messages.append({"chat_id": kwargs["chat_id"], "text": kwargs["text"], "at": harness.clock()})
                return types.SimpleNamespace(message_id=harness._message_id)

        def build_message(**kwargs):
            harness.builds += 1
            return _fake_build_message(**kwargs)

        def outcome(_prompt: str, requested_path: str, **_kwargs):
            if harness.on_image_generation:
                harness.on_image_generation()
            path = Path(requested_path)
            _write_dhash_fixture(path)
            attempts = [{"backend": "pollinations", "result": "success", "http_status": 200,
                         "content_type": "image/jpeg", "payload_byte_count": path.stat().st_size}]
            generated = types.SimpleNamespace(path=str(path), backend="pollinations",
                                              byte_count=path.stat().st_size, backend_attempts=attempts)
            return types.SimpleNamespace(result=generated, backend_attempts=attempts, error_type="",
                                         error_message="", exhausted=False, actual_backend_call_count=1)

        real_qa = self._old["qa"]

        def offline_qa(image_path, **kwargs):
            checks = {
                "scene_family_present_and_dominant": True, "composition_present_and_dominant": True,
                "cyprus_mediterranean_character": True, "tropical_or_jungle_mismatch": False,
                "major_landscape_or_architecture_malformed": False, "weather_compatible": True,
                "visible_text_logo_or_watermark": False, "screenshot_or_ui": False,
                "photographic_editorial_realism": True,
            }
            kwargs["structured_evaluator"] = lambda _path, _request: {"checks": checks}
            return real_qa(image_path, **kwargs)

        safe_module.TOKEN = "fixture-token"
        safe_module.Bot = FakeBot
        safe_module.build_message = build_message
        safe_module._cy_evening_clock = self.clock
        imagegen.generate_astro_image_outcome_with_exclusions = outcome
        imagegen.configured_image_backends = lambda **_kwargs: {
            "configured_backends": ["pollinations"], "available_backends": ["pollinations"],
            "unconfigured_backends": ["stable_horde", "custom"],
        }
        cyprus_provider_image_qa.evaluate_cyprus_provider_image_qa = offline_qa
        return self

    def __exit__(self, *exc) -> None:
        safe_module.TOKEN = self._old["TOKEN"]
        safe_module.Bot = self._old["Bot"]
        safe_module.build_message = self._old["build_message"]
        safe_module._cy_evening_clock = self._old["clock"]
        safe_module._CY_EVENING_CYCLE = None
        imagegen.generate_astro_image_outcome_with_exclusions = self._old["outcome"]
        imagegen.configured_image_backends = self._old["availability"]
        cyprus_provider_image_qa.evaluate_cyprus_provider_image_qa = self._old["qa"]
        cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH = self._old["prod_history"]
        cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_TEST_PATH = self._old["test_history"]
        sys.argv = self._old["argv"]
        os.chdir(self._old["cwd"])
        for name, value in self._old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def run(self, argv: list[str], *, event: str = "schedule", schedule: str = PRIMARY,
            anchor: bool = True, extra_env: dict | None = None) -> dict:
        for key in cy_cycle_date.ENV_KEYS:
            os.environ.pop(key, None)
        os.environ["GITHUB_EVENT_NAME"] = event
        os.environ["GITHUB_EVENT_SCHEDULE"] = schedule
        if anchor and event == "schedule":
            # Same identity the workflow step writes to GITHUB_ENV at job start.
            env_file = self.root / "github-env"
            env_file.write_text("", "utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                cy_cycle_date_main_with_clock(self.clock(), env_file)
            for line in env_file.read_text("utf-8").splitlines():
                key, _, value = line.partition("=")
                os.environ[key] = value
        os.environ.update(extra_env or {})
        sys.argv = list(argv)
        out = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out):
            try:
                asyncio.run(safe_module.main())
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        return {"code": code, "stdout": out.getvalue(), "photos": list(self.photos),
                "messages": list(self.messages)}

    def receipts(self, kind: str) -> dict[str, dict]:
        directory = self.root / ("text-receipts" if kind == "text" else "image-receipts")
        if not directory.is_dir():
            return {}
        return {p.name: json.loads(p.read_text("utf-8")) for p in sorted(directory.glob("*.json"))}

    def write_text_receipt(self, target: str) -> None:
        safe_module._cy_write_json_atomic(
            safe_module._cy_text_receipt_path(target, "evening"),
            {"target_date": target, "post_type": "evening", "chat_type": "production",
             "telegram_message_ids": [4100], "text_chunk_count": 1, "run_id": "1", "run_attempt": "1",
             "sent_at_utc": "2026-10-12T13:05:00Z"},
        )

    def write_image_receipt(self, target: str) -> None:
        safe_module._cy_write_json_atomic(
            safe_module._cy_image_receipt_path(target, "evening"),
            {"target_date": target, "post_type": "evening", "chat_type": "production",
             "telegram_message_id": 4200, "sha256": "a" * 64, "selected_scene": "fixture",
             "sent_at_utc": "2026-10-12T13:04:00Z"},
        )


def cy_cycle_date_main_with_clock(now: datetime, env_file: Path) -> None:
    """Run the workflow CLI (cy_cycle_date.main) with a fixed clock."""
    real_resolve = cy_cycle_date.resolve_evening_cycle

    def fixed(**kwargs):
        kwargs["now"] = now
        return real_resolve(**kwargs)

    cy_cycle_date.resolve_evening_cycle = fixed
    try:
        assert cy_cycle_date.main(["--github-env", str(env_file)]) == 0
    finally:
        cy_cycle_date.resolve_evening_cycle = real_resolve


def _markers(stdout: str, name: str) -> list[dict]:
    found = []
    for line in stdout.splitlines():
        if line.startswith(name + ": "):
            found.append(json.loads(line[len(name) + 2:]))
    return found


def _quiz_weather_receipt(directory: Path, target: date) -> None:
    path = directory / f"{target.isoformat()}-evening.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"target_date": target.isoformat(), "post_type": "evening",
                                "chat_type": "production", "text_chunk_count": 1,
                                "telegram_message_ids": [1], "sent_at_utc": "2026-10-12T13:05:00Z"}), "utf-8")


def _deliver_quiz(root: Path, *, schedule: str, clock, weather_dir: Path | None = None,
                  slot: str = "evening_general", cycle_env: dict | None = None, event: str = "schedule"):
    sends: list[dict] = []

    async def send_poll(**kwargs):
        sends.append(kwargs)
        return types.SimpleNamespace(message_id=1790 + len(sends), poll=types.SimpleNamespace(id=f"p{len(sends)}"))

    result = asyncio.run(quiz.deliver_quiz(
        slot=slot, enabled=True, event_name=event, event_schedule=schedule, chat_id="777", token="x",
        bank_path=BANK, bank_version=BANK_VERSION, anchor_date_text="2026-10-07",
        weather_receipt_dir=weather_dir or (root / "text-receipts"), fx_receipt_dir=root / "fx",
        quiz_receipt_dir=root / "quiz", send_poll=send_poll, clock=clock, cycle_env=cycle_env,
    ))
    return result, sends


def _load_restore_helper():
    spec = importlib.util.spec_from_file_location(
        "restore_cy_visual_snapshot", ROOT / ".github" / "scripts" / "restore_cy_visual_snapshot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- resolution


def resolution_contract_and_dst_boundaries() -> None:
    """Scenario 12: summer UTC+3, winter UTC+2 and DST transition days, through Asia/Nicosia."""
    cases = [
        # (instant, expected status, slot local date)
        ("2026-10-12T13:00:00Z", "valid", "2026-10-12"),      # 16:00 summer
        ("2026-10-12T20:59:59Z", "valid", "2026-10-12"),      # 23:59:59 summer
        ("2026-10-12T21:00:00Z", "expired", "2026-10-12"),    # 00:00 summer
        ("2026-10-25T14:00:00Z", "valid", "2026-10-25"),      # DST end day, 16:00 winter
        ("2026-10-25T21:59:59Z", "valid", "2026-10-25"),      # 23:59:59 winter
        ("2026-10-25T22:00:00Z", "expired", "2026-10-25"),    # 00:00 winter
        ("2026-10-24T20:59:59Z", "valid", "2026-10-24"),      # last summer evening
        ("2026-10-24T21:00:00Z", "expired", "2026-10-24"),
        ("2026-03-29T13:00:00Z", "valid", "2026-03-29"),      # DST start day, 16:00 summer
        ("2026-03-28T21:59:59Z", "valid", "2026-03-28"),      # winter evening before switch
        ("2026-03-28T22:00:00Z", "expired", "2026-03-28"),
        ("2026-12-21T14:00:00Z", "valid", "2026-12-21"),
        ("2026-12-21T22:30:00Z", "expired", "2026-12-21"),
    ]
    for instant, status, slot_day in cases:
        cycle = cy_cycle_date.resolve_evening_cycle(event_name="schedule", event_schedule=PRIMARY, now=utc(instant))
        assert cycle.status == status, (instant, cycle.as_record())
        assert cycle.slot_local_date.isoformat() == slot_day, (instant, cycle.as_record())
        assert cycle.slot_local_datetime.hour == 16 and cycle.slot_local_datetime.minute == 0
        assert cycle.expected_forecast_date == cycle.slot_local_date + timedelta(days=1)
        assert cycle.expected_image_date == cycle.expected_forecast_date
        assert cycle.expected_quiz_date == cycle.slot_local_date
    winter = cy_cycle_date.resolve_evening_cycle(event_name="schedule", event_schedule=PRIMARY,
                                                 now=utc("2026-10-25T14:00:00Z"))
    assert winter.slot_local_datetime.utcoffset() == timedelta(hours=2)
    summer = cy_cycle_date.resolve_evening_cycle(event_name="schedule", event_schedule=PRIMARY,
                                                 now=utc("2026-10-24T13:00:00Z"))
    assert summer.slot_local_datetime.utcoffset() == timedelta(hours=3)
    # Wall times that do not exist / occur twice on DST days are never guessed.
    assert cy_cycle_date.local_slot(date(2026, 3, 29), 3, 30, NICOSIA) is None
    assert cy_cycle_date.local_slot(date(2026, 10, 25), 3, 30, NICOSIA) is None
    assert cy_cycle_date.local_slot(date(2026, 10, 25), 16, 0, NICOSIA) is not None
    for schedule, hour, minute in ((RECOVERY_1645, 16, 45), (RECOVERY_1815, 18, 15)):
        cycle = cy_cycle_date.resolve_evening_cycle(event_name="schedule", event_schedule=schedule,
                                                    now=local(2026, 10, 12, 18, 30))
        assert cycle.is_valid and cycle.role == "recovery"
        assert (cycle.slot_local_datetime.hour, cycle.slot_local_datetime.minute) == (hour, minute)
    print("PASS resolution_contract_and_dst_boundaries")


def workflow_and_module_schedules_agree() -> None:
    """The guarded crons are exactly the workflow evening crons; the timetable is unchanged."""
    text = (ROOT / ".github" / "workflows" / "daily_post.yml").read_text("utf-8")
    crons = re.findall(r"- cron: '([^']+)'\n\s+timezone: '([^']+)'", text)
    assert crons == [
        ("0 6 * * *", "Asia/Nicosia"), ("15 8 * * *", "Asia/Nicosia"), ("5 12 * * *", "Asia/Nicosia"),
        ("45 9 * * *", "Asia/Nicosia"), ("0 16 * * *", "Asia/Nicosia"), ("45 16 * * *", "Asia/Nicosia"),
        ("15 18 * * *", "Asia/Nicosia"), ("0 12 * * *", "Asia/Nicosia"),
    ], crons
    assert cy_cycle_date.CYCLE_TZ == "Asia/Nicosia"
    assert set(cy_cycle_date.EVENING_SCHEDULES) == set(quiz.EVENING_SCHEDULES)
    evening = text.split("\n  evening:\n", 1)[1].split("\n  morning_image_recovery:\n", 1)[0]
    recovery = text.split("\n  evening_image_recovery:\n", 1)[1].split("\n  noon_fx:\n", 1)[0]
    for name, block in (("evening", evening), ("recovery", recovery)):
        step = block.index("Resolve Cyprus evening cycle identity")
        assert block.index("actions/checkout@v4") < step < block.index("restore_cy_visual_snapshot.py"), name
        assert 'python3 cy_cycle_date.py --github-env "$GITHUB_ENV"' in block, name
    assert "date -d tomorrow" not in recovery
    assert 'export CY_RECOVERY_TARGET_DATE="$CY_CYCLE_EXPECTED_FORECAST_DATE"' in recovery
    assert "github.event.schedule == '0 16 * * *'" in evening
    assert "github.event.schedule == '45 16 * * *' || github.event.schedule == '15 18 * * *'" in recovery
    morning = text.split("\n  morning:\n", 1)[1].split("\n  evening:\n", 1)[0]
    noon = text.split("\n  noon_fx:\n", 1)[1]
    assert "cy_cycle_date" not in morning and "cy_cycle_date" not in noon
    print("PASS workflow_and_module_schedules_agree")


def ambiguous_metadata_and_long_delays_fail_closed() -> None:
    """Scenarios 15/16: missing/unknown/legacy schedule, naive clocks, anchor drift, >24h delays."""
    now = utc("2026-10-12T13:30:00Z")
    for schedule, reason in (("", "missing_event_schedule"), ("0 13 * * *", "unknown_evening_schedule"),
                             ("*/5 * * * *", "unknown_evening_schedule"), ("garbage", "unknown_evening_schedule")):
        cycle = cy_cycle_date.resolve_evening_cycle(event_name="schedule", event_schedule=schedule, now=now)
        assert cycle.status == "ambiguous" and cycle.reason == reason, cycle.as_record()
        assert cycle.expected_forecast_date is None and cycle.expected_quiz_date is None
    naive = cy_cycle_date.resolve_evening_cycle(event_name="schedule", event_schedule=PRIMARY,
                                                now=datetime(2026, 10, 12, 16, 30))
    assert naive.status == "ambiguous" and naive.reason == "naive_execution_clock"
    assert cy_cycle_date.parse_daily_cron("0 16 * * 1") is None
    assert cy_cycle_date.parse_daily_cron("60 16 * * *") is None
    # Anchor written at job start must match the slot recomputed by every later component.
    good = cy_cycle_date.resolve_evening_cycle(event_name="schedule", event_schedule=PRIMARY, now=now)
    later = cy_cycle_date.resolve_evening_cycle(event_name="schedule", event_schedule=PRIMARY,
                                                now=now + timedelta(hours=2), anchor=good.env())
    assert later.is_valid and later.slot_local_datetime == good.slot_local_datetime
    for key, value, reason in (("CY_CYCLE_EVENT_SCHEDULE", RECOVERY_1645, "anchor_schedule_mismatch"),
                               ("CY_CYCLE_SLOT_LOCAL_DATETIME", "2026-10-11T16:00:00+03:00", "anchor_slot_mismatch"),
                               ("CY_CYCLE_SLOT_LOCAL_DATETIME", "not-a-date", "anchor_slot_unreadable")):
        anchor = good.env()
        anchor[key] = value
        drift = cy_cycle_date.resolve_evening_cycle(event_name="schedule", event_schedule=PRIMARY,
                                                    now=now, anchor=anchor)
        assert drift.status == "ambiguous" and drift.reason == reason, drift.as_record()
    # Delays: 8h30 (#2278) and >=32h land outside any publication window.
    slot_utc = utc("2026-10-12T13:00:00Z")
    for delay in (timedelta(hours=8, minutes=30), timedelta(hours=20), timedelta(hours=33), timedelta(hours=57)):
        cycle = cy_cycle_date.resolve_evening_cycle(event_name="schedule", event_schedule=PRIMARY,
                                                    now=slot_utc + delay)
        assert cycle.status == "expired", (delay, cycle.as_record())
    # 24h..32h is indistinguishable from the next day's on-time run: it resolves to the
    # *later* occurrence and never re-dates the original day's cycle.
    late = cy_cycle_date.resolve_evening_cycle(event_name="schedule", event_schedule=PRIMARY,
                                               now=slot_utc + timedelta(hours=26))
    assert late.slot_local_date == date(2026, 10, 13) and late.expected_forecast_date == date(2026, 10, 14)
    assert late.expected_forecast_date != date(2026, 10, 13)
    later = cy_cycle_date.resolve_evening_cycle(event_name="schedule", event_schedule=PRIMARY,
                                                now=slot_utc + timedelta(hours=50))
    assert later.slot_local_date == date(2026, 10, 14)  # the ambiguity band repeats every 24h
    dispatch = cy_cycle_date.resolve_evening_cycle(event_name="workflow_dispatch", event_schedule="", now=now)
    assert dispatch.status == "not_scheduled" and not dispatch.is_guarded
    print("PASS ambiguous_metadata_and_long_delays_fail_closed")


# --------------------------------------------------------------------------- publication


def normal_evening_publishes_one_consistent_cycle() -> None:
    """Scenarios 1 + 11: 16:00 run sends D+1 text/image, quiz D; every identity agrees."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        clock = Clock(local(2026, 10, 12, 16, 0, 40))
        with Harness(root, clock) as h:
            result = h.run(PRIMARY_ARGV)
            assert result["code"] == 0, result["stdout"][-2000:]
            assert len(result["photos"]) == 1 and len(result["messages"]) == 1
            assert "Кипр завтра (13.10.2026)" in result["messages"][0]["text"]
            assert _markers(result["stdout"], "CY_EVENING_CYCLE_VALID")[0]["slot_local_date"] == "2026-10-12"
            texts, images = h.receipts("text"), h.receipts("image")
            assert list(texts) == ["2026-10-13-evening.json"] and list(images) == ["2026-10-13-evening.json"]
            assert texts["2026-10-13-evening.json"]["target_date"] == "2026-10-13"
            assert images["2026-10-13-evening.json"]["target_date"] == "2026-10-13"
            clock.set(local(2026, 10, 12, 16, 8))
            quiz_result, sends = _deliver_quiz(root, schedule=PRIMARY, clock=clock,
                                               cycle_env={k: os.environ.get(k, "") for k in cy_cycle_date.ENV_KEYS})
        assert quiz_result["result"] == "quiz_sent" and len(sends) == 1, quiz_result
        receipt = json.loads(Path(quiz_result["receipt_path"]).read_text("utf-8"))
        assert Path(quiz_result["receipt_path"]).name == "2026-10-12.json"
        assert receipt["quiz_date"] == "2026-10-12" and receipt["weather_target_date"] == "2026-10-13"
        assert receipt["cycle_slot_local_datetime"] == "2026-10-12T16:00:00+03:00"
        assert receipt["state"] == "sent" and receipt["telegram_message_id"] == 1791
    print("PASS normal_evening_publishes_one_consistent_cycle")


def delayed_before_midnight_keeps_the_same_cycle() -> None:
    """Scenario 2: a 23:55 start still publishes the slot day's cycle (D+1 / quiz D)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        clock = Clock(local(2026, 10, 12, 23, 55))
        with Harness(root, clock) as h:
            result = h.run(PRIMARY_ARGV)
            assert result["code"] == 0, result["stdout"][-2000:]
            assert len(result["photos"]) == 1 and len(result["messages"]) == 1
            assert "Кипр завтра (13.10.2026)" in result["messages"][0]["text"]
            assert list(h.receipts("text")) == ["2026-10-13-evening.json"]
            assert list(h.receipts("image")) == ["2026-10-13-evening.json"]
            clock.set(local(2026, 10, 12, 23, 58))
            quiz_result, sends = _deliver_quiz(root, schedule=PRIMARY, clock=clock)
        assert quiz_result["result"] == "quiz_sent" and len(sends) == 1
        assert Path(quiz_result["receipt_path"]).name == "2026-10-12.json"
    print("PASS delayed_before_midnight_keeps_the_same_cycle")


def historical_2278_reproduction_publishes_nothing() -> None:
    """Scenario 3: slot 12 Oct 16:00, start 13 Oct 00:30 -> no sends, no receipts, no quiz."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        clock = Clock(utc("2026-10-12T21:30:32Z"))
        with Harness(root, clock) as h:
            result = h.run(PRIMARY_ARGV)
            assert result["code"] == safe_module._CY_EVENING_EXIT_EXPIRED
            assert result["photos"] == [] and result["messages"] == [] and h.builds == 0
            assert h.receipts("text") == {} and h.receipts("image") == {}
            expired = _markers(result["stdout"], "CY_EVENING_SLOT_EXPIRED")
            assert len(expired) == 1, result["stdout"]
            assert expired[0]["slot_local_date"] == "2026-10-12"
            assert expired[0]["execution_local_date"] == "2026-10-13"
            assert expired[0]["expected_forecast_date"] == "2026-10-13"
            assert "::error title=Cyprus evening cycle not published::" in result["stdout"]
            assert _markers(result["stdout"], "CY_EVENING_PRIMARY_NOT_PUBLISHED")[0]["stop_reason"] == "slot_expired"
            assert "Cyprus evening cycle not published" in (root / "step-summary.md").read_text("utf-8")
            assert not (root / "image-receipts" / "2026-10-14-evening.json").exists()
        # Even with the exact #2278 receipt for 14 Oct present, the quiz never re-dates itself.
        weather_dir = root / "w2278"
        _quiz_weather_receipt(weather_dir, date(2026, 10, 14))
        quiz_result, sends = _deliver_quiz(root, schedule=PRIMARY, clock=Clock(utc("2026-10-12T21:38:00Z")),
                                           weather_dir=weather_dir)
        assert quiz_result["result"] == "quiz_skipped_slot_expired" and sends == []
        assert quiz_result["cycle_slot_local_date"] == "2026-10-12"
        assert not (root / "quiz").exists()
        # Old clock-derived identity would have been forecast 14 Oct / quiz 13 Oct.
        cycle = cy_cycle_date.resolve_evening_cycle(event_name="schedule", event_schedule=PRIMARY,
                                                    now=utc("2026-10-12T21:30:32Z"))
        assert cycle.expected_forecast_date == date(2026, 10, 13) != date(2026, 10, 14)
        assert cycle.expected_quiz_date == date(2026, 10, 12) != date(2026, 10, 13)
    print("PASS historical_2278_reproduction_publishes_nothing")


def midnight_during_image_generation_stops_every_send() -> None:
    """Scenario 4: the window expires while the image is generated -> no image, no text, no receipts."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        clock = Clock(local(2026, 10, 12, 23, 58))
        with Harness(root, clock) as h:
            h.on_image_generation = lambda: clock.set(local(2026, 10, 13, 0, 1))
            result = h.run(PRIMARY_ARGV)
            assert result["code"] == safe_module._CY_EVENING_EXIT_EXPIRED, result["stdout"][-2000:]
            assert result["photos"] == [] and result["messages"] == []
            assert h.receipts("text") == {} and h.receipts("image") == {}
            blocked = _markers(result["stdout"], "CY_EVENING_SEND_BLOCKED")
            assert [b["stage"] for b in blocked] == ["image_send", "text_send"], blocked
            assert all(b["payload_target_date"] == "2026-10-13" for b in blocked)
            diag = json.loads((root / "diagnostics" / "2026-10-13-evening" / "image_result.json").read_text("utf-8"))
            assert diag["image_result"] == "skipped_slot_expired"
            assert not _markers(result["stdout"], "CY_EVENING_PARTIAL_DELIVERY")
            assert not any("2026-10-14" in name for name in os.listdir(root / "diagnostics"))
    print("PASS midnight_during_image_generation_stops_every_send")


def midnight_between_image_and_text_reports_partial_delivery() -> None:
    """Scenario 5: image delivered before midnight, text window gone -> truthful partial report."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        clock = Clock(local(2026, 10, 12, 23, 59, 30))
        with Harness(root, clock) as h:
            h.on_photo_sent = lambda: clock.set(local(2026, 10, 13, 0, 0, 5))
            result = h.run(PRIMARY_ARGV)
            assert result["code"] == safe_module._CY_EVENING_EXIT_EXPIRED, result["stdout"][-2000:]
            assert len(result["photos"]) == 1 and result["messages"] == []
            assert list(h.receipts("image")) == ["2026-10-13-evening.json"]  # the image really was sent
            assert h.receipts("text") == {}
            partial = _markers(result["stdout"], "CY_EVENING_PARTIAL_DELIVERY")
            assert len(partial) == 1 and partial[0]["image_sent"] and not partial[0]["text_sent"]
            assert partial[0]["text_target_date"] == "2026-10-13"
            stop = _markers(result["stdout"], "CY_EVENING_PRIMARY_NOT_PUBLISHED")[0]
            assert stop["stop_reason"] == "slot_expired" and stop["image_sent"] is True
            assert "CY_TEXT_RECEIPT_WRITTEN" not in result["stdout"]
    print("PASS midnight_between_image_and_text_reports_partial_delivery")


def quiz_midnight_before_reservation_and_before_send() -> None:
    """Scenarios 6 + 7: no reservation after expiry; an unsent reservation is never restorable."""
    restore = _load_restore_helper()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        weather = root / "weather"
        _quiz_weather_receipt(weather, date(2026, 10, 13))

        seq = iter([local(2026, 10, 12, 23, 59, 50), local(2026, 10, 13, 0, 0, 5), local(2026, 10, 13, 0, 0, 6)])
        current = {"t": None}

        def ticking():
            current["t"] = next(seq, current["t"])
            return current["t"]

        r6, sends6 = _deliver_quiz(root / "r6", schedule=PRIMARY, clock=ticking, weather_dir=weather)
        assert r6["result"] == "quiz_skipped_slot_expired" and r6["stage"] == "before_reservation", r6
        assert sends6 == [] and not (root / "r6" / "quiz").exists()

        seq2 = iter([local(2026, 10, 12, 23, 59, 50), local(2026, 10, 12, 23, 59, 55), local(2026, 10, 13, 0, 0, 1)])
        current2 = {"t": None}

        def ticking2():
            current2["t"] = next(seq2, current2["t"])
            return current2["t"]

        r7, sends7 = _deliver_quiz(root / "r7", schedule=PRIMARY, clock=ticking2, weather_dir=weather)
        assert r7["result"] == "quiz_skipped_slot_expired" and r7["stage"] == "before_send", r7
        assert sends7 == []
        record = json.loads(Path(r7["receipt_path"]).read_text("utf-8"))
        assert Path(r7["receipt_path"]).name == "2026-10-12.json"
        assert record["state"] == "aborted_slot_expired" and "telegram_message_id" not in record
        assert not restore._valid_quiz_receipt(record)
        assert quiz._sent_history("evening_general", root / "r7" / "quiz", date(2026, 10, 20), {record["question_id"]}) == set()
        again, sends_again = _deliver_quiz(root / "r7", schedule=PRIMARY, clock=Clock(local(2026, 10, 12, 23, 59, 58)),
                                           weather_dir=weather)
        assert again["result"] == "quiz_skipped_receipt_exists" and sends_again == []
    print("PASS quiz_midnight_before_reservation_and_before_send")


def same_day_recovery_still_recovers_and_stays_idempotent() -> None:
    """Scenarios 8, 10, 17: 16:45 recovers the missing D+1 image; 18:15 then sends nothing."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        clock = Clock(local(2026, 10, 12, 16, 46))
        with Harness(root, clock) as h:
            h.write_text_receipt("2026-10-13")
            first = h.run(RECOVERY_ARGV, schedule=RECOVERY_1645)
            assert first["code"] == 0, first["stdout"][-2000:]
            assert len(first["photos"]) == 1 and first["messages"] == []
            assert list(h.receipts("image")) == ["2026-10-13-evening.json"]
            clock.set(local(2026, 10, 12, 18, 16))
            second = h.run(RECOVERY_ARGV, schedule=RECOVERY_1815)
            assert second["code"] == 0 and len(second["photos"]) == 1 and second["messages"] == []
            assert "CY_SAFE_IMAGE_RECEIPT_EXISTS" in second["stdout"]
            # A late primary re-run on the same day is idempotent too.
            clock.set(local(2026, 10, 12, 19, 0))
            third = h.run(PRIMARY_ARGV)
            assert third["code"] == 0 and len(third["photos"]) == 1 and third["messages"] == []
            assert "CY_TEXT_DELIVERY_SKIP_RECEIPT_EXISTS" in third["stdout"]
            # Quiz recovery inside the window sends exactly once for quiz date D.
            clock.set(local(2026, 10, 12, 16, 50))
            q1, s1 = _deliver_quiz(root, schedule=RECOVERY_1645, clock=clock)
            clock.set(local(2026, 10, 12, 18, 20))
            q2, s2 = _deliver_quiz(root, schedule=RECOVERY_1815, clock=clock)
        assert q1["result"] == "quiz_sent" and len(s1) == 1
        assert Path(q1["receipt_path"]).name == "2026-10-12.json"
        assert q2["result"] == "quiz_skipped_receipt_exists" and s2 == []
    print("PASS same_day_recovery_still_recovers_and_stays_idempotent")


def delayed_recovery_after_midnight_never_targets_d_plus_2() -> None:
    """Scenario 9: 45 16 / 15 18 recoveries executing after midnight skip with a reason code."""
    for schedule, instant in ((RECOVERY_1645, "2026-10-12T21:51:15Z"), (RECOVERY_1815, "2026-10-12T22:29:15Z")):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clock = Clock(utc(instant))
            with Harness(root, clock) as h:
                h.write_text_receipt("2026-10-13")
                h.write_text_receipt("2026-10-14")  # even a next-cycle text receipt must not be used
                result = h.run(RECOVERY_ARGV, schedule=schedule)
                assert result["code"] == 0, result["stdout"][-2000:]
                assert result["photos"] == [] and result["messages"] == [] and h.builds == 0
                skipped = _markers(result["stdout"], "CY_EVENING_RECOVERY_SKIPPED")
                assert len(skipped) == 1 and skipped[0]["stop_reason"] == "slot_expired", result["stdout"]
                assert skipped[0]["expected_image_date"] == "2026-10-13"
                assert h.receipts("image") == {}
            quiz_result, sends = _deliver_quiz(root, schedule=schedule, clock=Clock(utc(instant)))
            assert quiz_result["result"] == "quiz_skipped_slot_expired" and sends == []
            assert not (root / "quiz").exists()
    # Recovery that starts before midnight but crosses it while generating sends nothing.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        clock = Clock(local(2026, 10, 12, 23, 59, 40))
        with Harness(root, clock) as h:
            h.write_text_receipt("2026-10-13")
            h.on_image_generation = lambda: clock.set(local(2026, 10, 13, 0, 0, 20))
            result = h.run(RECOVERY_ARGV, schedule=RECOVERY_1815)
            assert result["code"] == 0 and result["photos"] == [], result["stdout"][-2000:]
            skipped = _markers(result["stdout"], "CY_EVENING_RECOVERY_SKIPPED")
            assert len(skipped) == 1 and skipped[0]["stop_reason"] == "slot_expired"
            assert h.receipts("image") == {}
    print("PASS delayed_recovery_after_midnight_never_targets_d_plus_2")


def missing_or_ambiguous_identity_publishes_nothing() -> None:
    """Scenarios 15/16 end-to-end: primary exits non-zero, recovery skips, nothing is sent."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        clock = Clock(local(2026, 10, 12, 16, 5))
        with Harness(root, clock) as h:
            for schedule in ("", "0 13 * * *", "bogus"):
                result = h.run(PRIMARY_ARGV, schedule=schedule, anchor=False)
                assert result["code"] == safe_module._CY_EVENING_EXIT_AMBIGUOUS, (schedule, result["stdout"][-1500:])
                assert _markers(result["stdout"], "CY_EVENING_CYCLE_AMBIGUOUS"), result["stdout"]
                rec = h.run(RECOVERY_ARGV, schedule=schedule, anchor=False)
                assert rec["code"] == 0
                assert _markers(rec["stdout"], "CY_EVENING_RECOVERY_SKIPPED")[0]["stop_reason"] == "cycle_ambiguous"
            explicit = h.run(PRIMARY_ARGV + ["--date", "2026-10-12"])
            assert explicit["code"] == safe_module._CY_EVENING_EXIT_AMBIGUOUS
            assert _markers(explicit["stdout"], "CY_EVENING_CYCLE_AMBIGUOUS")[0]["reason"] == "explicit_date_on_scheduled_run"
            drift = h.run(PRIMARY_ARGV, anchor=False, extra_env={
                "CY_CYCLE_EVENT_SCHEDULE": PRIMARY, "CY_CYCLE_SLOT_LOCAL_DATETIME": "2026-10-11T16:00:00+03:00"})
            assert drift["code"] == safe_module._CY_EVENING_EXIT_AMBIGUOUS
            assert h.photos == [] and h.messages == [] and h.builds == 0
            assert h.receipts("text") == {} and h.receipts("image") == {}
        result, sends = _deliver_quiz(root, schedule=PRIMARY, clock=Clock(datetime(2026, 10, 12, 16, 5, tzinfo=NICOSIA)),
                                      cycle_env={"CY_CYCLE_EVENT_SCHEDULE": RECOVERY_1645,
                                                 "CY_CYCLE_SLOT_LOCAL_DATETIME": "2026-10-12T16:45:00+03:00"})
        assert result["result"] == "quiz_skipped_cycle_ambiguous" and sends == []
    print("PASS missing_or_ambiguous_identity_publishes_nothing")


def manual_dispatch_morning_and_fx_are_unchanged() -> None:
    """Scenarios 13/14: dispatch with work_date, morning 07:30 and FX 12:00 never consult the guard."""
    real_from_env = cy_cycle_date.evening_cycle_from_environment
    calls: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        clock = Clock(utc("2026-10-12T21:30:00Z"))  # after midnight on purpose
        with Harness(root, clock) as h:
            dispatch = h.run(PRIMARY_ARGV + ["--date", "2026-10-20"], event="workflow_dispatch", schedule="")
            assert dispatch["code"] == 0, dispatch["stdout"][-2000:]
            assert "Кипр завтра (21.10.2026)" in dispatch["messages"][0]["text"]
            assert list(h.receipts("text")) == ["2026-10-21-evening.json"]
            assert "CY_EVENING_" not in dispatch["stdout"]

            def tracking(*args, **kwargs):
                calls.append("called")
                return real_from_env(*args, **kwargs)

            cy_cycle_date.evening_cycle_from_environment = tracking
            try:
                morning = h.run(["safe_test_post.py", "--mode", "morning", "--chat-id", "777", "--send",
                                 "--no-test-label", "--date", "2026-10-13"], schedule="0 6 * * *", anchor=False)
            finally:
                cy_cycle_date.evening_cycle_from_environment = real_from_env
            assert morning["code"] == 0, morning["stdout"][-2000:]
            assert "Кипр сегодня (13.10.2026)" in morning["messages"][-1]["text"]
            assert calls == [] and "CY_EVENING_" not in morning["stdout"]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fx_dir = root / "fx"
        fx_dir.mkdir()
        (fx_dir / "2026-10-13.json").write_text(json.dumps({"publication_date": "2026-10-13", "chat_type": "production",
                                                            "telegram_message_id": 2, "sent_at_utc": "x"}), "utf-8")
        result, sends = _deliver_quiz(root, schedule="0 12 * * *", slot="fx_economy",
                                      clock=Clock(local(2026, 10, 13, 12, 3)))
        assert result["result"] == "quiz_sent" and len(sends) == 1
        assert Path(result["receipt_path"]).name == "2026-10-13.json"
        record = json.loads(Path(result["receipt_path"]).read_text("utf-8"))
        assert record["publication_date"] == "2026-10-13" and "cycle_slot_local_datetime" not in record
    print("PASS manual_dispatch_morning_and_fx_are_unchanged")


def workflow_cli_anchor_round_trip() -> None:
    """The workflow step writes the identity once; it is stable across midnight-free steps."""
    with tempfile.TemporaryDirectory() as tmp:
        env_file = Path(tmp) / "github-env"
        env_file.write_text("", "utf-8")
        old = {k: os.environ.get(k) for k in ("GITHUB_EVENT_NAME", "GITHUB_EVENT_SCHEDULE", *cy_cycle_date.ENV_KEYS)}
        try:
            os.environ["GITHUB_EVENT_NAME"] = "schedule"
            os.environ["GITHUB_EVENT_SCHEDULE"] = PRIMARY
            os.environ["CY_CYCLE_STATUS"] = "stale-value-from-parent"
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cy_cycle_date_main_with_clock(utc("2026-10-12T21:30:32Z"), env_file)
            assert out.getvalue().startswith("CY_EVENING_SLOT_EXPIRED: ")
            values = dict(line.split("=", 1) for line in env_file.read_text("utf-8").splitlines())
            assert values["CY_CYCLE_STATUS"] == "expired"
            assert values["CY_CYCLE_SLOT_LOCAL_DATE"] == "2026-10-12"
            assert values["CY_CYCLE_EXPECTED_FORECAST_DATE"] == "2026-10-13"
            assert values["CY_CYCLE_EXPECTED_QUIZ_DATE"] == "2026-10-12"
            assert values["CY_CYCLE_EXPECTED_IMAGE_DATE"] == "2026-10-13"
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    print("PASS workflow_cli_anchor_round_trip")


def forecast_date_mismatch_is_never_sent() -> None:
    """Scenario 11: inside a valid window a payload for another day is never sent or receipted."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        clock = Clock(local(2026, 10, 12, 16, 10))
        with Harness(root, clock) as h:
            safe_module.build_message = lambda **_kwargs: _forecast_text(date(2026, 10, 14))
            result = h.run(PRIMARY_ARGV)
            assert result["code"] == safe_module._CY_EVENING_EXIT_AMBIGUOUS, result["stdout"][-2000:]
            assert result["photos"] == [] and result["messages"] == []
            assert h.receipts("text") == {} and h.receipts("image") == {}
            blocked = _markers(result["stdout"], "CY_EVENING_SEND_BLOCKED")
            assert [b["block_reason"] for b in blocked] == ["target_date_mismatch", "target_date_mismatch"]
            assert all(b["payload_target_date"] == "2026-10-14" and b["expected_forecast_date"] == "2026-10-13"
                       for b in blocked)
    print("PASS forecast_date_mismatch_is_never_sent")


CHECKS = [
    resolution_contract_and_dst_boundaries,
    workflow_and_module_schedules_agree,
    ambiguous_metadata_and_long_delays_fail_closed,
    normal_evening_publishes_one_consistent_cycle,
    delayed_before_midnight_keeps_the_same_cycle,
    historical_2278_reproduction_publishes_nothing,
    midnight_during_image_generation_stops_every_send,
    midnight_between_image_and_text_reports_partial_delivery,
    forecast_date_mismatch_is_never_sent,
    quiz_midnight_before_reservation_and_before_send,
    same_day_recovery_still_recovers_and_stays_idempotent,
    delayed_recovery_after_midnight_never_targets_d_plus_2,
    missing_or_ambiguous_identity_publishes_nothing,
    manual_dispatch_morning_and_fx_are_unchanged,
    workflow_cli_anchor_round_trip,
]


def main() -> None:
    for check in CHECKS:
        check()
    print(f"OK: {len(CHECKS)} Cyprus evening date-contract checks passed")


if __name__ == "__main__":
    main()
