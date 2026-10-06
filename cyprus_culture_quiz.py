#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic, non-blocking Cyprus Culture Telegram quiz delivery."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo


TZ_NAME = "Asia/Nicosia"
DEFAULT_BANK_PATH = Path("data/cyprus_culture/v2026-10-pilot/questions.jsonl")
DEFAULT_QUIZ_RECEIPT_DIR = Path(".cache/cy_quiz_delivery")
DEFAULT_WEATHER_TEXT_RECEIPT_DIR = Path(".cache/cy_text_delivery")
ALLOWED_PRODUCTION_SCHEDULES = {"0 13 * * *", "45 13 * * *", "15 15 * * *"}
SUPPORTED_TEMPORAL_CLASSES = {"stable", "date_sensitive", "current_events"}
SUPPORTED_RIGHTS_STATUSES = {"official_derived_rewrite", "exact_source_wording"}
SUPPORTED_REVIEW_STATUSES = {"verified_official"}
PROVENANCE_TIER_RE = re.compile(r"^P[1-3]$")
SOURCE_TEXT_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SUPPORTED_CATEGORIES = {
    "geography",
    "history",
    "culture",
    "institutions",
    "society",
    "traditions",
    "language",
    "economy",
    "environment",
    "other",
}
QUESTION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
MAX_QUESTION_CHARS = 300
MAX_OPTION_CHARS = 100
MAX_EXPLANATION_CHARS = 200
MAX_EXPLANATION_LINE_FEEDS = 2


@dataclass(frozen=True)
class QuizQuestion:
    question_id: str
    rotation_rank: int
    category: str
    question_el: str
    question_ru: str
    options_el: tuple[str, ...]
    options_ru: tuple[str, ...]
    correct_option_index: int
    explanation_ru: str
    source: str
    verified: bool
    provenance_tier: str
    source_locator: str
    answer_source: str
    rights_status: str
    publication_mode: str
    source_text_hash: str
    as_of: str
    valid_from: str | None
    valid_until: str | None
    review_status: str
    temporal_class: str


@dataclass(frozen=True)
class QuizPayload:
    question: str
    options: tuple[str, ...]
    correct_option_index: int
    explanation: str


def _env_on(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_option(value: str) -> str:
    return " ".join(str(value or "").split()).casefold()


def _valid_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _positive_int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, int) and not isinstance(item, bool) and item > 0]


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "utf-8")
    tmp.replace(path)


def _reserve_json_exclusive(path: Path, payload: dict[str, Any]) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        raise
    return True


def _parse_question_record(record: Any) -> QuizQuestion | None:
    if not isinstance(record, dict) or record.get("verified") is not True:
        return None

    question_id = str(record.get("question_id") or "").strip()
    rotation_rank = record.get("rotation_rank")
    category = str(record.get("category") or "").strip()
    question_el = str(record.get("question_el") or "").strip()
    question_ru = str(record.get("question_ru") or "").strip()
    options_el = record.get("options_el")
    options_ru = record.get("options_ru")
    correct = record.get("correct_option_index")
    explanation = str(record.get("explanation_ru") or "").strip()
    source = str(record.get("source") or "").strip()
    provenance_tier = str(record.get("provenance_tier") or "").strip()
    source_locator = str(record.get("source_locator") or "").strip()
    answer_source = str(record.get("answer_source") or "").strip()
    rights_status = str(record.get("rights_status") or "").strip()
    publication_mode = str(record.get("publication_mode") or "").strip()
    source_text_hash = str(record.get("source_text_hash") or "").strip()
    as_of = str(record.get("as_of") or "").strip()
    review_status = str(record.get("review_status") or "").strip()
    temporal_class = str(record.get("temporal_class") or "").strip()

    if "valid_from" not in record or "valid_until" not in record:
        return None
    valid_from_raw = record.get("valid_from")
    valid_until_raw = record.get("valid_until")
    valid_from = None if valid_from_raw is None else str(valid_from_raw).strip()
    valid_until = None if valid_until_raw is None else str(valid_until_raw).strip()

    if not QUESTION_ID_RE.fullmatch(question_id):
        return None
    if not isinstance(rotation_rank, int) or isinstance(rotation_rank, bool) or rotation_rank < 0:
        return None
    if category not in SUPPORTED_CATEGORIES:
        return None
    if not question_el or not question_ru or not source:
        return None
    if not isinstance(options_el, list) or not isinstance(options_ru, list):
        return None
    if len(options_el) not in {3, 4} or len(options_ru) != len(options_el):
        return None

    clean_el = tuple(str(value or "").strip() for value in options_el)
    clean_ru = tuple(str(value or "").strip() for value in options_ru)
    if any(not value for value in clean_el + clean_ru):
        return None

    normalized_el = {_normalize_option(value) for value in clean_el}
    normalized_ru = {_normalize_option(value) for value in clean_ru}
    normalized_pairs = {
        (_normalize_option(el), _normalize_option(ru))
        for el, ru in zip(clean_el, clean_ru)
    }
    if (
        len(normalized_el) != len(clean_el)
        or len(normalized_ru) != len(clean_ru)
        or len(normalized_pairs) != len(clean_el)
    ):
        return None

    if not isinstance(correct, int) or isinstance(correct, bool) or not 0 <= correct < len(clean_el):
        return None
    if not PROVENANCE_TIER_RE.fullmatch(provenance_tier):
        return None
    if not source_locator or not answer_source.startswith("https://"):
        return None
    if rights_status not in SUPPORTED_RIGHTS_STATUSES or publication_mode != rights_status:
        return None
    if not SOURCE_TEXT_HASH_RE.fullmatch(source_text_hash):
        return None
    if not _valid_iso_date(as_of):
        return None
    if review_status not in SUPPORTED_REVIEW_STATUSES:
        return None
    if temporal_class not in SUPPORTED_TEMPORAL_CLASSES:
        return None
    for value in (valid_from, valid_until):
        if value is not None and (not value or not _valid_iso_date(value)):
            return None
    if valid_from is not None and valid_until is not None and valid_from > valid_until:
        return None
    if temporal_class in {"date_sensitive", "current_events"} and (
        valid_from is None or valid_until is None
    ):
        return None

    return QuizQuestion(
        question_id=question_id,
        rotation_rank=rotation_rank,
        category=category,
        question_el=question_el,
        question_ru=question_ru,
        options_el=clean_el,
        options_ru=clean_ru,
        correct_option_index=correct,
        explanation_ru=explanation,
        source=source,
        verified=True,
        provenance_tier=provenance_tier,
        source_locator=source_locator,
        answer_source=answer_source,
        rights_status=rights_status,
        publication_mode=publication_mode,
        source_text_hash=source_text_hash,
        as_of=as_of,
        valid_from=valid_from,
        valid_until=valid_until,
        review_status=review_status,
        temporal_class=temporal_class,
    )

def load_verified_questions(path: Path) -> list[QuizQuestion]:
    if not path.is_file():
        return []
    parsed: list[QuizQuestion] = []
    for raw_line in path.read_text("utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        question = _parse_question_record(record)
        if question is not None:
            parsed.append(question)

    id_counts: dict[str, int] = {}
    rank_counts: dict[int, int] = {}
    for question in parsed:
        id_counts[question.question_id] = id_counts.get(question.question_id, 0) + 1
        rank_counts[question.rotation_rank] = rank_counts.get(question.rotation_rank, 0) + 1

    return sorted(
        (
            question
            for question in parsed
            if id_counts[question.question_id] == 1 and rank_counts[question.rotation_rank] == 1
        ),
        key=lambda question: question.rotation_rank,
    )


def select_question(
    questions: list[QuizQuestion],
    *,
    quiz_date: date,
    anchor_date: date,
) -> QuizQuestion | None:
    if not questions or quiz_date < anchor_date:
        return None
    offset = (quiz_date - anchor_date).days
    return questions[offset % len(questions)]


def assemble_payload(question: QuizQuestion) -> QuizPayload | None:
    assembled_question = (
        "🇨🇾 Вопрос о Кипре\n"
        f"{question.question_el}\n"
        f"🇷🇺 {question.question_ru}"
    )
    options = tuple(
        f"{el} — {ru}" for el, ru in zip(question.options_el, question.options_ru)
    )
    explanation = question.explanation_ru.strip()

    if len(assembled_question) > MAX_QUESTION_CHARS:
        return None
    if any(len(option) > MAX_OPTION_CHARS for option in options):
        return None
    if len(explanation) > MAX_EXPLANATION_CHARS:
        return None
    if explanation.count("\n") > MAX_EXPLANATION_LINE_FEEDS:
        return None

    return QuizPayload(
        question=assembled_question,
        options=options,
        correct_option_index=question.correct_option_index,
        explanation=explanation,
    )


def weather_text_receipt_path(weather_target_date: date, directory: Path) -> Path:
    return directory / f"{weather_target_date.isoformat()}-evening.json"


def valid_weather_text_receipt(path: Path, weather_target_date: date) -> bool:
    try:
        data = json.loads(path.read_text("utf-8"))
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    if data.get("target_date") != weather_target_date.isoformat():
        return False
    if data.get("post_type") != "evening" or data.get("chat_type") != "production":
        return False
    chunk_count = data.get("text_chunk_count")
    if not isinstance(chunk_count, int) or isinstance(chunk_count, bool) or chunk_count < 1:
        return False
    if len(_positive_int_list(data.get("telegram_message_ids"))) < chunk_count:
        return False
    return isinstance(data.get("sent_at_utc"), str) and bool(data["sent_at_utc"].strip())


def quiz_receipt_path(quiz_date: date, directory: Path) -> Path:
    return directory / f"{quiz_date.isoformat()}.json"


def is_natural_production_schedule(event_name: str, event_schedule: str) -> bool:
    return event_name == "schedule" and event_schedule in ALLOWED_PRODUCTION_SCHEDULES


async def _default_send_poll(
    *,
    token: str,
    chat_id: str,
    payload: QuizPayload,
) -> Any:
    from telegram import Bot

    bot = Bot(token=token)
    return await bot.send_poll(
        chat_id=chat_id,
        question=payload.question,
        options=list(payload.options),
        type="quiz",
        correct_option_id=payload.correct_option_index,
        is_anonymous=True,
        explanation=payload.explanation or None,
    )


async def deliver_daily_quiz(
    *,
    enabled: bool,
    event_name: str,
    event_schedule: str,
    chat_id: str,
    token: str,
    bank_path: Path,
    bank_version: str,
    anchor_date_text: str,
    weather_receipt_dir: Path,
    quiz_receipt_dir: Path,
    now: datetime | None = None,
    send_poll: Callable[..., Awaitable[Any]] = _default_send_poll,
    run_id: str = "",
    run_attempt: str = "",
) -> dict[str, Any]:
    if not enabled:
        return {"result": "quiz_skipped_disabled"}

    if not is_natural_production_schedule(event_name, event_schedule):
        return {"result": "quiz_skipped_non_production"}

    if not chat_id or not token:
        return {"result": "quiz_failed_non_fatal", "reason": "telegram_config_missing"}

    bank_version = str(bank_version or "").strip()
    anchor_date_text = str(anchor_date_text or "").strip()
    if not bank_version or not anchor_date_text:
        return {"result": "quiz_skipped_invalid_question", "reason": "bank_release_unconfigured"}

    try:
        anchor_date = date.fromisoformat(anchor_date_text)
    except ValueError:
        return {"result": "quiz_skipped_invalid_question", "reason": "invalid_anchor_date"}

    local_now = now or datetime.now(ZoneInfo(TZ_NAME))
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=ZoneInfo(TZ_NAME))
    else:
        local_now = local_now.astimezone(ZoneInfo(TZ_NAME))
    quiz_date = local_now.date()
    weather_target_date = quiz_date + timedelta(days=1)

    weather_receipt = weather_text_receipt_path(weather_target_date, weather_receipt_dir)
    if not valid_weather_text_receipt(weather_receipt, weather_target_date):
        return {
            "result": "quiz_skipped_weather_not_delivered",
            "quiz_date": quiz_date.isoformat(),
            "weather_target_date": weather_target_date.isoformat(),
        }

    if not bank_path.is_file():
        return {
            "result": "quiz_skipped_no_bank",
            "quiz_date": quiz_date.isoformat(),
            "weather_target_date": weather_target_date.isoformat(),
        }

    questions = load_verified_questions(bank_path)
    if not questions:
        return {
            "result": "quiz_skipped_no_verified_questions",
            "quiz_date": quiz_date.isoformat(),
            "weather_target_date": weather_target_date.isoformat(),
        }

    selected = select_question(questions, quiz_date=quiz_date, anchor_date=anchor_date)
    if selected is None:
        return {
            "result": "quiz_skipped_invalid_question",
            "reason": "rotation_not_started",
            "quiz_date": quiz_date.isoformat(),
            "weather_target_date": weather_target_date.isoformat(),
        }

    payload = assemble_payload(selected)
    if payload is None:
        return {
            "result": "quiz_skipped_invalid_question",
            "reason": "telegram_payload_limits",
            "question_id": selected.question_id,
            "quiz_date": quiz_date.isoformat(),
            "weather_target_date": weather_target_date.isoformat(),
        }

    receipt_path = quiz_receipt_path(quiz_date, quiz_receipt_dir)
    if receipt_path.exists():
        return {
            "result": "quiz_skipped_receipt_exists",
            "question_id": selected.question_id,
            "quiz_date": quiz_date.isoformat(),
            "weather_target_date": weather_target_date.isoformat(),
            "receipt_path": str(receipt_path),
        }

    reservation = {
        "quiz_date": quiz_date.isoformat(),
        "weather_target_date": weather_target_date.isoformat(),
        "chat_type": "production",
        "question_id": selected.question_id,
        "bank_version": bank_version,
        "state": "reserved",
        "run_id": str(run_id or ""),
        "run_attempt": str(run_attempt or ""),
        "reserved_at_utc": _utc_now(),
    }
    if not _reserve_json_exclusive(receipt_path, reservation):
        return {
            "result": "quiz_skipped_receipt_exists",
            "question_id": selected.question_id,
            "quiz_date": quiz_date.isoformat(),
            "weather_target_date": weather_target_date.isoformat(),
            "receipt_path": str(receipt_path),
        }

    try:
        message = await send_poll(
            token=token,
            chat_id=chat_id,
            payload=payload,
        )
        message_id = getattr(message, "message_id", None)
        poll = getattr(message, "poll", None)
        poll_id = str(getattr(poll, "id", "") or "").strip()
        if not isinstance(message_id, int) or message_id <= 0 or not poll_id:
            raise RuntimeError("Telegram quiz response missing message_id/poll_id")

        sent_receipt = dict(reservation)
        sent_receipt.update(
            {
                "state": "sent",
                "telegram_message_id": message_id,
                "poll_id": poll_id,
                "sent_at_utc": _utc_now(),
            }
        )
        _atomic_write_json(receipt_path, sent_receipt)
        return {
            "result": "quiz_sent",
            "question_id": selected.question_id,
            "quiz_date": quiz_date.isoformat(),
            "weather_target_date": weather_target_date.isoformat(),
            "telegram_message_id": message_id,
            "poll_id": poll_id,
            "receipt_path": str(receipt_path),
        }
    except Exception as exc:
        return {
            "result": "quiz_failed_non_fatal",
            "reason": exc.__class__.__name__,
            "question_id": selected.question_id,
            "quiz_date": quiz_date.isoformat(),
            "weather_target_date": weather_target_date.isoformat(),
            "receipt_path": str(receipt_path),
        }


async def run_from_environment() -> dict[str, Any]:
    return await deliver_daily_quiz(
        enabled=_env_on("CY_CULTURE_QUIZ_ENABLED", False),
        event_name=os.getenv("GITHUB_EVENT_NAME", ""),
        event_schedule=os.getenv("GITHUB_EVENT_SCHEDULE", ""),
        chat_id=(os.getenv("CHANNEL_ID") or "").strip(),
        token=(os.getenv("TELEGRAM_TOKEN") or "").strip(),
        bank_path=Path(os.getenv("CY_CULTURE_QUIZ_BANK_PATH", str(DEFAULT_BANK_PATH))),
        bank_version=os.getenv("CY_CULTURE_QUIZ_BANK_VERSION", ""),
        anchor_date_text=os.getenv("CY_CULTURE_QUIZ_ANCHOR_DATE", ""),
        weather_receipt_dir=Path(
            os.getenv("CY_TEXT_DELIVERY_DIR", str(DEFAULT_WEATHER_TEXT_RECEIPT_DIR))
        ),
        quiz_receipt_dir=Path(
            os.getenv("CY_QUIZ_DELIVERY_DIR", str(DEFAULT_QUIZ_RECEIPT_DIR))
        ),
        run_id=os.getenv("GITHUB_RUN_ID", ""),
        run_attempt=os.getenv("GITHUB_RUN_ATTEMPT", ""),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Cyprus Culture daily Telegram quiz")
    parser.parse_args()
    try:
        result = asyncio.run(run_from_environment())
    except BaseException as exc:
        result = {
            "result": "quiz_failed_non_fatal",
            "reason": exc.__class__.__name__,
        }
    print("CY_CULTURE_QUIZ_RESULT=" + json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
