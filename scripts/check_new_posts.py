import os
import requests
import feedparser
from bs4 import BeautifulSoup
import re

# Configuration
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "7161645581:AAGPm5qc6CTSy9OkU_GduAYFcLzo9twMtzs")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6261478342")
STREAMLIT_APP_URL = os.environ.get("STREAMLIT_APP_URL", "")

BLOG_ID = "ranto28"
RSS_URL = f"https://rss.blog.naver.com/{BLOG_ID}.xml"
LAST_ID_FILE = os.path.join("data", "last_post_id.txt")

def escape_html(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def scrape_post_content(post_id):
    url = f"https://blog.naver.com/PostView.naver?blogId={BLOG_ID}&logNo={post_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.75 Safari/537.36"
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        response.encoding = 'utf-8'
        soup = BeautifulSoup(response.text, 'html.parser')
        
        main_container = soup.find(class_='se-main-container')
        if not main_container:
            main_container = soup.find(id='postViewArea')
            
        if not main_container:
            return None, "본문 내용을 찾을 수 없습니다."
            
        elements = []
        for element in main_container.find_all(recursive=True):
            classes = element.get('class', [])
            
            if 'se-text-paragraph' in classes:
                text = element.get_text().strip()
                if text and not element.find(class_='se-text-paragraph'):
                    elements.append({"type": "p", "text": text})
            elif 'se-quote-text' in classes:
                text = element.get_text().strip()
                if text:
                    elements.append({"type": "quote", "text": text})
            elif 'se-list-item' in classes:
                text = element.get_text().strip()
                if text:
                    elements.append({"type": "list-item", "text": text})
                    
        # Fallback if specific classes not found
        if not elements:
            p_tags = main_container.find_all('p')
            if p_tags:
                for p in p_tags:
                    text = p.get_text().strip()
                    if text:
                        elements.append({"type": "p", "text": text})
            else:
                lines = [l.strip() for l in main_container.get_text().split('\n') if l.strip()]
                for line in lines:
                    elements.append({"type": "p", "text": line})
                    
        # Remove consecutive duplicates & filter out image credits (containing ©)
        cleaned_elements = []
        for el in elements:
            el["text"] = el["text"].replace('\xa0', ' ').replace('\u200b', '')
            el["text"] = re.sub(r'\s+', ' ', el["text"]).strip()
            if el["text"]:
                if '©' in el["text"]:
                    continue
                if not cleaned_elements or cleaned_elements[-1]["text"] != el["text"]:
                    cleaned_elements.append(el)
                    
        return cleaned_elements, None
    except Exception as e:
        return None, str(e)

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

    if last_post_id != post_id:
        print(f"New post detected! Title: {latest_entry.title}")
        
        # Scrape and format post content
        body_text = ""
        elements, scrape_err = scrape_post_content(post_id)
        if elements:
            formatted_paras = []
            for el in elements:
                text = escape_html(el["text"])
                if el["type"] == "quote":
                    formatted_paras.append(f"<blockquote>{text}</blockquote>")
                elif el["type"] == "list-item":
                    formatted_paras.append(f"• {text}")
                else:
                    formatted_paras.append(text)
            
            full_body = "\n\n".join(formatted_paras)
            # Telegram character limit is 4096, reserve space for headers and links (max 3000 chars)
            if len(full_body) > 3000:
                body_text = f"\n\n📖 <b>본문 내용 (일부):</b>\n{full_body[:3000]}...\n\n<i>(본문이 길어 일부 생략되었습니다.)</i>"
            else:
                body_text = f"\n\n📖 <b>본문 내용:</b>\n{full_body}"
        else:
            body_text = f"\n\n⚠️ <i>본문 내용을 가져올 수 없습니다. ({scrape_err or '본문 비어있음'})</i>"

        # Build the telegram notification text
        app_link_text = ""
        if STREAMLIT_APP_URL:
            app_url = STREAMLIT_APP_URL if STREAMLIT_APP_URL.startswith("http") else f"https://{STREAMLIT_APP_URL}"
            app_link_text = f"\n🎙️ <b>오디오 리더에서 듣기:</b> <a href='{app_url}'>바로가기</a>"

        message = (
            f"🔔 <b>새로운 블로그 글이 업로드되었습니다!</b>\n\n"
            f"✍️ <b>제목:</b> {escape_html(latest_entry.title)}\n"
            f"🔗 <b>네이버 블로그 링크:</b> <a href='{link}'>원문 읽기</a>"
            f"{app_link_text}"
            f"{body_text}"
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
