"""Tests for canonical Naver post metadata and body parsing."""

from types import SimpleNamespace

import pytest

import blog_source


SMART_EDITOR_HTML = """
<!doctype html>
<html>
  <head>
    <title>문서 제목 : 네이버 블로그</title>
    <meta property="og:title" content="OG &amp; 정확한 제목">
  </head>
  <body>
    <div class="se-title-text">화면 제목</div>
    <span class="se_publishDate"> 2026. 9. 28. 00:10 </span>
    <div class="se-main-container">
      <p class="se-text-paragraph"><span>첫&nbsp; 문단 &amp; &lt;태그&gt;</span></p>
      <div class="se-quote-text">
        <p class="se-text-paragraph"><span>인용문</span></p>
      </div>
      <div class="se-list-item">
        <p class="se-text-paragraph"><span>목록 항목</span></p>
      </div>
      <p class="se-text-paragraph">중복 문장</p>
      <p class="se-text-paragraph">중복 문장</p>
      <p class="se-text-paragraph">사진 © 제공자</p>
    </div>
  </body>
</html>
"""


@pytest.mark.parametrize(
    "link, expected",
    [
        ("https://blog.naver.com/ranto28/224423696087?fromRss=true", "224423696087"),
        ("https://m.blog.naver.com/ranto28/224423696087", "224423696087"),
        (
            "https://blog.naver.com/PostView.naver?blogId=ranto28&logNo=224423696087",
            "224423696087",
        ),
        ("https://example.com/ranto28/224423696087", None),
        ("https://blog.naver.com/ranto28/not-a-post", None),
        ("https://blog.naver.com/ranto28/１２３", None),
        ("https://blog.naver.com/ranto28/" + "1" * 21, None),
        (None, None),
    ],
)
def test_extract_post_id_accepts_only_valid_naver_links(link, expected):
    assert blog_source.extract_post_id(link) == expected


def test_parse_rss_validates_links_and_sorts_by_published_date():
    rss = b"""<?xml version='1.0' encoding='UTF-8'?>
    <rss version='2.0'><channel>
      <item><title>Older</title><link>https://blog.naver.com/ranto28/100</link>
        <pubDate>Sun, 27 Sep 2026 00:10:00 +0900</pubDate><description>old</description></item>
      <item><title>Wrong host</title><link>https://example.com/ranto28/999</link>
        <pubDate>Tue, 29 Sep 2026 00:10:00 +0900</pubDate></item>
      <item><title>Wrong blog</title><link>https://blog.naver.com/someone_else/998</link>
        <pubDate>Tue, 29 Sep 2026 00:09:00 +0900</pubDate></item>
      <item><title> Newest </title><link>https://blog.naver.com/ranto28/200?fromRss=true</link>
        <pubDate>Mon, 28 Sep 2026 00:10:00 +0900</pubDate><description>new</description></item>
    </channel></rss>"""

    assert blog_source.parse_rss_feed(rss, "ranto28") == [
        {
            "title": "Newest",
            "link": "https://blog.naver.com/ranto28/200?fromRss=true",
            "post_id": "200",
            "published": "Sep 28, 2026 00:10",
            "description": "new",
        },
        {
            "title": "Older",
            "link": "https://blog.naver.com/ranto28/100",
            "post_id": "100",
            "published": "Sep 27, 2026 00:10",
            "description": "old",
        },
    ]


def test_parse_rss_rejects_a_feed_without_valid_posts():
    with pytest.raises(RuntimeError, match="valid post"):
        blog_source.parse_rss_feed("<rss><channel></channel></rss>", "ranto28")


def test_fetch_latest_posts_uses_timeout_and_status(monkeypatch):
    calls = []

    class Response:
        content = b"""<rss version='2.0'><channel><item><title>Title</title>
        <link>https://blog.naver.com/ranto28/123</link>
        <pubDate>Tue, 29 Sep 2026 00:10:00 +0900</pubDate>
        </item></channel></rss>"""

        def raise_for_status(self):
            calls.append("status")

    def fake_get(url, *, headers, timeout):
        calls.append((url, headers, timeout))
        return Response()

    monkeypatch.setattr(blog_source.requests, "get", fake_get)

    posts = blog_source.fetch_latest_posts("ranto28", timeout=4.5)

    assert calls[0][0] == "https://rss.blog.naver.com/ranto28.xml"
    assert calls[0][1]["User-Agent"].startswith("Mozilla/5.0")
    assert calls[0][2] == 4.5
    assert calls[1] == "status"
    assert posts[0]["post_id"] == "123"


def test_parse_current_smart_editor_metadata_and_clean_elements():
    metadata, elements = blog_source.parse_post_page(
        SMART_EDITOR_HTML,
        "224423696087",
        "ranto28",
    )

    assert metadata == {
        "title": "OG & 정확한 제목",
        "link": "https://blog.naver.com/ranto28/224423696087",
        "post_id": "224423696087",
        "published": "2026. 9. 28. 00:10",
    }
    assert elements == [
        {"type": "p", "text": "첫 문단 & <태그>"},
        {"type": "quote", "text": "인용문"},
        {"type": "list-item", "text": "목록 항목"},
        {"type": "p", "text": "중복 문장"},
    ]


@pytest.mark.parametrize(
    "head, expected",
    [
        ("<div class='se-title-text'> Smart 제목 </div>", "Smart 제목"),
        ("<div class='pcol1'> 구형 제목 </div>", "구형 제목"),
        ("<title> 문서 제목 : 네이버 블로그 </title>", "문서 제목"),
        ("", "블로그 글 123"),
    ],
)
def test_title_fallback_order_after_missing_og_title(head, expected):
    html = f"<html><head>{head}</head><body><div id='postViewArea'><p>본문</p></div></body></html>"

    metadata, elements = blog_source.parse_post_page(html, "123", "ranto28")

    assert metadata["title"] == expected
    assert elements == [{"type": "p", "text": "본문"}]


def test_parse_post_page_rejects_a_missing_content_container():
    with pytest.raises(ValueError, match="content container"):
        blog_source.parse_post_page(
            "<html><head><meta property='og:title' content='제목'></head></html>",
            "123",
            "ranto28",
        )


@pytest.mark.parametrize(
    "post_id",
    [None, "", "abc", "123 ", "+123", "１２３", "١٢٣", "1" * 21],
)
def test_fetch_post_rejects_invalid_ids_before_network(monkeypatch, post_id):
    def unexpected_request(*_args, **_kwargs):
        raise AssertionError("network must not be called")

    monkeypatch.setattr(blog_source.requests, "get", unexpected_request)

    with pytest.raises(ValueError):
        blog_source.fetch_post(post_id)


@pytest.mark.parametrize("blog_id", ["", "ranto 28", "ranto/28", "라운드28"])
def test_fetchers_reject_invalid_blog_ids_before_network(monkeypatch, blog_id):
    def unexpected_request(*_args, **_kwargs):
        raise AssertionError("network must not be called")

    monkeypatch.setattr(blog_source.requests, "get", unexpected_request)

    with pytest.raises(ValueError):
        blog_source.fetch_latest_posts(blog_id)
    with pytest.raises(ValueError):
        blog_source.fetch_post("123", blog_id=blog_id)


def test_fetch_post_uses_fixed_post_view_url_timeout_status_and_utf8(monkeypatch):
    calls = []

    class Response:
        text = SMART_EDITOR_HTML
        encoding = None

        def raise_for_status(self):
            calls.append(("raise_for_status", self.encoding))

    response = Response()

    def fake_get(url, *, headers, timeout):
        calls.append(("get", url, headers, timeout))
        return response

    monkeypatch.setattr(blog_source.requests, "get", fake_get)

    metadata, elements = blog_source.fetch_post(
        "224423696087",
        blog_id="ranto_28",
        timeout=3.5,
    )

    method, url, headers, timeout = calls[0]
    assert method == "get"
    assert url == (
        "https://blog.naver.com/PostView.naver?"
        "blogId=ranto_28&logNo=224423696087"
    )
    assert headers["User-Agent"].startswith("Mozilla/5.0")
    assert timeout == 3.5
    assert calls[1] == ("raise_for_status", None)
    assert response.encoding == "utf-8"
    assert metadata["link"] == "https://blog.naver.com/ranto_28/224423696087"
    assert elements[0] == {"type": "p", "text": "첫 문단 & <태그>"}
