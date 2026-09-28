import streamlit as st
import os
import asyncio
import base64
import json
import edge_tts
from gtts import gTTS
from html import escape

from auth import require_access
from blog_source import fetch_latest_posts as load_latest_posts
from blog_source import fetch_post
from navigation import post_query_params, requested_post_id, select_post

# 1. Page Configuration (Must be first)
st.set_page_config(
    page_title="Naver Blog TTS Reader",
    page_icon="🎙️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Authenticate before fetching posts, producing audio, or rendering private UI.
require_access()

# 2. Theme State Management
if st.session_state.get("theme") not in {"dark", "light"}:
    st.session_state.theme = "dark"

def toggle_theme():
    st.session_state.theme = "light" if st.session_state.theme == "dark" else "dark"

IS_DARK = st.session_state.theme == "dark"

# Initialize TTS settings in session state to prevent intermediate slider run conflicts
if "tts_voice" not in st.session_state:
    st.session_state.tts_voice = "ko-KR-InJoonNeural (남성 - 차분함, 기본)"
if "tts_speed" not in st.session_state:
    st.session_state.tts_speed = "1.0x (보통)"

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
    color-scheme: {"dark" if IS_DARK else "light"};
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif !important;
}}

.block-container {{
    padding: 1.5rem 2rem 2rem !important;
    max-width: 1400px !important;
}}

/* Brand Styling */
.brand-divider {{
    border-bottom: 1px solid {border_color};
    margin-bottom: 1.5rem;
    padding-top: 0.75rem;
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

@st.cache_data(ttl=300, max_entries=2)
def fetch_latest_posts():
    try:
        return load_latest_posts(BLOG_ID), None
    except Exception as exc:
        return None, str(exc)


@st.cache_data(ttl=1800, max_entries=100)
def fetch_post_details(post_id):
    try:
        metadata, elements = fetch_post(post_id, BLOG_ID)
        return metadata, elements, None
    except Exception as exc:
        return None, None, str(exc)

# 5. TTS Helpers
async def generate_edge_tts(text, output_path, voice, rate):
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(output_path)

def get_tts_audio(text, post_id, voice, speed_rate):
    os.makedirs(".cache", exist_ok=True)

    # Generate unique cached file name
    safe_voice = voice.replace(":", "_").replace("-", "_")
    safe_rate = speed_rate.replace("+", "p").replace("-", "m").replace("%", "")
    audio_filename = f"audio_{post_id}_{safe_voice}_{safe_rate}.mp3"
    audio_path = os.path.join(".cache", audio_filename)

    if os.path.exists(audio_path):
        return audio_path, None

    try:
        # Run async TTS generator synchronously
        asyncio.run(generate_edge_tts(text, audio_path, voice, speed_rate))
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
header_title, header_theme = st.columns([4, 1], vertical_alignment="center")
with header_title:
    st.markdown(f"""
    <div class="brand-title">🎙️ Naver Blog <span>Audio Reader</span></div>
    """, unsafe_allow_html=True)
with header_theme:
    theme_label = "☀️ 라이트" if IS_DARK else "🌙 다크"
    st.button(
        theme_label,
        key="theme_toggle",
        help="화면 색상 모드 전환",
        on_click=toggle_theme,
        width="stretch",
    )
st.markdown('<div class="brand-divider"></div>', unsafe_allow_html=True)

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

# Honor an explicit post URL; otherwise always follow the newest RSS post.
# This prevents a previous browser session from making an older title look like
# the newest post. RSS only exposes 50 posts, so a missing deep link is resolved
# against the canonical PostView page before rendering a placeholder.
linked_post_id = requested_post_id(st.query_params)
preloaded_post = None
if linked_post_id and not any(post["post_id"] == linked_post_id for post in posts):
    metadata, elements, direct_error = fetch_post_details(linked_post_id)
    preloaded_post = (metadata, elements, direct_error)
    direct_post = metadata or {
        "title": f"블로그 글 {linked_post_id}",
        "link": f"https://blog.naver.com/{BLOG_ID}/{linked_post_id}",
        "post_id": linked_post_id,
        "published": "",
    }
    posts = [
        {
            **direct_post,
            "description": "",
        },
        *posts,
    ]

st.session_state.selected_post = select_post(
    posts,
    linked_post_id,
)

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
                width="stretch",
            ):
                st.session_state.selected_post = post
                st.session_state.list_expanded = False  # Auto-collapse on mobile when selected
                st.query_params.update(post_query_params(post["post_id"]))
                st.rerun()

# --- Right Column: Reader & TTS ---
selected_post = st.session_state.selected_post

with col_right:
    st.markdown(f"### 📖 읽기 및 듣기")

    # Fetch canonical metadata and body together so edited or older posts never
    # retain a synthetic/stale title.
    if preloaded_post is not None and selected_post["post_id"] == linked_post_id:
        post_metadata, post_elements, scrape_err = preloaded_post
    else:
        post_metadata, post_elements, scrape_err = fetch_post_details(
            selected_post["post_id"]
        )

    if post_metadata:
        selected_post = {
            **selected_post,
            "title": post_metadata["title"],
            "link": post_metadata["link"],
            "published": selected_post.get("published") or post_metadata["published"],
        }
        st.session_state.selected_post = selected_post

    # Design of the Reading panel
    with st.container():
        # Top Header of Post
        st.markdown(f"""
        <div class="reader-header">
            <div class="reader-title">{escape(selected_post['title'])}</div>
            <div class="reader-meta">
                <div class="reader-meta-item">📅 {escape(selected_post['published'])}</div>
                <div class="reader-meta-item">🔗 <a href="{escape(selected_post['link'], quote=True)}" target="_blank" rel="noopener noreferrer" style="color: {accent_color}; text-decoration: none;">원문 보기</a></div>
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

        # TTS Settings Drawer/Expander inside a Form to buffer edits and prevent slider-dragging race conditions
        with st.expander("⚙️ TTS 음성 및 속도 설정", expanded=False):
            with st.form(key="tts_settings_form"):
                voice_col, speed_col = st.columns(2)

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

                apply_button = st.form_submit_button(
                    label="⚙️ 설정 적용하기",
                    width="stretch",
                )
                if apply_button:
                    st.session_state.tts_voice = form_voice
                    st.session_state.tts_speed = form_speed
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
        audio_path, tts_warning = get_tts_audio(full_text, selected_post["post_id"], voice_id, speed_rate)

        if tts_warning:
            st.warning(tts_warning)

        if audio_path and os.path.exists(audio_path):
            # Render audio player nicely
            with open(audio_path, "rb") as f:
                audio_bytes = f.read()

            # Only authenticated sessions receive audio bytes. Streamlit's
            # public /media URLs are disabled; playback/download stay in the
            # browser's memory and do not create an externally usable URL.
            encoded_audio = json.dumps(base64.b64encode(audio_bytes).decode("ascii"))
            download_name = escape(f"{selected_post['title'][:15]}.mp3", quote=True)
            st.iframe(
                f"""<!doctype html><html lang="ko"><head><style>
                body {{ margin: 0; font-family: sans-serif; color: {text_color}; background: {card_color}; }}
                .player {{ display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }}
                audio {{ flex: 1; min-width: 200px; height: 44px; }}
                a {{ color: {accent_color}; text-decoration: none; font-size: 13px;
                     padding: 12px; border: 1px solid {border_color}; border-radius: 8px; }}
                </style></head><body><div class="player">
                <audio id="audio" controls preload="metadata" aria-label="블로그 음성"></audio>
                <a id="download" download="{download_name}">💾 MP3 다운로드</a>
                </div><script>
                const bytes = Uint8Array.from(atob({encoded_audio}), c => c.charCodeAt(0));
                const url = URL.createObjectURL(new Blob([bytes], {{type: "audio/mpeg"}}));
                document.getElementById("audio").src = url;
                document.getElementById("download").href = url;
                window.addEventListener("pagehide", () => URL.revokeObjectURL(url), {{once: true}});
                </script></body></html>""",
                height=110,
            )
        else:
            st.error("오디오 파일을 생성하거나 불러오는 중에 오류가 발생했습니다.")

        st.markdown("<hr style='border-color: {}; margin: 1.5rem 0;'>".format(border_subtle), unsafe_allow_html=True)

        # Render full text paragraphs nicely formatted
        body_html = '<div class="reader-body">'
        for el in post_elements:
            safe_text = escape(el["text"])
            if el["type"] == "quote":
                body_html += f'<div class="reader-quote">{safe_text}</div>'
            elif el["type"] == "list-item":
                body_html += f'<div class="reader-list-item">• {safe_text}</div>'
            else:
                body_html += f'<div class="reader-paragraph">{safe_text}</div>'
        body_html += '</div>'

        st.markdown(body_html, unsafe_allow_html=True)

# 7. Sidebar Utilities
st.sidebar.markdown("### ⚙️ 시스템 설정")
# Clear Cache button
if st.sidebar.button(
    "🧹 캐시 지우기 (오디오 및 파싱 결과)",
    width="stretch",
):
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
