import argparse
from html import escape
import os
from pathlib import Path
import re
import tempfile

from blog_source import fetch_latest_posts, fetch_post
from navigation import build_post_url
from telegram_delivery import (
    TelegramReceipt,
    load_delivery_state,
    save_delivery_state,
    send_telegram_message as deliver_telegram_message,
)


BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
STREAMLIT_APP_URL = os.environ.get(
    "STREAMLIT_APP_URL",
    "https://blogtts-sh.streamlit.app",
).strip()

BLOG_ID = "ranto28"
LAST_ID_FILE = os.path.join("data", "last_post_id.txt")
DELIVERY_STATE_FILE = os.path.join("data", "telegram_delivery.json")
_POST_ID_PATTERN = re.compile(r"[0-9]{1,20}\Z")


def escape_html(text):
    return escape(str(text), quote=True)


def _escaped_prefix(text, max_length):
    """Escape complete characters without cutting through an HTML entity."""
    escaped_parts = []
    escaped_length = 0
    for character in str(text):
        escaped_character = escape_html(character)
        if escaped_length + len(escaped_character) > max_length:
            return "".join(escaped_parts), False
        escaped_parts.append(escaped_character)
        escaped_length += len(escaped_character)
    return "".join(escaped_parts), True


def format_telegram_body(elements, max_length=3000):
    """Build valid Telegram HTML within a conservative message budget."""
    parts = []
    rendered_length = 0

    for element in elements:
        separator = "\n\n" if parts else ""
        element_type = element.get("type")
        if element_type == "quote":
            prefix, suffix = "<blockquote>", "</blockquote>"
        elif element_type == "list-item":
            prefix, suffix = "• ", ""
        else:
            prefix, suffix = "", ""

        available = max_length - rendered_length - len(separator) - len(prefix) - len(suffix)
        if available <= 0:
            return "\n\n".join(parts), True

        safe_text, complete = _escaped_prefix(element.get("text", ""), available)
        if safe_text:
            parts.append(f"{prefix}{safe_text}{suffix}")
            rendered_length += len(separator) + len(prefix) + len(safe_text) + len(suffix)

        if not complete:
            return "\n\n".join(parts), True

    return "\n\n".join(parts), False


def send_telegram_message(text: str) -> TelegramReceipt:
    """Send to the configured chat and require a verified Telegram receipt."""
    return deliver_telegram_message(BOT_TOKEN, CHAT_ID, text)


def _last_delivered_post_id() -> str | None:
    state_path = Path(DELIVERY_STATE_FILE)
    if state_path.exists():
        state = load_delivery_state(state_path)
        if state is None:
            raise RuntimeError("Telegram delivery state is invalid")
        return state["post_id"]

    legacy_path = Path(LAST_ID_FILE)
    if not legacy_path.exists():
        return None
    post_id = legacy_path.read_text(encoding="utf-8").strip()
    if not _POST_ID_PATTERN.fullmatch(post_id):
        raise RuntimeError("Legacy notification state is invalid")
    return post_id


def _atomic_write_last_post_id(post_id: str) -> None:
    if not _POST_ID_PATTERN.fullmatch(post_id):
        raise ValueError("Invalid post ID")

    target = Path(LAST_ID_FILE)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
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
            temporary_file.write(post_id)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, target)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _notification_message(post, elements) -> str:
    full_body, truncated = format_telegram_body(elements)
    if truncated:
        body_text = (
            "\n\n📖 <b>본문 내용 (일부):</b>\n"
            f"{full_body}\n\n<i>(본문이 길어 일부 생략되었습니다.)</i>"
        )
    else:
        body_text = f"\n\n📖 <b>본문 내용:</b>\n{full_body}"

    app_url = build_post_url(
        STREAMLIT_APP_URL or "https://blogtts-sh.streamlit.app",
        post["post_id"],
    )
    return (
        "🔔 <b>새로운 블로그 글이 업로드되었습니다!</b>\n\n"
        f"✍️ <b>제목:</b> {escape_html(post['title'])}\n"
        f"🔗 <b>네이버 블로그 링크:</b> "
        f"<a href='{escape_html(post['link'])}'>원문 읽기</a>"
        "\n🎙️ <b>오디오 리더에서 듣기:</b> "
        f"<a href='{escape_html(app_url)}'>바로가기</a>"
        f"{body_text}"
    )


def main(*, force_resend: bool = False) -> TelegramReceipt | None:
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("Telegram delivery secrets are not configured")

    print("Fetching RSS feed...")
    latest_feed_post = fetch_latest_posts(BLOG_ID)[0]
    post_id = latest_feed_post["post_id"]
    last_post_id = _last_delivered_post_id()
    print(f"Latest post ID on feed: {post_id}")
    print(f"Last delivery-state post ID: {last_post_id}")

    if not force_resend and last_post_id == post_id:
        print("No new posts detected.")
        return None

    canonical_post, elements = fetch_post(post_id, BLOG_ID)
    if not elements:
        raise RuntimeError("Naver post body is empty")

    action = "Forced resend" if force_resend else "New post detected"
    print(f"{action}! Title: {canonical_post['title']}")
    receipt = send_telegram_message(_notification_message(canonical_post, elements))
    print(
        "Telegram delivery verified: "
        f"message_id={receipt.message_id}, "
        f"chat={receipt.chat_fingerprint[:19]}"
    )

    # Advance state only after Telegram confirms the exact destination and
    # returns a message ID. Both files are committed by the workflow.
    _atomic_write_last_post_id(post_id)
    save_delivery_state(
        DELIVERY_STATE_FILE,
        post_id,
        canonical_post["title"],
        receipt,
    )
    print("Updated verified delivery state.")
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force-resend",
        action="store_true",
        help="Resend the current newest post and replace its delivery receipt.",
    )
    arguments = parser.parse_args()
    main(force_resend=arguments.force_resend)
