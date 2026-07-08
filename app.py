import streamlit as st
import feedparser
import requests
from bs4 import BeautifulSoup
import re
import os
import email.utils
import asyncio
import edge_tts
from gtts import gTTS

# 1. Page Configuration (Must be first)
st.set_page_config(
    page_title="Naver Blog TTS Reader",
    page_icon="🎙️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# 2. Theme State Management
if "theme" not in st.session_state:
    st.session_state.theme = "dark"

def toggle_theme():
    st.session_state.theme = "light" if st.session_state.theme == "dark" else "dark"

IS_DARK = st.session_state.theme == "dark"

# Initialize TTS settings in session state to prevent intermediate slider run conflicts
if "tts_voice" not in st.session_state:
    st.session_state.tts_voice = "ko-KR-InJoonNeural (남성 - 차분함, 기본)"
if "tts_speed" not in st.session_state:
    st.session_state.tts_speed = "1.0x (보통)"
if "tts_volume" not in st.session_state:
    st.session_state.tts_volume = 100

# 3. CSS Design System (Theme-aware UI)
bg_color = "#09090b" if IS_DARK else "#ffffff"
bg_subtle = "#0c0c0f" if IS_DARK else "#f9fafb"
card_color = "#0c0c0f" if IS_DARK else "#ffffff"
card_hover = "#131316" if IS_DARK else "#f4f4f5"
border_color = "#1e1e24" if IS_DARK else "#e4e4e7"
border_subtle = "#16161a" if IS_DARK else "#f0f0f2"
text_color = "#fafafa" if IS_DARK else "#09090b"
text_muted = "#71717a" if IS_DARK else "#71717a"
text_dim = "#52525b" if IS_DARK else "#a1a1aa"
accent_color = "#3b82f6" if IS_DARK else "#2563eb"
accent_hover = "#2563eb" if IS_DARK else "#1d4ed8"

css = f"""
<style>
/* Hide Streamlit chrome */
header[data-testid="stHeader"], footer, [data-testid="stToolbar"],
[data-testid="stDecoration"], [data-testid="stStatusWidget"], .stDeployButton,
div[data-testid="stSidebarCollapsedControl"] {{
    display: none !important;
}}

/* Global app styling */
html, body, [data-testid="stAppViewContainer"], [data-testid="stApp"], .main, .block-container, section[data-testid="stMain"] {{
    background-color: {bg_color} !important;
    color: {text_color} !important;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif !important;
}}

.block-container {{
    padding: 1.5rem 2rem 2rem !important;
    max-width: 1400px !important;
}}

/* Brand Styling */
.brand-container {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding-bottom: 1.25rem;
    border-bottom: 1px solid {border_color};
    margin-bottom: 1.5rem;
}}
.brand-title {{
    font-size: 1.5rem;
    font-weight: 700;
    color: {text_color};
    letter-spacing: -0.02em;
    display: flex;
    align-items: center;
    gap: 8px;
}}
.brand-title span {{
    color: {accent_color};
}}

/* Custom styling for list buttons */
div[data-testid="stButton"] button {{
    text-align: left !important;
    white-space: normal !important;
    word-break: break-all !important;
    height: auto !important;
    padding: 0.75rem 1rem !important;
    background-color: {card_color} !important;
    border: 1px solid {border_color} !important;
    color: {text_color} !important;
    font-size: 0.85rem !important;
    line-height: 1.4 !important;
    border-radius: 8px !important;
    transition: all 0.2s ease !important;
}}
div[data-testid="stButton"] button:hover {{
    border-color: {accent_color} !important;
    background-color: {card_hover} !important;
    color: {accent_color} !important;
}}

/* Reader UI styling */
.reader-card {{
    background-color: {card_color};
    border: 1px solid {border_color};
    border-radius: 12px;
    padding: 2rem;
    min-height: 500px;
}}
.reader-header {{
    border-bottom: 1px solid {border_subtle};
    padding-bottom: 1.25rem;
    margin-bottom: 1.5rem;
}}
.reader-title {{
    font-size: 1.75rem;
    font-weight: 700;
    color: {text_color};
    line-height: 1.3;
    margin-bottom: 0.5rem;
    letter-spacing: -0.01em;
}}
.reader-meta {{
    font-size: 0.85rem;
    color: {text_muted};
    display: flex;
    gap: 15px;
}}
.reader-meta-item {{
    display: flex;
    align-items: center;
    gap: 4px;
}}

/* TTS Config Panel */
.tts-panel {{
    background-color: {bg_subtle};
    border: 1px solid {border_color};
    border-radius: 8px;
    padding: 1rem;
    margin-bottom: 1.5rem;
}}

/* Content block styles */
.reader-body {{
    font-size: 1.05rem;
    line-height: 1.8;
    color: {text_color};
}}
.reader-paragraph {{
    margin-bottom: 1.25rem;
    text-align: justify;
}}
.reader-quote {{
    border-left: 4px solid {accent_color};
    padding-left: 1rem;
    font-style: italic;
    color: {text_muted};
    margin: 1.5rem 0;
}}
.reader-list-item {{
    margin-left: 1rem;
    margin-bottom: 0.5rem;
}}
</style>
"""
st.markdown(css, unsafe_allow_html=True)

# 4. Helpers for Fetching & Parsing Blog
BLOG_ID = "ranto28"
RSS_URL = f"https://rss.blog.naver.com/{BLOG_ID}.xml"

@st.cache_data(ttl=300) # Cache list for 5 minutes
def fetch_latest_posts():
    try:
        feed = feedparser.parse(RSS_URL)
        posts = []
        for entry in feed.entries:
            # Extract post_id from link
            link = entry.link
            post_id = None
            parts = link.split('/')
            if len(parts) >= 5:
                post_id = parts[4].split('?')[0]
            
            # Format published date
            pub_date = ""
            try:
                dt = email.utils.parsedate_to_datetime(entry.published)
                pub_date = dt.strftime("%b %d, %Y %H:%M")
            except Exception:
                pub_date = entry.published
                
            posts.append({
                "title": entry.title,
                "link": link,
                "post_id": post_id,
                "published": pub_date,
                "description": entry.description
            })
        return posts, None
    except Exception as e:
        return None, str(e)

@st.cache_data(ttl=1800) # Cache content for 30 minutes
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
            return None, "본문 내용을 찾을 수 없습니다 (Naver SmartEditor 파싱 실패)."
            
        elements = []
        
        # Traverse direct children of the container to keep elements in order
        # Look for components and paragraphs
        for element in main_container.find_all(recursive=True):
            classes = element.get('class', [])
            
            # Only read leaves or distinct components to prevent double-dipping nested divs
            if 'se-text-paragraph' in classes:
                # Normal paragraph text
                text = element.get_text().strip()
                if text and not element.find(class_='se-text-paragraph'):
                    elements.append({"type": "p", "text": text})
            elif 'se-quote-text' in classes:
                # Blockquotes
                text = element.get_text().strip()
                if text:
                    elements.append({"type": "quote", "text": text})
            elif 'se-list-item' in classes:
                # Bullet list items
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
                # Raw layout lines split
                lines = [l.strip() for l in main_container.get_text().split('\n') if l.strip()]
                for line in lines:
                    elements.append({"type": "p", "text": line})
                    
        # Remove consecutive duplicates
        cleaned_elements = []
        for el in elements:
            el["text"] = el["text"].replace('\xa0', ' ').replace('\u200b', '')
            el["text"] = re.sub(r'\s+', ' ', el["text"]).strip()
            if el["text"]:
                if not cleaned_elements or cleaned_elements[-1]["text"] != el["text"]:
                    cleaned_elements.append(el)
                    
        return cleaned_elements, None
    except Exception as e:
        return None, str(e)

# 5. TTS Helpers
import traceback

async def generate_edge_tts(text, output_path, voice, rate, volume):
    communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume)
    await communicate.save(output_path)

def get_tts_audio(text, post_id, voice, speed_rate, volume_rate="+0%"):
    os.makedirs(".cache", exist_ok=True)
    
    # Generate unique cached file name
    safe_voice = voice.replace(":", "_").replace("-", "_")
    safe_rate = speed_rate.replace("+", "p").replace("-", "m").replace("%", "")
    safe_volume = volume_rate.replace("+", "p").replace("-", "m").replace("%", "")
    audio_filename = f"audio_{post_id}_{safe_voice}_{safe_rate}_{safe_volume}.mp3"
    audio_path = os.path.join(".cache", audio_filename)
    
    if os.path.exists(audio_path):
        return audio_path, None
        
    try:
        # Run async TTS generator synchronously
        asyncio.run(generate_edge_tts(text, audio_path, voice, speed_rate, volume_rate))
        return audio_path, None
    except Exception as e:
        err_msg = f"Edge TTS 오류: {str(e)}"
        # Fallback to standard Google TTS
        try:
            fallback_filename = f"audio_{post_id}_gtts.mp3"
            fallback_path = os.path.join(".cache", fallback_filename)
            if os.path.exists(fallback_path):
                return fallback_path, f"Edge TTS 오류로 Google TTS 캐시를 로드했습니다. ({err_msg})"
            
            tts = gTTS(text=text, lang='ko')
            tts.save(fallback_path)
            return fallback_path, f"Edge TTS 오류로 Google TTS를 사용해 임시 생성했습니다. ({err_msg})"
        except Exception as fallback_err:
            fallback_err_msg = f"Fallback gTTS 오류: {str(fallback_err)}"
            return None, f"TTS 생성 실패!\n\n1. {err_msg}\n\n2. {fallback_err_msg}"

# 6. Streamlit Main Interface
# Header Row
st.markdown(f"""
<div class="brand-container">
    <div class="brand-title">🎙️ Naver Blog <span>Audio Reader</span></div>
</div>
""", unsafe_allow_html=True)

# Main Screen Layout
# We have a sidebar lists of posts on the left (column 1) and details on the right (column 2)
col_left, col_right = st.columns([5, 8])

# Fetch posts
posts, err = fetch_latest_posts()

if err:
    st.error(f"블로그 RSS 피드를 가져오는데 실패했습니다: {err}")
    st.stop()

if not posts:
    st.info("불러온 블로그 글이 없습니다.")
    st.stop()

# Track selection in Session State
if "selected_post" not in st.session_state:
    st.session_state.selected_post = posts[0]

if "list_expanded" not in st.session_state:
    st.session_state.list_expanded = True

# --- Left Column: Post List ---
with col_left:
    # Use expander with session state to let user collapse/expand the list (great for mobile)
    with st.expander("📝 최신 포스트 목록 (접기/펴기)", expanded=st.session_state.list_expanded):
        # Search / Filter
        search_query = st.text_input("🔍 글 검색", "", placeholder="제목을 입력하세요...")
        
        filtered_posts = posts
        if search_query:
            filtered_posts = [p for p in posts if search_query.lower() in p["title"].lower()]
            
        # Render custom HTML list
        for idx, post in enumerate(filtered_posts):
            is_selected = post["post_id"] == st.session_state.selected_post["post_id"]
            active_class = "post-card-active" if is_selected else ""
            
            # We can use st.button styled as card or handle click using streamlit keys
            button_label = f"[{post['published']}] {post['title']}"
            
            # Using Streamlit columns & expander or custom button list
            if st.button(
                f"{'▶ ' if is_selected else ''}{post['title']}\n({post['published']})",
                key=f"post_{post['post_id']}_{idx}",
                use_container_width=True,
            ):
                st.session_state.selected_post = post
                st.session_state.list_expanded = False  # Auto-collapse on mobile when selected
                st.rerun()

# --- Right Column: Reader & TTS ---
selected_post = st.session_state.selected_post

with col_right:
    st.markdown(f"### 📖 읽기 및 듣기")
    
    # Scrape post body
    post_elements, scrape_err = scrape_post_content(selected_post["post_id"])
    
    # Design of the Reading panel
    with st.container():
        # Top Header of Post
        st.markdown(f"""
        <div class="reader-header">
            <div class="reader-title">{selected_post['title']}</div>
            <div class="reader-meta">
                <div class="reader-meta-item">📅 {selected_post['published']}</div>
                <div class="reader-meta-item">🔗 <a href="{selected_post['link']}" target="_blank" style="color: {accent_color}; text-decoration: none;">원문 보기</a></div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        if scrape_err:
            st.error(scrape_err)
            st.stop()
            
        if not post_elements:
            st.warning("이 포스트는 본문 내용이 비어있거나 파싱할 수 없는 형식입니다.")
            st.stop()
            
        # Resolve currently selected TTS configurations from Session State
        voice_options = {
            "ko-KR-InJoonNeural (남성 - 차분함, 기본)": "ko-KR-InJoonNeural",
            "ko-KR-SunHiNeural (여성 - 선명함)": "ko-KR-SunHiNeural",
            "ko-KR-HyunminNeural (남성 - 친근함)": "ko-KR-HyunminNeural"
        }
        speed_options = {
            "0.9x": "-10%",
            "1.0x (보통)": "+0%",
            "1.1x": "+10%",
            "1.2x": "+20%",
            "1.3x": "+30%",
            "1.5x": "+50%"
        }
        
        voice_id = voice_options.get(st.session_state.tts_voice, "ko-KR-InJoonNeural")
        speed_rate = speed_options.get(st.session_state.tts_speed, "+0%")
        
        volume_val = st.session_state.tts_volume
        if volume_val == 100:
            volume_rate = "+0%"
        else:
            volume_rate = f"{volume_val - 100:+.0f}%"

        # TTS Settings Drawer/Expander inside a Form to buffer edits and prevent slider-dragging race conditions
        with st.expander("⚙️ TTS 음성, 속도 및 볼륨 설정", expanded=False):
            with st.form(key="tts_settings_form"):
                voice_col, speed_col, volume_col = st.columns(3)
                
                with voice_col:
                    voice_keys = list(voice_options.keys())
                    default_voice_idx = voice_keys.index(st.session_state.tts_voice) if st.session_state.tts_voice in voice_keys else 0
                    form_voice = st.selectbox(
                        "음성 선택 (Microsoft AI Voice)",
                        options=voice_keys,
                        index=default_voice_idx
                    )
                    
                with speed_col:
                    speed_keys = list(speed_options.keys())
                    default_speed_idx = speed_keys.index(st.session_state.tts_speed) if st.session_state.tts_speed in speed_keys else 1
                    form_speed = st.selectbox(
                        "재생 속도",
                        options=speed_keys,
                        index=default_speed_idx
                    )
                    
                with volume_col:
                    form_volume = st.slider(
                        "음량 조절",
                        min_value=50,
                        max_value=150,
                        value=st.session_state.tts_volume,
                        step=10,
                        format="%d%%"
                    )
                
                apply_button = st.form_submit_button(label="⚙️ 설정 적용하기", use_container_width=True)
                if apply_button:
                    st.session_state.tts_voice = form_voice
                    st.session_state.tts_speed = form_speed
                    st.session_state.tts_volume = form_volume
                    st.rerun()
        
        # Compile full text for TTS conversion
        # Join paragraphs with periods to ensure natural pausing
        full_text_list = []
        for el in post_elements:
            text = el["text"]
            # Ensure text ends with a punctuation mark for natural pause in TTS
            if not text.endswith(('.', '!', '?', '"', '”')):
                text += '.'
            full_text_list.append(text)
            
        full_text = " ".join(full_text_list)
        
        # Audio Player Section
        audio_status_placeholder = st.empty()
        
        # Audio file path
        audio_path, tts_warning = get_tts_audio(full_text, selected_post["post_id"], voice_id, speed_rate, volume_rate)
        
        if tts_warning:
            st.warning(tts_warning)
            
        if audio_path and os.path.exists(audio_path):
            # Render audio player nicely
            with open(audio_path, "rb") as f:
                audio_bytes = f.read()
                
            audio_col1, audio_col2 = st.columns([8, 2])
            with audio_col1:
                st.audio(audio_bytes, format="audio/mp3")
            with audio_col2:
                # Download button
                st.download_button(
                    label="💾 MP3 다운로드",
                    data=audio_bytes,
                    file_name=f"{selected_post['title'][:15]}.mp3",
                    mime="audio/mp3",
                    use_container_width=True
                )
        else:
            st.error("오디오 파일을 생성하거나 불러오는 중에 오류가 발생했습니다.")
            
        st.markdown("<hr style='border-color: {}; margin: 1.5rem 0;'>".format(border_subtle), unsafe_allow_html=True)
        
        # Render full text paragraphs nicely formatted
        body_html = '<div class="reader-body">'
        for el in post_elements:
            if el["type"] == "quote":
                body_html += f'<div class="reader-quote">{el["text"]}</div>'
            elif el["type"] == "list-item":
                body_html += f'<div class="reader-list-item">• {el["text"]}</div>'
            else:
                body_html += f'<div class="reader-paragraph">{el["text"]}</div>'
        body_html += '</div>'
        
        st.markdown(body_html, unsafe_allow_html=True)

# 7. Sidebar Theme Toggle Utility (Alternative representation)
st.sidebar.markdown("### ⚙️ 시스템 설정")
theme_label = "☀️ 라이트 모드" if IS_DARK else "🌙 다크 모드"
st.sidebar.button(theme_label, on_click=toggle_theme, use_container_width=True)

# Clear Cache button
if st.sidebar.button("🧹 캐시 지우기 (오디오 및 파싱 결과)", use_container_width=True):
    st.cache_data.clear()
    # Remove cached mp3 files
    if os.path.exists(".cache"):
        for f in os.listdir(".cache"):
            if f.endswith(".mp3"):
                try:
                    os.remove(os.path.join(".cache", f))
                except Exception:
                    pass
    st.sidebar.success("캐시가 초기화되었습니다.")
    st.rerun()
