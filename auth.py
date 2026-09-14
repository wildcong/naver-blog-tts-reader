"""Server-verified app access using Streamlit's supported v2 state bridge.

Community Cloud filters custom HTTP cookies, so only a signed 30-day token is
remembered in localStorage and delivered through the component state channel.
The reader must call require_access before any private rendering or data access.
Failure limits and logout revocations are process-local and reset on restart.
"""

from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import threading
import time


SESSION_SECONDS = 30 * 24 * 60 * 60
PASSWORD_ITERATIONS = 600_000
ROBOTS_POLICY = "noindex, nofollow, noarchive, nosnippet"
_PASSWORD_SALT = "a19b3d92035fff663d54959785f7e18e"
_DEFAULT_PASSWORD_HASH = (
    "pbkdf2_sha256$600000$" + _PASSWORD_SALT + "$"
    "ba5de98573f49e33edf4965549b5044cf4d2208e9b046fca25d15d02fb1d53b9"
)
_TOKEN_PATTERN = re.compile(r"(v1|c1)\.([0-9]{1,11})\.([0-9]{1,11})\.([A-Za-z0-9_-]{32})\.([a-f0-9]{64})\Z")
_STATE_LOCK = threading.Lock()
_ATTEMPT_LOCK = threading.Lock()
_FAILURES = deque(maxlen=20)
_REVOKED: dict[str, int] = {}
_CONFIG_ERROR = "접속 설정을 준비하지 못했습니다. 서버의 인증 설정을 확인해 주세요."


class AuthConfigurationError(ValueError):
    """Configuration errors contain no secret values or provider exceptions."""


def hash_password(password: str, salt: str | None = None) -> str:
    """Create a PBKDF2-SHA256 verifier; salt is a hexadecimal 16-byte value."""
    if not isinstance(password, str) or not 4 <= len(password) <= 1024:
        raise AuthConfigurationError(_CONFIG_ERROR)
    salt = secrets.token_hex(16) if salt is None else salt
    if not isinstance(salt, str) or not re.fullmatch(r"[a-f0-9]{32}", salt):
        raise AuthConfigurationError(_CONFIG_ERROR)
    try:
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PASSWORD_ITERATIONS)
    except UnicodeError:
        raise AuthConfigurationError(_CONFIG_ERROR) from None
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt}${digest.hex()}"


@dataclass(frozen=True)
class AuthSettings:
    password_hash: str = field(repr=False)
    signing_secret: str = field(repr=False)

    def __post_init__(self):
        if (
            not isinstance(self.password_hash, str)
            or not re.fullmatch(r"pbkdf2_sha256\$600000\$[a-f0-9]{32}\$[a-f0-9]{64}", self.password_hash)
            or not isinstance(self.signing_secret, str)
            or not 32 <= len(self.signing_secret) <= 4096
        ):
            raise AuthConfigurationError(_CONFIG_ERROR)
        try:
            self.signing_secret.encode("utf-8")
        except UnicodeError:
            raise AuthConfigurationError(_CONFIG_ERROR) from None


@lru_cache(maxsize=8)
def _override_hash(password: str) -> str:
    # Stable across requests and process restarts; salt is public, not a key.
    return hash_password(password, _PASSWORD_SALT)


def load_settings() -> AuthSettings:
    """Read server environment/Secrets. APP_COOKIE_SECRET has no fallback."""
    values = {}
    if "APP_COOKIE_SECRET" not in os.environ or "APP_PASSWORD" not in os.environ:
        try:
            import streamlit as st
            from streamlit.errors import StreamlitSecretNotFoundError

            try:
                values = {name: st.secrets.get(name) for name in ("APP_COOKIE_SECRET", "APP_PASSWORD")}
            except StreamlitSecretNotFoundError:
                values = {}
        except Exception:
            raise AuthConfigurationError(_CONFIG_ERROR) from None
    signing_secret = os.environ.get("APP_COOKIE_SECRET", values.get("APP_COOKIE_SECRET"))
    password = os.environ.get("APP_PASSWORD", values.get("APP_PASSWORD"))
    verifier = _DEFAULT_PASSWORD_HASH if password is None else _override_hash(password)
    return AuthSettings(verifier, signing_secret)


def _key(settings: AuthSettings, kind: str) -> bytes:
    context = f"blog-daily/{kind}/{settings.password_hash}".encode("ascii")
    return hmac.new(settings.signing_secret.encode("utf-8"), context, hashlib.sha256).digest()


def _mint_token(settings: AuthSettings, kind: str, ttl: int, now: int | None) -> str:
    issued = int(time.time()) if now is None else now
    if isinstance(issued, bool) or not isinstance(issued, int) or issued < 0 or issued + ttl >= 10**11:
        raise ValueError("Invalid timestamp")
    payload = f"{kind}.{issued}.{issued + ttl}.{secrets.token_urlsafe(24)}"
    signature = hmac.new(_key(settings, kind), payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def issue_token(settings: AuthSettings, *, now: int | None = None) -> str:
    """Issue a credential that expires exactly 30 days after this login."""
    return _mint_token(settings, "v1", SESSION_SECONDS, now)


def _valid_token(token: object, settings: AuthSettings, kind: str, ttl: int, now: int | None) -> bool:
    if not isinstance(token, str) or len(token) > 512:
        return False
    match = _TOKEN_PATTERN.fullmatch(token)
    if not match or match[1] != kind:
        return False
    current = int(time.time()) if now is None else now
    if isinstance(current, bool) or not isinstance(current, int):
        return False
    issued, expiry = int(match[2]), int(match[3])
    if not issued <= current < expiry or expiry - issued != ttl:
        return False
    payload, signature = token.rsplit(".", 1)
    expected = hmac.new(_key(settings, kind), payload.encode("ascii"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def _prune_revocations(now: int) -> None:
    for token_hash, expiry in list(_REVOKED.items()):
        if expiry <= now:
            del _REVOKED[token_hash]


def validate_token(token: object, settings: AuthSettings, *, now: int | None = None) -> bool:
    """Check signature, fixed expiry, password context, and local revocations."""
    if not _valid_token(token, settings, "v1", SESSION_SECONDS, now):
        return False
    current = int(time.time()) if now is None else now
    with _STATE_LOCK:
        _prune_revocations(current)
        return hashlib.sha256(token.encode("ascii")).hexdigest() not in _REVOKED


def _revoke(token: str) -> None:
    with _STATE_LOCK:
        _prune_revocations(int(time.time()))
        _REVOKED[hashlib.sha256(token.encode("ascii")).hexdigest()] = int(token.split(".")[2])


def _check_password(password: str, settings: AuthSettings) -> tuple[bool, int]:
    # Serialize check+failure recording across Streamlit script threads.
    with _ATTEMPT_LOCK:
        current = time.monotonic()
        while _FAILURES and _FAILURES[0] <= current - 300:
            _FAILURES.popleft()
        if len(_FAILURES) >= 20:
            return False, max(1, int(_FAILURES[0] + 300 - current) + 1)
        try:
            candidate = hash_password(password, settings.password_hash.split("$")[2])
            correct = hmac.compare_digest(candidate, settings.password_hash)
        except AuthConfigurationError:
            correct = False
        if not correct:
            _FAILURES.append(time.monotonic())
        return correct, 0


_PREFIX = "_blog_auth_"
_TOKEN_KEY = _PREFIX + "token"
_COMMAND_KEY = _PREFIX + "command"
_REPLY_KEY = _PREFIX + "reply_seen"
_EVENT_KEY = _PREFIX + "event_seen"
_PASSWORD_KEY = _PREFIX + "password"
_ERROR_KEY = _PREFIX + "error"
_AVAILABLE_KEY = _PREFIX + "storage_available"
_LOCKED_KEY = _PREFIX + "locked"


def access_valid() -> bool:
    """Recheck the signed credential, never trust an authenticated flag."""
    import streamlit as st

    if st.session_state.get(_LOCKED_KEY):
        return False
    try:
        return validate_token(st.session_state.get(_TOKEN_KEY), load_settings())
    except (AuthConfigurationError, TypeError, ValueError):
        return False


def _command(action: str, token: str = "") -> None:
    import streamlit as st

    st.session_state[_COMMAND_KEY] = {
        "id": secrets.token_urlsafe(12), "action": action, "token": token,
    }


def _logout(*, revoke: bool = True, message: str = "") -> None:
    """Clear private session data and schedule browser credential removal."""
    import streamlit as st

    token = st.session_state.pop(_TOKEN_KEY, None)
    if revoke and isinstance(token, str) and _TOKEN_PATTERN.fullmatch(token):
        _revoke(token)
    for key in list(st.session_state):
        if not key.startswith(_PREFIX):
            del st.session_state[key]
    st.session_state.pop(_PASSWORD_KEY, None)
    st.session_state[_LOCKED_KEY] = True
    st.session_state[_ERROR_KEY] = message
    _command("clear")


def _submit_password() -> None:
    """Verify on the server and remove the submitted password immediately."""
    import streamlit as st

    password = st.session_state.pop(_PASSWORD_KEY, "")
    try:
        settings = load_settings()
        correct, wait = _check_password(password, settings)
    except (AuthConfigurationError, TypeError, ValueError):
        st.session_state[_ERROR_KEY] = _CONFIG_ERROR
        return
    if wait:
        st.session_state[_ERROR_KEY] = f"입력 시도가 많습니다. 약 {wait}초 후 다시 시도해 주세요."
    elif not correct:
        st.session_state[_ERROR_KEY] = "비밀번호가 맞지 않습니다."
    else:
        token = issue_token(settings)
        st.session_state[_TOKEN_KEY] = token
        st.session_state[_LOCKED_KEY] = False
        st.session_state.pop(_ERROR_KEY, None)
        st.session_state.pop(_AVAILABLE_KEY, None)
        _command("set", token)


def _auth_component():
    """Register this runtime's bridge; never cache across Streamlit runtimes."""
    import streamlit as st

    return st.components.v2.component(
        "blog_daily_access_storage",
        js=Path(__file__).with_name("auth_component.js").read_text(encoding="utf-8"),
    )


def _process_reply(reply, command, settings: AuthSettings) -> bool:
    """Consume one matching bridge reply; return whether a new command exists."""
    import streamlit as st

    if not isinstance(reply, dict) or reply.get("id") != command["id"]:
        return False
    action = reply.get("action")
    if action == "event":
        event_id = reply.get("eventId")
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 64:
            return False
        if st.session_state.get(_EVENT_KEY) == event_id:
            return False
        st.session_state[_EVENT_KEY] = event_id
    elif action == command["action"]:
        if st.session_state.get(_REPLY_KEY) == command["id"]:
            return False
        st.session_state[_REPLY_KEY] = command["id"]
    else:
        return False
    available = reply.get("available") is True
    st.session_state[_AVAILABLE_KEY] = available
    if action not in ("read", "event"):
        return False
    token = reply.get("token", "")
    if validate_token(token, settings) and not st.session_state.get(_LOCKED_KEY):
        st.session_state[_TOKEN_KEY] = token
        return False
    # A storage failure cannot undo a freshly verified password in this tab.
    # A reported storage removal/expiry does lock an already open session.
    if action == "event" and available and st.session_state.get(_TOKEN_KEY):
        _logout(message="접속이 종료되었습니다. 비밀번호를 다시 입력해 주세요.")
        return True
    if token:
        _logout(revoke=False)
        return True
    return False


def _watch_access() -> None:
    """An idle authenticated page must replace its private content on expiry."""
    import streamlit as st

    if not access_valid():
        _logout(revoke=False, message="접속 시간이 만료되었습니다. 다시 로그인해 주세요.")
        st.rerun(scope="app")


def require_access() -> None:
    """Stop before reader execution until a server-verified token is present."""
    import streamlit as st

    try:
        settings = load_settings()
    except (AuthConfigurationError, TypeError, ValueError):
        _logout(revoke=False)
        st.error(_CONFIG_ERROR)
        st.stop()
    prior_token = st.session_state.get(_TOKEN_KEY)
    if prior_token and not validate_token(prior_token, settings):
        _logout(revoke=False, message="접속 시간이 만료되었습니다. 다시 로그인해 주세요.")
    if _COMMAND_KEY not in st.session_state:
        _command("read")
    command = st.session_state[_COMMAND_KEY]
    result = _auth_component()(
        key=_PREFIX + "browser", data=command, default={"reply": None},
        on_reply_change=lambda: None, height=0,
    )
    if _process_reply(result.reply, command, settings):
        st.rerun()
    if access_valid() and st.session_state.get(_REPLY_KEY) != command["id"]:
        # The bridge's acknowledgement triggers a rerun. Complete it before
        # the reader begins network/TTS work that should not be interrupted.
        st.caption("접속 정보를 확인하고 있습니다…")
        st.stop()
    if not access_valid():
        st.markdown("""
<style>
[data-testid="stAppViewContainer"]{background:#09090b;color:#fafafa}
[data-testid="stHeader"],footer{display:none}
.st-key-blog-daily-login{max-width:420px;margin:9vh auto 0;padding:32px;
border:1px solid #27272a;border-radius:20px;background:#101013}
.st-key-blog-daily-login h1{font-size:28px;letter-spacing:-.04em;color:#fafafa}
.st-key-blog-daily-login p,.st-key-blog-daily-login label{color:#a1a1aa}
.st-key-blog-daily-login [data-testid="stForm"]{border:0;padding:0}
.st-key-blog-daily-login [data-testid="stFormSubmitButton"] button{width:100%;background:#2563eb;color:white;border:0}
@media(max-width:480px){.st-key-blog-daily-login{padding:24px;margin-top:5vh}}
</style>""", unsafe_allow_html=True)
        with st.container(key="blog-daily-login"):
            st.markdown('<div data-blog-daily-auth><p>AUDIO READER</p><h1>블로그 리더</h1>'
                        '<p>비밀번호로 보호된 개인 오디오 리더입니다.</p></div>', unsafe_allow_html=True)
            if st.session_state.get(_ERROR_KEY):
                st.error(st.session_state[_ERROR_KEY])
            with st.form(_PREFIX + "form", clear_on_submit=True):
                st.text_input("비밀번호", type="password", key=_PASSWORD_KEY, max_chars=1024)
                st.form_submit_button("로그인", on_click=_submit_password, type="primary", use_container_width=True)
            st.caption("로그인하면 이 브라우저에서 30일 동안 이용할 수 있습니다.")
            if st.session_state.get(_AVAILABLE_KEY) is False:
                st.caption("브라우저 저장이 제한되어 있습니다. 창을 새로 열면 다시 로그인해 주세요.")
        st.stop()
    with st.container(key="blog-daily-access-controls"):
        st.button("로그아웃", key=_PREFIX + "logout", on_click=_logout)
        if st.session_state.get(_AVAILABLE_KEY) is False:
            st.caption("이 브라우저는 로그인을 기억할 수 없습니다. 현재 창에서는 계속 이용할 수 있습니다.")
    st.fragment(run_every=30)(_watch_access)()


class PrivacyAuthMiddleware:
    """Serve the Streamlit shell; the script gate protects all private content.

    Public media/upload/static-file endpoints stay closed because their URLs
    cannot carry the verified component session. Audio is delivered inline only.
    """

    def __init__(self, app, settings: AuthSettings | None = None):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def private_send(message):
            if message["type"] == "http.response.start":
                headers = [(key, value) for key, value in message.get("headers", [])
                           if key.lower() not in (b"x-robots-tag", b"cache-control")]
                headers.extend([(b"x-robots-tag", ROBOTS_POLICY.encode()),
                                (b"cache-control", b"private, no-store"),
                                (b"x-content-type-options", b"nosniff")])
                message = {**message, "headers": headers}
            await send(message)

        path = scope.get("path", "/")
        blocked = ("/media", "/_stcore/upload_file", "/app/static")
        if any(path == prefix or path.startswith(prefix + "/") for prefix in blocked):
            return await self._respond(private_send, 404, b"Not found", scope)
        if path in ("/auth/login", "/auth/logout"):
            return await self._respond(private_send, 303, b"", scope, [(b"location", b"/")])
        if path == "/robots.txt":
            return await self._respond(private_send, 200, b"User-agent: *\nAllow: /\n", scope)
        return await self.app(scope, receive, private_send)

    async def _respond(self, send, status, body, scope, headers=None):
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"text/plain; charset=utf-8"),
            (b"content-length", str(len(body)).encode()), *(headers or []),
        ]})
        await send({"type": "http.response.body", "body": b"" if scope.get("method") == "HEAD" else body})
