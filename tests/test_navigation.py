"""Deep-link behavior shared by Telegram notifications and the reader UI."""

from html import escape
from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit

import pytest

from navigation import (
    build_post_url,
    post_query_params,
    requested_post_id,
    select_post,
)
from scripts import check_new_posts


class _QueryParams:
    """Small Streamlit-like query object whose repeated values stay observable."""

    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        values = self._values.get(key)
        return values[-1] if values else default

    def get_all(self, key):
        return list(self._values.get(key, []))


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href is not None:
                self.hrefs.append(href)


def test_build_post_url_preserves_existing_query_and_fragment_and_replaces_post_id():
    result = build_post_url(
        "https://reader.example/private?utm_source=telegram&tag=a%2Fb&tag=c&post_id=old#listen",
        "123456789",
    )

    parsed = urlsplit(result)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)

    assert (parsed.scheme, parsed.netloc, parsed.path, parsed.fragment) == (
        "https",
        "reader.example",
        "/private",
        "listen",
    )
    assert [(key, value) for key, value in pairs if key != "post_id"] == [
        ("utm_source", "telegram"),
        ("tag", "a/b"),
        ("tag", "c"),
    ]
    assert [(key, value) for key, value in pairs if key == "post_id"] == [
        ("post_id", "123456789")
    ]


def test_build_post_url_adds_https_to_a_bare_app_host():
    result = build_post_url("blogtts-sh.streamlit.app/reader", "123")
    parsed = urlsplit(result)

    assert parsed.scheme == "https"
    assert parsed.netloc == "blogtts-sh.streamlit.app"
    assert parsed.path == "/reader"
    assert parse_qsl(parsed.query) == [("post_id", "123")]


@pytest.mark.parametrize(
    "base_url",
    [
        "",
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "ftp://reader.example/private",
        "http:reader.example/private",
        "https://user:secret@reader.example/private",
        "https://reader.example:not-a-port/private",
        "https://",
    ],
)
def test_build_post_url_rejects_invalid_or_unsafe_app_urls(base_url):
    with pytest.raises(ValueError):
        build_post_url(base_url, "123")


@pytest.mark.parametrize("post_id", ["", "abc", "12&admin=true", "1" * 1000])
def test_url_helpers_reject_malformed_post_ids(post_id):
    with pytest.raises(ValueError):
        build_post_url("https://reader.example", post_id)
    with pytest.raises(ValueError):
        post_query_params(post_id)


def test_post_query_params_returns_a_fresh_value_for_button_url_updates():
    first = post_query_params("123")
    second = post_query_params("456")

    assert first == {"post_id": "123"}
    assert second == {"post_id": "456"}
    assert first is not second


@pytest.mark.parametrize(
    "query_params, expected",
    [
        ({}, None),
        ({"unrelated": "123"}, None),
        ({"post_id": "123456789"}, "123456789"),
        ({"post_id": ["123456789"]}, "123456789"),
        (_QueryParams({"post_id": ["123456789"]}), "123456789"),
    ],
)
def test_requested_post_id_accepts_only_one_present_value(query_params, expected):
    assert requested_post_id(query_params) == expected


@pytest.mark.parametrize(
    "query_params",
    [
        {"post_id": None},
        {"post_id": 123},
        {"post_id": ""},
        {"post_id": " 123"},
        {"post_id": "123 "},
        {"post_id": "+123"},
        {"post_id": "-123"},
        {"post_id": "12.3"},
        {"post_id": "１２３"},
        {"post_id": "١٢٣"},
        {"post_id": "1" * 1000},
        {"post_id": ["123", "456"]},
        _QueryParams({"post_id": ["123", "456"]}),
        _QueryParams({"post_id": ["123", "123"]}),
    ],
)
def test_requested_post_id_rejects_malformed_or_repeated_values(query_params):
    assert requested_post_id(query_params) is None


def test_select_post_prefers_requested_then_current_then_first_rss_post():
    posts = [
        {"post_id": "100", "title": "Newest"},
        {"post_id": "200", "title": "Current"},
        {"post_id": "300", "title": "Linked"},
    ]

    assert select_post(posts, "300", current_id="200") is posts[2]
    assert select_post(posts, "999", current_id="200") is posts[1]
    assert select_post(posts, None, current_id="200") is posts[1]
    assert select_post(posts, "999", current_id="888") is posts[0]
    assert select_post(posts, None) is posts[0]
    assert select_post([], "300", current_id="200") is None


def test_telegram_notification_links_to_the_detected_post_and_escapes_the_href(
    monkeypatch, tmp_path,
):
    post_id = "123456789"
    app_base = "https://reader.example/read'er?utm_source=telegram&post_id=old#listen"
    blog_link = f"https://blog.naver.com/ranto28/{post_id}"
    messages = []

    monkeypatch.setattr(check_new_posts, "BOT_TOKEN", "test-token")
    monkeypatch.setattr(check_new_posts, "CHAT_ID", "test-chat")
    monkeypatch.setattr(check_new_posts, "STREAMLIT_APP_URL", app_base)
    monkeypatch.setattr(check_new_posts, "LAST_ID_FILE", str(tmp_path / "last_post_id.txt"))
    monkeypatch.setattr(
        check_new_posts.feedparser,
        "parse",
        lambda _url: SimpleNamespace(
            entries=[SimpleNamespace(link=blog_link, title="새 글")]
        ),
    )
    monkeypatch.setattr(
        check_new_posts,
        "scrape_post_content",
        lambda _post_id: ([{"type": "p", "text": "본문"}], None),
    )
    monkeypatch.setattr(check_new_posts, "send_telegram_message", messages.append)

    check_new_posts.main()

    expected_url = build_post_url(app_base, post_id)
    assert len(messages) == 1
    assert f"href='{escape(expected_url, quote=True)}'" in messages[0]
    assert "&amp;" in messages[0]
    parser = _Links()
    parser.feed(messages[0])
    assert expected_url in parser.hrefs
    assert (tmp_path / "last_post_id.txt").read_text() == post_id


def test_telegram_body_truncation_keeps_entities_and_quote_tags_complete():
    body, truncated = check_new_posts.format_telegram_body(
        [{"type": "quote", "text": "<&'\"" * 1000}],
        max_length=127,
    )

    assert truncated
    assert len(body) <= 127
    assert body.startswith("<blockquote>")
    assert body.endswith("</blockquote>")
    assert not body.removesuffix("</blockquote>").endswith("&")
    parser = _Links()
    parser.feed(body)


def test_failed_telegram_delivery_does_not_record_the_post(monkeypatch, tmp_path):
    post_id = "123456789"
    last_id_file = tmp_path / "last_post_id.txt"
    monkeypatch.setattr(check_new_posts, "BOT_TOKEN", "test-token")
    monkeypatch.setattr(check_new_posts, "CHAT_ID", "test-chat")
    monkeypatch.setattr(check_new_posts, "STREAMLIT_APP_URL", "https://reader.example")
    monkeypatch.setattr(check_new_posts, "LAST_ID_FILE", str(last_id_file))
    monkeypatch.setattr(
        check_new_posts.feedparser,
        "parse",
        lambda _url: SimpleNamespace(
            entries=[
                SimpleNamespace(
                    link=f"https://blog.naver.com/ranto28/{post_id}",
                    title="새 글",
                )
            ]
        ),
    )
    monkeypatch.setattr(
        check_new_posts,
        "scrape_post_content",
        lambda _post_id: ([{"type": "p", "text": "본문"}], None),
    )
    monkeypatch.setattr(
        check_new_posts,
        "send_telegram_message",
        lambda _message: (_ for _ in ()).throw(RuntimeError("delivery failed")),
    )

    with pytest.raises(RuntimeError, match="delivery failed"):
        check_new_posts.main()

    assert not last_id_file.exists()
