"""Check the server-validated browser session and private-content boundary."""

import time
from types import SimpleNamespace

import pytest
import streamlit
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from streamlit.testing.v1 import AppTest

import auth
from auth import (
    AuthSettings,
    PrivacyAuthMiddleware,
    hash_password,
    issue_token,
    load_settings,
    validate_token,
)


PASSWORD = "test-only-password"
ORIGIN = "https://testserver"
SESSION_SECONDS = 30 * 24 * 60 * 60
PRIVATE_SENTINEL = "PRIVATE_CONTENT_SHOULD_ONLY_RENDER_AFTER_AUTHENTICATION"


@pytest.fixture(scope="module")
def settings():
    return AuthSettings(
        password_hash=hash_password(PASSWORD),
        signing_secret="test-only-browser-signing-secret-at-least-32-characters",
    )


@pytest.fixture
def client():
    async def shell(request):
        return PlainTextResponse("public Streamlit shell")

    application = PrivacyAuthMiddleware(
        Starlette(routes=[Route("/{path:path}", shell, methods=["GET", "HEAD", "POST"])])
    )
    with TestClient(application, base_url=ORIGIN, follow_redirects=False) as browser:
        yield browser


def _assert_private_headers(response):
    assert "noindex" in response.headers["x-robots-tag"].lower()
    assert "nofollow" in response.headers["x-robots-tag"].lower()
    assert "no-store" in response.headers["cache-control"].lower()


@pytest.mark.parametrize("path", ["/", "/static/app.js", "/_stcore/health"])
def test_public_application_shell_is_noindex_and_not_cached(client, path):
    response = client.get(path)
    assert response.status_code == 200
    _assert_private_headers(response)


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/media/private.mp3"),
        ("HEAD", "/media/private.mp3"),
        ("GET", "/app/static/private.txt"),
        ("POST", "/_stcore/upload_file/session/file"),
    ],
)
def test_shared_media_and_upload_routes_are_always_blocked(client, method, path):
    response = client.request(method, path)
    assert response.status_code in (403, 404)
    assert "public Streamlit shell" not in response.text
    _assert_private_headers(response)


def test_robots_can_fetch_the_noindex_response(client):
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert "Allow: /" in response.text
    assert "public Streamlit shell" not in response.text
    _assert_private_headers(response)


def test_expiration_is_exactly_30_days(settings):
    issued_at = 1_800_000_000
    token = issue_token(settings, now=issued_at)
    assert validate_token(token, settings, now=issued_at)
    assert validate_token(token, settings, now=issued_at + SESSION_SECONDS - 1)
    assert not validate_token(token, settings, now=issued_at + SESSION_SECONDS)
    assert not validate_token(token, settings, now=issued_at + SESSION_SECONDS + 1)


def test_future_tokens_and_changed_signatures_are_rejected(settings):
    issued_at = 1_800_000_000
    token = issue_token(settings, now=issued_at)
    assert not validate_token(token, settings, now=issued_at - 60)
    parts = token.split(".")
    signature = parts[-1]
    parts[-1] = ("A" if signature[0] != "A" else "B") + signature[1:]
    assert not validate_token(".".join(parts), settings, now=issued_at)
    other_settings = AuthSettings(
        password_hash=settings.password_hash,
        signing_secret="different-test-signing-secret-at-least-32-characters",
    )
    assert not validate_token(token, other_settings, now=issued_at)


def test_changing_password_invalidates_previous_sessions(settings):
    token = issue_token(settings)
    changed = AuthSettings(
        password_hash=hash_password("different-test-password"),
        signing_secret=settings.signing_secret,
    )
    assert not validate_token(token, changed)


@pytest.mark.parametrize("token", [None, b"not-a-token", "", "not-a-token", "...", "a.b.c", "☃.☃", "x" * 513])
def test_malformed_tokens_fail_closed(settings, token):
    assert not validate_token(token, settings)


def test_unicode_timestamp_digits_fail_closed(settings):
    parts = issue_token(settings).split(".")
    arabic_indic_digits = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
    parts[1] = parts[1].translate(arabic_indic_digits)
    parts[2] = parts[2].translate(arabic_indic_digits)
    assert not validate_token(".".join(parts), settings)


def test_missing_signing_secret_fails_closed(monkeypatch, tmp_path):
    monkeypatch.delenv("APP_COOKIE_SECRET", raising=False)
    monkeypatch.setenv("APP_PASSWORD", PASSWORD)
    monkeypatch.setattr(streamlit, "secrets", {})
    monkeypatch.chdir(tmp_path)
    with pytest.raises((RuntimeError, ValueError)):
        load_settings()


def test_password_verification_and_process_wide_failure_limit(settings):
    with auth._ATTEMPT_LOCK:
        previous_failures = list(auth._FAILURES)
        auth._FAILURES.clear()
    try:
        assert auth._check_password(PASSWORD, settings) == (True, 0)
        for _ in range(20):
            assert auth._check_password("incorrect-test-password", settings) == (False, 0)
        allowed, retry_after = auth._check_password(PASSWORD, settings)
        assert not allowed
        assert retry_after > 0
    finally:
        with auth._ATTEMPT_LOCK:
            auth._FAILURES.clear()
            auth._FAILURES.extend(previous_failures)


def _private_app():
    return AppTest.from_string(
        "import streamlit as st\n"
        "from auth import require_access\n"
        "require_access()\n"
        "st.session_state['private_calls'] = st.session_state.get('private_calls', 0) + 1\n"
        f"st.write({PRIVATE_SENTINEL!r})\n",
        default_timeout=5,
    )


def _assert_locked(app):
    assert not app.exception
    assert "private_calls" not in app.session_state
    assert not any(PRIVATE_SENTINEL in element.value for element in app.markdown)


def _assert_unlocked(app):
    assert not app.exception
    assert app.session_state["private_calls"] > 0
    assert any(PRIVATE_SENTINEL in element.value for element in app.markdown)


class _BrowserStorage:
    """Mock only the browser transport; authentication runs in real Streamlit."""

    def __init__(self):
        self.token = ""
        self.commands = []

    def __call__(self, *, data, **kwargs):
        command = data
        self.commands.append(dict(command))
        if command["action"] == "set":
            self.token = command["token"]
        elif command["action"] == "clear":
            self.token = ""
        return SimpleNamespace(
            reply={
                "id": command["id"],
                "action": command["action"],
                "available": True,
                "token": self.token if command["action"] == "read" else "",
            }
        )


@pytest.fixture
def browser_storage(monkeypatch, settings):
    browser = _BrowserStorage()
    monkeypatch.setattr(auth, "load_settings", lambda: settings)
    monkeypatch.setattr(auth, "_auth_component", lambda: browser)
    return browser


def _submit_password(app, password):
    app.text_input[0].input(password)
    next(button for button in app.button if button.label == "로그인").click().run()


def test_anonymous_browser_stops_before_private_data_is_processed(browser_storage):
    app = _private_app().run()
    _assert_locked(app)
    assert app.text_input[0].label == "비밀번호"
    assert browser_storage.token == ""


def test_wrong_password_does_not_unlock_or_store_a_token(browser_storage):
    app = _private_app().run()
    _submit_password(app, "incorrect-test-password")
    _assert_locked(app)
    assert app.error
    assert browser_storage.token == ""


def test_login_persists_for_a_new_session_without_renewing_expiry(browser_storage, settings):
    app = _private_app().run()
    _submit_password(app, PASSWORD)
    _assert_unlocked(app)
    original_token = browser_storage.token
    assert validate_token(original_token, settings)
    assert int(original_token.split(".")[2]) - int(original_token.split(".")[1]) == SESSION_SECONDS
    original_writes = sum(command["action"] == "set" for command in browser_storage.commands)
    reopened = _private_app().run()
    _assert_unlocked(reopened)
    reopened.run()
    _assert_unlocked(reopened)
    assert browser_storage.token == original_token
    assert sum(command["action"] == "set" for command in browser_storage.commands) == original_writes


@pytest.mark.parametrize("kind", ["expired", "forged", "unicode"])
def test_invalid_remembered_token_never_processes_private_data(browser_storage, settings, kind):
    if kind == "expired":
        token = issue_token(settings, now=int(time.time()) - SESSION_SECONDS)
    elif kind == "forged":
        token = issue_token(settings)
        token = token[:-1] + ("a" if token[-1] != "a" else "b")
    else:
        parts = issue_token(settings).split(".")
        parts[1] = parts[1].translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))
        token = ".".join(parts)
    browser_storage.token = token
    app = _private_app().run()
    _assert_locked(app)
    assert browser_storage.token == ""


def test_logout_clears_browser_storage_and_private_state_and_revokes_token(browser_storage, settings):
    app = _private_app().run()
    _submit_password(app, PASSWORD)
    _assert_unlocked(app)
    original_token = browser_storage.token
    next(button for button in app.button if button.label == "로그아웃").click().run()
    _assert_locked(app)
    assert browser_storage.token == ""
    assert not validate_token(original_token, settings)
    browser_storage.token = original_token
    replayed = _private_app().run()
    _assert_locked(replayed)


def test_missing_configuration_does_not_process_private_data(browser_storage, monkeypatch):
    def unavailable_settings():
        raise auth.AuthConfigurationError("Missing test configuration")

    monkeypatch.setattr(auth, "load_settings", unavailable_settings)
    app = _private_app().run()
    _assert_locked(app)
    assert app.error
