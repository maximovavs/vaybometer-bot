#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build and optionally send a sanitized VayboMeter post.

Used for safe FORMAT_V2 tests and controlled manual routing. Scheduled production
runs stay on the legacy path unless the workflow explicitly enables FORMAT_V2.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import logging
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import pendulum
from telegram import Bot, constants
try:  # python-telegram-bot exposes retryable exceptions here in CI/prod.
    from telegram.error import NetworkError, RetryAfter, ServerError, TimedOut
except Exception:  # pragma: no cover - local lightweight telegram module fallback
    NetworkError = RetryAfter = ServerError = TimedOut = None  # type: ignore[assignment]

from editorial_voice import build_evening_human_line, build_morning_human_line
from post_common import build_message, sup_safety_level
from post_safety import sanitize_post_text, split_telegram_text, validation_summary
from visibility_context import (
    has_structured_visibility_alert,
    visibility_air_penalty,
    visibility_condition_from_text,
    visibility_penalty,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TZ_STR = os.getenv("TZ", "Asia/Nicosia")

SEA_LABEL = "Морские города"
OTHER_LABEL = "Континентальные города"
SEA_CITIES_ORDERED = [
    ("Limassol", (34.707, 33.022)),
    ("Pafos", (34.776, 32.424)),
    ("Ayia Napa", (34.988, 34.012)),
    ("Larnaca", (34.916, 33.624)),
]
OTHER_CITIES_ALL = {
    "Nicosia": (35.170, 33.360),
    "Troodos": (34.916, 32.823),
}

_CY_CANONICAL_DAILY_HASHTAGS = "#Кипр #погода #здоровье #Никосия #Тродос"
_CY_IMAGE_DELIVERY_DIR = Path(".cache/cy_image_delivery")
_CY_TEXT_DELIVERY_DIR = Path(".cache/cy_text_delivery")
_CY_IMAGE_DIAGNOSTICS_DIR = Path(".cache/cy_image_diagnostics")
_TELEGRAM_RETRY_EXCEPTIONS = tuple(
    exc
    for exc in (TimedOut, NetworkError, RetryAfter, ServerError, ConnectionError, TimeoutError, OSError)
    if isinstance(exc, type) and issubclass(exc, BaseException)
)

_CY_MORNING_ACTIVE = False
_CY_MORNING_TARGET_DATE = ""
_CY_MORNING_FINAL_TEXT = ""
_CY_MORNING_PARTIAL_TEXT_MESSAGE_IDS: list[int] = []
_CY_MORNING_PHASE_LOG: list[dict[str, object]] = []

_DIR_RU = {
    "N": "северный ветер",
    "NE": "северо-восточный ветер",
    "E": "восточный ветер",
    "SE": "юго-восточный ветер",
    "S": "южный ветер",
    "SW": "юго-западный ветер",
    "W": "западный ветер",
    "NW": "северо-западный ветер",
}

_SHORE_RU = {
    "onshore": "к берегу",
    "offshore": "от берега",
    "cross": "вдоль берега",
}


def _env_on(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _env_any(*names: str) -> bool:
    return any(_env_on(name) for name in names)


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _redact_secret_text(value: str) -> str:
    redacted = value
    for secret in (
        TOKEN,
        os.getenv("TELEGRAM_TOKEN", ""),
        os.getenv("OPENAI_API_KEY", ""),
        os.getenv("GEMINI_API_KEY", ""),
        os.getenv("GROQ_API_KEY", ""),
        os.getenv("POLLINATIONS_TOKEN", ""),
        os.getenv("STABLE_HORDE_API_KEY", ""),
        os.getenv("CUSTOM_IMAGE_API_KEY", ""),
    ):
        secret = (secret or "").strip()
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    redacted = re.sub(
        r"https?://[^\s\"']*(?:token|key|apikey|api_key|auth|authorization)=[^\s\"'&]+",
        "[redacted-url]",
        redacted,
        flags=re.I,
    )
    redacted = re.sub(r"(Authorization:\s*(?:Bearer\s+)?)[^\s,;]+", r"\1[redacted]", redacted, flags=re.I)
    return redacted


def cy_morning_delivery_dir() -> Path:
    return Path(os.getenv("CY_MORNING_DELIVERY_DIR", ".cache/cy_morning_delivery"))


def cy_morning_delivery_path(target_date: str) -> Path:
    safe_date = str(target_date or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", safe_date):
        raise ValueError(f"invalid Cyprus morning target date: {target_date!r}")
    return cy_morning_delivery_dir() / f"{safe_date}.json"


def cy_morning_target_date(date_text: str = "", tz_name: str | None = None) -> str:
    raw = str(date_text or "").strip()
    if raw:
        return dt.date.fromisoformat(raw[:10]).isoformat()
    tz = ZoneInfo(tz_name or TZ_STR)
    return dt.datetime.now(tz).date().isoformat()


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    _atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def cy_morning_delivery_payload(
    *,
    target_date: str,
    chat_type: str,
    telegram_message_ids: list[int],
    text_chunk_count: int,
    event_schedule: str | None = None,
) -> dict[str, object]:
    return {
        "target_date": target_date,
        "chat_type": chat_type,
        "telegram_message_ids": list(telegram_message_ids),
        "text_chunk_count": int(text_chunk_count),
        "run_id": os.getenv("GITHUB_RUN_ID", ""),
        "run_attempt": os.getenv("GITHUB_RUN_ATTEMPT", ""),
        "event_schedule": event_schedule if event_schedule is not None else os.getenv("GITHUB_EVENT_SCHEDULE", ""),
        "sent_at_utc": _utc_now_iso(),
    }


def cy_morning_write_delivery_receipt(
    *,
    target_date: str,
    chat_type: str,
    telegram_message_ids: list[int],
    text_chunk_count: int,
    event_schedule: str | None = None,
) -> Path:
    payload = cy_morning_delivery_payload(
        target_date=target_date,
        chat_type=chat_type,
        telegram_message_ids=telegram_message_ids,
        text_chunk_count=text_chunk_count,
        event_schedule=event_schedule,
    )
    path = cy_morning_delivery_path(target_date)
    _atomic_write_json(path, payload)
    return path


def cy_morning_maybe_write_delivery_receipt(
    *,
    target_date: str,
    chat_type: str,
    telegram_message_ids: list[int],
    text_chunk_count: int,
    sent: bool,
    event_schedule: str | None = None,
) -> Path | None:
    if not sent:
        return None
    if chat_type != "production":
        return None
    if text_chunk_count < 1 or len(telegram_message_ids) < text_chunk_count:
        return None
    return cy_morning_write_delivery_receipt(
        target_date=target_date,
        chat_type=chat_type,
        telegram_message_ids=telegram_message_ids,
        text_chunk_count=text_chunk_count,
        event_schedule=event_schedule,
    )


def cy_morning_load_delivery_receipt(target_date: str) -> dict[str, object] | None:
    path = cy_morning_delivery_path(target_date)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:
        logging.warning("Cyprus morning delivery receipt read failed: %s", exc)
        return None
    return data if isinstance(data, dict) else None


def cy_morning_is_valid_production_receipt(data: object, target_date: str) -> bool:
    if not isinstance(data, dict):
        return False
    if data.get("target_date") != target_date:
        return False
    if data.get("chat_type") != "production":
        return False
    message_ids = data.get("telegram_message_ids")
    if not isinstance(message_ids, list) or not message_ids:
        return False
    chunk_count = data.get("text_chunk_count")
    if not isinstance(chunk_count, int) or chunk_count < 1:
        return False
    if len(message_ids) < chunk_count:
        return False
    sent_at = data.get("sent_at_utc")
    return isinstance(sent_at, str) and bool(sent_at.strip())


def cy_morning_has_valid_production_receipt(target_date: str) -> bool:
    return cy_morning_is_valid_production_receipt(
        cy_morning_load_delivery_receipt(target_date),
        target_date,
    )

def cy_text_delivery_path(target_date: str, post_type: str) -> Path:
    return _cy_text_receipt_path(target_date, post_type)


def cy_image_delivery_path(target_date: str, post_type: str) -> Path:
    return _cy_image_receipt_path(target_date, post_type)


def _load_json_object(path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:
        logging.warning("Receipt read failed: %s: %s", path, exc)
        return None
    return data if isinstance(data, dict) else None


def _positive_int_list(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    out: list[int] = []
    for item in value:
        if isinstance(item, int) and item > 0:
            out.append(item)
    return out


def is_valid_cy_text_receipt(target_date: str, post_type: str) -> bool:
    data = _load_json_object(cy_text_delivery_path(target_date, post_type))
    if not isinstance(data, dict):
        return False
    if data.get("target_date") != target_date:
        return False
    if data.get("post_type") != post_type:
        return False
    if data.get("chat_type") != "production":
        return False
    chunk_count = data.get("text_chunk_count")
    if not isinstance(chunk_count, int) or chunk_count < 1:
        return False
    if len(_positive_int_list(data.get("telegram_message_ids"))) < chunk_count:
        return False
    sent_at = data.get("sent_at_utc")
    return isinstance(sent_at, str) and bool(sent_at.strip())


def is_valid_cy_image_receipt(target_date: str, post_type: str) -> bool:
    data = _load_json_object(cy_image_delivery_path(target_date, post_type))
    if not isinstance(data, dict):
        return False
    if data.get("target_date") != target_date:
        return False
    if data.get("post_type") != post_type:
        return False
    if data.get("chat_type") != "production":
        return False
    message_id = data.get("telegram_message_id")
    if not isinstance(message_id, int) or message_id <= 0:
        return False
    if not str(data.get("sha256") or "").strip():
        return False
    if not str(data.get("selected_scene") or "").strip():
        return False
    sent_at = data.get("sent_at_utc")
    return isinstance(sent_at, str) and bool(sent_at.strip())


def has_valid_cy_text_delivery(target_date: str, post_type: str, *, allow_legacy_morning: bool = True) -> bool:
    if is_valid_cy_text_receipt(target_date, post_type):
        return True
    return bool(
        allow_legacy_morning
        and post_type == "morning"
        and cy_morning_has_valid_production_receipt(target_date)
    )


def cy_morning_image_phase_for_result(image_result: str) -> str:
    return {
        "sent": "image_sent",
        "generated": "image_generated",
        "failed_non_fatal": "image_failed_non_fatal",
        "failed_after_duplicates": "image_failed_non_fatal",
        "skipped": "image_skipped",
        "skipped_receipt_exists": "image_skipped",
        "skipped_receipt_appeared_during_generation": "image_skipped",
        "skipped_no_text_receipt": "image_skipped",
        "skipped_duplicate": "image_skipped",
        "skipped_duplicate_before_send": "image_skipped",
        "skipped_duplicate_local_weather_card": "image_skipped",
        "skipped_duplicate_local_informative_cover": "image_skipped",
    }.get(str(image_result or "unknown"), "image_result")


def _cy_morning_chat_type(args: argparse.Namespace) -> str:
    if args.to_test or args.send_image_to_test:
        return "test"
    if args.chat_id and os.getenv("GITHUB_EVENT_NAME") == "schedule":
        return "production"
    if args.chat_id:
        return "override"
    if args.send:
        return "send_without_chat"
    return "dry_run"


def _cy_morning_phase(phase: str, **fields: object) -> None:
    if not _CY_MORNING_ACTIVE:
        return
    record: dict[str, object] = {
        "phase": phase,
        "ts_utc": _utc_now_iso(),
    }
    if _CY_MORNING_TARGET_DATE:
        record["target_date"] = _CY_MORNING_TARGET_DATE
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            record[key] = [str(item) for item in value]
        else:
            record[key] = value
    _CY_MORNING_PHASE_LOG.append(record)
    detail = " ".join(
        f"{key}={json.dumps(value, ensure_ascii=False)}"
        for key, value in record.items()
        if key not in {"phase", "ts_utc"}
    )
    line = f"CY_MORNING_PHASE={phase}"
    if detail:
        line += f" {detail}"
    print(line)
    logging.info(line)


def _write_cy_morning_diagnostics(exc: BaseException) -> None:
    if not (_CY_MORNING_ACTIVE or _CY_MORNING_PHASE_LOG):
        return
    diagnostics_dir = Path(".cache/cy_morning_diagnostics")
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    (diagnostics_dir / "phase_log.json").write_text(
        json.dumps(_CY_MORNING_PHASE_LOG, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if _CY_MORNING_FINAL_TEXT:
        (diagnostics_dir / "sanitized_final_text.txt").write_text(
            _CY_MORNING_FINAL_TEXT,
            encoding="utf-8",
        )
    exception_payload = {
        "type": exc.__class__.__name__,
        "message": _redact_secret_text(str(exc)),
        "target_date": _CY_MORNING_TARGET_DATE,
        "partial_telegram_message_ids": list(_CY_MORNING_PARTIAL_TEXT_MESSAGE_IDS),
        "ts_utc": _utc_now_iso(),
    }
    (diagnostics_dir / "exception.json").write_text(
        json.dumps(exception_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _plain(text: str) -> str:
    return re.sub(r"</?b>", "", str(text or "")).strip()


_CY_STORM_NEGATION_RE = re.compile(
    r"шторм\w*\s+не\s+ожида|без\s+шторма|штормов\w*\s+предупрежден\w*\s+нет|риск\s+шторма\s+низк",
    re.I,
)
_CY_STORM_POSITIVE_RE = re.compile(r"\b(?:шторм\w*|шквал\w*)\b", re.I)


def _has_actual_cyprus_storm_signal(text: str, gust_max: float | None = None) -> bool:
    if isinstance(gust_max, (int, float)) and gust_max >= 15:
        return True
    for line in str(text or "").splitlines():
        if _CY_STORM_NEGATION_RE.search(line):
            continue
        if _CY_STORM_POSITIVE_RE.search(line):
            return True
    return False


def _num(pattern: str, text: str) -> float | None:
    m = re.search(pattern, _plain(text), flags=re.I)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except Exception:
        return None


def _numbers(pattern: str, text: str) -> list[float]:
    out: list[float] = []
    for raw in re.findall(pattern, _plain(text), flags=re.I):
        val = raw[0] if isinstance(raw, tuple) else raw
        try:
            out.append(float(str(val).replace(",", ".")))
        except Exception:
            pass
    return out


def _score_label(score: float) -> str:
    return "отлично" if score >= 8.5 else "хорошо" if score >= 7 else "с оговорками" if score >= 5.5 else "бережный режим"


def _cy_place(city: str) -> str:
    c = str(city or "").strip()
    if not c:
        return ""
    return {"Никосия": "в Никосии", "Тродос": "на Тродосе"}.get(c, f"в {c}")


def _cyprus_conditions(v2_text: str) -> dict[str, float | bool | str | None]:
    lines = [x.strip() for x in str(v2_text or "").splitlines() if x.strip()]
    temp_line = next((x for x in lines if x.startswith("🌡 Теплее всего")), "")
    wind_line = next((x for x in lines if x.startswith("💨")), "")
    uv_line = next((x for x in lines if x.startswith("☀️")), "")
    air_line = next((x for x in lines if x.startswith("🏭")), "")

    warm = re.search(r"Теплее всего\s*[—-]\s*([^()]+)\(([-+]?\d+(?:[\.,]\d+)?)°\)", _plain(temp_line))
    cool = re.search(r"прохладнее\s*[—-]\s*([^()]+)\(([-+]?\d+(?:[\.,]\d+)?)°\)", _plain(temp_line))
    return {
        "warm_city": warm.group(1).strip() if warm else "",
        "cool_city": cool.group(1).strip() if cool else "",
        "warm_t": float(warm.group(2).replace(",", ".")) if warm else None,
        "cool_t": float(cool.group(2).replace(",", ".")) if cool else None,
        "wind": _num(r"Ветер:\s*(\d+(?:[\.,]\d+)?)", wind_line),
        "gust": _num(r"порывы\s+до\s*(\d+(?:[\.,]\d+)?)", wind_line),
        "uv": _num(r"УФ\s*(\d+(?:[\.,]\d+)?)", uv_line),
        "aqi": _num(r"AQI\s*(\d+(?:[\.,]\d+)?)", air_line),
    }


def _cyprus_feels_line(v2_text: str) -> str:
    c = _cyprus_conditions(v2_text)
    warm_city = str(c.get("warm_city") or "")
    cool_city = str(c.get("cool_city") or "")
    warm_t = c.get("warm_t")
    wind = c.get("wind")
    gust = c.get("gust")
    uv = c.get("uv")

    parts: list[str] = []
    warm_place = _cy_place(warm_city)
    cool_place = _cy_place(cool_city)
    if isinstance(warm_t, (int, float)) and warm_place:
        if warm_t >= 31:
            parts.append(f"жарко {warm_place}")
        elif warm_t >= 28:
            parts.append(f"очень тепло {warm_place}")
        else:
            parts.append(f"тепло {warm_place}")
    if cool_place:
        parts.append(f"свежее {cool_place}")
    if isinstance(gust, (int, float)) and gust >= 15:
        parts.append("у моря порывы ощутимы")
    elif isinstance(wind, (int, float)) and wind >= 5:
        parts.append("ветер заметный у моря")
    if isinstance(uv, (int, float)) and uv >= 8:
        parts.append("на солнце высокая нагрузка")
    elif isinstance(uv, (int, float)) and uv >= 6:
        # Sensation only: the protective action (SPF) belongs to the plan line.
        parts.append("на солнце ощутимо печёт")
    return "🌡 Ощущается: " + "; ".join(parts[:4]) + "." if parts else ""


def _cyprus_best_window_line(v2_text: str) -> str:
    for line in str(v2_text or "").splitlines():
        clean = line.strip()
        if not clean.startswith("🕒 Лучшее окно:"):
            continue
        if re.search(r"\b\d{2}:\d{2}\s*[–—-]\s*\d{2}:\d{2}\b", clean):
            return clean
    return ""


def _cyprus_visibility_condition(v2_text: str) -> str:
    return visibility_condition_from_text(_plain(v2_text))


def _cyprus_smart_plan_line(v2_text: str) -> str:
    c = _cyprus_conditions(v2_text)
    warm_t = c.get("warm_t")
    uv = c.get("uv")
    gust = c.get("gust")
    hot = isinstance(warm_t, (int, float)) and warm_t >= 31
    high_uv = isinstance(uv, (int, float)) and uv >= 8
    windy = isinstance(gust, (int, float)) and gust >= 15
    visibility_condition = _cyprus_visibility_condition(v2_text)

    if visibility_condition in {"dense_fog", "fog"} and (hot or high_uv):
        return "✅ План: утром снизить скорость и увеличить дистанцию; после прояснения — вода, SPF и тень."
    if visibility_condition in {"dense_fog", "fog"}:
        return "✅ План: утром снизить скорость и увеличить дистанцию; прогулку у моря перенести на время после прояснения."

    if hot and high_uv and windy:
        return "✅ План: дела и прогулка до 11:00; 11–16 — тень/помещение; SPF 50 и вода с собой; у моря — защищённые места."
    if high_uv and windy:
        return "✅ План: активность до 11:00 или после 18:30; 11–16 — тень; SPF 50, вода; у моря — защищённые места."
    if hot and high_uv:
        return "✅ План: основные дела до 11:00; 11–16 — тень/помещение; SPF 50 и вода; прогулка ближе к закату."
    if high_uv:
        return "✅ План: SPF 50, вода с собой; полдень провести в тени; прогулка утром или ближе к закату."
    if windy:
        return "✅ План: у моря выбирать защищённые места; лёгкие вещи закрепить; прогулку сверять с фактическим ветром."
    return ""


def _has_cyprus_precip_risk(text: str) -> bool:
    low = _plain(text).lower()
    if re.search(r"без\s+осад|осад\w*\s+не\s+ожида|дожд\w*\s+не\s+буд", low, flags=re.I):
        return False
    return bool(
        re.search(
            r"местами\s+(?:дожд|ливн|гроз|осад)|(?:дожд|ливн|гроз|осад)\w*\s+возмож|возмож\w*\s+(?:дожд|ливн|гроз|осад)",
            low,
            flags=re.I,
        )
    )


def _cyprus_score_line(v2_text: str) -> str:
    c = _cyprus_conditions(v2_text)
    warm_t = c.get("warm_t")
    uv = c.get("uv")
    gust = c.get("gust")
    wind = c.get("wind")
    aqi = c.get("aqi")
    visibility_condition = _cyprus_visibility_condition(v2_text)

    score = 10.0
    reasons: list[str] = []
    if isinstance(warm_t, (int, float)):
        if warm_t >= 35:
            score -= 2.0; reasons.append("сильная жара")
        elif warm_t >= 32:
            score -= 1.4; reasons.append("жара")
        elif warm_t >= 30:
            score -= 0.8; reasons.append("тепло")
    if isinstance(uv, (int, float)):
        if uv >= 9:
            score -= 1.5; reasons.append("очень высокий УФ")
        elif uv >= 8:
            score -= 1.3; reasons.append("высокий УФ")
        elif uv >= 6:
            score -= 0.7; reasons.append("УФ заметный")
    if isinstance(gust, (int, float)):
        if gust >= 18:
            score -= 1.1; reasons.append("порывы у моря")
        elif gust >= 15:
            score -= 0.8; reasons.append("ветер у моря")
    elif isinstance(wind, (int, float)) and wind >= 6:
        score -= 0.5; reasons.append("ветер")
    air_penalty = 0.8 if isinstance(aqi, (int, float)) and aqi > 80 else 0.0
    atmospheric_penalty = visibility_air_penalty(visibility_condition, air_penalty)
    if atmospheric_penalty:
        score -= atmospheric_penalty
    if visibility_condition == "mixed_visibility" or (air_penalty and visibility_penalty(visibility_condition)):
        reasons.append("видимость и воздух хуже")
    elif air_penalty:
        reasons.append("воздух похуже")
    elif visibility_condition in {"dense_fog", "fog"}:
        reasons.append("утренний туман")
    elif visibility_condition != "clear":
        reasons.append("видимость снижена")

    score = max(1.0, min(10.0, score))
    label = _score_label(score)
    if reasons:
        return f"✨ VayboMeter: {score:.1f}/10 — {label}; " + ", ".join(reasons[:3]) + "."
    return f"✨ VayboMeter: {score:.1f}/10 — {label} для обычных дел и прогулок."


def _cyprus_evening_score_line(v2_text: str) -> str:
    text = _plain(v2_text)
    low = text.lower()
    daily_highs = _numbers(r"(-?\d+(?:[\.,]\d+)?)\s*/\s*-?\d+(?:[\.,]\d+)?\s*°", text)
    gusts = _numbers(r"порывы\s*(?:до\s*)?(\d+(?:[\.,]\d+)?)", text)
    winds = _numbers(r"💨\s*(\d+(?:[\.,]\d+)?)", text)
    max_t = max(daily_highs) if daily_highs else None
    max_gust = max(gusts) if gusts else None
    max_wind = max(winds) if winds else None
    visibility_condition = _cyprus_visibility_condition(v2_text)
    forecast_aqi = _cyprus_evening_forecast_aqi(v2_text)

    score = 10.0
    reasons: list[str] = []

    if max_t is not None:
        if max_t >= 35:
            score -= 1.8; reasons.append("сильная жара")
        elif max_t >= 33:
            score -= 1.5; reasons.append("жара")
        elif max_t >= 31:
            score -= 1.0; reasons.append("тепло")
    if isinstance(max_gust, (int, float)):
        if max_gust >= 16:
            score -= 1.1; reasons.append("порывы у моря")
        elif max_gust >= 12:
            score -= 0.8; reasons.append("порывы у моря")
        elif max_gust >= 10:
            score -= 0.5; reasons.append("порывы у моря")
    if isinstance(max_wind, (int, float)) and max_wind >= 6:
        score -= 0.4; reasons.append("ветер у моря")
    air_penalty = 0.8 if isinstance(forecast_aqi, (int, float)) and forecast_aqi > 80 else 0.0
    atmospheric_penalty = visibility_air_penalty(visibility_condition, air_penalty)
    if atmospheric_penalty:
        score -= atmospheric_penalty
        if visibility_condition == "mixed_visibility" or (air_penalty and visibility_penalty(visibility_condition)):
            reasons.append("видимость и воздух хуже")
        elif air_penalty:
            reasons.append("воздух похуже")
        elif visibility_condition in {"dense_fog", "fog"}:
            reasons.append("утренний туман")
        else:
            reasons.append("видимость снижена")
    if _has_cyprus_precip_risk(text):
        score -= 0.7; reasons.append("локальная погода")
    if _has_actual_cyprus_storm_signal(text, max_gust):
        score -= 1.0; reasons.append("штормовые порывы")
    if "микросценар" in low:
        score -= 0.2; reasons.append("разные зоны острова")

    score = max(1.0, min(10.0, score))
    label = _score_label(score)
    if reasons:
        cleaned = _dedupe_score_reasons(reasons[:3])
        return f"✨ VayboMeter завтра: {score:.1f}/10 — {label}; " + _format_reason_list(cleaned) + "."
    return f"✨ VayboMeter завтра: {score:.1f}/10 — {label} для обычных дел и прогулок."


def _cyprus_evening_forecast_aqi(v2_text: str) -> float | None:
    """Read only explicitly tomorrow/forecast-labelled AQI from an evening post."""
    for raw_line in str(v2_text or "").splitlines():
        line = _plain(raw_line).strip()
        low = line.lower()
        if "aqi" not in low:
            continue
        is_forecast = bool(
            re.search(r"\bвоздух\s+завтра(?:\s+утром)?\b", low)
            or re.search(r"\bпрогноз\w*\s+(?:воздуха|aqi)\b", low)
            or re.search(r"\baqi\s+завтра(?:\s+утром)?\b", low)
        )
        if not is_forecast:
            continue
        value = _num(r"\bAQI\s*(\d+(?:[\.,]\d+)?)", line)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _translate_shore_notes(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        d = match.group(1).upper()
        shore = match.group(2).lower()
        direction = _DIR_RU.get(d, d)
        if shore == "none":
            return f"({direction})"
        return f"({direction}, {_SHORE_RU.get(shore, shore)})"

    return re.sub(
        r"\((N|NE|E|SE|S|SW|W|NW)/(onshore|offshore|cross|None)\)",
        repl,
        str(text or ""),
        flags=re.I,
    )


def _fmt_ms(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def _format_reason_list(reasons: list[str]) -> str:
    if len(reasons) <= 1:
        return reasons[0] if reasons else ""
    if len(reasons) == 2:
        return f"{reasons[0]} и {reasons[1]}"
    return ", ".join(reasons[:-1]) + " и " + reasons[-1]


def _dedupe_score_reasons(reasons: list[str]) -> list[str]:
    out: list[str] = []
    by_key: dict[str, int] = {}

    def key_for(reason: str) -> str:
        low = reason.lower()
        if "порыв" in low or ("ветер" in low and "мор" in low):
            return "wind_sea"
        if "жара" in low or "тепло" in low:
            return "heat"
        if "дым" in low or "туман" in low:
            return "visibility"
        if "предупреж" in low or "шторм" in low:
            return "warning"
        if "дожд" in low or "осад" in low or "гроз" in low or "локальная погода" in low:
            return "local_weather"
        return low

    def better(new: str, old: str) -> bool:
        new_low, old_low = new.lower(), old.lower()
        if "сильная жара" in new_low and "сильная жара" not in old_low:
            return True
        if "порыв" in new_low and "порыв" not in old_low:
            return True
        return False

    for raw in reasons:
        reason = re.sub(r"\s+", " ", str(raw or "")).strip(" .;—-")
        if not reason:
            continue
        key = key_for(reason)
        if key in by_key:
            idx = by_key[key]
            if better(reason, out[idx]):
                out[idx] = reason
            continue
        by_key[key] = len(out)
        out.append(reason)
    return out


def _sup_shore_from_line(line: str) -> str | None:
    low = str(line or "").lower()
    if "offshore" in low or "от берега" in low:
        return "offshore"
    if "onshore" in low or "к берегу" in low:
        return "onshore"
    if "cross" in low or "вдоль берега" in low:
        return "cross"
    return None


def _sup_guard_line(line: str) -> str:
    wind = _num(r"(?:^|[•;])\s*ветер\s*(\d+(?:[\.,]\d+)?)\s*м/с", line)
    gust = _num(r"порывы\s*(?:до\s*)?(\d+(?:[\.,]\d+)?)\s*м/с", line)
    wave = _num(r"волна\s*(\d+(?:[\.,]\d+)?)\s*м", line)
    shore = _sup_shore_from_line(line)
    level = sup_safety_level(
        wind_ms=wind,
        gust_ms=gust,
        wave_h=wave,
        shore=shore,
        samples_aligned=all(value is not None for value in (wind, gust, wave, shore)),
    )
    if level == "excellent":
        return line

    note_m = re.search(r"\(([^()]+)\)", line)
    note = f" ({note_m.group(1)})" if note_m else ""
    if level == "delay":
        if isinstance(gust, (int, float)) and gust >= 15:
            reason = f"порывы до {_fmt_ms(gust)} м/с"
        elif shore == "offshore":
            reason = "ветер от берега"
        else:
            reason = "условия небезопасны"
        return f"🧜‍♂️ SUP лучше отложить: {reason}{note}."
    if level == "caution":
        if isinstance(gust, (int, float)) and gust >= 12:
            reason = f"порывы до {_fmt_ms(gust)} м/с"
        elif shore == "offshore":
            reason = "ветер от берега"
        else:
            reason = "условия требуют осторожности"
        return f"🧜‍♂️ SUP: только опытным и короткая сессия • {reason}{note}."
    return "🧜‍♂️ SUP: данных для уверенной оценки недостаточно; проверить ветер, порывы, направление и волну перед выходом."


def _downgrade_sup_lines(text: str) -> str:
    lines = str(text or "").splitlines()
    out: list[str] = []
    for line in lines:
        if "SUP" in line and "Отлично" in line:
            out.append(_sup_guard_line(line))
            continue
        out.append(line)
    return "\n".join(out)


def _surf_thresholds() -> tuple[float, float, float]:
    def read(name: str, default: str) -> float:
        try:
            return float(os.getenv(name, default))
        except Exception:
            return float(default)

    return (
        read("SURF_WAVE_GOOD_MIN", "0.9"),
        read("SURF_WAVE_GOOD_MAX", "2.5"),
        read("SURF_WIND_MAX", "10"),
    )


def _wave_from_city_line(line: str) -> float | None:
    s = str(line or "")
    m = re.search(r"(?:волна|wave)[^\d]{0,16}(\d+(?:[\.,]\d+)?)\s*м", s, flags=re.I)
    if m:
        return float(m.group(1).replace(",", "."))
    if "🌊" in s:
        tail = s.split("🌊", 1)[1]
        nums = [float(x.replace(",", ".")) for x in re.findall(r"(\d+(?:[\.,]\d+)?)", tail)]
        if len(nums) >= 2 and 0 <= nums[1] <= 5:
            return nums[1]
    m = re.search(r"(?:^|[•;])\s*(\d+(?:[\.,]\d+)?)\s*м\b", s)
    if m:
        value = float(m.group(1).replace(",", "."))
        if 0 <= value <= 5:
            return value
    return None


def _wind_from_city_line(line: str) -> float | None:
    return _num(r"💨\s*(\d+(?:[\.,]\d+)?)\s*м/с", line)


def _polish_surf_lines(text: str) -> str:
    lines = str(text or "").splitlines()
    out: list[str] = []
    last_wave: float | None = None
    last_wind: float | None = None
    good_min, good_max, wind_max = _surf_thresholds()
    for line in lines:
        wave = _wave_from_city_line(line)
        wind = _wind_from_city_line(line)
        if wave is not None:
            last_wave = wave
        if wind is not None:
            last_wind = wind

        if "Отлично:" in line and re.search(r"\b(?:Серф|Сёрф|Surf)\b", line, flags=re.I):
            if last_wave is None:
                out.append("🏄 Серф: данных для уверенной оценки недостаточно; проверить спот перед выездом.")
                continue
            if good_min <= last_wave <= good_max and (last_wind is None or last_wind <= wind_max):
                out.append("🏄 Серф: есть рабочие окна по волне; проверить конкретный спот.")
                continue
            out.append("🏄 Серф: отдельные окна возможны, но решение — по фактической волне и ветру на споте.")
            continue
        out.append(line)
    return "\n".join(out)


def _apply_format_v2_test_polish(v2_text: str) -> str:
    if not _env_any("FORMAT_V2_POLISH", "FORMAT_V2_TEST_POLISH"):
        return v2_text
    text = _translate_shore_notes(v2_text)
    text = _downgrade_sup_lines(text)
    text = _polish_surf_lines(text)
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r"🌙\s+🌙", "🌙", text)
    return text


def _score_line(v2_text: str) -> str:
    return next((x.strip() for x in str(v2_text or "").splitlines() if "VayboMeter" in x and "/10" in x), "")


def _score_value(v2_text: str) -> float | None:
    return _num(r"VayboMeter\s+завтра:\s*(\d+(?:[\.,]\d+)?)\s*/\s*10", v2_text)


def _score_reasons(v2_text: str) -> str:
    line = _score_line(v2_text)
    m = re.search(r";\s*(.*?)\.?$", line)
    return (m.group(1) if m else "").lower()


def _cyprus_score_conclusion(score: float) -> str:
    if score >= 8.5:
        return "День комфортный для обычных дел и прогулок; у моря всё равно стоит сверить ветер утром."
    if score >= 7:
        return "День в целом хороший: основные дела можно планировать свободно, а прогулки у моря — с поправкой на ветер и жару."
    if score >= 5.5:
        return "День рабочий, но с нагрузкой: лучше тень/вода, короткие прогулки и гибкий план по ветру."
    return "День лучше вести в бережном режиме: минимум перегрева, больше тени, воды и запасной план."


def _cyprus_reason_conclusion(score: float, reasons: str, v2_text: str) -> str:
    low = (reasons + " " + _plain(v2_text)).lower()
    heat = any(x in low for x in ("жара", "тепло", "перегрев"))
    wind = any(x in low for x in ("порыв", "ветер"))
    mist = has_structured_visibility_alert(v2_text)
    gusts = _numbers(r"порывы\s*(?:до\s*)?(\d+(?:[\.,]\d+)?)", v2_text)
    max_gust = max(gusts) if gusts else None
    warning = _has_actual_cyprus_storm_signal(v2_text, max_gust)
    if warning:
        return "День лучше планировать гибко: держать запасной сценарий, сверять предупреждения утром и не перегружать поездки к морю."
    if heat and wind:
        return "День лучше проживать в два окна: основные дела утром/вечером, днём — вода и тень; у моря выбирай защищённые места."
    if heat:
        return "Главная нагрузка — жара: активность лучше утром или после заката, днём — вода, тень и минимум открытого солнца."
    if wind:
        return "Для прогулок у моря выбирай закрытые бухты и защищённые променады; лёгкие вещи лучше закрепить, а ветер сверить утром."
    if mist:
        return "Утром возможна дымка: для дороги и прогулок лучше заложить запас времени и сверить видимость по факту."
    return _cyprus_score_conclusion(score)


def _replace_conclusion(v2_text: str, conclusion: str) -> str:
    lines = str(v2_text or "").splitlines()
    out: list[str] = []
    in_conclusion = False
    replaced = False
    for line in lines:
        if line.strip().startswith("📌 <b>Вывод"):
            in_conclusion = True
            replaced = False
            out.append(line)
            continue
        if in_conclusion and not replaced and line.strip():
            out.append(conclusion)
            replaced = True
            in_conclusion = False
            continue
        out.append(line)
    return "\n".join(out)


def _apply_score_conclusion(v2_text: str) -> str:
    if not _env_any("FORMAT_V2_SCORE_CONCLUSION", "FORMAT_V2_TEST_CONCLUSION"):
        return v2_text
    score = _score_value(v2_text)
    if score is None:
        return v2_text
    if _env_on("FORMAT_V2_REASON_CONCLUSION"):
        return _replace_conclusion(v2_text, _cyprus_reason_conclusion(score, _score_reasons(v2_text), v2_text))
    return _replace_conclusion(v2_text, _cyprus_score_conclusion(score))


def _score_reason_mentions_heat(reasons: str) -> bool:
    """True when the score line already names heat as a reason."""
    return bool(re.search(r"жар\w*|пекл\w*|зно\w*", str(reasons or ""), flags=re.I))


def _score_reason_mentions_wind(reasons: str) -> bool:
    """True when the score line already names wind/gusts as a reason."""
    return bool(re.search(r"порыв\w*|ветер|ветр\w*", str(reasons or ""), flags=re.I))


def _cyprus_main_nuance(v2_text: str) -> str:
    reasons = _score_reasons(v2_text)
    low = (reasons + " " + _plain(v2_text)).lower()
    heat = any(x in low for x in ("жара", "тепло"))
    wind = any(x in low for x in ("порыв", "ветер"))
    mist = has_structured_visibility_alert(v2_text)
    rain = _has_cyprus_precip_risk(v2_text)
    troodos = "тродос" in low or "горы" in low
    visibility_condition = _cyprus_visibility_condition(v2_text)
    if visibility_condition in {"dense_fog", "fog"}:
        return "⚠️ Главный нюанс: до рассеивания тумана осторожнее на дорогах и развязках."
    if visibility_condition in {"mist", "reduced_visibility", "mixed_visibility"}:
        return "⚠️ Главный нюанс: утром видимость снижена — на дорогах и развязках нужна дополнительная дистанция."
    if rain and wind and (heat or troodos):
        return "⚠️ Главный нюанс: осадки возможны локально, особенно в горах; у моря жарко и порывисто."
    if rain:
        return "⚠️ Главный нюанс: осадки возможны локально; по маршруту лучше оставить запасной вариант."
    if mist:
        return "⚠️ Главный нюанс: локальная утренняя дымка/туман."
    # Heat and coastal gusts are routinely already named by the score reasons.
    # The nuance keeps only the hazard the score has not stated, and is dropped
    # entirely when it would merely rephrase the score.
    heat_is_new = heat and not _score_reason_mentions_heat(reasons)
    wind_is_new = wind and not _score_reason_mentions_wind(reasons)
    if heat_is_new and wind_is_new:
        return "⚠️ Главный нюанс: жара в Никосии и порывы у моря."
    if heat_is_new:
        return "⚠️ Главный нюанс: жара во внутренних районах острова."
    if wind_is_new:
        # Signal only: the concrete wind guidance stays in the plan line.
        return "⚠️ Главный нюанс: порывы у моря."
    return ""


def _insert_main_nuance(v2_text: str) -> str:
    if not _env_on("FORMAT_V2_MAIN_NUANCE"):
        return v2_text
    if "⚠️ Главный нюанс:" in v2_text or "⚠️ Нюанс:" in v2_text:
        return v2_text
    return _inject_after_anchor(v2_text, _cyprus_main_nuance(v2_text), ("✨ VayboMeter завтра:", "✨ VayboMeter:"))


def _date_from_text(v2_text: str) -> str:
    m = re.search(r"\((\d{2}\.\d{2}\.\d{4})\)", str(v2_text or ""))
    return m.group(1) if m else ""


def _iso_date_from_text(v2_text: str) -> str:
    value = _date_from_text(v2_text)
    if not value:
        return ""
    try:
        return dt.datetime.strptime(value, "%d.%m.%Y").date().isoformat()
    except ValueError:
        return ""


def _without_editorial_voice(v2_text: str) -> list[str]:
    return [
        line
        for line in str(v2_text or "").splitlines()
        if not line.strip().startswith(("💬 По ощущениям", "💬 Настрой", "💬 По-человечески"))
    ]


def _cyprus_voice_conditions(v2_text: str, mode: str = "morning") -> dict[str, object]:
    c = _cyprus_conditions(v2_text)
    plain = _plain(v2_text)
    text = plain.lower()
    evidence_text = text
    aqi = c.get("aqi")
    if not str(mode or "").startswith("morn"):
        evidence_lines: list[str] = []
        for raw_line in str(v2_text or "").splitlines():
            line = _plain(raw_line).strip()
            low = line.lower()
            if line.startswith(("🏭", "🏙")) and (
                re.search(r"\bвоздух\s+завтра(?:\s+утром)?\b", low)
                or re.search(r"\bпрогноз\w*\s+(?:воздуха|aqi)\b", low)
                or re.search(r"\baqi\s+завтра(?:\s+утром)?\b", low)
            ):
                evidence_lines.append(line)
            elif line.startswith("🌫 Видимость:") and re.search(r"пылев\w*\s+дымк|сух\w*\s+пыл", low):
                evidence_lines.append(line)
        evidence_text = _plain("\n".join(evidence_lines)).lower()
        aqi = _cyprus_evening_forecast_aqi(v2_text)

    text_without_pollen = re.sub(r"\bпыльца\w*", "", evidence_text, flags=re.I)
    pm25 = _num(r"(?:PM₂\.₅|PM2\.?5)\s*(\d+(?:[\.,]\d+)?)", evidence_text)
    pm10 = _num(r"(?:PM₁₀|PM10)\s*(\d+(?:[\.,]\d+)?)", evidence_text)
    explicit_poor_air = "воздух неидеален" in evidence_text
    explicit_dust = bool(
        re.search(
            r"пыль\s+в\s+воздухе|пылев\w+\s+дымк\w*|задымлен\w*|\bдым\s*/\s*смог\b|(?<![а-яё])дым(?!к|[а-яё])|(?<![а-яё])смог(?![а-яё])",
            text_without_pollen,
            flags=re.I,
        )
    )
    return {
        "max_temp": c.get("warm_t"),
        "uv": c.get("uv"),
        "uv_high": isinstance(c.get("uv"), (int, float)) and c["uv"] >= 6,
        "wind": isinstance(c.get("wind"), (int, float)) and c["wind"] >= 6,
        "gust": c.get("gust"),
        "aqi": aqi,
        "pm25": pm25,
        "pm10": pm10,
        "poor_air": explicit_poor_air or explicit_dust,
        "rain": _has_cyprus_precip_risk(v2_text),
        "local": any(x in text for x in ("локаль", "местами", "тродос", "горы")),
        "troodos": "тродос" in text,
    }


def _insert_editorial_after(lines: list[str], line_to_add: str, prefixes: tuple[str, ...]) -> str:
    if not line_to_add:
        return "\n".join(lines)
    insert_at = None
    for idx, line in enumerate(lines):
        if line.strip().startswith(prefixes):
            insert_at = idx
    if insert_at is None:
        insert_at = 0
    out = list(lines)
    out.insert(insert_at + 1, line_to_add)
    return "\n".join(out)


def _apply_editorial_voice(v2_text: str, mode: str) -> str:
    lines = _without_editorial_voice(v2_text)
    date_s = _date_from_text(v2_text)
    conditions = _cyprus_voice_conditions(v2_text, mode)
    if mode.startswith("morn"):
        line = build_morning_human_line("Кипр", date_s or "today", conditions)
        return _insert_editorial_after(lines, line, ("⚠️ Главный нюанс:", "✨ VayboMeter:"))
    line = build_evening_human_line("Кипр", date_s or "tomorrow", conditions)
    return _insert_editorial_after(
        lines,
        line,
        ("⚠️ Нюанс:", "⚠️ Главный нюанс:", "🧭 Главное завтра:", "✨ VayboMeter завтра:", "✨ VayboMeter:"),
    )


def finalize_hashtags_at_end(text: str, canonical_hashtags: str | None = None) -> str:
    """Move hashtag-only lines to one deterministic final non-empty line."""
    body: list[str] = []
    hashtag_tokens: list[str] = []
    hashtag_line_re = re.compile(r"^#[^\s#<>]+(?:\s+#[^\s#<>]+)*$")
    for raw_line in str(text or "").splitlines():
        stripped = raw_line.strip()
        if stripped and hashtag_line_re.fullmatch(stripped):
            hashtag_tokens.extend(stripped.split())
            continue
        body.append(raw_line.rstrip())

    if canonical_hashtags:
        hashtag_tokens = canonical_hashtags.split()
    deduplicated = list(dict.fromkeys(hashtag_tokens))
    while body and not body[-1].strip():
        body.pop()
    if deduplicated:
        body.append(" ".join(deduplicated))
    return "\n".join(body)


def _cy_image_caption(
    mode: str,
    target_date: str,
    *,
    test_label: bool,
    current_date: dt.date | None = None,
) -> str:
    target = dt.date.fromisoformat(str(target_date)[:10])
    today = current_date or dt.datetime.now(ZoneInfo(TZ_STR)).date()
    prefix = "🧪 " if test_label else ""
    if mode.startswith("morn") and target == today:
        body = "Визуальный вайб сегодняшнего дня на Кипре 🌊"
    elif mode.startswith("even") and target == today + dt.timedelta(days=1):
        body = "Визуальный вайб погоды на Кипре завтра 🌊"
    elif target == today:
        body = "Визуальный вайб погоды на Кипре сегодня 🌊"
    elif target == today + dt.timedelta(days=1):
        body = "Визуальный вайб погоды на Кипре завтра 🌊"
    else:
        body = f"Визуальный вайб погоды на Кипре на {target.strftime('%d.%m.%Y')} 🌊"
    return prefix + body


def _apply_confidence_polish(v2_text: str) -> str:
    if not _env_on("FORMAT_V2_CONFIDENCE_POLISH"):
        return v2_text
    return str(v2_text or "").replace(
        "✅ Давление/общий фон: можно использовать для планирования дня.",
        "✅ Общий фон: стабильный — день можно планировать заранее.",
    )


_MOON_PHASE_PREFIXES = ("🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘", "🌙")


def _is_illumination_astro_line(line: str) -> bool:
    return line.startswith("✨") and ("%" in line or "освещ" in line.lower())


def _is_general_background_astro_line(line: str) -> bool:
    return line.startswith(("✅", "⚠️", "➿")) and "общий фон" in line.lower()


def _is_voc_astro_line(line: str) -> bool:
    return line.startswith(("⚫️", "⚫")) and "voc" in line.lower()


def _is_plus_astro_line(line: str) -> bool:
    return line.startswith("💚 В плюсе")


def _is_astro_candidate_line(line: str) -> bool:
    return (
        line.startswith(("🌅", "🌇"))
        or line.startswith(_MOON_PHASE_PREFIXES)
        or _is_illumination_astro_line(line)
        or _is_general_background_astro_line(line)
        or _is_plus_astro_line(line)
        or _is_voc_astro_line(line)
    )


def _append_first_astro(out: list[str], lines: list[str], predicate) -> None:
    for line in lines:
        if predicate(line) and line not in out:
            out.append(line)
            return


def _ordered_astro_details(candidates: list[str], fallback: list[str]) -> list[str]:
    lines = list(candidates)
    if not any(line.startswith(_MOON_PHASE_PREFIXES) for line in lines):
        lines.extend(x for x in fallback if x not in lines)

    out: list[str] = []
    _append_first_astro(out, lines, lambda s: s.startswith("🌅"))
    _append_first_astro(out, lines, lambda s: s.startswith("🌇"))
    _append_first_astro(out, lines, lambda s: s.startswith(_MOON_PHASE_PREFIXES))
    _append_first_astro(out, lines, _is_illumination_astro_line)
    _append_first_astro(out, lines, _is_general_background_astro_line)
    _append_first_astro(out, lines, _is_plus_astro_line)
    _append_first_astro(out, lines, _is_voc_astro_line)
    return out[:7]


def _astro_fallback_candidates(v2_text: str) -> list[str]:
    m = re.search(r"\((\d{2})\.(\d{2})\.(\d{4})\)", str(v2_text or ""))
    if not m:
        return []
    try:
        date_local = pendulum.parse(f"{m.group(3)}-{m.group(2)}-{m.group(1)}")
        from post_common import build_astro_section

        old_offset = os.environ.get("ASTRO_OFFSET")
        os.environ["ASTRO_OFFSET"] = "0"
        try:
            section = build_astro_section(date_local=date_local, tz_local=TZ_STR)
        finally:
            if old_offset is None:
                os.environ.pop("ASTRO_OFFSET", None)
            else:
                os.environ["ASTRO_OFFSET"] = old_offset
    except Exception:
        return []

    out: list[str] = []
    for raw in str(section or "").splitlines():
        line = raw.strip()
        if line and _is_astro_candidate_line(line):
            out.append(line)
    return out


def _apply_astro_cleanup(v2_text: str) -> str:
    if not _env_on("FORMAT_V2_ASTRO_CLEANUP"):
        return v2_text
    lines = str(v2_text or "").splitlines()
    out: list[str] = []
    in_astro = False
    candidates: list[str] = []
    fallback = _astro_fallback_candidates(v2_text)

    def flush_astro() -> None:
        nonlocal candidates
        if candidates:
            out.extend(_ordered_astro_details(candidates, fallback))
        candidates = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("☀️ <b>Солнце", "🌙 <b>Астроритм")):
            if in_astro:
                flush_astro()
            in_astro = True
            out.append(line)
            continue
        if in_astro and stripped.startswith(("📌 <b>Вывод", "✅ <b>Рекомендации", "✅ План", "🎯", "#")):
            flush_astro()
            in_astro = False
        if in_astro and stripped:
            if stripped.endswith("для первых") or stripped.endswith("и вдо…"):
                continue
            if "…" in stripped and len(stripped) > 80:
                continue
            if stripped.startswith("✅ В целом:"):
                line = "✅ Астроритм: благоприятный."
                stripped = line
            if not _is_astro_candidate_line(stripped):
                continue
            if stripped not in candidates:
                candidates.append(stripped)
            continue
        out.append(line)
    if in_astro:
        flush_astro()
    return "\n".join(out)


def _apply_cyprus_sensor_cleanup(v2_text: str) -> str:
    out: list[str] = []
    for line in str(v2_text or "").splitlines():
        stripped = line.strip()
        low = stripped.lower()
        if "частный датчик" in low or "радиационный фон" in low:
            continue
        if stripped.startswith("🧪") or "safecast" in low:
            critical = bool(re.search(r"critical|alert|опасн|критич|🔴", stripped, flags=re.I))
            if not critical:
                continue
            body = re.sub(r"^🧪\s*", "", stripped).strip()
            body = re.sub(r"^Safecast(?:\s*CY)?\s*:?\s*", "", body, flags=re.I).strip()
            out.append("🧪 Safecast CY: " + body)
            continue
        out.append(line)
    return "\n".join(out)


def _fmt_cy_temp(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _cy_date_from_text(text: str) -> str:
    m = re.search(r"\((\d{2}\.\d{2}\.\d{4})\)", str(text or ""))
    return m.group(1) if m else ""


def _cy_sea_temp_bounds(date_s: str) -> tuple[float, float]:
    try:
        month = dt.datetime.strptime(date_s, "%d.%m.%Y").month
    except Exception:
        month = 7
    if 6 <= month <= 10:
        return 22.0, 34.0
    return 15.0, 29.0


def _valid_cy_sea_temp(value: float, date_s: str) -> bool:
    low, high = _cy_sea_temp_bounds(date_s)
    return low <= value <= high


def _cy_morning_sea_line_from_source(source_text: str) -> str:
    waters: list[float] = []
    date_s = _cy_date_from_text(source_text)

    for raw in str(source_text or "").splitlines():
        s = _plain(raw).replace("\u00a0", " ").strip()
        low = s.lower()
        if "закат" in low or "рассвет" in low or re.search(r"\b(?:aqi|pm₂|pm2|pm₁|pm10|гпа|hpa|давл|ветер|уф)\b", low, flags=re.I):
            continue
        if not re.search(r"🌊|\bвода\b|\bsea\b", s, flags=re.I):
            continue
        if "🌊" in s:
            tail = s.split("🌊", 1)[1]
            m = re.search(r"([+-]?\d+(?:[\.,]\d+)?)", tail)
            if m:
                try:
                    value = float(m.group(1).replace(",", "."))
                    if _valid_cy_sea_temp(value, date_s):
                        waters.append(value)
                except Exception:
                    pass
        for pattern in (
            r"(?:\bвода\b|\bsea\b)[^\d+-]{0,12}([+-]?\d+(?:[\.,]\d+)?)\s*°?\s*C?",
            r"([+-]?\d+(?:[\.,]\d+)?)\s*°?\s*C?\s*(?:\bвода\b|\bsea\b)",
        ):
            m = re.search(pattern, s, flags=re.I)
            if not m:
                continue
            try:
                value = float(m.group(1).replace(",", "."))
                if _valid_cy_sea_temp(value, date_s):
                    waters.append(value)
            except Exception:
                pass
            break
    if len(waters) >= 2:
        avg = sum(waters) / len(waters)
        return f"🌊 Море: средняя вода {_fmt_cy_temp(avg)}°C."
    if len(waters) == 1:
        return f"🌊 Море: вода {_fmt_cy_temp(waters[0])}°C; волна спокойная."
    return ""


def _replace_cy_morning_sea_line(v2_text: str, sea_line: str) -> str:
    if not sea_line:
        return v2_text
    lines = [line for line in str(v2_text or "").splitlines() if not line.strip().startswith("🌊 Море:")]
    return _insert_before_anchor(
        "\n".join(lines),
        sea_line,
        ("🌍 Сейсмика 24ч:", "🧲", "☀️ <b>Солнце", "🌇", "✅ План:", "#"),
    )


def _cy_morning_astro_block_from_source(source_text: str, fallback_text: str) -> list[str]:
    raw_lines = [line.strip() for line in str(source_text or "").splitlines() if line.strip()]
    candidates = [line for line in raw_lines if _is_astro_candidate_line(line)]
    if not candidates:
        candidates = [line for line in str(fallback_text or "").splitlines() if _is_astro_candidate_line(line.strip())]
    details = _ordered_astro_details(candidates, [])
    has_moon = any(line.startswith(_MOON_PHASE_PREFIXES) or "луна" in line.lower() or "полнолуние" in line.lower() for line in details)
    if not has_moon:
        return []
    return ["☀️ <b>Солнце, Луна и ритм дня</b>", *details[:5]]


def _replace_cy_morning_astro_block(v2_text: str, astro_block: list[str]) -> str:
    if not astro_block:
        return v2_text
    lines = str(v2_text or "").splitlines()
    out: list[str] = []
    in_astro = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("☀️ <b>Солнце"):
            in_astro = True
            continue
        if in_astro and stripped.startswith(("✅ План:", "😷", "#")):
            in_astro = False
        if in_astro:
            continue
        if _is_astro_candidate_line(stripped):
            continue
        out.append(line)
    return _insert_before_anchor("\n".join(out), "\n".join(astro_block), ("✅ План:", "😷", "#"))


def _apply_cyprus_morning_raw_context(v2_text: str, raw_msg: str, legacy_text: str, mode: str) -> str:
    if not mode.startswith("morn"):
        return v2_text
    out = v2_text
    sea_line = _cy_morning_sea_line_from_source(raw_msg) or _cy_morning_sea_line_from_source(legacy_text)
    if sea_line:
        out = _replace_cy_morning_sea_line(out, sea_line)
    astro_block = _cy_morning_astro_block_from_source(raw_msg, legacy_text)
    if astro_block:
        out = _replace_cy_morning_astro_block(out, astro_block)
    return out


def _apply_compact(v2_text: str) -> str:
    if not _env_on("FORMAT_V2_COMPACT"):
        return v2_text
    lines = str(v2_text or "").splitlines()
    out: list[str] = []
    in_main = False
    main_text_seen = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("🧭 <b>Главный сценарий"):
            in_main = True
            main_text_seen = 0
            out.append(line)
            continue
        if in_main and stripped.startswith(("✨ VayboMeter", "🎯")):
            in_main = False
        if in_main and stripped and not stripped.startswith("🧭"):
            main_text_seen += 1
            if main_text_seen > 1:
                continue
        if stripped.startswith("🧜‍♂️ Отлично: SUP"):
            continue
        out.append(line)
    return "\n".join(out)


def _inject_after_anchor(v2_text: str, line_to_add: str, anchors: tuple[str, ...]) -> str:
    if not line_to_add:
        return v2_text
    lines = str(v2_text or "").splitlines()
    out: list[str] = []
    inserted = False
    for line in lines:
        out.append(line)
        if not inserted and line.strip().startswith(anchors):
            out.append(line_to_add)
            inserted = True
    return "\n".join(out)


def _insert_before_anchor(v2_text: str, line_to_add: str, anchors: tuple[str, ...]) -> str:
    if not line_to_add:
        return v2_text
    lines = str(v2_text or "").splitlines()
    out: list[str] = []
    inserted = False
    for line in lines:
        if not inserted and line.strip().startswith(anchors):
            if out and out[-1].strip():
                out.append("")
            out.append(line_to_add)
            out.append("")
            inserted = True
        out.append(line)
    if not inserted:
        out.append(line_to_add)
    return "\n".join(out)


def _replace_plan(v2_text: str, new_plan: str) -> str:
    if not new_plan:
        return v2_text
    lines = str(v2_text or "").splitlines()
    out: list[str] = []
    replaced = False
    for line in lines:
        if not replaced and line.strip().startswith("✅"):
            out.append(new_plan)
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(new_plan)
    return "\n".join(out)


def _inject_morning_feels(v2_text: str, mode: str) -> str:
    if not (mode.startswith("morn") and _env_on("MORNING_FEELS_LIKE")):
        return v2_text
    feels = _cyprus_feels_line(v2_text)
    if not feels:
        return v2_text
    lines = str(v2_text or "").splitlines()
    out: list[str] = []
    inserted = False
    for line in lines:
        out.append(line)
        if not inserted and line.strip().startswith("💨"):
            out.append(feels)
            inserted = True
    if not inserted:
        for i, line in enumerate(out):
            if line.strip().startswith("🌡"):
                out.insert(i + 1, feels)
                inserted = True
                break
    return "\n".join(out)


def _inject_morning_best_window(v2_text: str, mode: str) -> str:
    if not (mode.startswith("morn") and _env_on("MORNING_BEST_WINDOW")):
        return v2_text
    window = _cyprus_best_window_line(v2_text)
    if not window:
        return v2_text
    if "🌡 Ощущается:" in v2_text:
        return _inject_after_anchor(v2_text, window, ("🌡 Ощущается:",))
    return _inject_after_anchor(v2_text, window, ("💨", "🌡"))


def _inject_morning_score(v2_text: str, mode: str) -> str:
    if not (mode.startswith("morn") and _env_on("MORNING_VAYBOMETER_SCORE")):
        return v2_text
    score = _cyprus_score_line(v2_text)
    if "🕒 Лучшее окно:" in v2_text:
        return _inject_after_anchor(v2_text, score, ("🕒 Лучшее окно:",))
    if "🌡 Ощущается:" in v2_text:
        return _inject_after_anchor(v2_text, score, ("🌡 Ощущается:",))
    return _inject_after_anchor(v2_text, score, ("💨", "🌡"))


def _replace_existing_score_line(v2_text: str, new_score: str, prefixes: tuple[str, ...]) -> tuple[str, bool]:
    """Replace an existing score line in place; report whether one was found.

    The score is a single verdict about the day, so the recomputed line replaces the
    one the factual layer already produced instead of being published beside it.
    """
    if not new_score:
        return v2_text, False
    lines = str(v2_text or "").splitlines()
    out: list[str] = []
    replaced = False
    for line in lines:
        if not replaced and line.strip().startswith(prefixes):
            out.append(new_score)
            replaced = True
            continue
        out.append(line)
    return "\n".join(out), replaced


def _inject_evening_score(v2_text: str, mode: str) -> str:
    if mode.startswith("morn") or not _env_on("EVENING_VAYBOMETER_SCORE"):
        return v2_text
    score = _cyprus_evening_score_line(v2_text)
    replaced_text, replaced = _replace_existing_score_line(
        v2_text, score, ("✨ VayboMeter завтра:", "✨ VayboMeter:")
    )
    if replaced:
        return replaced_text
    return _insert_before_anchor(v2_text, score, ("🎯 <b>Уверенность", "🎯"))


def _inject_morning_smart_plan(v2_text: str, mode: str) -> str:
    if not (mode.startswith("morn") and _env_on("MORNING_SMART_PLAN")):
        return v2_text
    return _replace_plan(v2_text, _cyprus_smart_plan_line(v2_text))


def resolve_chat_id(args_chat: str, to_test: bool) -> int:
    chat = (args_chat or "").strip()
    if chat:
        return int(chat)
    if to_test:
        chat = os.getenv("CHANNEL_ID_TEST", "").strip()
        if not chat:
            raise SystemExit("--to-test задан, но CHANNEL_ID_TEST не определён")
        return int(chat)
    raise SystemExit("Safe runner refuses production send. Use --to-test or --chat-id explicitly.")


def _cy_safe_image_min_bytes() -> int:
    raw = os.getenv("CY_IMG_MIN_BYTES", "12000").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"CY_IMG_MIN_BYTES должен быть числом, получено: {raw!r}") from exc
    if value < 1:
        raise SystemExit("CY_IMG_MIN_BYTES должен быть положительным")
    return value


def _cy_accept_lru_recent_visual_candidate(metadata: dict[str, object], reason: str) -> bool:
    if reason == "recent_scene_family":
        return metadata.get("scene_selection_mode") == "least_recently_used"
    if reason == "recent_composition":
        return metadata.get("composition_selection_mode") == "least_recently_used"
    return False


def _cy_safe_image_output_path(style_name: str, *, nonce: str = "") -> Path:
    safe_style = re.sub(r"[^a-zA-Z0-9_-]+", "_", style_name).strip("_") or "cyprus_safe"
    safe_nonce = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(nonce or "")).strip("_")
    if safe_nonce:
        safe_style = f"{safe_style}_{safe_nonce}"
    output_dir = Path(os.getenv("CY_SAFE_IMAGE_DIR", ".cache/cy_safe_images"))
    return output_dir / f"{safe_style}.jpg"


def _cy_utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _cy_receipt_key(target_date: str, post_type: str) -> str:
    safe_date = re.sub(r"[^0-9-]+", "_", str(target_date or "undated")) or "undated"
    safe_type = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(post_type or "post")) or "post"
    return f"{safe_date}-{safe_type}.json"


def _cy_extract_receipt_date(text: str, fallback: str) -> str:
    value = str(text or "")
    match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", value)
    if match:
        return match.group(0)
    match = re.search(r"\b(\d{2})[./-](\d{2})[./-](\d{4})\b", value)
    if match:
        return f"{match.group(3)}-{match.group(2)}-{match.group(1)}"
    return fallback


def _cy_image_receipt_path(target_date: str, post_type: str) -> Path:
    return Path(os.getenv("CY_IMAGE_DELIVERY_DIR", str(_CY_IMAGE_DELIVERY_DIR))) / _cy_receipt_key(target_date, post_type)


def _cy_text_receipt_path(target_date: str, post_type: str) -> Path:
    return Path(os.getenv("CY_TEXT_DELIVERY_DIR", str(_CY_TEXT_DELIVERY_DIR))) / _cy_receipt_key(target_date, post_type)


def _cy_write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), "utf-8")
    tmp.replace(path)


def _cy_is_production_image_send(*, send_image_to_chat: bool, image_chat: int | None) -> bool:
    production_chat = (os.getenv("CHANNEL_ID") or "").strip()
    return bool(send_image_to_chat and production_chat and image_chat is not None and str(image_chat) == production_chat)


def _cy_is_production_text_send(chat_id: int | None) -> bool:
    production_chat = (os.getenv("CHANNEL_ID") or "").strip()
    return bool(production_chat and chat_id is not None and str(chat_id) == production_chat)


_CY_LOCAL_RENDERER_NAME = "local_informative_cover"

# Fixed vocabulary; diagnostics must not invent new stage names.
_CY_ERROR_STAGES = (
    "none",
    "decision",
    "provider_generation",
    "provider_validation",
    "dedup",
    "fallback_render",
    "telegram_send",
    "history",
    "receipt",
    "orchestration",
)

# Unambiguous lifecycle states for the network → local fallback transition.
_CY_FALLBACK_NOT_USED = "network_fallback_not_used"
_CY_FALLBACK_ELIGIBLE = "network_exhausted_local_eligible"
_CY_FALLBACK_LOCAL_SELECTED = "local_selected"
_CY_FALLBACK_LOCAL_FAILED = "local_failed"


def _cy_local_decision_id(local_metadata: dict) -> str:
    """Deterministic diagnostics-only id for a local informative-cover decision.

    Derived from the local identity that is already final, so it never enters the
    local cache payload and cannot change the renderer's existing cache key.
    """
    parts = (
        str(local_metadata.get("cache_key", "")),
        _CY_LOCAL_RENDERER_NAME,
        str(local_metadata.get("selected_scene", "")),
        str(local_metadata.get("composition", "")),
        str(local_metadata.get("visual_archetype", "")),
        str(local_metadata.get("target_date", "")),
        str(local_metadata.get("post_type", "")),
    )
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _cy_fallback_state(
    result: str,
    *,
    local_generated: bool,
    local_failed: bool = False,
    is_local: bool = False,
) -> str:
    """Derive the fallback lifecycle state from the outcome actually reached."""
    if is_local or local_generated:
        return _CY_FALLBACK_LOCAL_SELECTED
    if local_failed:
        return _CY_FALLBACK_LOCAL_FAILED
    if str(result) in {"failed", "failed_non_fatal", "failed_after_duplicates"}:
        return _CY_FALLBACK_ELIGIBLE
    return _CY_FALLBACK_NOT_USED


def _cy_normalize_error_stage(
    error_stage: str,
    result: str,
    error: BaseException | None,
) -> str:
    """Clamp the reported stage to the fixed vocabulary."""
    stage = str(error_stage or "").strip()
    if stage in _CY_ERROR_STAGES:
        return stage
    if error is None and str(result) in {"sent", "generated"}:
        return "none"
    if error is None:
        return "none"
    return "orchestration"


def _cy_write_image_diagnostics(
    *,
    mode: str,
    target_date: str,
    result: str,
    error: BaseException | None = None,
    prompt_metadata: dict | None = None,
    attempts: list[dict] | None = None,
    telegram_attempts: list[dict] | None = None,
    write_history_path: Path | None = None,
    reference_history_paths: tuple[Path, ...] = (),
    history_count_before: int | None = None,
    history_count_after: int | None = None,
    generation_summary: dict | None = None,
    fallback_state: str = "",
    error_stage: str = "",
) -> Path:
    safe_date = re.sub(r"[^0-9-]+", "_", target_date or "undated")
    out_dir = Path(os.getenv("CY_IMAGE_DIAGNOSTICS_DIR", str(_CY_IMAGE_DIAGNOSTICS_DIR))) / f"{safe_date}-{mode}"
    payload = {
        "image_result": result,
        "target_date": target_date,
        "post_type": mode,
        "sent_at_utc": _cy_utc_now(),
        "prompt_metadata": prompt_metadata or {},
        "selected_scene_attempts": attempts or [],
        "telegram_send_attempts": telegram_attempts or [],
        "write_history_path": str(write_history_path) if write_history_path else "",
        "reference_history_paths": [str(path) for path in reference_history_paths],
        "history_count_before": history_count_before,
        "history_count_after": history_count_after,
    }
    payload.update(generation_summary or {})
    # Canonical decision identity and the actual lifecycle outcome, surfaced as
    # unambiguous top-level fields rather than a separate diagnostics architecture.
    meta = prompt_metadata or {}
    summary = generation_summary or {}
    backend = str(summary.get("selected_backend") or "")
    is_local = backend == _CY_LOCAL_RENDERER_NAME
    payload["decision_id"] = meta.get("decision_id", "")
    payload["routing_inputs"] = meta.get("routing_inputs", {})
    payload["cooldown_inputs"] = meta.get("cooldown_inputs", {})
    # A local cover is never reported as if a network provider had produced it.
    payload["actual_provider"] = "" if is_local else backend
    payload["actual_renderer"] = _CY_LOCAL_RENDERER_NAME if is_local else ""
    payload["fallback_state"] = fallback_state or _cy_fallback_state(
        result,
        local_generated=bool(summary.get("local_fallback_generated")),
        local_failed=bool(summary.get("local_render_failed")),
        is_local=is_local,
    )
    payload["error_stage"] = _cy_normalize_error_stage(error_stage, result, error)
    if error is not None:
        payload["error"] = {
            "type": type(error).__name__,
            "message": _redact_secret_text(re.sub(r"\s+", " ", str(error)))[:500],
        }
    _cy_write_json_atomic(out_dir / "image_result.json", payload)
    return out_dir / "image_result.json"


async def _cy_send_photo_with_retry(
    bot: Bot,
    *,
    chat_id: int,
    image_path: Path,
    caption: str,
) -> tuple[object, list[dict]]:
    delays = [2, 5, 10]
    attempts: list[dict] = []
    for index in range(1, 5):
        try:
            with image_path.open("rb") as photo:
                message = await bot.send_photo(chat_id=chat_id, photo=photo, caption=caption)
            attempts.append({"attempt": index, "result": "sent"})
            return message, attempts
        except _TELEGRAM_RETRY_EXCEPTIONS as exc:
            wait_seconds = getattr(exc, "retry_after", None)
            if wait_seconds is None:
                wait_seconds = delays[min(index - 1, len(delays) - 1)]
            attempts.append(
                {
                    "attempt": index,
                    "result": "retry",
                    "error_type": type(exc).__name__,
                    "message": _redact_secret_text(re.sub(r"\s+", " ", str(exc)))[:300],
                    "sleep_seconds": wait_seconds,
                }
            )
            if index >= 4:
                raise
            await asyncio.sleep(float(wait_seconds))
    raise RuntimeError("unreachable Telegram send retry state")


def _cy_image_backend_name(generated: object) -> str:
    backend = str(getattr(generated, "backend", "") or "").strip().lower()
    if backend == "horde":
        return "stable_horde"
    if backend in {"pollinations", "stable_horde", "custom", "cache"}:
        return backend
    return "custom"


def _cy_image_backend_attempts(generated: object) -> list[dict]:
    attempts = getattr(generated, "backend_attempts", None)
    if isinstance(attempts, list):
        return [item for item in attempts if isinstance(item, dict)]
    return []


def _cy_image_actual_backend_call_count(outcome: object) -> int:
    try:
        value = int(getattr(outcome, "actual_backend_call_count", 0) or 0)
    except Exception:
        value = 0
    return max(0, value)


def _cy_image_provider_failure_count(backend_attempts: list[dict]) -> int:
    failures = 0
    last_index = len(backend_attempts) - 1
    for index, item in enumerate(backend_attempts):
        status = str(item.get("result") or "").strip().lower()
        if status in {"failed", "exception", "invalid"}:
            failures += 1
        elif not status and index < last_index:
            failures += 1
    return failures


def _cy_image_result_path(generated: object) -> Path | None:
    if generated is None:
        return None
    value = getattr(generated, "path", generated)
    if not value:
        return None
    return Path(os.fspath(value))


def _cy_file_size(path: Path | None) -> int | None:
    try:
        return path.stat().st_size if path is not None and path.exists() else None
    except Exception:
        return None


def _cy_remove_broken_image(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


async def _build_safe_test_image(
    final_text: str,
    mode: str,
    *,
    generate_image: bool,
    send_image_to_test: bool,
    send_image_to_chat: bool,
    image_chat_id: int | None,
    image_only_recovery: bool = False,
) -> dict[str, object]:
    if send_image_to_test and send_image_to_chat:
        raise SystemExit(
            "--send-image-to-test и --send-image-to-chat нельзя использовать вместе"
        )

    if not generate_image:
        if send_image_to_test or send_image_to_chat:
            raise SystemExit(
                "Отправка изображения требует --generate-image"
            )
        return {"result": "skipped", "message_ids": []}

    image_chat: int | None = None
    image_caption_is_test = False
    if send_image_to_test:
        test_chat = os.getenv("CHANNEL_ID_TEST", "").strip()
        if not test_chat:
            raise SystemExit(
                "--send-image-to-test задан, но CHANNEL_ID_TEST не определён"
            )
        try:
            image_chat = int(test_chat)
        except ValueError as exc:
            raise SystemExit("CHANNEL_ID_TEST должен быть числом") from exc
        image_caption_is_test = True
    elif send_image_to_chat:
        if image_chat_id is None:
            raise SystemExit(
                "--send-image-to-chat требует явно разрешённый --chat-id или --to-test"
            )
        image_chat = image_chat_id

    production_image_send = _cy_is_production_image_send(
        send_image_to_chat=send_image_to_chat,
        image_chat=image_chat,
    )

    if image_chat is not None:
        if not TOKEN:
            raise SystemExit(
                "Для отправки изображения TELEGRAM_TOKEN должен быть определён"
            )

    last_metadata: dict | None = None
    last_decision: object | None = None
    # Tracks which lifecycle stage is currently executing, so an escaping exception is
    # attributed to the stage that actually raised it instead of a blanket fallback.
    lifecycle_stage = "orchestration"
    attempts: list[dict] = []
    telegram_attempts: list[dict] = []
    target_date_for_diag = "undated"
    write_history_path_for_diag: Path | None = None
    reference_history_paths_for_diag: tuple[Path, ...] = ()
    before_history_count: int | None = None
    after_history_count: int | None = None
    visibility_metadata: dict[str, object] | None = None
    try:
        from cyprus_visual_dedup import (
            cyprus_visual_history_path,
            ensure_pillow_for_visual_dedup,
            evaluate_cyprus_visual_candidate,
            load_cyprus_visual_history,
            load_cyprus_visual_reference_history,
            record_cyprus_visual_publication,
            sha256_file,
        )
        from cyprus_image_recovery import (
            LOCAL_INFORMATIVE_COVER_VERSION,
            load_provider_health,
            mark_provider_duplicate,
            provider_health_exclusions,
            provider_health_path,
            record_provider_attempts,
            render_local_informative_cover,
            write_provider_health,
        )
        from cyprus_visual_policy import (
            CYPRUS_MACRO_LOCAL_COVER,
            blocked_macro_families,
        )
        import image_prompt_cy_scene as visual_decision_module
        from image_prompt_cy_scene import build_visual_context_cy
        from weather import load_cyprus_visibility_diagnostics
        import world_en.imagegen as imagegen_module

        visibility_target_date = _iso_date_from_text(final_text)
        if visibility_target_date:
            visibility_metadata = load_cyprus_visibility_diagnostics(
                visibility_target_date,
                mode,
            )

        # Parse the finalized post exactly once. Every candidate decision, the local
        # fallback renderer and all diagnostics reuse this context, so the whole
        # lifecycle reports one consistent provenance.
        lifecycle_stage = "decision"
        canonical_visual_context = build_visual_context_cy(
            final_text,
            post_type=mode,
            visibility_metadata=visibility_metadata,
        )
        lifecycle_stage = "orchestration"

        history_namespace = "test" if send_image_to_test else "prod" if send_image_to_chat else "test"
        write_history_path = cyprus_visual_history_path(history_namespace)
        reference_history_paths = (
            (cyprus_visual_history_path("prod"), cyprus_visual_history_path("test"))
            if history_namespace == "test"
            else (cyprus_visual_history_path("prod"),)
        )
        write_history_path_for_diag = write_history_path
        reference_history_paths_for_diag = reference_history_paths
        ensure_pillow_for_visual_dedup()
        restored_history = load_cyprus_visual_reference_history(reference_history_paths)
        before_history_count = len(load_cyprus_visual_history(write_history_path))
        recent_scene_values = tuple(
            str(entry.get("selected_scene") or "").strip()
            for entry in restored_history[-3:]
            if isinstance(entry, dict) and str(entry.get("selected_scene") or "").strip()
        )
        recent_composition_values = tuple(
            str(entry.get("composition") or "").strip()
            for entry in restored_history[-5:]
            if isinstance(entry, dict) and str(entry.get("composition") or "").strip()
        )
        blocked_archetype_values: list[str] = []
        recent_archetypes = [
            str(entry.get("visual_archetype") or "").strip()
            for entry in restored_history
            if isinstance(entry, dict) and str(entry.get("visual_archetype") or "").strip()
        ]
        if recent_archetypes:
            blocked_archetype_values.append(recent_archetypes[-1])
        if "bay_panorama" in recent_archetypes[-10:]:
            blocked_archetype_values.append("bay_panorama")
        if "elevated_cliff_panorama" in recent_archetypes[-6:]:
            blocked_archetype_values.append("elevated_cliff_panorama")
        blocked_archetypes = tuple(dict.fromkeys(blocked_archetype_values))
        # Macro blockers come from the restored history. Local informative covers are
        # excluded inside the policy, so provider outages cannot flush the macro window.
        blocked_macro_family_values = blocked_macro_families(restored_history)
        if blocked_macro_family_values:
            print(
                "CY_SAFE_IMAGE_BLOCKED_MACRO_FAMILIES: "
                + ",".join(blocked_macro_family_values)
            )
        print(f"CY_SAFE_IMAGE_HISTORY_NAMESPACE: {history_namespace}")
        print(f"CY_SAFE_IMAGE_WRITE_HISTORY_PATH: {write_history_path}")
        print("CY_SAFE_IMAGE_REFERENCE_HISTORY_PATHS: " + ", ".join(map(str, reference_history_paths)))
        print(f"CY_SAFE_IMAGE_HISTORY_COUNT_BEFORE: {before_history_count}")
        logging.info(
            "Cyprus visual history count before generation: namespace=%s write_path=%s references=%s count=%s",
            history_namespace,
            write_history_path,
            reference_history_paths,
            before_history_count,
        )

        selected_candidate = None
        minimum = _cy_safe_image_min_bytes()

        variation_attempt = 0
        generation_attempt = 0
        backend_generation_calls = 0
        backend_call_limit = 10
        generation_failures = 0
        valid_candidate_count = 0
        duplicate_candidate_count = 0
        provider_failure_count = 0
        excluded_backends: set[str] = set()
        backend_duplicate_counts: dict[str, int] = {}
        seen_run_hashes: dict[tuple[str, str], dict[str, str]] = {}
        provider_call_limits = {"pollinations": 2, "stable_horde": 3, "custom": 2}
        provider_call_counts = {name: 0 for name in provider_call_limits}
        provider_health: dict | None = None
        provider_health_file = ""
        configured_backends: list[str] = []
        available_backends: list[str] = []
        unconfigured_backends: list[str] = []
        local_fallback_generated = False
        local_render_failed = False
        last_failure_stage = ""
        horde_credential_state: dict[str, object] = {}

        def _remaining_provider_calls() -> dict[str, int]:
            return {
                name: max(0, limit - provider_call_counts.get(name, 0))
                for name, limit in provider_call_limits.items()
            }

        def _network_backends_exhausted() -> bool:
            network = [name for name in configured_backends if name in provider_call_limits]
            return not network or all(
                name in excluded_backends
                or provider_call_counts.get(name, 0) >= provider_call_limits[name]
                for name in network
            )

        def _generation_summary(final_reason: str, selected_backend: str = "") -> dict:
            return {
                "valid_candidate_count": valid_candidate_count,
                "duplicate_candidate_count": duplicate_candidate_count,
                "provider_failure_count": provider_failure_count,
                "backend_call_count": backend_generation_calls,
                "backend_call_limit": backend_call_limit,
                "excluded_backends": sorted(excluded_backends),
                "configured_backends": configured_backends,
                "available_backends": [
                    name for name in available_backends if name not in excluded_backends
                ],
                "unconfigured_backends": unconfigured_backends,
                "provider_call_limits": provider_call_limits,
                "provider_call_counts": provider_call_counts,
                "provider_health_path": provider_health_file,
                "local_fallback_generated": local_fallback_generated,
                "local_render_failed": local_render_failed,
                "selected_backend": selected_backend,
                "final_reason": final_reason,
            }

        while (
            generation_attempt < 5
            and backend_generation_calls < backend_call_limit
            and variation_attempt < 40
        ):
            lifecycle_stage = "decision"
            decision = visual_decision_module.build_cyprus_visual_decision(
                final_text,
                post_type=mode,
                variation_attempt=variation_attempt,
                blocked_scenes=recent_scene_values,
                blocked_compositions=recent_composition_values,
                blocked_archetypes=blocked_archetypes,
                blocked_macro_families=blocked_macro_family_values,
                visibility_metadata=visibility_metadata,
                visual_context=canonical_visual_context,
            )
            lifecycle_stage = "orchestration"
            prompt = decision.prompt
            style_name = decision.style_name
            metadata = decision.metadata
            last_metadata = dict(metadata)
            last_decision = decision
            target_date_for_diag = str(metadata["forecast_date"])
            cache_key = metadata["cache_key"]
            if provider_health is None:
                provider_health = load_provider_health(
                    metadata["forecast_date"],
                    mode,
                    history_namespace,
                )
                provider_health_file = str(
                    provider_health_path(metadata["forecast_date"], mode, history_namespace)
                )
                health_excluded = provider_health_exclusions(provider_health)
                excluded_backends.update(health_excluded)
                availability_fn = getattr(imagegen_module, "configured_image_backends", None)
                if callable(availability_fn):
                    availability = availability_fn(excluded_backends=set(excluded_backends))
                    configured_backends = list(availability.get("configured_backends") or [])
                    available_backends = list(availability.get("available_backends") or [])
                    unconfigured_backends = list(availability.get("unconfigured_backends") or [])
                else:
                    configured_backends = ["pollinations", "stable_horde"]
                    available_backends = [
                        name for name in configured_backends if name not in excluded_backends
                    ]
                    unconfigured_backends = ["custom"]
                print(f"CY_SAFE_IMAGE_CONFIGURED_BACKENDS: {','.join(configured_backends) or 'none'}")
                print(f"CY_SAFE_IMAGE_AVAILABLE_BACKENDS: {','.join(available_backends) or 'none'}")
                print(f"CY_SAFE_IMAGE_UNCONFIGURED_BACKENDS: {','.join(unconfigured_backends) or 'none'}")
                print(f"CY_SAFE_IMAGE_PROVIDER_HEALTH_PATH: {provider_health_file}")
                if health_excluded:
                    print(
                        "CY_SAFE_IMAGE_PROVIDER_HEALTH_EXCLUDED: "
                        + ",".join(sorted(health_excluded))
                    )
            if production_image_send:
                if is_valid_cy_image_receipt(metadata["forecast_date"], mode):
                    receipt_path = _cy_image_receipt_path(metadata["forecast_date"], mode)
                    print(f"CY_SAFE_IMAGE_RECEIPT_EXISTS: {receipt_path}")
                    _cy_write_image_diagnostics(
                        mode=mode,
                        target_date=metadata["forecast_date"],
                        result="skipped_receipt_exists",
                        prompt_metadata=metadata,
                        attempts=attempts,
                        telegram_attempts=telegram_attempts,
                        write_history_path=write_history_path,
                        reference_history_paths=reference_history_paths,
                        history_count_before=before_history_count,
                    )
                    return {"result": "skipped_receipt_exists", "message_ids": []}
                if image_only_recovery:
                    if not has_valid_cy_text_delivery(metadata["forecast_date"], mode):
                        text_receipt = _cy_text_receipt_path(metadata["forecast_date"], mode)
                        print(f"CY_SAFE_IMAGE_RECOVERY_SKIP_NO_TEXT_RECEIPT: {text_receipt}")
                        _cy_write_image_diagnostics(
                            mode=mode,
                            target_date=metadata["forecast_date"],
                            result="skipped_no_text_receipt",
                            prompt_metadata=metadata,
                            attempts=attempts,
                            telegram_attempts=telegram_attempts,
                            write_history_path=write_history_path,
                            reference_history_paths=reference_history_paths,
                            history_count_before=before_history_count,
                        )
                        return {"result": "skipped_no_text_receipt", "message_ids": []}
            print(f"\nCY_SAFE_IMAGE_ATTEMPT: {generation_attempt + 1}/5")
            print("CY_SAFE_IMAGE_PROMPT_BEGIN")
            print(prompt)
            print("CY_SAFE_IMAGE_PROMPT_END")
            print(f"CY_SAFE_IMAGE_STYLE: {style_name}")
            print(f"CY_SAFE_IMAGE_CACHE_KEY: {cache_key}")
            logging.info("Cyprus visual cache key: %s", cache_key)

            force_regenerate = bool(image_only_recovery)
            nonce = ""
            if force_regenerate:
                nonce = "|".join(
                    [
                        os.getenv("GITHUB_RUN_ID", "local"),
                        os.getenv("GITHUB_RUN_ATTEMPT", "0"),
                        str(os.getpid()),
                        str(generation_attempt),
                        dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S%f"),
                    ]
                )
            requested_path = _cy_safe_image_output_path(style_name, nonce=nonce)
            requested_path.parent.mkdir(parents=True, exist_ok=True)
            cache_state = "miss" if force_regenerate else "hit" if requested_path.exists() else "miss"
            print(f"CY_SAFE_IMAGE_CACHE_STATUS: {cache_state}")
            logging.info("Cyprus visual cache hit/miss: %s", cache_state)

            generated = None
            backend = "cache" if cache_state == "hit" else "imagegen"
            image_path: Path | None = requested_path if cache_state == "hit" else None
            image_size: int | None = None
            backend_attempts: list[dict] = []
            backend_calls_before = backend_generation_calls
            provider_failures_before = provider_failure_count
            outcome_exhausted = False
            structured_outcome_returned = False
            stop_generation = False
            lifecycle_stage = "provider_generation"
            try:
                if cache_state == "hit":
                    generated = str(requested_path)
                else:
                    remaining_backend_calls = backend_call_limit - backend_generation_calls
                    outcome_fn = getattr(
                        imagegen_module,
                        "generate_astro_image_outcome_with_exclusions",
                        None,
                    )
                    if callable(outcome_fn):
                        outcome = outcome_fn(
                            prompt,
                            str(requested_path),
                            excluded_backends=set(excluded_backends),
                            max_backend_calls=remaining_backend_calls,
                            backend_call_limits=_remaining_provider_calls(),
                            horde_credential_state=horde_credential_state,
                        )
                        structured_outcome_returned = True
                        backend_attempts = _cy_image_backend_attempts(outcome)
                        reported_calls = _cy_image_actual_backend_call_count(outcome)
                        if reported_calls <= 0 and backend_attempts:
                            reported_calls = len(backend_attempts)
                        backend_generation_calls += min(remaining_backend_calls, reported_calls)
                        provider_failure_count += _cy_image_provider_failure_count(backend_attempts)
                        for item in backend_attempts:
                            item_backend = str(item.get("backend") or "").strip().lower()
                            if item_backend == "horde":
                                item_backend = "stable_horde"
                            if item_backend in provider_call_counts:
                                provider_call_counts[item_backend] += 1
                        if provider_health is not None and backend_attempts:
                            record_provider_attempts(
                                provider_health,
                                backend_attempts,
                                run_id=os.getenv("GITHUB_RUN_ID", ""),
                            )
                            write_provider_health(provider_health)
                        generated = getattr(outcome, "result", None)
                        outcome_exhausted = bool(getattr(outcome, "exhausted", False))
                        if not generated:
                            error_type = str(getattr(outcome, "error_type", "") or "ImageGenerationFailed")
                            error_message = str(
                                getattr(outcome, "error_message", "") or "image backend returned no file"
                            )
                            raise RuntimeError(f"{error_type}: {error_message}")
                    else:
                        result_fn = getattr(imagegen_module, "generate_astro_image_result_with_exclusions", None)
                        if callable(result_fn):
                            generated = result_fn(
                                prompt,
                                str(requested_path),
                                excluded_backends=set(excluded_backends),
                                max_backend_calls=remaining_backend_calls,
                                backend_call_limits=_remaining_provider_calls(),
                            )
                        else:
                            generated = imagegen_module.generate_astro_image(prompt, str(requested_path))
                        backend_attempts = _cy_image_backend_attempts(generated)
                        reported_calls = max(1, len(backend_attempts))
                        backend_generation_calls += min(remaining_backend_calls, reported_calls)
                        provider_failure_count += _cy_image_provider_failure_count(backend_attempts)
                        for item in backend_attempts:
                            item_backend = str(item.get("backend") or "").strip().lower()
                            if item_backend == "horde":
                                item_backend = "stable_horde"
                            if item_backend in provider_call_counts:
                                provider_call_counts[item_backend] += 1
                        if provider_health is not None and backend_attempts:
                            record_provider_attempts(
                                provider_health,
                                backend_attempts,
                                run_id=os.getenv("GITHUB_RUN_ID", ""),
                            )
                            write_provider_health(provider_health)
                        if not generated:
                            provider_failure_count += int(not backend_attempts)
                            raise RuntimeError("image backend returned no file")
                    backend = _cy_image_backend_name(generated)
                    image_path = _cy_image_result_path(generated)

                lifecycle_stage = "provider_validation"
                if image_path is None or not image_path.is_file():
                    raise RuntimeError(f"generated image does not exist: {image_path}")

                image_size = image_path.stat().st_size
                if image_size <= minimum:
                    raise RuntimeError(
                        f"generated image is too small: {image_size} bytes; "
                        f"must be greater than {minimum}"
                    )
            except Exception as exc:
                # Remember whether the provider itself or its output validation failed.
                last_failure_stage = lifecycle_stage
                lifecycle_stage = "orchestration"
                generation_failures += 1
                if (
                    cache_state != "hit"
                    and not structured_outcome_returned
                    and backend_generation_calls == backend_calls_before
                ):
                    backend_generation_calls += min(1, backend_call_limit - backend_generation_calls)
                    provider_failure_count += 1
                elif (
                    cache_state != "hit"
                    and (not structured_outcome_returned or backend_generation_calls > backend_calls_before)
                    and provider_failure_count == provider_failures_before
                    and backend_generation_calls > backend_calls_before
                ):
                    provider_failure_count += 1
                if outcome_exhausted and backend_generation_calls == backend_calls_before:
                    stop_generation = True
                if backend_generation_calls >= backend_call_limit:
                    stop_generation = True
                image_size = image_size if image_size is not None else _cy_file_size(image_path)
                attempts.append(
                    {
                        "attempt": generation_attempt + 1,
                        "variation_attempt": variation_attempt,
                        "selected_scene": metadata["selected_scene"],
                        "composition": metadata.get("composition", ""),
                        "scene_selection_mode": metadata.get("scene_selection_mode", ""),
                        "composition_selection_mode": metadata.get("composition_selection_mode", ""),
                        "style_name": style_name,
                        "cache_key": cache_key,
                        "cache_status": cache_state,
                        "backend": backend,
                        "backend_attempts": backend_attempts,
                        "backend_call_count": backend_generation_calls,
                        "backend_call_limit": backend_call_limit,
                        "backend_excluded": sorted(excluded_backends),
                        "error_type": exc.__class__.__name__,
                        "error": _redact_secret_text(re.sub(r"\s+", " ", str(exc)))[:300],
                        "image_path": str(image_path) if image_path else "",
                        "image_bytes": image_size,
                    }
                )
                _cy_remove_broken_image(image_path)
                logging.warning(
                    "Cyprus visual candidate generation failed: type=%s attempt=%s backend=%s bytes=%s",
                    exc.__class__.__name__,
                    variation_attempt,
                    backend,
                    image_size,
                )
                variation_attempt += 1
                if stop_generation:
                    break
                continue

            valid_candidate_count += 1
            lifecycle_stage = "dedup"
            duplicate_result = evaluate_cyprus_visual_candidate(
                image_path,
                date_value=metadata["forecast_date"],
                post_type=mode,
                selected_scene=metadata["selected_scene"],
                prompt_version=metadata["prompt_version"],
                composition=metadata.get("composition"),
                visual_archetype=metadata.get("visual_archetype"),
                reference_history_paths=reference_history_paths,
            )
            lifecycle_stage = "orchestration"
            print(
                "CY_SAFE_IMAGE_DEDUP: "
                f"{duplicate_result.reason}; sha256={duplicate_result.sha256[:12]}; "
                f"dhash={duplicate_result.perceptual_hash or 'n/a'}; "
                f"phash={duplicate_result.phash or 'n/a'}; "
                f"min_distance={duplicate_result.min_distance}; "
                f"min_phash_distance={duplicate_result.min_phash_distance}"
            )
            same_run_key = (
                duplicate_result.perceptual_hash or "",
                duplicate_result.phash or "",
            )
            provider_switch_reason = ""
            if same_run_key[0] and same_run_key[1] and same_run_key in seen_run_hashes:
                previous = seen_run_hashes[same_run_key]
                if previous.get("cache_key") != cache_key:
                    provider_switch_reason = "provider_repeated_output"
                    duplicate_reason = "provider_repeated_output"
                    if backend and backend != "cache":
                        excluded_backends.add(backend)
                    print(
                        "CY_SAFE_IMAGE_PROVIDER_REPEATED_OUTPUT: "
                        f"backend={backend}; dhash={same_run_key[0]}; phash={same_run_key[1]}"
                    )
                else:
                    duplicate_reason = duplicate_result.reason
            else:
                duplicate_reason = duplicate_result.reason
                if same_run_key[0] and same_run_key[1]:
                    seen_run_hashes[same_run_key] = {
                        "backend": backend,
                        "cache_key": cache_key,
                    }

            candidate_attempt = generation_attempt + 1
            if provider_switch_reason != "provider_repeated_output":
                generation_attempt += 1

            if duplicate_result.reason in {"exact_duplicate", "near_duplicate", "near_duplicate_phash"}:
                backend_duplicate_counts[backend] = backend_duplicate_counts.get(backend, 0) + 1
                if (
                    backend == "pollinations"
                    and backend_duplicate_counts[backend] >= 2
                    and duplicate_result.perceptual_hash
                ):
                    excluded_backends.add("pollinations")
                    provider_switch_reason = provider_switch_reason or "pollinations_repeated_perceptual_duplicate"
                if backend_duplicate_counts[backend] >= 3 and backend != "cache":
                    excluded_backends.add(backend)
                    provider_switch_reason = provider_switch_reason or "backend_three_duplicate_candidates"

            if provider_health is not None and (
                provider_switch_reason == "provider_repeated_output"
                or duplicate_result.reason in {"exact_duplicate", "near_duplicate", "near_duplicate_phash"}
            ):
                mark_provider_duplicate(
                    provider_health,
                    backend,
                    dhash=duplicate_result.perceptual_hash or "",
                    phash=duplicate_result.phash or "",
                    stuck=provider_switch_reason == "provider_repeated_output",
                    run_id=os.getenv("GITHUB_RUN_ID", ""),
                )
                write_provider_health(provider_health)

            attempts.append(
                {
                    "attempt": candidate_attempt,
                    "variation_attempt": variation_attempt,
                    "selected_scene": metadata["selected_scene"],
                    "composition": metadata.get("composition", ""),
                    "visual_archetype": metadata.get("visual_archetype", ""),
                    "scene_selection_mode": metadata.get("scene_selection_mode", ""),
                    "composition_selection_mode": metadata.get("composition_selection_mode", ""),
                    "style_name": style_name,
                    "cache_key": cache_key,
                    "cache_status": cache_state,
                    "backend": backend,
                    "backend_attempts": backend_attempts,
                    "backend_call_count": backend_generation_calls,
                    "backend_call_limit": backend_call_limit,
                    "backend_excluded": sorted(excluded_backends),
                    "provider_switch_reason": provider_switch_reason,
                    "repeated_output_hash": ":".join(same_run_key) if provider_switch_reason else "",
                    "image_path": str(image_path),
                    "image_bytes": image_size,
                    "dedup_reason": duplicate_reason,
                    "sha256": duplicate_result.sha256,
                    "perceptual_hash": duplicate_result.perceptual_hash,
                    "phash": duplicate_result.phash,
                    "min_distance": duplicate_result.min_distance,
                    "min_phash_distance": duplicate_result.min_phash_distance,
                }
            )

            candidate = (
                decision,
                image_path,
                image_size,
                duplicate_result,
                backend,
            )
            if provider_switch_reason == "provider_repeated_output":
                duplicate_candidate_count += 1
            elif duplicate_result.accepted:
                selected_candidate = candidate
                break
            elif _cy_accept_lru_recent_visual_candidate(metadata, duplicate_result.reason):
                attempts[-1]["dedup_reason"] = f"{duplicate_result.reason}_lru_allowed"
                print(f"CY_SAFE_IMAGE_DEDUP_LRU_ALLOWED: {duplicate_result.reason}")
                selected_candidate = candidate
                break
            else:
                duplicate_candidate_count += 1
            try:
                quarantine = image_path.with_suffix(image_path.suffix + f".rejected.{duplicate_reason}")
                image_path.replace(quarantine)
                attempts[-1]["quarantined_path"] = str(quarantine)
            except Exception as exc:
                logging.warning("Cyprus rejected visual quarantine failed: %s", exc)
            logging.warning(
                "Cyprus visual candidate rejected: reason=%s scene=%s attempt=%s",
                duplicate_reason,
                metadata["selected_scene"],
                variation_attempt,
            )
            variation_attempt += 1

        if selected_candidate is None:
            network_backends_exhausted = bool(
                _network_backends_exhausted() or backend_generation_calls >= backend_call_limit
            )
            valid_image_receipt = bool(
                target_date_for_diag != "undated"
                and is_valid_cy_image_receipt(target_date_for_diag, mode)
            )
            primary_fallback_allowed = bool(
                production_image_send
                and not image_only_recovery
                and last_metadata
                and not valid_image_receipt
                and network_backends_exhausted
            )
            recovery_fallback_allowed = bool(
                production_image_send
                and image_only_recovery
                and last_metadata
                and has_valid_cy_text_delivery(target_date_for_diag, mode)
                and not valid_image_receipt
                and network_backends_exhausted
            )
            fallback_allowed = primary_fallback_allowed or recovery_fallback_allowed
            if fallback_allowed:
                local_metadata = dict(last_metadata or {})
                local_metadata.update(
                    {
                        "prompt_version": LOCAL_INFORMATIVE_COVER_VERSION,
                        "selected_scene": "local_informative_cover",
                        "composition": "informative_cover",
                        "visual_archetype": "factual_weather_cover",
                        # The local cover is a renderer, not a place: it carries the
                        # technical macro and never inherits the network macro.
                        "scene_macro_family": CYPRUS_MACRO_LOCAL_COVER,
                        "scene_selection_mode": "local_fallback",
                        "composition_selection_mode": "local_fallback",
                    }
                )
                local_path = _cy_safe_image_output_path(
                    f"local_informative_cover_{target_date_for_diag}_{mode}"
                ).with_suffix(".png")
                lifecycle_stage = "fallback_render"
                try:
                    local_result = render_local_informative_cover(
                        final_text,
                        target_date=target_date_for_diag,
                        post_type=mode,
                        output_path=local_path,
                        minimum_bytes=minimum,
                        visual_context=canonical_visual_context,
                        visibility_metadata=visibility_metadata,
                    )
                except Exception as exc:
                    attempts.append(
                        {
                            "attempt": generation_attempt + 1,
                            "variation_attempt": variation_attempt,
                            "selected_scene": "local_informative_cover",
                            "composition": "informative_cover",
                            "visual_archetype": "factual_weather_cover",
                            "style_name": "local_informative_cover",
                            "cache_status": "local_renderer_failed",
                            "backend": "local_informative_cover",
                            "backend_attempts": [],
                            "backend_call_count": backend_generation_calls,
                            "backend_call_limit": backend_call_limit,
                            "backend_excluded": sorted(excluded_backends),
                            "image_path": "",
                            "image_bytes": 0,
                            "error_type": exc.__class__.__name__,
                            "error": str(exc),
                            "primary_fallback_allowed": primary_fallback_allowed,
                            "recovery_fallback_allowed": recovery_fallback_allowed,
                        }
                    )
                    last_metadata = local_metadata
                    local_render_failed = True
                    last_failure_stage = "fallback_render"
                    lifecycle_stage = "orchestration"
                    logging.error("Cyprus local informative cover failed non-fatally: %s", exc)
                else:
                    lifecycle_stage = "orchestration"
                    local_path = Path(str(local_result["path"]))
                    local_size = int(local_result["bytes"])
                    renderer_metadata = dict(local_result.get("metadata") or {})
                    local_metadata.update(renderer_metadata)
                    # The local cover carries its own identity: the network style name and
                    # network decision_id inherited from last_metadata must not survive.
                    # The renderer's cache key is already final at this point, so the local
                    # decision_id below is diagnostics-only and cannot affect it.
                    local_metadata["style_name"] = _CY_LOCAL_RENDERER_NAME
                    local_metadata["scene_macro_family"] = CYPRUS_MACRO_LOCAL_COVER
                    local_metadata["decision_id"] = _cy_local_decision_id(local_metadata)
                    local_sha256 = sha256_file(local_path)
                    existing_local_exact = next(
                        (
                            entry
                            for entry in restored_history
                            if str(entry.get("sha256") or "") == local_sha256
                            and str(entry.get("date") or "") == target_date_for_diag
                            and str(entry.get("post_type") or "") == mode
                        ),
                        None,
                    )
                    attempts.append(
                        {
                            "attempt": generation_attempt + 1,
                            "variation_attempt": variation_attempt,
                            "selected_scene": "local_informative_cover",
                            "composition": "informative_cover",
                            "visual_archetype": "factual_weather_cover",
                            "style_name": "local_informative_cover",
                            "cache_key": local_metadata["cache_key"],
                            "cache_status": "local_generated",
                            "backend": "local_informative_cover",
                            "backend_attempts": [],
                            "backend_call_count": backend_generation_calls,
                            "backend_call_limit": backend_call_limit,
                            "backend_excluded": sorted(excluded_backends),
                            "image_path": str(local_path),
                            "image_bytes": local_size,
                            "dedup_reason": "exact_duplicate" if existing_local_exact else "local_exact_sha_clear",
                            "sha256": local_sha256,
                            "local_metadata": renderer_metadata,
                            "primary_fallback_allowed": primary_fallback_allowed,
                            "recovery_fallback_allowed": recovery_fallback_allowed,
                        }
                    )
                    local_fallback_generated = True
                    last_metadata = local_metadata
                    if existing_local_exact:
                        final_reason = "skipped_duplicate_local_informative_cover"
                        _cy_write_image_diagnostics(
                            mode=mode,
                            target_date=target_date_for_diag,
                            result=final_reason,
                            prompt_metadata=local_metadata,
                            attempts=attempts,
                            telegram_attempts=telegram_attempts,
                            write_history_path=write_history_path,
                            reference_history_paths=reference_history_paths,
                            history_count_before=before_history_count,
                            generation_summary=_generation_summary(
                                final_reason,
                                "local_informative_cover",
                            ),
                        )
                        return {
                            "result": final_reason,
                            "message_ids": [],
                            "backend": "local_informative_cover",
                            "attempts": attempts,
                            **_generation_summary(final_reason, "local_informative_cover"),
                        }
                    selected_candidate = (
                        visual_decision_module.CyprusVisualDecision(
                            context=canonical_visual_context,
                            prompt="",
                            style_name="local_informative_cover",
                            metadata=local_metadata,
                            visibility_metadata=visibility_metadata,
                        ),
                        local_path,
                        local_size,
                        None,
                        "local_informative_cover",
                    )
                    print(
                        "CY_SAFE_IMAGE_LOCAL_INFORMATIVE_COVER: "
                        f"path={local_path}; bytes={local_size}; sha256={local_sha256[:12]}"
                    )

        if selected_candidate is None:
            mixed_failure = bool(
                duplicate_candidate_count
                and provider_failure_count
                and generation_failures
            )
            if mixed_failure:
                final_reason = "failed_after_duplicates"
                error = RuntimeError("backend failure prevented a distinct Cyprus image after duplicates")
                _cy_write_image_diagnostics(
                    mode=mode,
                    target_date=target_date_for_diag,
                    result=final_reason,
                    error=error,
                    prompt_metadata=last_metadata,
                    attempts=attempts,
                    telegram_attempts=telegram_attempts,
                    write_history_path=write_history_path,
                    reference_history_paths=reference_history_paths,
                    history_count_before=before_history_count,
                    generation_summary=_generation_summary(final_reason),
                    error_stage="dedup",
                )
                logging.error("CY SAFE IMAGE failed after duplicates and backend errors: %s", error)
                return {
                    "result": final_reason,
                    "message_ids": [],
                    "error_type": error.__class__.__name__,
                    "error": str(error),
                    "attempts": attempts,
                    **_generation_summary(final_reason),
                }
            if valid_candidate_count == 0 and generation_failures:
                final_reason = "failed_non_fatal"
                error = RuntimeError("no valid Cyprus image candidate generated")
                _cy_write_image_diagnostics(
                    mode=mode,
                    target_date=target_date_for_diag,
                    result=final_reason,
                    error=error,
                    prompt_metadata=last_metadata,
                    attempts=attempts,
                    telegram_attempts=telegram_attempts,
                    write_history_path=write_history_path,
                    reference_history_paths=reference_history_paths,
                    history_count_before=before_history_count,
                    generation_summary=_generation_summary(final_reason),
                    # A failed local cover is a fallback_render failure, not a provider one.
                    error_stage=(
                        "fallback_render"
                        if local_render_failed
                        else (last_failure_stage or "provider_generation")
                    ),
                )
                logging.error("CY SAFE IMAGE failed after candidate errors: %s", error)
                return {
                    "result": "failed_non_fatal",
                    "message_ids": [],
                    "error_type": error.__class__.__name__,
                    "error": str(error),
                    "attempts": attempts,
                    **_generation_summary(final_reason),
                }
            final_reason = "skipped_duplicate"
            print("CY_SAFE_IMAGE_RESULT: skipped_duplicate")
            _cy_write_image_diagnostics(
                mode=mode,
                target_date=target_date_for_diag,
                result="skipped_duplicate",
                prompt_metadata=last_metadata,
                attempts=attempts,
                telegram_attempts=telegram_attempts,
                write_history_path=write_history_path,
                reference_history_paths=reference_history_paths,
                history_count_before=before_history_count,
                generation_summary=_generation_summary(final_reason),
            )
            return {
                "result": final_reason,
                "message_ids": [],
                "attempts": attempts,
                **_generation_summary(final_reason),
            }

        # The canonical decision selected above is reused verbatim: no late rebuild and
        # no reselection, so provider input, dedup, history, receipt and diagnostics all
        # report the same identity.
        selected_decision, image_path, image_size, duplicate_result, selected_backend = selected_candidate
        prompt = selected_decision.prompt
        style_name = selected_decision.style_name
        metadata = selected_decision.metadata

        print(f"CY_SAFE_IMAGE_PATH: {image_path.resolve()}")
        print(f"CY_SAFE_IMAGE_BYTES: {image_size}")

        sent_message_ids: list[int] = []
        if image_chat is not None:
            if selected_backend != "local_informative_cover":
                duplicate_result = evaluate_cyprus_visual_candidate(
                    image_path,
                    date_value=metadata["forecast_date"],
                    post_type=mode,
                    selected_scene=metadata["selected_scene"],
                    prompt_version=metadata["prompt_version"],
                    composition=metadata.get("composition"),
                    visual_archetype=metadata.get("visual_archetype"),
                    reference_history_paths=reference_history_paths,
                )
                if (
                    not duplicate_result.accepted
                    and not _cy_accept_lru_recent_visual_candidate(metadata, duplicate_result.reason)
                ):
                    print(f"CY_SAFE_IMAGE_RESULT: skipped_duplicate_before_send ({duplicate_result.reason})")
                    _cy_write_image_diagnostics(
                        mode=mode,
                        target_date=metadata["forecast_date"],
                        result="skipped_duplicate_before_send",
                        prompt_metadata=metadata,
                        attempts=attempts,
                        telegram_attempts=telegram_attempts,
                        write_history_path=write_history_path,
                        reference_history_paths=reference_history_paths,
                        history_count_before=before_history_count,
                        generation_summary=_generation_summary(
                            "skipped_duplicate_before_send",
                            selected_backend,
                        ),
                    )
                    return {
                        "result": "skipped_duplicate_before_send",
                        "message_ids": [],
                        "backend": selected_backend,
                        **_generation_summary("skipped_duplicate_before_send", selected_backend),
                    }
            if production_image_send and is_valid_cy_image_receipt(metadata["forecast_date"], mode):
                receipt_path = _cy_image_receipt_path(metadata["forecast_date"], mode)
                print(f"CY_SAFE_IMAGE_RECEIPT_APPEARED_DURING_GENERATION: {receipt_path}")
                _cy_write_image_diagnostics(
                    mode=mode,
                    target_date=metadata["forecast_date"],
                    result="skipped_receipt_appeared_during_generation",
                    prompt_metadata=metadata,
                    attempts=attempts,
                    telegram_attempts=telegram_attempts,
                    write_history_path=write_history_path,
                    reference_history_paths=reference_history_paths,
                    history_count_before=before_history_count,
                    generation_summary=_generation_summary(
                        "skipped_receipt_appeared_during_generation",
                        selected_backend,
                    ),
                )
                return {
                    "result": "skipped_receipt_appeared_during_generation",
                    "message_ids": [],
                    "backend": selected_backend,
                    **_generation_summary(
                        "skipped_receipt_appeared_during_generation",
                        selected_backend,
                    ),
                }
            image_bot = Bot(token=TOKEN)
            image_caption = _cy_image_caption(
                mode,
                metadata["forecast_date"],
                test_label=image_caption_is_test,
            )
            lifecycle_stage = "telegram_send"
            sent_message, telegram_attempts = await _cy_send_photo_with_retry(
                image_bot,
                chat_id=image_chat,
                image_path=image_path,
                caption=image_caption,
            )
            message_id = getattr(sent_message, "message_id", None)
            if isinstance(message_id, int):
                sent_message_ids.append(message_id)
            lifecycle_stage = "history"
            history_entry = record_cyprus_visual_publication(
                date_value=metadata["forecast_date"],
                post_type=mode,
                image_path=image_path,
                selected_scene=metadata["selected_scene"],
                prompt_version=metadata["prompt_version"],
                cache_key=metadata["cache_key"],
                style_name=style_name,
                composition=metadata.get("composition"),
                visual_archetype=metadata.get("visual_archetype"),
                history_path=write_history_path,
            )
            after_history_count = len(load_cyprus_visual_history(write_history_path))
            lifecycle_stage = "orchestration"
            print(f"CY_SAFE_IMAGE_HISTORY_COUNT_AFTER: {after_history_count}")
            logging.info(
                "Cyprus visual history count after publication: namespace=%s path=%s count=%s",
                history_namespace,
                write_history_path,
                after_history_count,
            )
            message_id = getattr(sent_message, "message_id", None)
            if production_image_send and isinstance(message_id, int) and message_id > 0:
                receipt = {
                    "target_date": metadata["forecast_date"],
                    "post_type": mode,
                    "chat_type": "production",
                    "telegram_message_id": message_id,
                    "sha256": history_entry.get("sha256"),
                    "perceptual_hash": history_entry.get("perceptual_hash"),
                    "phash": history_entry.get("phash"),
                    "selected_scene": metadata["selected_scene"],
                    "composition": metadata.get("composition", ""),
                    "visual_archetype": metadata.get("visual_archetype", ""),
                    # Additive only; no other receipt field changes meaning.
                    "scene_macro_family": metadata.get("scene_macro_family", ""),
                    "style_name": style_name,
                    "cache_key": metadata["cache_key"],
                    "backend": selected_backend,
                    "run_id": os.getenv("GITHUB_RUN_ID", ""),
                    "run_attempt": os.getenv("GITHUB_RUN_ATTEMPT", ""),
                    "sent_at_utc": _cy_utc_now(),
                }
                lifecycle_stage = "receipt"
                receipt_path = _cy_image_receipt_path(metadata["forecast_date"], mode)
                _cy_write_json_atomic(receipt_path, receipt)
                lifecycle_stage = "orchestration"
                print(f"CY_SAFE_IMAGE_RECEIPT_WRITTEN: {receipt_path}")
            elif production_image_send:
                print("CY_SAFE_IMAGE_RECEIPT_NOT_WRITTEN: missing valid Telegram message_id")
            _cy_write_image_diagnostics(
                mode=mode,
                target_date=metadata["forecast_date"],
                result="sent",
                prompt_metadata=metadata,
                attempts=attempts,
                telegram_attempts=telegram_attempts,
                write_history_path=write_history_path,
                reference_history_paths=reference_history_paths,
                history_count_before=before_history_count,
                history_count_after=after_history_count,
                generation_summary=_generation_summary("sent", selected_backend),
            )
            logging.info("CY SAFE IMAGE sent before text to chat=%s", image_chat)
            return {
                "result": "sent",
                "message_ids": sent_message_ids,
                "path": str(image_path),
                "bytes": image_size,
                "style_name": style_name,
                "cache_key": metadata.get("cache_key", ""),
                "backend": selected_backend,
                "attempts": attempts,
                "metadata": metadata,
                **_generation_summary("sent", selected_backend),
            }
        _cy_write_image_diagnostics(
            mode=mode,
            target_date=metadata["forecast_date"],
            result="generated",
            prompt_metadata=metadata,
            attempts=attempts,
            telegram_attempts=telegram_attempts,
            write_history_path=write_history_path,
            reference_history_paths=reference_history_paths,
            history_count_before=before_history_count,
            generation_summary=_generation_summary("generated", selected_backend),
        )
        return {
            "result": "generated",
            "message_ids": [],
            "path": str(image_path),
            "bytes": image_size,
            "style_name": style_name,
            "cache_key": metadata.get("cache_key", ""),
            "backend": selected_backend,
            "attempts": attempts,
            "metadata": metadata,
            **_generation_summary("generated", selected_backend),
        }
    except Exception as exc:
        _cy_write_image_diagnostics(
            mode=mode,
            target_date=target_date_for_diag,
            result="failed",
            error=exc,
            prompt_metadata=last_metadata,
            attempts=attempts,
            telegram_attempts=telegram_attempts,
            write_history_path=write_history_path_for_diag,
            reference_history_paths=reference_history_paths_for_diag,
            history_count_before=before_history_count,
            history_count_after=after_history_count,
            # Attribute the failure to the stage that was actually executing.
            error_stage=lifecycle_stage,
        )
        logging.exception(
            "CY SAFE IMAGE failed; existing text safe-test flow will continue: %s",
            exc,
        )
        return {
            "result": "failed_non_fatal",
            "message_ids": [],
            "error_type": exc.__class__.__name__,
            "error": _redact_secret_text(str(exc)),
        }


def _is_transient_telegram_error(exc: Exception) -> bool:
    name = exc.__class__.__name__
    if name in {"TimedOut", "NetworkError", "RetryAfter", "ServerError"}:
        return True
    return isinstance(exc, (ConnectionError, TimeoutError, OSError))


async def _send_telegram_message_with_retry(bot: Bot, **kwargs) -> object:
    delays = [2.0, 5.0, 10.0]
    for attempt in range(1, len(delays) + 2):
        try:
            return await bot.send_message(**kwargs)
        except Exception as exc:
            if not _is_transient_telegram_error(exc) or attempt > len(delays):
                raise
            delay = getattr(exc, "retry_after", None)
            try:
                delay_seconds = float(delay) if delay is not None else delays[attempt - 1]
            except (TypeError, ValueError):
                delay_seconds = delays[attempt - 1]
            logging.warning(
                "Telegram text send transient failure: type=%s attempt=%d/%d retry_in=%.1fs",
                exc.__class__.__name__,
                attempt,
                len(delays) + 1,
                delay_seconds,
            )
            await asyncio.sleep(delay_seconds)
    raise RuntimeError("unreachable Telegram retry state")


async def _send_telegram_text_chunks(
    bot: Bot,
    *,
    chat_id: int,
    chunks: list[str],
    add_test_label: bool,
    partial_message_ids: list[int] | None = None,
) -> list[int]:
    message_ids: list[int] = []
    for idx, chunk in enumerate(chunks, start=1):
        if add_test_label:
            prefix = f"<b>Test safe post {idx}/{len(chunks)}</b>\n" if len(chunks) > 1 else "<b>Test safe post</b>\n"
            text = prefix + chunk
        else:
            text = chunk
        message = await _send_telegram_message_with_retry(
            bot,
            chat_id=chat_id,
            text=text,
            parse_mode=constants.ParseMode.HTML,
            disable_web_page_preview=True,
        )
        message_id = getattr(message, "message_id", None)
        if isinstance(message_id, int):
            message_ids.append(message_id)
            if partial_message_ids is not None:
                partial_message_ids.append(message_id)
    return message_ids


class _TodayPatch:
    def __init__(self, base_date: pendulum.DateTime):
        self.base_date = base_date
        self._orig_today = None
        self._orig_now = None

    def __enter__(self):
        self._orig_today = pendulum.today
        self._orig_now = pendulum.now

        def _fake(dt: pendulum.DateTime, tz_arg=None):
            return dt.in_tz(tz_arg) if tz_arg else dt

        pendulum.today = lambda tz_arg=None: _fake(self.base_date, tz_arg)  # type: ignore[assignment]
        pendulum.now = lambda tz_arg=None: _fake(self.base_date, tz_arg)    # type: ignore[assignment]
        logging.info("Дата зафиксирована как %s (%s)", self.base_date.to_datetime_string(), self.base_date.timezone_name)
        return self

    def __exit__(self, *args):
        if self._orig_today:
            pendulum.today = self._orig_today  # type: ignore[assignment]
        if self._orig_now:
            pendulum.now = self._orig_now      # type: ignore[assignment]
        return False


async def main() -> None:
    global _CY_MORNING_ACTIVE, _CY_MORNING_FINAL_TEXT, _CY_MORNING_TARGET_DATE, _CY_MORNING_PARTIAL_TEXT_MESSAGE_IDS
    parser = argparse.ArgumentParser(description="Safe post builder for Cyprus VayboMeter")
    parser.add_argument("--mode", choices=["morning", "evening"], default=os.getenv("POST_MODE", "evening"))
    parser.add_argument("--date", default=os.getenv("WORK_DATE", ""))
    parser.add_argument("--for-tomorrow", action="store_true")
    parser.add_argument("--to-test", action="store_true")
    parser.add_argument("--chat-id", default="")
    parser.add_argument("--format-v2", action="store_true", help="Build scenario-style FORMAT_V2 text after legacy sanitizing.")
    parser.add_argument("--send", action="store_true", help="Actually send to CHANNEL_ID_TEST / --chat-id. Omit for dry-run.")
    parser.add_argument("--generate-image", action="store_true", help="Generate a Cyprus safe-test image after final text is built.")
    parser.add_argument("--send-image-to-test", action="store_true", help="Send the generated image only to CHANNEL_ID_TEST.")
    parser.add_argument("--send-image-to-chat", action="store_true", help="Send the generated image to the same explicitly resolved chat as the text post.")
    parser.add_argument("--image-only-recovery", action="store_true", help="Recovery mode: send only a missing production image when text receipt exists.")
    parser.add_argument("--no-test-label", action="store_true", help="Do not prepend the 'Test safe post' label when sending.")
    args = parser.parse_args()

    if args.send_image_to_test and args.send_image_to_chat:
        raise SystemExit(
            "--send-image-to-test и --send-image-to-chat нельзя использовать вместе"
        )
    if args.send_image_to_chat and not args.generate_image:
        raise SystemExit("--send-image-to-chat требует --generate-image")
    if args.send_image_to_chat and not args.send and not args.image_only_recovery:
        raise SystemExit("--send-image-to-chat требует --send")
    if args.image_only_recovery:
        if not args.generate_image or not args.send_image_to_chat:
            raise SystemExit("--image-only-recovery требует --generate-image и --send-image-to-chat")
        if args.send_image_to_test or args.to_test:
            raise SystemExit("--image-only-recovery разрешён только для production chat")

    mode = (args.mode or "evening").strip().lower()
    os.environ["POST_MODE"] = mode
    _CY_MORNING_ACTIVE = mode == "morning"
    _CY_MORNING_PHASE_LOG.clear()
    _CY_MORNING_FINAL_TEXT = ""
    _CY_MORNING_PARTIAL_TEXT_MESSAGE_IDS = []
    use_format_v2 = bool(args.format_v2 or _env_on("FORMAT_V2"))
    os.environ["FORMAT_V2"] = "1" if use_format_v2 else "0"

    tz = pendulum.timezone(TZ_STR)
    base_date = pendulum.parse(args.date).in_tz(tz) if args.date else pendulum.now(tz)
    if args.for_tomorrow:
        base_date = base_date.add(days=1)
    _CY_MORNING_TARGET_DATE = base_date.to_date_string()
    chat_type = _cy_morning_chat_type(args)
    _cy_morning_phase(
        "build_started",
        target_date=_CY_MORNING_TARGET_DATE,
        chat_type=chat_type,
        format_v2=use_format_v2,
        send=args.send,
        image_requested=args.generate_image,
    )

    with _TodayPatch(base_date):
        raw_msg = build_message(
            region_name="Кипр",
            sea_label=SEA_LABEL,
            sea_cities=SEA_CITIES_ORDERED,
            other_label=OTHER_LABEL,
            other_cities=OTHER_CITIES_ALL,
            tz=TZ_STR,
            mode=mode,
        )

    legacy_result = sanitize_post_text(raw_msg)
    final_result = legacy_result
    final_label = "SAFE MESSAGE"

    if use_format_v2:
        from format_v2 import build_format_v2
        v2_raw = build_format_v2("Кипр", mode, legacy_result.text)
        v2_raw = _inject_morning_feels(v2_raw, mode)
        v2_raw = _inject_morning_best_window(v2_raw, mode)
        v2_raw = _inject_morning_score(v2_raw, mode)
        v2_raw = _inject_evening_score(v2_raw, mode)
        v2_raw = _apply_format_v2_test_polish(v2_raw)
        v2_raw = _apply_confidence_polish(v2_raw)
        v2_raw = _insert_main_nuance(v2_raw)
        v2_raw = _apply_astro_cleanup(v2_raw)
        v2_raw = _apply_cyprus_morning_raw_context(v2_raw, raw_msg, legacy_result.text, mode)
        v2_raw = _apply_cyprus_sensor_cleanup(v2_raw)
        v2_raw = _apply_score_conclusion(v2_raw)
        v2_raw = _inject_morning_smart_plan(v2_raw, mode)
        # Editorial voice is applied exactly once, after every factual transformation
        # and before compaction/sanitizing, so it reads the final factual text and
        # cannot be reshaped by later factual passes. The helper strips any existing
        # voice line first, so re-applying it never duplicates the 💬 line.
        v2_raw = _apply_editorial_voice(v2_raw, mode)
        v2_raw = _apply_compact(v2_raw)
        final_result = sanitize_post_text(v2_raw)
        final_label = "FORMAT_V2 MESSAGE"
        print("\n===== FORMAT_V2 RAW BEGIN =====\n")
        print(v2_raw)
        print("\n===== FORMAT_V2 RAW END =====\n")
        print("\n===== FORMAT_V2 SAFETY SUMMARY =====\n")
        print(validation_summary(final_result))

    final_result.text = finalize_hashtags_at_end(
        final_result.text,
        canonical_hashtags=_CY_CANONICAL_DAILY_HASHTAGS if use_format_v2 else None,
    )
    chunks = split_telegram_text(final_result.text)
    _CY_MORNING_FINAL_TEXT = final_result.text
    _cy_morning_phase(
        "text_ready",
        target_date=_CY_MORNING_TARGET_DATE,
        chat_type=chat_type,
        final_text_length=len(final_result.text),
        chunk_count=len(chunks),
    )

    print("\n===== RAW MESSAGE BEGIN =====\n")
    print(raw_msg)
    print("\n===== RAW MESSAGE END =====\n")
    print("\n===== LEGACY SAFETY SUMMARY =====\n")
    print(validation_summary(legacy_result))
    print(f"\n===== {final_label} BEGIN =====\n")
    print(final_result.text)
    print(f"\n===== {final_label} END =====\n")

    resolved_text_chat_id: int | None = None
    if args.send_image_to_chat:
        resolved_text_chat_id = resolve_chat_id(args.chat_id, args.to_test)
    if args.image_only_recovery and not _cy_is_production_text_send(resolved_text_chat_id):
        raise SystemExit("--image-only-recovery разрешён только для CHANNEL_ID production")

    if args.generate_image:
        _cy_morning_phase(
            "image_started",
            target_date=_CY_MORNING_TARGET_DATE,
            chat_type=chat_type,
        )
    image_result = await _build_safe_test_image(
        final_result.text,
        mode,
        generate_image=args.generate_image,
        send_image_to_test=args.send_image_to_test,
        send_image_to_chat=args.send_image_to_chat,
        image_chat_id=resolved_text_chat_id,
        image_only_recovery=args.image_only_recovery,
    )
    image_status = str(image_result.get("result") or "unknown")
    image_phase = cy_morning_image_phase_for_result(image_status)
    _cy_morning_phase(
        image_phase,
        target_date=_CY_MORNING_TARGET_DATE,
        chat_type=chat_type,
        image_result=image_status,
        telegram_message_ids=image_result.get("message_ids"),
        image_error_type=image_result.get("error_type"),
    )

    if args.image_only_recovery:
        logging.info("CY IMAGE RECOVERY: text send skipped by design")
        return

    if not args.send:
        logging.info("SAFE DRY-RUN: отправка пропущена, format_v2=%s, chunks=%d", use_format_v2, len(chunks))
        _cy_morning_phase(
            "completed",
            target_date=_CY_MORNING_TARGET_DATE,
            chat_type=chat_type,
            sent=False,
            chunk_count=len(chunks),
            final_text_length=len(final_result.text),
            image_result=image_result.get("result"),
        )
        return

    if not TOKEN:
        raise SystemExit("TELEGRAM_TOKEN не задан")
    chat_id = (
        resolved_text_chat_id
        if resolved_text_chat_id is not None
        else resolve_chat_id(args.chat_id, args.to_test)
    )


    bot = Bot(token=TOKEN)
    _cy_morning_phase(
        "text_send_started",
        target_date=_CY_MORNING_TARGET_DATE,
        chat_type=chat_type,
        chunk_count=len(chunks),
        final_text_length=len(final_result.text),
    )
    try:
        sent_message_ids = await _send_telegram_text_chunks(
            bot,
            chat_id=chat_id,
            chunks=chunks,
            add_test_label=not args.no_test_label,
            partial_message_ids=_CY_MORNING_PARTIAL_TEXT_MESSAGE_IDS,
        )
    except Exception as exc:
        _cy_morning_phase(
            "text_send_failed",
            target_date=_CY_MORNING_TARGET_DATE,
            chat_type=chat_type,
            chunk_count=len(chunks),
            telegram_message_ids=list(_CY_MORNING_PARTIAL_TEXT_MESSAGE_IDS),
            error_type=exc.__class__.__name__,
        )
        raise
    _cy_morning_phase(
        "text_sent",
        target_date=_CY_MORNING_TARGET_DATE,
        chat_type=chat_type,
        chunk_count=len(chunks),
        telegram_message_ids=sent_message_ids,
    )
    receipt_path = ""
    if mode == "morning" and chat_type == "production":
        receipt = cy_morning_maybe_write_delivery_receipt(
            target_date=_CY_MORNING_TARGET_DATE,
            chat_type=chat_type,
            telegram_message_ids=sent_message_ids,
            text_chunk_count=len(chunks),
            sent=True,
            event_schedule=os.getenv("GITHUB_EVENT_SCHEDULE", ""),
        )
        if receipt is not None:
            receipt_path = str(receipt)
            _cy_morning_phase(
                "delivery_receipt_written",
                target_date=_CY_MORNING_TARGET_DATE,
                chat_type=chat_type,
                receipt_path=receipt_path,
                chunk_count=len(chunks),
                telegram_message_ids=sent_message_ids,
            )
        else:
            _cy_morning_phase(
                "delivery_receipt_not_written",
                target_date=_CY_MORNING_TARGET_DATE,
                chat_type=chat_type,
                reason="missing_telegram_message_ids",
                chunk_count=len(chunks),
                telegram_message_ids=sent_message_ids,
            )
    _cy_morning_phase(
        "completed",
        target_date=_CY_MORNING_TARGET_DATE,
        chat_type=chat_type,
        sent=True,
        chunk_count=len(chunks),
        final_text_length=len(final_result.text),
        image_result=image_result.get("result"),
        receipt_path=receipt_path,
    )
    if _cy_is_production_text_send(chat_id) and len(sent_message_ids) >= len(chunks):
        target_date = _cy_extract_receipt_date(final_result.text, base_date.to_date_string())
        receipt = {
            "target_date": target_date,
            "post_type": mode,
            "chat_type": "production",
            "telegram_message_ids": sent_message_ids,
            "text_chunk_count": len(chunks),
            "run_id": os.getenv("GITHUB_RUN_ID", ""),
            "run_attempt": os.getenv("GITHUB_RUN_ATTEMPT", ""),
            "sent_at_utc": _cy_utc_now(),
        }
        receipt_path = _cy_text_receipt_path(target_date, mode)
        _cy_write_json_atomic(receipt_path, receipt)
        print(f"CY_TEXT_RECEIPT_WRITTEN: {receipt_path}")
    elif _cy_is_production_text_send(chat_id):
        print("CY_TEXT_RECEIPT_NOT_WRITTEN: missing valid Telegram message ids")
    logging.info("SAFE TEST sent: chat=%s chunks=%d format_v2=%s", chat_id, len(chunks), use_format_v2)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except BaseException as exc:
        _write_cy_morning_diagnostics(exc)
        raise
