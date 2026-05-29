"""
Smartex - AI Chat Assistant
Supports both Groq cloud models and local Ollama models.
Run with: streamlit run app.py
"""

import os
import sys
import json
import threading
import time
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

GROQ_MODELS = {
    "llama-3.3-70b-versatile": "⭐ Llama 3.3 70B  (Best Quality)",
    "llama3-8b-8192":          "⚡ Llama 3 8B     (Fastest)",
    "mixtral-8x7b-32768":      "🔀 Mixtral 8x7B   (Long Context)",
    "gemma2-9b-it":            "💎 Gemma 2 9B     (Lightweight)",
}

DEFAULT_SYSTEM_PROMPT = (
    "You are Smartex, a helpful and knowledgeable AI assistant. "
    "Your name is Smartex. "
    "When asked about your name, always say 'My name is Smartex'. "
    "Never say you are Claude, GPT, Llama, Gemma, or any other AI. You are always Smartex. "
    "Answer questions clearly and accurately. "
    "If the user writes in Bengali (বাংলা), always reply in Bengali. "
    "If the user writes in English, reply in English."
)

# ─────────────────────────────────────────────
# Page Config
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="Smartex",
    page_icon="✦",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ─── THEME-AWARE CSS (uses Streamlit's theme variables) ──────────────────
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;600&display=swap');

    html, body, [class*="css"] {
        font-family: 'IBM Plex Sans', sans-serif;
    }

    /* Main app background – uses Streamlit's theme background */
    .stApp {
        background-color: var(--st-color-background);
    }

    /* Sidebar – match theme background, subtle border */
    [data-testid="stSidebar"] {
        background-color: var(--st-color-background);
        border-right: 1px solid var(--st-color-border);
    }

    /* Hide default Streamlit elements */
    #MainMenu, footer, header {
        visibility: hidden;
    }

    /* Titles */
    .app-title {
        font-family: 'IBM Plex Mono', monospace;
        font-size: 1.5rem;
        font-weight: 600;
        color: var(--st-color-primary);
        letter-spacing: 0.08em;
        padding: 1rem 0 0.2rem 0;
    }
    .app-subtitle {
        font-size: 0.78rem;
        color: var(--st-color-text-secondary);
        font-family: 'IBM Plex Mono', monospace;
        margin-bottom: 1.5rem;
    }

    /* Model badges – brand colours remain but adapt text contrast */
    .model-badge {
        display: inline-block;
        background: var(--st-color-secondary-background);
        border: 1px solid var(--st-color-primary);
        color: var(--st-color-primary);
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.72rem;
        padding: 3px 10px;
        border-radius: 3px;
        margin-bottom: 0.5rem;
    }
    .groq-badge {
        display: inline-block;
        background: var(--st-color-secondary-background);
        border: 1px solid #818cf8;
        color: #818cf8;
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.72rem;
        padding: 3px 10px;
        border-radius: 3px;
        margin-bottom: 0.5rem;
    }

    /* Provider tags */
    .provider-tag-groq {
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.7rem;
        color: #818cf8;
        background: var(--st-color-secondary-background);
        border: 1px solid #6366f144;
        padding: 2px 8px;
        border-radius: 3px;
        margin-top: 0.3rem;
        display: inline-block;
    }
    .provider-tag-ollama {
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.7rem;
        color: var(--st-color-primary);
        background: var(--st-color-secondary-background);
        border: 1px solid var(--st-color-primary);
        padding: 2px 8px;
        border-radius: 3px;
        margin-top: 0.3rem;
        display: inline-block;
    }

    /* Chat bubbles – use theme colours with slight adjustments */
    [data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) {
        background-color: var(--st-color-secondary-background) !important;
        border: 1px solid var(--st-color-border) !important;
        border-radius: 8px !important;
        padding: 0.5rem !important;
        margin: 0.3rem 0 !important;
    }
    [data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-assistant"]) {
        background-color: var(--st-color-secondary-background) !important;
        border: 1px solid var(--st-color-border) !important;
        border-radius: 8px !important;
        padding: 0.5rem !important;
        margin: 0.3rem 0 !important;
    }

    /* Thinking boxes – adapt to theme */
    .thinking-box {
        background: var(--st-color-secondary-background);
        border-left: 3px solid var(--st-color-primary);
        padding: 10px 14px;
        border-radius: 4px;
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.82rem;
        color: var(--st-color-primary);
        margin: 0.5rem 0;
        animation: pulse 2s infinite;
    }
    .thinking-box-groq {
        background: var(--st-color-secondary-background);
        border-left: 3px solid #818cf8;
        padding: 10px 14px;
        border-radius: 4px;
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.82rem;
        color: #818cf8;
        margin: 0.5rem 0;
        animation: pulse 1.5s infinite;
    }

    @keyframes pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.5; }
    }

    /* Sidebar sections */
    .sidebar-section {
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.7rem;
        color: var(--st-color-text-secondary);
        text-transform: uppercase;
        letter-spacing: 0.1em;
        margin: 1.2rem 0 0.5rem 0;
        border-bottom: 1px solid var(--st-color-border);
        padding-bottom: 4px;
    }

    /* Buttons */
    .stButton > button {
        background-color: var(--st-color-secondary-background) !important;
        color: var(--st-color-text) !important;
        border: 1px solid var(--st-color-border) !important;
        border-radius: 6px !important;
        font-family: 'IBM Plex Mono', monospace !important;
        font-size: 0.8rem !important;
        transition: all 0.2s !important;
    }
    .stButton > button:hover {
        border-color: var(--st-color-primary) !important;
        color: var(--st-color-primary) !important;
    }

    /* Scrollbar */
    ::-webkit-scrollbar { width: 4px; }
    ::-webkit-scrollbar-track { background: var(--st-color-background); }
    ::-webkit-scrollbar-thumb { background: var(--st-color-border); border-radius: 2px; }

    /* Main container width */
    .main .block-container {
        padding-top: 1rem;
        padding-bottom: 5rem;
        max-width: 900px;
    }

    /* ─── CODE BLOCKS: adapt to light/dark theme ─── */
    pre, code, .stCodeBlock, .stCodeBlock div[data-testid="stMarkdown"] pre {
        background-color: var(--st-color-background) !important;
        color: var(--st-color-text) !important;
        border-radius: 6px;
        border: 1px solid var(--st-color-border);
        font-family: 'IBM Plex Mono', monospace;
    }
    /* Ensure inline code also adapts */
    code:not(pre code) {
        background-color: var(--st-color-secondary-background) !important;
        color: var(--st-color-primary) !important;
        padding: 0.2em 0.4em;
        border-radius: 4px;
    }
    /* Override any forced highlight.js themes */
    .hljs {
        background: transparent !important;
        color: var(--st-color-text) !important;
    }
    .hljs-comment, .hljs-quote {
        color: var(--st-color-text-secondary) !important;
    }
    .hljs-keyword, .hljs-selector-tag, .hljs-subst {
        color: var(--st-color-primary) !important;
    }
    .hljs-string, .hljs-doctag {
        color: #a5d6ff;  /* keep a readable blue, but can be changed */
    }
    /* fallback for light mode: ensure syntax colours have good contrast */
    @media (prefers-color-scheme: light) {
        .hljs-string, .hljs-doctag { color: #0b5d8c; }
        .hljs-keyword { color: #c678dd; }
    }
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────
# Helper Functions (unchanged)
# ─────────────────────────────────────────────
def get_ollama_models() -> list:
    try:
        r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        if r.status_code == 200:
            return [m["name"] for m in r.json().get("models", [])]
        return []
    except Exception:
        return []


def is_ollama_running() -> bool:
    try:
        requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3)
        return True
    except Exception:
        return False


def is_groq_configured() -> bool:
    return bool(GROQ_API_KEY and GROQ_API_KEY != "your_groq_api_key_here")


def is_groq_model(model_name: str) -> bool:
    return model_name in GROQ_MODELS


# ─────────────────────────────────────────────
# API Calls (unchanged)
# ─────────────────────────────────────────────
def chat_with_groq(messages: list, model: str, temperature: float, result: dict):
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=1500,
        )
        result["answer"] = response.choices[0].message.content
        result["done"] = True
    except Exception as e:
        msg = str(e)
        if "rate_limit" in msg.lower():
            result["error"] = "⏱️ Groq rate limit reached. Wait a moment and try again."
        elif "invalid_api_key" in msg.lower() or "authentication" in msg.lower():
            result["error"] = "❌ Invalid Groq API key. Check your .env file."
        else:
            result["error"] = f"❌ Groq error: {msg}"
        result["done"] = True


def chat_with_ollama(messages: list, model: str, temperature: float, result: dict):
    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "stream": True,
                "options": {
                    "temperature": temperature,
                    "num_predict": 1000,
                    "num_ctx": 2048,
                }
            },
            stream=True,
            timeout=(60, 600)
        )
        response.raise_for_status()
        full_response = ""
        for line in response.iter_lines():
            if line:
                try:
                    chunk = json.loads(line.decode("utf-8"))
                    token = chunk.get("message", {}).get("content", "")
                    full_response += token
                    if chunk.get("done", False):
                        break
                except json.JSONDecodeError:
                    continue
        result["answer"] = full_response
        result["done"] = True
    except requests.exceptions.ConnectionError:
        result["error"] = "❌ Cannot connect to Ollama. Make sure the Ollama app is open."
        result["done"] = True
    except Exception as e:
        result["error"] = f"❌ Ollama error: {str(e)}"
        result["done"] = True


# ─────────────────────────────────────────────
# Session State (unchanged)
# ─────────────────────────────────────────────
def init_state():
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "temperature" not in st.session_state:
        st.session_state.temperature = 0.7
    if "system_prompt" not in st.session_state:
        st.session_state.system_prompt = DEFAULT_SYSTEM_PROMPT
    if "selected_model" not in st.session_state:
        if is_groq_configured():
            st.session_state.selected_model = "llama-3.3-70b-versatile"
        else:
            models = get_ollama_models()
            st.session_state.selected_model = models[0] if models else ""


# ─────────────────────────────────────────────
# Sidebar (unchanged logic, only CSS adapted)
# ─────────────────────────────────────────────
def render_sidebar():
    with st.sidebar:
        st.markdown('<div class="app-title">✦ SMARTEX</div>', unsafe_allow_html=True)
        st.markdown('<div class="app-subtitle">your personal ai assistant</div>', unsafe_allow_html=True)

        current = st.session_state.get("selected_model", "")
        is_groq = is_groq_model(current)

        if is_groq:
            st.markdown(f'<div class="groq-badge">☁ {current}</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="model-badge">▶ {current}</div>', unsafe_allow_html=True)

        # Groq Models
        st.markdown('<div class="sidebar-section">☁ Groq Models (Best performance)</div>', unsafe_allow_html=True)

        if is_groq_configured():
            groq_options = list(GROQ_MODELS.keys())
            groq_labels = list(GROQ_MODELS.values())
            default_idx = groq_options.index(current) if current in groq_options else 0
            groq_selected_label = st.selectbox(
                "Groq model", options=groq_labels,
                index=default_idx, label_visibility="collapsed"
            )
            groq_selected = groq_options[groq_labels.index(groq_selected_label)]
            if st.button("Use this Groq model", use_container_width=True):
                st.session_state.selected_model = groq_selected
                st.session_state.messages = []
                st.rerun()
        else:
            st.warning("Groq not configured.\nAdd to `.env`:\n`GROQ_API_KEY=gsk_...`\nGet free key: console.groq.com")

        # Ollama Models
        st.markdown('<div class="sidebar-section">💻 Local Ollama Models</div>', unsafe_allow_html=True)

        ollama_models = get_ollama_models()
        if ollama_models:
            status_color = "var(--st-color-primary)" if is_ollama_running() else "#ff4444"
            status_text = "running" if is_ollama_running() else "offline"
            st.markdown(
                f'<span style="color:{status_color}; font-family: IBM Plex Mono; font-size:0.75rem;">● {status_text}</span>',
                unsafe_allow_html=True
            )
            default_ollama = ollama_models.index(current) if current in ollama_models else 0
            ollama_selected = st.selectbox(
                "Ollama model", options=ollama_models,
                index=default_ollama, label_visibility="collapsed"
            )
            if st.button("Use this local model", use_container_width=True):
                st.session_state.selected_model = ollama_selected
                st.session_state.messages = []
                st.rerun()
        else:
            st.info("No local models found.\nRun: `ollama pull phi3`")

        # Settings
        st.markdown('<div class="sidebar-section">Settings</div>', unsafe_allow_html=True)

        st.session_state.temperature = st.slider(
            "Temperature", min_value=0.0, max_value=1.0,
            value=st.session_state.temperature, step=0.1,
            label_visibility="collapsed"
        )
        col1, col2 = st.columns(2)
        with col1:
            st.caption("0.0 Factual")
        with col2:
            st.caption("1.0 Creative")

        with st.expander("⚙ Advanced: Edit System Prompt"):
            st.session_state.system_prompt = st.text_area(
                "System prompt",
                value=st.session_state.system_prompt,
                height=150,
                label_visibility="collapsed"
            )
            if st.button("↺ Reset to Default", use_container_width=True):
                st.session_state.system_prompt = DEFAULT_SYSTEM_PROMPT
                st.rerun()

        # Actions
        st.markdown('<div class="sidebar-section">Actions</div>', unsafe_allow_html=True)
        if st.button("🗑️ Clear Chat", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

        if st.session_state.messages:
            total = len([m for m in st.session_state.messages if m["role"] == "user"])
            st.markdown(
                f'<div style="font-family: IBM Plex Mono; font-size: 0.72rem; color: var(--st-color-text-secondary); margin-top: 0.8rem;">'
                f'{total} message{"s" if total != 1 else ""} this session</div>',
                unsafe_allow_html=True
            )


# ─────────────────────────────────────────────
# Main Chat (provider bar uses theme variables)
# ─────────────────────────────────────────────
def render_chat():
    model = st.session_state.get("selected_model", "")
    is_groq = is_groq_model(model)

    provider = "☁ Groq Cloud" if is_groq else "💻 Local Ollama"
    color = "#818cf8" if is_groq else "var(--st-color-primary)"

    # Use theme-aware inline style (no hardcoded colors except brand accent)
    st.markdown(
        f'<div style="font-family: IBM Plex Mono; font-size: 0.78rem; color: var(--st-color-text-secondary); margin-bottom: 1rem;">'
        f'<span style="color:{color}">{provider}</span> · '
        f'<span style="color:{color}">{model}</span> · '
        f'temp <span style="color:{color}">{st.session_state.temperature}</span>'
        f'</div>',
        unsafe_allow_html=True
    )

    if not st.session_state.messages:
        st.markdown(f"""
        <div style="text-align:center; padding:4rem 2rem; font-family: IBM Plex Mono, monospace;">
            <div style="font-size:3rem; margin-bottom:1rem;">✦</div>
            <div style="font-size:1.1rem; color: var(--st-color-text-secondary); margin-bottom:0.4rem;">Hi, I'm Smartex</div>
            <div style="font-size:0.78rem; color: var(--st-color-text-tertiary);">
                {"☁ Using Groq — fast, free, powerful" if is_groq else "💻 Using local Ollama — private, offline"}
            </div>
            <div style="font-size:0.72rem; color: var(--st-color-text-tertiary); margin-top:0.3rem;">
                English · বাংলা · and more
            </div>
        </div>
        """, unsafe_allow_html=True)

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg["role"] == "assistant":
                tag = msg.get("provider", "")
                if tag == "groq":
                    st.markdown('<span class="provider-tag-groq">☁ groq</span>', unsafe_allow_html=True)
                elif tag == "ollama":
                    st.markdown('<span class="provider-tag-ollama">💻 local</span>', unsafe_allow_html=True)

    placeholder_text = f"Message Smartex ({model})..." if model else "Select a model first..."
    if prompt := st.chat_input(placeholder_text, disabled=not model):
        handle_message(prompt)


# ─────────────────────────────────────────────
# Handle Message (unchanged)
# ─────────────────────────────────────────────
def handle_message(question: str):
    model = st.session_state.get("selected_model", "")
    is_groq = is_groq_model(model)

    if not model:
        st.error("No model selected.")
        return

    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        thinking_placeholder = st.empty()

        if is_groq:
            thinking_placeholder.markdown(
                '<div class="thinking-box-groq">☁ Smartex is thinking...</div>',
                unsafe_allow_html=True
            )
        else:
            thinking_placeholder.markdown(
                '<div class="thinking-box">◉ Smartex is thinking...</div>',
                unsafe_allow_html=True
            )

        # Build messages
        api_messages = []
        if st.session_state.system_prompt.strip():
            api_messages.append({"role": "system", "content": st.session_state.system_prompt})
        for msg in st.session_state.messages[-10:]:
            api_messages.append({"role": msg["role"], "content": msg["content"]})

        # Run in background thread
        result = {"answer": None, "error": None, "done": False}
        target = chat_with_groq if is_groq else chat_with_ollama
        thread = threading.Thread(
            target=target,
            args=(api_messages, model, st.session_state.temperature, result),
            daemon=True
        )
        thread.start()

        # Wait with live timer
        elapsed = 0
        while not result["done"]:
            time.sleep(2)
            elapsed += 2
            if is_groq:
                thinking_placeholder.markdown(
                    f'<div class="thinking-box-groq">☁ Smartex is thinking... ({elapsed}s)</div>',
                    unsafe_allow_html=True
                )
            else:
                if elapsed < 60:
                    thinking_placeholder.markdown(
                        f'<div class="thinking-box">◉ Smartex is thinking... ({elapsed}s)</div>',
                        unsafe_allow_html=True
                    )
                else:
                    m, s = elapsed // 60, elapsed % 60
                    thinking_placeholder.markdown(
                        f'<div class="thinking-box">◉ Loading model... ({m}m {s}s)</div>',
                        unsafe_allow_html=True
                    )

        thinking_placeholder.empty()

        if result["error"]:
            st.error(result["error"])
            st.session_state.messages.pop()
            return

        answer = result["answer"]
        if not answer or not answer.strip():
            st.error("Got empty response. Please try again.")
            st.session_state.messages.pop()
            return

        st.markdown(answer)

        if is_groq:
            st.markdown('<span class="provider-tag-groq">☁ groq</span>', unsafe_allow_html=True)
        else:
            st.markdown('<span class="provider-tag-ollama">💻 local</span>', unsafe_allow_html=True)

        st.session_state.messages.append({
            "role": "assistant",
            "content": answer,
            "provider": "groq" if is_groq else "ollama"
        })


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main():
    init_state()
    render_sidebar()
    render_chat()


if __name__ == "__main__":
    main()