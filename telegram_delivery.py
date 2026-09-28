"""Verified Telegram delivery receipts and durable notification state."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

import requests


_STATE_VERSION = 1
_FINGERPRINT_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_NUMERIC_CHAT_PATTERN = re.compile(r"-?[0-9]+\Z")
_USERNAME_PATTERN = re.compile(r"@[A-Za-z0-9_]+\Z")
_POST_ID_PATTERN = re.compile(r"[0-9]{1,20}\Z")


@dataclass(frozen=True, slots=True)
class TelegramReceipt:
    """Non-secret evidence returned after Telegram accepts a message."""

    message_id: int
    chat_fingerprint: str
    sent_at: str


def _chat_fingerprint(chat_id: int, bot_token: str) -> str:
    """Create a stable identifier that cannot be guessed without the bot token."""

    digest = hmac.new(
        bot_token.encode("utf-8"),
        f"telegram-chat:{chat_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"sha256:{digest}"


def _configured_chat_matches(
    configured_chat_id: object,
    response_chat_id: int,
    response_username: object,
) -> bool:
    configured = str(configured_chat_id).strip()
    if _NUMERIC_CHAT_PATTERN.fullmatch(configured):
        return int(configured) == response_chat_id

    if _USERNAME_PATTERN.fullmatch(configured):
        return (
            isinstance(response_username, str)
            and response_username != ""
            and configured[1:].casefold() == response_username.casefold()
        )

    return False


def send_telegram_message(
    bot_token: str,
    chat_id: str | int,
    text: str,
    request_post: Callable[..., Any] = requests.post,
) -> TelegramReceipt:
    """Send one HTML message and return a receipt for the verified chat.

    Exceptions from the HTTP client and response details are deliberately not
    chained because the request URL contains the bot token.
    """

    if not isinstance(bot_token, str) or not bot_token.strip():
        raise RuntimeError("Telegram delivery configuration is invalid.") from None
    if not isinstance(text, str) or not text:
        raise RuntimeError("Telegram message text is invalid.") from None

    configured_chat = str(chat_id).strip()
    if not (
        _NUMERIC_CHAT_PATTERN.fullmatch(configured_chat)
        or _USERNAME_PATTERN.fullmatch(configured_chat)
    ):
        raise RuntimeError("Telegram delivery configuration is invalid.") from None

    url = f"https://api.telegram.org/bot{bot_token.strip()}/sendMessage"
    payload = {
        "chat_id": configured_chat,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    try:
        response = request_post(url, json=payload, timeout=10)
        response.raise_for_status()
    except Exception:
        raise RuntimeError("Telegram delivery request failed.") from None

    try:
        response_body = response.json()
    except Exception:
        raise RuntimeError("Telegram returned an invalid delivery response.") from None

    if not isinstance(response_body, Mapping) or response_body.get("ok") is not True:
        raise RuntimeError("Telegram rejected the delivery request.") from None

    result = response_body.get("result")
    if not isinstance(result, Mapping):
        raise RuntimeError("Telegram returned an invalid delivery response.") from None

    message_id = result.get("message_id")
    chat = result.get("chat")
    if (
        isinstance(message_id, bool)
        or not isinstance(message_id, int)
        or message_id <= 0
        or not isinstance(chat, Mapping)
    ):
        raise RuntimeError("Telegram returned an invalid delivery response.") from None

    response_chat_id = chat.get("id")
    if isinstance(response_chat_id, bool) or not isinstance(response_chat_id, int):
        raise RuntimeError("Telegram returned an invalid delivery response.") from None

    if not _configured_chat_matches(
        configured_chat,
        response_chat_id,
        chat.get("username"),
    ):
        raise RuntimeError("Telegram delivered to an unexpected chat.") from None

    return TelegramReceipt(
        message_id=message_id,
        chat_fingerprint=_chat_fingerprint(response_chat_id, bot_token.strip()),
        sent_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def _valid_receipt_dict(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "message_id",
        "chat_fingerprint",
        "sent_at",
    }:
        return False

    message_id = value.get("message_id")
    if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id <= 0:
        return False

    fingerprint = value.get("chat_fingerprint")
    if not isinstance(fingerprint, str) or not _FINGERPRINT_PATTERN.fullmatch(fingerprint):
        return False

    sent_at = value.get("sent_at")
    if not isinstance(sent_at, str):
        return False
    try:
        parsed_sent_at = datetime.fromisoformat(sent_at)
    except ValueError:
        return False
    return parsed_sent_at.tzinfo is not None


def _valid_delivery_state(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "version",
        "post_id",
        "title",
        "receipt",
    }:
        return False
    if value.get("version") != _STATE_VERSION:
        return False

    post_id = value.get("post_id")
    title = value.get("title")
    return (
        isinstance(post_id, str)
        and _POST_ID_PATTERN.fullmatch(post_id) is not None
        and isinstance(title, str)
        and bool(title.strip())
        and _valid_receipt_dict(value.get("receipt"))
    )


def save_delivery_state(
    path: str | os.PathLike[str],
    post_id: str,
    title: str,
    receipt: TelegramReceipt,
) -> None:
    """Atomically persist non-secret evidence for the last delivered post."""

    state = {
        "version": _STATE_VERSION,
        "post_id": post_id,
        "title": title,
        "receipt": asdict(receipt) if isinstance(receipt, TelegramReceipt) else None,
    }
    if not _valid_delivery_state(state):
        raise ValueError("Invalid Telegram delivery state.")

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(
                state,
                temporary_file,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        os.replace(temporary_path, target)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def load_delivery_state(path: str | os.PathLike[str]) -> dict[str, Any] | None:
    """Load a valid delivery state, returning ``None`` for untrusted input."""

    try:
        with Path(path).open("r", encoding="utf-8") as state_file:
            state = json.load(state_file)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None

    if not _valid_delivery_state(state):
        return None
    return state
