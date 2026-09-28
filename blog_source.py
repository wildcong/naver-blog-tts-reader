"""Canonical Naver blog post metadata and content parsing."""

from __future__ import annotations

import re
import email.utils
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

import feedparser
import requests
from bs4 import BeautifulSoup


POST_VIEW_URL = "https://blog.naver.com/PostView.naver"
RSS_ROOT_URL = "https://rss.blog.naver.com"
_NAVER_BLOG_HOSTS = {"blog.naver.com", "m.blog.naver.com"}
_MAX_POST_ID_LENGTH = 20
_BLOG_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,50}\Z")
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/100.0.4896.75 Safari/537.36"
)


def _validated_post_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("post_id must be a string")
    if not 1 <= len(value) <= _MAX_POST_ID_LENGTH:
        raise ValueError("post_id has an invalid length")
    if not value.isascii() or not value.isdecimal():
        raise ValueError("post_id must contain ASCII digits only")
    return value


def _validated_blog_id(value: object) -> str:
    if not isinstance(value, str) or _BLOG_ID_PATTERN.fullmatch(value) is None:
        raise ValueError("blog_id contains unsupported characters")
    return value


def extract_post_id(link: object) -> str | None:
    """Extract a validated post ID from a canonical Naver blog link."""

    if not isinstance(link, str) or not link:
        return None

    try:
        parsed = urlsplit(link)
    except ValueError:
        return None

    if parsed.scheme not in {"http", "https"} or parsed.hostname not in _NAVER_BLOG_HOSTS:
        return None

    candidate = None
    if parsed.path.rstrip("/") == "/PostView.naver":
        values = parse_qs(parsed.query, keep_blank_values=True).get("logNo", [])
        if len(values) == 1:
            candidate = values[0]
    else:
        path_parts = [part for part in parsed.path.split("/") if part]
        if len(path_parts) == 2:
            candidate = path_parts[-1]

    try:
        return _validated_post_id(candidate)
    except ValueError:
        return None


def _link_matches_blog(link: str, blog_id: str) -> bool:
    parsed = urlsplit(link)
    if parsed.path.rstrip("/") == "/PostView.naver":
        values = parse_qs(parsed.query, keep_blank_values=True).get("blogId", [])
        linked_blog_id = values[0] if len(values) == 1 else ""
    else:
        path_parts = [part for part in parsed.path.split("/") if part]
        linked_blog_id = path_parts[0] if len(path_parts) == 2 else ""
    return bool(linked_blog_id) and linked_blog_id.casefold() == blog_id.casefold()


def parse_rss_feed(data: bytes | str, blog_id: str) -> list[dict[str, str]]:
    """Parse, validate, and newest-first sort a Naver RSS document."""

    safe_blog_id = _validated_blog_id(blog_id)
    feed = feedparser.parse(data)
    posts: list[tuple[float, int, dict[str, str]]] = []
    for index, entry in enumerate(feed.entries):
        link = str(getattr(entry, "link", ""))
        post_id = extract_post_id(link)
        if post_id is None or not _link_matches_blog(link, safe_blog_id):
            continue

        raw_published = str(getattr(entry, "published", ""))
        try:
            published_at = email.utils.parsedate_to_datetime(raw_published)
            sort_timestamp = published_at.timestamp()
            published = published_at.strftime("%b %d, %Y %H:%M")
        except (TypeError, ValueError, OverflowError):
            sort_timestamp = float("-inf")
            published = _clean_text(raw_published)

        title = _clean_text(getattr(entry, "title", "")) or f"블로그 글 {post_id}"
        posts.append(
            (
                sort_timestamp,
                index,
                {
                    "title": title,
                    "link": link,
                    "post_id": post_id,
                    "published": published,
                    "description": str(getattr(entry, "description", "")),
                },
            )
        )

    if not posts:
        raise RuntimeError("Naver RSS did not contain a valid post")

    posts.sort(key=lambda item: (-item[0], item[1]))
    return [post for _, _, post in posts]


def fetch_latest_posts(
    blog_id: str = "ranto28",
    timeout: float = 10,
) -> list[dict[str, str]]:
    """Fetch a Naver RSS feed with an explicit timeout."""

    safe_blog_id = quote(_validated_blog_id(blog_id), safe="")
    response = requests.get(
        f"{RSS_ROOT_URL}/{safe_blog_id}.xml",
        headers={"User-Agent": _USER_AGENT},
        timeout=timeout,
    )
    response.raise_for_status()
    return parse_rss_feed(response.content, str(blog_id))


def _clean_text(value: object) -> str:
    text = str(value or "").replace("\xa0", " ").replace("\u200b", "")
    return re.sub(r"\s+", " ", text).strip()


def _document_title(soup: BeautifulSoup) -> str:
    if not soup.title:
        return ""
    title = _clean_text(soup.title.get_text(" ", strip=True))
    return re.sub(r"\s*(?::|\||-)\s*네이버\s*블로그\s*$", "", title).strip()


def _canonical_title(soup: BeautifulSoup, post_id: str) -> str:
    og_title = soup.find("meta", attrs={"property": "og:title"})
    candidates = [
        og_title.get("content") if og_title else "",
        *(
            element.get_text(" ", strip=True)
            for selector in (".se-title-text", ".pcol1")
            if (element := soup.select_one(selector)) is not None
        ),
        _document_title(soup),
    ]
    for candidate in candidates:
        title = _clean_text(candidate)
        if title:
            return title
    return f"블로그 글 {post_id}"


def _content_elements(container: Any) -> list[dict[str, str]]:
    elements: list[dict[str, str]] = []

    for element in container.find_all(recursive=True):
        classes = element.get("class", [])
        element_type = None
        if "se-text-paragraph" in classes:
            if element.find(class_="se-text-paragraph"):
                continue
            element_type = "p"
        elif "se-quote-text" in classes:
            element_type = "quote"
        elif "se-list-item" in classes:
            element_type = "list-item"

        if element_type:
            text = _clean_text(element.get_text(" ", strip=True))
            if text:
                elements.append({"type": element_type, "text": text})

    if not elements:
        paragraphs = container.find_all("p")
        if paragraphs:
            elements = [
                {"type": "p", "text": text}
                for paragraph in paragraphs
                if (text := _clean_text(paragraph.get_text(" ", strip=True)))
            ]
        else:
            elements = [
                {"type": "p", "text": text}
                for line in container.get_text("\n").splitlines()
                if (text := _clean_text(line))
            ]

    cleaned: list[dict[str, str]] = []
    for element in elements:
        text = _clean_text(element["text"])
        if not text or "©" in text:
            continue
        if cleaned and cleaned[-1]["text"] == text:
            continue
        cleaned.append({"type": element["type"], "text": text})
    return cleaned


def parse_post_page(
    html: str,
    post_id: str,
    blog_id: str,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Parse canonical metadata and ordered readable content from a post page."""

    safe_post_id = _validated_post_id(post_id)
    safe_blog_id = _validated_blog_id(blog_id)
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one(".se-main-container") or soup.select_one("#postViewArea")
    if container is None:
        raise ValueError("Naver post content container was not found")

    publish_date = soup.select_one(".se_publishDate")
    metadata = {
        "title": _canonical_title(soup, safe_post_id),
        "link": f"https://blog.naver.com/{quote(safe_blog_id, safe='')}/{safe_post_id}",
        "post_id": safe_post_id,
        "published": _clean_text(
            publish_date.get_text(" ", strip=True) if publish_date else ""
        ),
    }
    return metadata, _content_elements(container)


def fetch_post(
    post_id: str,
    blog_id: str = "ranto28",
    timeout: float = 10,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Fetch and parse one Naver post through its fixed PostView endpoint."""

    safe_post_id = _validated_post_id(post_id)
    safe_blog_id = quote(_validated_blog_id(blog_id), safe="")
    url = f"{POST_VIEW_URL}?blogId={safe_blog_id}&logNo={safe_post_id}"
    response = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=timeout)
    response.raise_for_status()
    response.encoding = "utf-8"
    return parse_post_page(response.text, safe_post_id, str(blog_id))
