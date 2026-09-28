from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import pytest
import requests

import telegram_delivery
from telegram_delivery import (
    TelegramReceipt,
    load_delivery_state,
    save_delivery_state,
    send_telegram_message,
)


class _Response:
    def __init__(self, body, *, status_error: Exception | None = None):
        self._body = body
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error is not None:
            raise self._status_error

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _success_body(*, message_id=41, chat_id=-1001234567890, username=None):
    chat = {"id": chat_id}
    if username is not None:
        chat["username"] = username
    return {
        "ok": True,
        "result": {
            "message_id": message_id,
            "chat": chat,
        },
    }


def test_send_numeric_chat_returns_verified_receipt_and_posts_html():
    calls = []

    def request_post(url, **kwargs):
        calls.append((url, kwargs))
        return _Response(_success_body())

    receipt = send_telegram_message(
        "super-secret-token",
        "-1001234567890",
        "<b>새 글</b>",
        request_post=request_post,
    )

    assert receipt.message_id == 41
    assert receipt.chat_fingerprint.startswith("sha256:")
    assert "-1001234567890" not in receipt.chat_fingerprint
    assert datetime.fromisoformat(receipt.sent_at).tzinfo is not None
    assert calls == [
        (
            "https://api.telegram.org/botsuper-secret-token/sendMessage",
            {
                "json": {
                    "chat_id": "-1001234567890",
                    "text": "<b>새 글</b>",
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                },
                "timeout": 10,
            },
        )
    ]


def test_chat_fingerprint_is_keyed_by_the_bot_token():
    response = lambda *_args, **_kwargs: _Response(_success_body(chat_id=12345))

    first = send_telegram_message(
        "first-private-token",
        "12345",
        "message",
        request_post=response,
    )
    second = send_telegram_message(
        "second-private-token",
        "12345",
        "message",
        request_post=response,
    )

    assert first.chat_fingerprint != second.chat_fingerprint


def test_send_username_chat_matches_case_insensitively():
    receipt = send_telegram_message(
        "token",
        "@Expected_Channel",
        "message",
        request_post=lambda *_args, **_kwargs: _Response(
            _success_body(chat_id=-10044, username="expected_channel")
        ),
    )

    assert receipt.message_id == 41


@pytest.mark.parametrize(
    ("configured_chat", "response_chat_id", "response_username"),
    [
        ("-100123", -100999, None),
        ("@expected", -100123, "somewhere_else"),
        ("@expected", -100123, None),
    ],
)
def test_send_rejects_wrong_chat_without_exposing_identifiers(
    configured_chat,
    response_chat_id,
    response_username,
):
    with pytest.raises(RuntimeError) as error:
        send_telegram_message(
            "do-not-leak-token",
            configured_chat,
            "message",
            request_post=lambda *_args, **_kwargs: _Response(
                _success_body(
                    chat_id=response_chat_id,
                    username=response_username,
                )
            ),
        )

    rendered_error = str(error.value)
    assert "do-not-leak-token" not in rendered_error
    assert str(configured_chat) not in rendered_error
    assert str(response_chat_id) not in rendered_error
    assert error.value.__cause__ is None


def test_send_rejects_ok_false():
    with pytest.raises(RuntimeError, match="rejected") as error:
        send_telegram_message(
            "token-that-must-stay-private",
            "12345",
            "message",
            request_post=lambda *_args, **_kwargs: _Response(
                {"ok": False, "description": "secret response detail"}
            ),
        )

    assert "token-that-must-stay-private" not in str(error.value)
    assert "secret response detail" not in str(error.value)
    assert error.value.__cause__ is None


@pytest.mark.parametrize(
    "body",
    [
        ValueError("not json"),
        None,
        [],
        {"ok": True},
        {"ok": True, "result": {}},
        {"ok": True, "result": {"message_id": True, "chat": {"id": 12345}}},
        {"ok": True, "result": {"message_id": 1, "chat": {"id": True}}},
    ],
)
def test_send_rejects_invalid_json_or_incomplete_success(body):
    with pytest.raises(RuntimeError):
        send_telegram_message(
            "token",
            "12345",
            "message",
            request_post=lambda *_args, **_kwargs: _Response(body),
        )


@pytest.mark.parametrize(
    "failure",
    [
        requests.ConnectionError(
            "failed https://api.telegram.org/botraw-secret-token/sendMessage"
        ),
        requests.HTTPError(
            "403 for https://api.telegram.org/botraw-secret-token/sendMessage"
        ),
    ],
)
def test_request_failures_are_wrapped_without_secret_or_cause(failure):
    def request_post(*_args, **_kwargs):
        if isinstance(failure, requests.HTTPError):
            return _Response({}, status_error=failure)
        raise failure

    with pytest.raises(RuntimeError) as error:
        send_telegram_message(
            "raw-secret-token",
            "12345",
            "message",
            request_post=request_post,
        )

    assert str(error.value) == "Telegram delivery request failed."
    assert "raw-secret-token" not in str(error.value)
    assert error.value.__cause__ is None


def test_save_and_load_delivery_state_atomically_without_raw_chat(
    tmp_path,
    monkeypatch,
):
    state_path = tmp_path / "nested" / "delivery.json"
    receipt = send_telegram_message(
        "token-not-for-state",
        "-100987654321",
        "message",
        request_post=lambda *_args, **_kwargs: _Response(
            _success_body(message_id=912, chat_id=-100987654321)
        ),
    )
    replace_calls = []
    real_replace = telegram_delivery.os.replace

    def recording_replace(source, destination):
        replace_calls.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(telegram_delivery.os, "replace", recording_replace)

    save_delivery_state(
        state_path,
        "224423696087",
        "한국 반도체 시작 썰",
        receipt,
    )

    assert len(replace_calls) == 1
    temporary_path, destination = replace_calls[0]
    assert temporary_path.parent == state_path.parent
    assert destination == state_path
    assert not temporary_path.exists()

    raw_state = state_path.read_text(encoding="utf-8")
    assert "token-not-for-state" not in raw_state
    assert "-100987654321" not in raw_state
    assert load_delivery_state(state_path) == {
        "version": 1,
        "post_id": "224423696087",
        "title": "한국 반도체 시작 썰",
        "receipt": {
            "message_id": 912,
            "chat_fingerprint": receipt.chat_fingerprint,
            "sent_at": receipt.sent_at,
        },
    }


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        json.dumps([]),
        json.dumps({"version": 1}),
        json.dumps(
            {
                "version": 1,
                "post_id": "224423696087",
                "title": "title",
                "receipt": {
                    "message_id": 1,
                    "chat_fingerprint": "raw-chat-id",
                    "sent_at": "2026-09-28T00:00:00+00:00",
                },
            }
        ),
        json.dumps(
            {
                "version": 1,
                "post_id": "224423696087",
                "title": "title",
                "receipt": {
                    "message_id": 1,
                    "chat_fingerprint": f"sha256:{'a' * 64}",
                    "sent_at": "not-a-date",
                },
            }
        ),
    ],
)
def test_load_delivery_state_fails_closed_on_malformed_content(tmp_path, payload):
    state_path = tmp_path / "delivery.json"
    state_path.write_text(payload, encoding="utf-8")

    assert load_delivery_state(state_path) is None


def test_load_delivery_state_returns_none_when_missing(tmp_path):
    assert load_delivery_state(tmp_path / "missing.json") is None


def test_save_delivery_state_rejects_invalid_input(tmp_path):
    with pytest.raises(ValueError):
        save_delivery_state(
            tmp_path / "delivery.json",
            "not-a-post-id",
            "title",
            TelegramReceipt(
                message_id=1,
                chat_fingerprint=f"sha256:{'a' * 64}",
                sent_at="2026-09-28T00:00:00+00:00",
            ),
        )
