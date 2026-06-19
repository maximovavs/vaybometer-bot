#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Manual fixture runner for the isolated Cyprus visual prompt/image pipeline."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from image_prompt_cy_scene import build_cyprus_scene_prompt


_DEFAULT_CAPTIONS = {
    "morning": "🧪 Визуальный вайб сегодняшнего утра на Кипре 🌊",
    "evening": "🧪 Визуальный вайб сегодняшнего вечера на Кипре 🌊",
}


def _bool_value(raw: str) -> bool:
    value = str(raw).strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def _minimum_image_bytes() -> int:
    raw = os.getenv("CY_IMG_MIN_BYTES", "12000").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"CY_IMG_MIN_BYTES must be an integer, got {raw!r}") from exc
    if value < 1:
        raise SystemExit("CY_IMG_MIN_BYTES must be positive")
    return value


def _output_path(style_name: str) -> Path:
    output_dir = Path(os.getenv("CY_FIXTURE_OUTPUT_DIR", "cy_fixture_images"))
    safe_style = re.sub(r"[^a-zA-Z0-9_-]+", "_", style_name).strip("_") or "cyprus_fixture"
    return output_dir / f"{safe_style}.jpg"


def _generate_image(prompt: str, style_name: str) -> tuple[Path, int]:
    from world_en.imagegen import generate_astro_image

    requested_path = _output_path(style_name)
    requested_path.parent.mkdir(parents=True, exist_ok=True)
    generated = generate_astro_image(prompt, str(requested_path))
    if not generated:
        raise SystemExit("Cyprus fixture image generation returned no file")

    image_path = Path(generated)
    if not image_path.is_file():
        raise SystemExit(f"Cyprus fixture image does not exist: {image_path}")

    image_size = image_path.stat().st_size
    minimum = _minimum_image_bytes()
    if image_size <= minimum:
        raise SystemExit(
            f"Cyprus fixture image is too small: {image_size} bytes; "
            f"must be greater than {minimum}"
        )
    return image_path, image_size


def _resolve_test_chat_id(explicit_chat_id: str) -> str:
    chat_id = explicit_chat_id.strip() or os.getenv("CHANNEL_ID_TEST", "").strip()
    if not chat_id:
        raise SystemExit(
            "--send-image-to-test true requires --chat-id or CHANNEL_ID_TEST"
        )
    return chat_id


async def _send_test_photo(image_path: Path, chat_id: str, caption: str) -> None:
    from telegram import Bot

    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    if not token:
        raise SystemExit("--send-image-to-test true requires TELEGRAM_TOKEN")

    bot = Bot(token=token)
    with image_path.open("rb") as photo:
        await bot.send_photo(chat_id=chat_id, photo=photo, caption=caption)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a Cyprus fixture prompt and optionally generate/send its image."
    )
    parser.add_argument("--message-file", type=Path, required=True)
    parser.add_argument(
        "--post-type",
        choices=("morning", "evening"),
        default="evening",
    )
    parser.add_argument(
        "--generate-image",
        type=_bool_value,
        default=False,
        metavar="true|false",
    )
    parser.add_argument(
        "--send-image-to-test",
        type=_bool_value,
        default=False,
        metavar="true|false",
    )
    parser.add_argument("--chat-id", default="")
    parser.add_argument("--caption", default="")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if not args.message_file.is_file():
        raise SystemExit(f"Fixture message file does not exist: {args.message_file}")

    text = args.message_file.read_text(encoding="utf-8")
    prompt, style_name = build_cyprus_scene_prompt(text, post_type=args.post_type)

    print("CY_FIXTURE_PROMPT_BEGIN")
    print(prompt)
    print("CY_FIXTURE_PROMPT_END")
    print(f"CY_FIXTURE_STYLE: {style_name}")

    if not args.generate_image:
        if args.send_image_to_test:
            raise SystemExit(
                "--send-image-to-test true requires --generate-image true"
            )
        return

    image_path, image_size = _generate_image(prompt, style_name)
    print(f"CY_FIXTURE_IMAGE_PATH: {image_path.resolve()}")
    print(f"CY_FIXTURE_IMAGE_BYTES: {image_size}")

    if not args.send_image_to_test:
        return

    chat_id = _resolve_test_chat_id(args.chat_id)
    caption = args.caption.strip() or _DEFAULT_CAPTIONS[args.post_type]
    asyncio.run(_send_test_photo(image_path, chat_id, caption))
    print(f"CY_FIXTURE_TEST_PHOTO_SENT: chat_id={chat_id}")


if __name__ == "__main__":
    main()
