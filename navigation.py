"""Safe deep-link helpers shared by the reader and notification job."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_MAX_POST_ID_LENGTH = 20


def _validated_post_id(value: object) -> str:
    """Return a canonical Naver post ID or raise for unsafe input."""

    if not isinstance(value, str):
        raise ValueError("post_id must be a string")
    if not 1 <= len(value) <= _MAX_POST_ID_LENGTH:
        raise ValueError("post_id has an invalid length")
    if not value.isascii() or not value.isdecimal():
        raise ValueError("post_id must contain ASCII digits only")
    return value


def build_post_url(base_url: str, post_id: str) -> str:
    """Add one validated post ID to an HTTP(S) application URL."""

    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("base_url is required")

    raw_url = base_url.strip()
    explicit_scheme = re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", raw_url)
    if raw_url.startswith("//"):
        raw_url = f"https:{raw_url}"
    elif explicit_scheme:
        scheme = explicit_scheme.group(0)[:-1].lower()
        if scheme in {"http", "https"}:
            if not raw_url.lower().startswith(f"{scheme}://"):
                raise ValueError("base_url has a malformed HTTP(S) scheme")
        # A bare local host may include a numeric port (for example
        # localhost:8501); every other explicit scheme is rejected.
        elif re.fullmatch(r"[^/?#\s:]+:\d+(?:[/?#].*)?", raw_url):
            raw_url = f"https://{raw_url}"
        else:
            raise ValueError("base_url must use HTTP or HTTPS")
    else:
        raw_url = f"https://{raw_url}"

    try:
        parsed = urlsplit(raw_url)
        hostname = parsed.hostname
        parsed.port  # Validate a supplied port while preserving it below.
    except ValueError as exc:
        raise ValueError("base_url has an invalid host or port") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        raise ValueError("base_url must be an HTTP(S) URL with a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("base_url must not contain credentials")
    if "\\" in parsed.netloc or any(character.isspace() for character in parsed.netloc):
        raise ValueError("base_url has an invalid host")

    safe_post_id = _validated_post_id(post_id)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key != "post_id"
    ]
    query.append(("post_id", safe_post_id))

    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc,
            parsed.path,
            urlencode(query, doseq=True),
            parsed.fragment,
        )
    )


def requested_post_id(query_params: Mapping[str, Any] | Any) -> str | None:
    """Read exactly one valid post ID from Streamlit-like query params."""

    try:
        get_all = getattr(query_params, "get_all", None)
        if callable(get_all):
            values = get_all("post_id")
        else:
            raw_value = query_params.get("post_id")
            if isinstance(raw_value, (list, tuple)):
                values = list(raw_value)
            elif raw_value is None:
                values = []
            else:
                values = [raw_value]
    except (AttributeError, KeyError, TypeError, ValueError):
        return None

    if len(values) != 1:
        return None
    try:
        return _validated_post_id(values[0])
    except ValueError:
        return None


def select_post(
    posts: Sequence[Mapping[str, Any]] | Iterable[Mapping[str, Any]],
    requested_id: str | None,
    current_id: str | None = None,
) -> Mapping[str, Any] | None:
    """Choose the linked post, then the current post, then the newest post."""

    available_posts = list(posts)
    if not available_posts:
        return None

    for candidate_id in (requested_id, current_id):
        if candidate_id is None:
            continue
        for post in available_posts:
            if str(post.get("post_id", "")) == candidate_id:
                return post
    return available_posts[0]


def post_query_params(post_id: str) -> dict[str, str]:
    """Return a fresh Streamlit query update for a selected post."""

    return {"post_id": _validated_post_id(post_id)}
