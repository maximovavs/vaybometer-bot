#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline regressions for the additive Cyprus Culture quiz layer."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
import importlib.util
import hashlib
from collections import Counter
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

import cyprus_culture_quiz as quiz


PILOT_BANK = ROOT / "data" / "cyprus_culture" / "v2026-10-pilot" / "questions.jsonl"
PILOT_MANIFEST = ROOT / "data" / "cyprus_culture" / "v2026-10-pilot" / "manifest.json"
EXPECTED_PILOT_IDS = [
    "jul2026-a03",
    "jul2026-a04",
    "jul2026-a09",
    "jul2026-a11",
    "jul2026-a12",
    "jul2026-a13",
    "jul2026-a15",
    "jul2026-a20",
    "jul2026-a22",
    "jul2026-a23",
    "jul2026-a24",
    "jul2026-b06",
    "jul2026-b07",
    "jul2026-b09",
    "jul2026-b10",
    "jul2026-b14",
    "jul2026-b16",
    "jul2026-b18",
    "jul2026-b23",
    "jul2026-b25"
]
EXPECTED_CATEGORY_COUNTS = {
    "geography": 7,
    "history": 2,
    "culture": 3,
    "institutions": 4,
    "traditions": 2,
    "language": 1,
    "environment": 1,
}


def _assert(name: str, condition: bool, detail="") -> None:
    if not condition:
        raise AssertionError(f"{name}: {detail or 'assertion failed'}")


def _record(
    question_id: str = "geo-001",
    rotation_rank: int = 0,
    *,
    verified: bool = True,
    option_count: int = 3,
    correct_option_index: int = 1,
    question_el: str = "Ποια είναι η έκταση της Κύπρου;",
    question_ru: str = "Какова площадь Кипра?",
    explanation_ru: str = "Правильный ответ подтверждён источником.",
    **extra: object,
) -> dict:
    options_el = ["8 251 km²", "9 251 km²", "10 251 km²", "11 251 km²"][:option_count]
    options_ru = ["8 251 км²", "9 251 км²", "10 251 км²", "11 251 км²"][:option_count]
    if option_count > 4:
        options_el += [f"{12 + idx} 251 km²" for idx in range(option_count - 4)]
        options_ru += [f"{12 + idx} 251 км²" for idx in range(option_count - 4)]
    record = {
        "question_id": question_id,
        "rotation_rank": rotation_rank,
        "category": "geography",
        "question_el": question_el,
        "question_ru": question_ru,
        "options_el": options_el,
        "options_ru": options_ru,
        "correct_option_index": correct_option_index,
        "explanation_ru": explanation_ru,
        "source": "https://example.invalid/verified-source",
        "verified": verified,
        "provenance_tier": "P1",
        "source_locator": "library:fixture#question",
        "answer_source": "https://example.invalid/verified-source",
        "rights_status": "official_derived_rewrite",
        "publication_mode": "official_derived_rewrite",
        "source_text_hash": "sha256:" + "a" * 64,
        "as_of": "2026-10-06",
        "valid_from": None,
        "valid_until": None,
        "review_status": "verified_official",
        "temporal_class": "stable",
    }
    record.update(extra)
    return record

def _write_bank(path: Path, records: list[dict | str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        for item in records
    ]
    path.write_text("\n".join(lines) + "\n", "utf-8")


def _write_weather_receipt(path: Path, target: date) -> bytes:
    payload = {
        "target_date": target.isoformat(),
        "post_type": "evening",
        "chat_type": "production",
        "telegram_message_ids": [901],
        "text_chunk_count": 1,
        "sent_at_utc": "2026-10-06T13:05:00Z",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), "utf-8")
    return path.read_bytes()


class _FakeSender:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls = 0

    async def __call__(self, **_kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("fixture telegram failure")
        return SimpleNamespace(
            message_id=777,
            poll=SimpleNamespace(id="poll-fixture-777"),
        )


def _deliver(
    tmp: Path,
    *,
    sender: _FakeSender,
    bank_path: Path | None = None,
    event_name: str = "schedule",
    event_schedule: str = "0 13 * * *",
) -> dict:
    quiz_date = date(2026, 10, 6)
    weather_target = date(2026, 10, 7)
    weather_dir = tmp / "weather"
    quiz_dir = tmp / "quiz"
    _write_weather_receipt(weather_dir / f"{weather_target.isoformat()}-evening.json", weather_target)
    selected_bank = bank_path or tmp / "questions.jsonl"
    if bank_path is None:
        _write_bank(selected_bank, [_record()])
    return asyncio.run(
        quiz.deliver_daily_quiz(
            enabled=True,
            event_name=event_name,
            event_schedule=event_schedule,
            chat_id="-100123",
            token="fixture-token",
            bank_path=selected_bank,
            bank_version="fixture-v1",
            anchor_date_text="2026-10-06",
            weather_receipt_dir=weather_dir,
            quiz_receipt_dir=quiz_dir,
            now=datetime(2026, 10, 6, 16, 0, tzinfo=ZoneInfo("Asia/Nicosia")),
            send_poll=sender,
            run_id="fixture-run",
            run_attempt="1",
        )
    )


def test_valid_verified_bilingual_question_is_accepted() -> None:
    question = quiz._parse_question_record(_record())
    _assert("valid_record", question is not None)
    payload = quiz.assemble_payload(question)
    _assert("valid_payload", payload is not None)
    _assert("greek_first", "Ποια είναι" in payload.question)
    _assert("russian_present", "Какова площадь" in payload.question)
    _assert("three_options", len(payload.options) == 3)
    print("PASS valid_verified_bilingual_question_is_accepted")


def test_unverified_is_never_publishable() -> None:
    with tempfile.TemporaryDirectory() as tmp_name:
        path = Path(tmp_name) / "questions.jsonl"
        _write_bank(path, [_record(verified=False)])
        _assert("unverified_filtered", quiz.load_verified_questions(path) == [])
    print("PASS unverified_is_never_publishable")


def test_malformed_record_is_skipped() -> None:
    with tempfile.TemporaryDirectory() as tmp_name:
        path = Path(tmp_name) / "questions.jsonl"
        _write_bank(path, ['{"broken":', _record("geo-002", 1)])
        questions = quiz.load_verified_questions(path)
        _assert("one_valid_after_malformed", [q.question_id for q in questions] == ["geo-002"])
    print("PASS malformed_record_is_skipped")


def test_invalid_correct_index_is_skipped() -> None:
    _assert(
        "invalid_correct_index",
        quiz._parse_question_record(_record(correct_option_index=9)) is None,
    )
    print("PASS invalid_correct_index_is_skipped")


def test_two_or_five_options_fail_product_contract() -> None:
    _assert("two_options", quiz._parse_question_record(_record(option_count=2)) is None)
    _assert("five_options", quiz._parse_question_record(_record(option_count=5)) is None)
    print("PASS two_or_five_options_fail_product_contract")


def test_final_question_over_300_fails_closed() -> None:
    question = quiz._parse_question_record(_record(question_el="Ω" * 280))
    _assert("record_structurally_valid", question is not None)
    _assert("assembled_too_long", quiz.assemble_payload(question) is None)
    print("PASS final_question_over_300_fails_closed")


def test_final_option_over_100_fails_closed() -> None:
    record = _record()
    record["options_el"][0] = "Α" * 95
    question = quiz._parse_question_record(record)
    _assert("long_option_record_valid", question is not None)
    _assert("long_option_payload_invalid", quiz.assemble_payload(question) is None)
    print("PASS final_option_over_100_fails_closed")


def test_explanation_over_200_fails_closed() -> None:
    question = quiz._parse_question_record(_record(explanation_ru="Я" * 201))
    _assert("long_explanation_record_valid", question is not None)
    _assert("long_explanation_payload_invalid", quiz.assemble_payload(question) is None)
    print("PASS explanation_over_200_fails_closed")


def test_same_date_selection_is_deterministic() -> None:
    questions = [
        quiz._parse_question_record(_record(f"q-{idx}", idx))
        for idx in range(4)
    ]
    questions = [q for q in questions if q is not None]
    first = quiz.select_question(questions, quiz_date=date(2026, 10, 8), anchor_date=date(2026, 10, 6))
    second = quiz.select_question(questions, quiz_date=date(2026, 10, 8), anchor_date=date(2026, 10, 6))
    _assert("same_selection", first == second and first.question_id == "q-2")
    print("PASS same_date_selection_is_deterministic")


def test_no_repetition_across_complete_rotation_cycle() -> None:
    questions = [
        quiz._parse_question_record(_record(f"q-{idx}", idx))
        for idx in range(4)
    ]
    questions = [q for q in questions if q is not None]
    picked = [
        quiz.select_question(
            questions,
            quiz_date=date(2026, 10, 6 + idx),
            anchor_date=date(2026, 10, 6),
        ).question_id
        for idx in range(4)
    ]
    _assert("cycle_unique", len(set(picked)) == 4, picked)
    print("PASS no_repetition_across_complete_rotation_cycle")


def test_existing_sent_receipt_blocks_duplicate() -> None:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        receipt = tmp / "quiz" / "2026-10-06.json"
        quiz._atomic_write_json(receipt, {"state": "sent"})
        sender = _FakeSender()
        result = _deliver(tmp, sender=sender)
        _assert("sent_receipt_skip", result["result"] == "quiz_skipped_receipt_exists", result)
        _assert("sent_receipt_no_poll", sender.calls == 0)
    print("PASS existing_sent_receipt_blocks_duplicate")


def test_existing_reserved_receipt_blocks_duplicate() -> None:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        receipt = tmp / "quiz" / "2026-10-06.json"
        quiz._atomic_write_json(receipt, {"state": "reserved"})
        sender = _FakeSender()
        result = _deliver(tmp, sender=sender)
        _assert("reserved_receipt_skip", result["result"] == "quiz_skipped_receipt_exists", result)
        _assert("reserved_receipt_no_poll", sender.calls == 0)
    print("PASS existing_reserved_receipt_blocks_duplicate")


def test_weather_receipt_missing_means_no_quiz_send() -> None:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        bank = tmp / "questions.jsonl"
        _write_bank(bank, [_record()])
        sender = _FakeSender()
        result = asyncio.run(
            quiz.deliver_daily_quiz(
                enabled=True,
                event_name="schedule",
                event_schedule="0 13 * * *",
                chat_id="-100123",
                token="fixture-token",
                bank_path=bank,
                bank_version="fixture-v1",
                anchor_date_text="2026-10-06",
                weather_receipt_dir=tmp / "weather",
                quiz_receipt_dir=tmp / "quiz",
                now=datetime(2026, 10, 6, 16, 0, tzinfo=ZoneInfo("Asia/Nicosia")),
                send_poll=sender,
            )
        )
        _assert("missing_weather_skip", result["result"] == "quiz_skipped_weather_not_delivered", result)
        _assert("missing_weather_no_poll", sender.calls == 0)
    print("PASS weather_receipt_missing_means_no_quiz_send")


def test_successful_send_writes_sent_receipt() -> None:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        sender = _FakeSender()
        result = _deliver(tmp, sender=sender)
        receipt = json.loads((tmp / "quiz" / "2026-10-06.json").read_text("utf-8"))
        _assert("sent_result", result["result"] == "quiz_sent", result)
        _assert("sent_once", sender.calls == 1)
        _assert("receipt_sent", receipt["state"] == "sent", receipt)
        _assert("message_id", receipt["telegram_message_id"] == 777, receipt)
        _assert("poll_id", receipt["poll_id"] == "poll-fixture-777", receipt)
    print("PASS successful_send_writes_sent_receipt")


def test_telegram_failure_leaves_reservation_nonfatal() -> None:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        sender = _FakeSender(fail=True)
        result = _deliver(tmp, sender=sender)
        receipt = json.loads((tmp / "quiz" / "2026-10-06.json").read_text("utf-8"))
        _assert("failure_nonfatal", result["result"] == "quiz_failed_non_fatal", result)
        _assert("reserved_left", receipt["state"] == "reserved", receipt)
        _assert("one_attempt", sender.calls == 1)
    print("PASS telegram_failure_leaves_reservation_nonfatal")


def test_retry_after_ambiguous_reservation_sends_zero_polls() -> None:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        first_sender = _FakeSender(fail=True)
        first = _deliver(tmp, sender=first_sender)
        second_sender = _FakeSender()
        second = _deliver(tmp, sender=second_sender)
        _assert("first_failed", first["result"] == "quiz_failed_non_fatal", first)
        _assert("second_skipped", second["result"] == "quiz_skipped_receipt_exists", second)
        _assert("second_zero_sends", second_sender.calls == 0)
    print("PASS retry_after_ambiguous_reservation_sends_zero_polls")


def test_quiz_failure_never_mutates_weather_receipts() -> None:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        weather_path = tmp / "weather" / "2026-10-07-evening.json"
        before = _write_weather_receipt(weather_path, date(2026, 10, 7))
        bank = tmp / "questions.jsonl"
        _write_bank(bank, [_record()])
        sender = _FakeSender(fail=True)
        result = asyncio.run(
            quiz.deliver_daily_quiz(
                enabled=True,
                event_name="schedule",
                event_schedule="0 13 * * *",
                chat_id="-100123",
                token="fixture-token",
                bank_path=bank,
                bank_version="fixture-v1",
                anchor_date_text="2026-10-06",
                weather_receipt_dir=tmp / "weather",
                quiz_receipt_dir=tmp / "quiz",
                now=datetime(2026, 10, 6, 16, 0, tzinfo=ZoneInfo("Asia/Nicosia")),
                send_poll=sender,
            )
        )
        _assert("failure_expected", result["result"] == "quiz_failed_non_fatal", result)
        _assert("weather_unchanged", weather_path.read_bytes() == before)
    print("PASS quiz_failure_never_mutates_weather_receipts")


def test_manual_nonproduction_cannot_publish() -> None:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        sender = _FakeSender()
        result = _deliver(
            tmp,
            sender=sender,
            event_name="workflow_dispatch",
            event_schedule="",
        )
        _assert("manual_skipped", result["result"] == "quiz_skipped_non_production", result)
        _assert("manual_no_send", sender.calls == 0)
        _assert("manual_no_receipt", not (tmp / "quiz").exists())
    print("PASS manual_nonproduction_cannot_publish")


def test_missing_production_bank_safely_skips() -> None:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        sender = _FakeSender()
        missing = tmp / "missing.jsonl"
        result = _deliver(tmp, sender=sender, bank_path=missing)
        _assert("missing_bank_skip", result["result"] == "quiz_skipped_no_bank", result)
        _assert("missing_bank_no_send", sender.calls == 0)
        _assert("missing_bank_no_receipt", not (tmp / "quiz").exists())
    print("PASS missing_production_bank_safely_skips")


def _load_snapshot_helper():
    path = ROOT / ".github" / "scripts" / "restore_cy_visual_snapshot.py"
    spec = importlib.util.spec_from_file_location("restore_cy_visual_snapshot_quiz_test", path)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load restore helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_quiz_snapshot_restore_does_not_alter_weather_gating() -> None:
    helper = _load_snapshot_helper()
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        source = tmp / "artifact"
        destination = tmp / "destination"
        weather_path = destination / "cy_text_delivery" / "2026-10-07-evening.json"
        before = _write_weather_receipt(weather_path, date(2026, 10, 7))
        quiz._atomic_write_json(
            source / ".cache" / "cy_quiz_delivery" / "2026-10-06.json",
            {
                "quiz_date": "2026-10-06",
                "weather_target_date": "2026-10-07",
                "chat_type": "production",
                "question_id": "geo-001",
                "bank_version": "fixture-v1",
                "state": "sent",
                "reserved_at_utc": "2026-10-06T13:06:00Z",
                "telegram_message_id": 777,
                "poll_id": "poll-fixture",
                "sent_at_utc": "2026-10-06T13:06:01Z",
            },
        )
        restored, status = helper._restore_quiz_receipts(source, destination)
        _assert("quiz_receipt_restored", restored == 1 and status in {"restored", "bulk_restored"}, (restored, status))
        _assert("weather_bytes_unchanged", weather_path.read_bytes() == before)
        weather = json.loads(weather_path.read_text("utf-8"))
        _assert(
            "weather_still_valid",
            helper._valid_text_receipt(
                weather,
                target_date="2026-10-07",
                post_type="evening",
            ),
        )
    print("PASS quiz_snapshot_restore_does_not_alter_weather_gating")



def test_missing_provenance_metadata_fails_closed() -> None:
    required = ["provenance_tier","source_locator","answer_source","rights_status","publication_mode","source_text_hash","as_of","valid_from","valid_until","review_status","temporal_class"]
    for field in required:
        record = _record()
        record.pop(field)
        _assert(f"missing_{field}", quiz._parse_question_record(record) is None)
    print("PASS missing_provenance_metadata_fails_closed")


def test_invalid_temporal_and_rights_metadata_fail_closed() -> None:
    _assert("invalid_temporal", quiz._parse_question_record(_record(temporal_class="breaking_news")) is None)
    _assert("invalid_rights", quiz._parse_question_record(_record(rights_status="unknown", publication_mode="unknown")) is None)
    _assert("mismatched_publication_mode", quiz._parse_question_record(_record(publication_mode="exact_source_wording")) is None)
    print("PASS invalid_temporal_and_rights_metadata_fail_closed")


def test_date_sensitive_requires_bounded_validity_window() -> None:
    _assert("date_sensitive_without_window", quiz._parse_question_record(_record(temporal_class="date_sensitive")) is None)
    valid = quiz._parse_question_record(_record(temporal_class="date_sensitive", valid_from="2026-10-06", valid_until="2026-12-31"))
    _assert("date_sensitive_with_window", valid is not None)
    print("PASS date_sensitive_requires_bounded_validity_window")


def test_pilot_release_contract() -> None:
    raw = PILOT_BANK.read_bytes()
    _assert("pilot_final_newline", raw.endswith(b"\n"))
    _assert("pilot_lf_only", b"\r" not in raw)
    text = raw.decode("utf-8")
    lines = text.splitlines()
    _assert("pilot_raw_count", len(lines) == 20, str(len(lines)))
    records = [json.loads(line) for line in lines]
    for idx, (line, record) in enumerate(zip(lines, records)):
        canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        _assert(f"canonical_line_{idx}", line == canonical)
    questions = quiz.load_verified_questions(PILOT_BANK)
    _assert("pilot_verified_count", len(questions) == 20, str(len(questions)))
    _assert("pilot_ids", [q.question_id for q in questions] == EXPECTED_PILOT_IDS)
    _assert("pilot_ranks", [q.rotation_rank for q in questions] == list(range(20)))
    _assert("pilot_category_counts", Counter(q.category for q in questions) == Counter(EXPECTED_CATEGORY_COUNTS))
    _assert("pilot_temporal", all(q.temporal_class == "stable" for q in questions))
    _assert("pilot_rights", all(q.rights_status == "official_derived_rewrite" and q.publication_mode == "official_derived_rewrite" for q in questions))
    _assert("pilot_official_answer_sources", all(q.answer_source.startswith("https://") for q in questions))
    _assert("pilot_source_hashes", all(quiz.SOURCE_TEXT_HASH_RE.fullmatch(q.source_text_hash) for q in questions))
    manifest = json.loads(PILOT_MANIFEST.read_text("utf-8"))
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    _assert("manifest_bank_sha", manifest["bank_sha256"] == digest, digest)
    _assert("manifest_version", manifest["bank_version"] == "v2026-10-pilot")
    _assert("manifest_anchor", manifest["anchor_date"] == "2026-10-06")
    _assert("manifest_count", manifest["question_count"] == 20)
    _assert("manifest_verified", manifest["verified_count"] == 20)
    _assert("manifest_ids", manifest["ordered_question_ids"] == EXPECTED_PILOT_IDS)
    _assert("manifest_categories", manifest["category_counts"] == EXPECTED_CATEGORY_COUNTS)
    _assert("manifest_temporal", manifest["temporal_class_counts"] == {"stable": 20})
    _assert("manifest_provenance", manifest["provenance_tier_counts"] == {"P1": 8, "P2": 12})
    _assert("manifest_rights", manifest["rights_mode_counts"] == {"official_derived_rewrite": 20})
    _assert("manifest_immutable", manifest["immutability"] is True)
    _assert("manifest_as_of", manifest["verification_as_of"] == "2026-10-06")
    _assert("manifest_no_source_wording_reuse", manifest["source_provenance_summary"]["source_wording_reuse"] is False)
    print("PASS pilot_release_contract")


def test_pilot_rotation_matches_manifest_order() -> None:
    questions = quiz.load_verified_questions(PILOT_BANK)
    picked = [quiz.select_question(questions, quiz_date=date(2026, 10, 6 + idx), anchor_date=date(2026, 10, 6)).question_id for idx in range(20)]
    _assert("pilot_rotation_order", picked == EXPECTED_PILOT_IDS, str(picked))
    print("PASS pilot_rotation_matches_manifest_order")


TESTS = [
    test_missing_provenance_metadata_fails_closed,
    test_invalid_temporal_and_rights_metadata_fail_closed,
    test_date_sensitive_requires_bounded_validity_window,
    test_pilot_release_contract,
    test_pilot_rotation_matches_manifest_order,
    test_valid_verified_bilingual_question_is_accepted,
    test_unverified_is_never_publishable,
    test_malformed_record_is_skipped,
    test_invalid_correct_index_is_skipped,
    test_two_or_five_options_fail_product_contract,
    test_final_question_over_300_fails_closed,
    test_final_option_over_100_fails_closed,
    test_explanation_over_200_fails_closed,
    test_same_date_selection_is_deterministic,
    test_no_repetition_across_complete_rotation_cycle,
    test_existing_sent_receipt_blocks_duplicate,
    test_existing_reserved_receipt_blocks_duplicate,
    test_weather_receipt_missing_means_no_quiz_send,
    test_successful_send_writes_sent_receipt,
    test_telegram_failure_leaves_reservation_nonfatal,
    test_retry_after_ambiguous_reservation_sends_zero_polls,
    test_quiz_failure_never_mutates_weather_receipts,
    test_manual_nonproduction_cannot_publish,
    test_missing_production_bank_safely_skips,
    test_quiz_snapshot_restore_does_not_alter_weather_gating,
]


def main() -> None:
    for test in TESTS:
        test()
    print(f"OK: {len(TESTS)} Cyprus Culture quiz offline checks passed")


if __name__ == "__main__":
    main()
