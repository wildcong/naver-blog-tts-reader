import os
import requests
import feedparser

# Configuration
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "7161645581:AAGPm5qc6CTSy9OkU_GduAYFcLzo9twMtzs")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6261478342")
STREAMLIT_APP_URL = os.environ.get("STREAMLIT_APP_URL", "")

BLOG_ID = "ranto28"
RSS_URL = f"https://rss.blog.naver.com/{BLOG_ID}.xml"
LAST_ID_FILE = os.path.join("data", "last_post_id.txt")

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        print("Telegram message sent successfully.")
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")

def main():
    if not CHAT_ID:
        print("TELEGRAM_CHAT_ID is not configured.")
        return

    print("Fetching RSS feed...")
    feed = feedparser.parse(RSS_URL)
    if not feed.entries:
        print("Failed to fetch RSS feed or feed is empty.")
        return

    latest_entry = feed.entries[0]
    link = latest_entry.link
    post_id = None
    parts = link.split('/')
    if len(parts) >= 5:
        post_id = parts[4].split('?')[0]

    if not post_id:
        print(f"Failed to parse post ID from link: {link}")
        return

    print(f"Latest post ID on feed: {post_id}")

    # Check last post ID
    last_post_id = None
    if os.path.exists(LAST_ID_FILE):
        with open(LAST_ID_FILE, "r") as f:
            last_post_id = f.read().strip()
    
    print(f"Last recorded post ID: {last_post_id}")

    # If it's a new post or there's no recorded post ID yet
    # Note: If last_post_id is None, it's the first time running. 
    # Let's save the current post ID, but don't flood notification unless specified, or notify it anyway.
    # To be useful, let's trigger a notification if they are different.
    if last_post_id != post_id:
        print(f"New post detected! Title: {latest_entry.title}")
        
        # Build the telegram notification text
        app_link_text = ""
        if STREAMLIT_APP_URL:
            # Ensure it starts with http
            app_url = STREAMLIT_APP_URL if STREAMLIT_APP_URL.startswith("http") else f"https://{STREAMLIT_APP_URL}"
            app_link_text = f"\n🎙️ <b>오디오 리더에서 듣기:</b> <a href='{app_url}'>바로가기</a>"

        message = (
            f"🔔 <b>새로운 블로그 글이 업로드되었습니다!</b>\n\n"
            f"✍️ <b>제목:</b> {latest_entry.title}\n"
            f"🔗 <b>네이버 블로그 링크:</b> <a href='{link}'>원문 읽기</a>"
            f"{app_link_text}"
        )
        
        send_telegram_message(message)
        
        # Save the new post ID
        os.makedirs(os.path.dirname(LAST_ID_FILE), exist_ok=True)
        with open(LAST_ID_FILE, "w") as f:
            f.write(post_id)
        print("Updated last post ID file.")
    else:
        print("No new posts detected.")

if __name__ == "__main__":
    main()
