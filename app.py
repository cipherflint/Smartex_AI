from flask import Flask, render_template, request, jsonify, session, redirect, url_for, Response, stream_with_context
import requests
import json, uuid, os, hashlib, secrets, base64, time

app = Flask(__name__)
app.secret_key = "smartex_secret_key_2025_fixed"   # fixed so sessions survive restarts

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE    = os.path.dirname(__file__)
DATA    = os.path.join(BASE, "data")
USERS_F = os.path.join(DATA, "users.json")
CHATS_D = os.path.join(DATA, "chats")
CFG_F   = os.path.join(DATA, "config.json")
os.makedirs(CHATS_D, exist_ok=True)

# ── Config (admin-managed: API keys + custom models) ──────────────────────────
def load_config():
    try:
        with open(CFG_F) as f:
            c = json.load(f)
    except Exception:
        c = {}
    c.setdefault("openrouter_keys", [])
    c.setdefault("groq_keys", [])
    c.setdefault("custom_models", [])
    return c

def save_config(c):
    with open(CFG_F, "w") as f:
        json.dump(c, f, indent=2)

# ── Built-in model list ────────────────────────────────────────────────────────
BUILTIN_MODELS = [
    {"id": "openai/gpt-4o-mini",                     "name": "GPT-4o Mini",             "provider": "openrouter", "tag": "free"},
    {"id": "meta-llama/llama-3.3-70b-instruct:free",  "name": "Llama 3.3 70B (Free)",    "provider": "openrouter", "tag": "free"},
    {"id": "meta-llama/llama-3.1-8b-instruct:free",   "name": "Llama 3.1 8B (Free)",     "provider": "openrouter", "tag": "free"},
    {"id": "google/gemini-2.0-flash-exp:free",        "name": "Gemini 2.0 Flash (Free)", "provider": "openrouter", "tag": "free"},
    {"id": "deepseek/deepseek-r1:free",               "name": "DeepSeek R1 (Free)",      "provider": "openrouter", "tag": "free"},
    {"id": "qwen/qwen3-235b-a22b:free",               "name": "Qwen3 235B (Free)",       "provider": "openrouter", "tag": "free"},
    {"id": "mistralai/mistral-7b-instruct:free",      "name": "Mistral 7B (Free)",       "provider": "openrouter", "tag": "free"},
    {"id": "openai/gpt-4o",                           "name": "GPT-4o",                  "provider": "openrouter", "tag": ""},
    {"id": "anthropic/claude-sonnet-4-5",             "name": "Claude Sonnet 4",         "provider": "openrouter", "tag": ""},
    {"id": "anthropic/claude-opus-4-5",               "name": "Claude Opus 4",           "provider": "openrouter", "tag": ""},
    {"id": "google/gemini-pro-1.5",                   "name": "Gemini 1.5 Pro",          "provider": "openrouter", "tag": ""},
    {"id": "meta-llama/llama-3.1-405b-instruct",      "name": "Llama 3.1 405B",          "provider": "openrouter", "tag": ""},
    {"id": "x-ai/grok-3-mini-beta",                   "name": "Grok 3 Mini",             "provider": "openrouter", "tag": ""},
    # Groq models
    {"id": "llama-3.3-70b-versatile",                 "name": "Llama 3.3 70B (Groq)",    "provider": "groq",       "tag": "fast"},
    {"id": "llama-3.1-8b-instant",                    "name": "Llama 3.1 8B (Groq)",     "provider": "groq",       "tag": "fast"},
    {"id": "mixtral-8x7b-32768",                      "name": "Mixtral 8x7B (Groq)",     "provider": "groq",       "tag": "fast"},
    {"id": "gemma2-9b-it",                            "name": "Gemma 2 9B (Groq)",       "provider": "groq",       "tag": "fast"},
    {"id": "deepseek-r1-distill-llama-70b",           "name": "DeepSeek R1 70B (Groq)",  "provider": "groq",       "tag": "fast"},
]

def get_all_models():
    cfg = load_config()
    models = list(BUILTIN_MODELS)
    for m in cfg.get("custom_models", []):
        if not any(x["id"] == m["id"] for x in models):
            models.append(m)
    return models

# ── Users ──────────────────────────────────────────────────────────────────────
def load_users():
    try:
        with open(USERS_F) as f: return json.load(f)
    except: return {}

def save_users(u):
    with open(USERS_F, "w") as f: json.dump(u, f, indent=2)

def hash_pw(pw):
    salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + pw).encode()).hexdigest()
    return f"{salt}:{h}"

def verify_pw(stored, given):
    try:
        salt, h = stored.split(":", 1)
        return hashlib.sha256((salt + given).encode()).hexdigest() == h
    except: return False

# ── Chats ──────────────────────────────────────────────────────────────────────
def chats_path(username):
    safe = "".join(c for c in username if c.isalnum() or c in "_-")
    return os.path.join(CHATS_D, f"{safe}.json")

def load_chats(username):
    try:
        with open(chats_path(username)) as f: return json.load(f)
    except: return {}

def save_chats(username, chats):
    with open(chats_path(username), "w") as f: json.dump(chats, f, indent=2)

def del_user_data(username):
    p = chats_path(username)
    if os.path.exists(p): os.remove(p)

def logged_in(): return "username" in session

# ── Context builder ────────────────────────────────────────────────────────────
def build_context(history, system_prompt, new_content, max_ctx=40):
    """Build messages list with full history (last max_ctx msgs for context)."""
    messages = [{"role": "system", "content": system_prompt}]
    # Include recent history for context memory
    recent = history[-max_ctx:] if len(history) > max_ctx else history
    for msg in recent:
        role = "user" if msg["role"] == "user" else "assistant"
        messages.append({"role": role, "content": msg["content"]})
    messages.append({"role": "user", "content": new_content})
    return messages

# ── API callers (non-streaming fallback) ────────────────────────────────────────
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GROQ_URL       = "https://api.groq.com/openai/v1/chat/completions"

def get_provider_for_model(model_id):
    models = get_all_models()
    for m in models:
        if m["id"] == model_id:
            return m.get("provider", "openrouter")
    return "openrouter"

def get_api_url(provider):
    return GROQ_URL if provider == "groq" else OPENROUTER_URL

def get_keys_for_provider(provider):
    cfg = load_config()
    if provider == "groq":
        return [k for k in cfg.get("groq_keys", []) if k.strip()]
    return [k for k in cfg.get("openrouter_keys", []) if k.strip()]

def call_api_stream(model, messages):
    """Generator: yields SSE chunks. Falls back gracefully."""
    provider = get_provider_for_model(model)
    api_url  = get_api_url(provider)
    keys     = get_keys_for_provider(provider)

    if not keys:
        yield f"data: {json.dumps({'error': 'No API keys configured. Ask admin to add keys.'})}\n\n"
        return

    for i, key in enumerate(keys):
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        if provider == "openrouter":
            headers["HTTP-Referer"] = "http://localhost:5000"
            headers["X-Title"] = "Smartex AI"

        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 4096,
            "stream": True,
        }

        try:
            resp = requests.post(api_url, headers=headers, json=payload,
                                 stream=True, timeout=90)

            if resp.status_code == 200:
                full_text = ""
                for line in resp.iter_lines():
                    if not line:
                        continue
                    line = line.decode("utf-8", errors="replace")
                    if line.startswith("data: "):
                        chunk_str = line[6:]
                        if chunk_str.strip() == "[DONE]":
                            yield f"data: {json.dumps({'done': True, 'full': full_text})}\n\n"
                            return
                        try:
                            chunk = json.loads(chunk_str)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            token = delta.get("content", "")
                            if token:
                                full_text += token
                                yield f"data: {json.dumps({'token': token})}\n\n"
                        except json.JSONDecodeError:
                            pass
                # If stream ended without [DONE]
                yield f"data: {json.dumps({'done': True, 'full': full_text})}\n\n"
                return

            elif resp.status_code in (401, 403):
                if i == len(keys) - 1:
                    yield f"data: {json.dumps({'error': 'Invalid API key.'})}\n\n"
            elif resp.status_code == 429:
                if i == len(keys) - 1:
                    yield f"data: {json.dumps({'error': 'Rate limited. Add more keys.'})}\n\n"
            elif resp.status_code == 402:
                if i == len(keys) - 1:
                    yield f"data: {json.dumps({'error': 'Insufficient credits on API key.'})}\n\n"
            else:
                try:
                    err = resp.json().get("error", {}).get("message", resp.text[:200])
                except:
                    err = resp.text[:200]
                if i == len(keys) - 1:
                    yield f"data: {json.dumps({'error': err})}\n\n"

        except requests.exceptions.Timeout:
            if i == len(keys) - 1:
                yield f"data: {json.dumps({'error': 'Request timed out.'})}\n\n"
        except Exception as e:
            if i == len(keys) - 1:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"


# ════════════════════════════════════════════════════════════════════════════════
# Routes
# ════════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    if not logged_in(): return redirect(url_for("auth_page"))
    return render_template("index.html", models=get_all_models(), username=session["username"])

@app.route("/auth")
def auth_page():
    if logged_in(): return redirect(url_for("index"))
    return render_template("auth.html")

# ── Auth ───────────────────────────────────────────────────────────────────────
@app.route("/api/register", methods=["POST"])
def register():
    d = request.get_json()
    u = (d.get("username") or "").strip().lower()
    p = (d.get("password") or "").strip()
    if not u or not p: return jsonify({"error": "Username and password required."}), 400
    if len(u) < 3 or len(u) > 20: return jsonify({"error": "Username must be 3–20 chars."}), 400
    if not u.replace("_","").replace("-","").isalnum(): return jsonify({"error": "Username: letters/numbers/- only."}), 400
    if len(p) < 6: return jsonify({"error": "Password min 6 chars."}), 400
    users = load_users()
    if u in users: return jsonify({"error": "Username taken."}), 409
    users[u] = {"password": hash_pw(p), "created_at": int(time.time())}
    save_users(users)
    session["username"] = u
    return jsonify({"ok": True, "username": u})

@app.route("/api/login", methods=["POST"])
def login():
    d = request.get_json()
    u = (d.get("username") or "").strip().lower()
    p = (d.get("password") or "").strip()
    users = load_users()
    if u not in users or not verify_pw(users[u]["password"], p):
        return jsonify({"error": "Invalid username or password."}), 401
    session["username"] = u
    return jsonify({"ok": True, "username": u})

@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})

@app.route("/api/delete-account", methods=["POST"])
def delete_account():
    if not logged_in(): return jsonify({"error": "Not logged in."}), 401
    u = session["username"]
    users = load_users()
    if u in users: del users[u]; save_users(users)
    del_user_data(u)
    session.clear()
    return jsonify({"ok": True})

# ── Chat (SSE streaming) ───────────────────────────────────────────────────────
@app.route("/chat/stream", methods=["POST"])
def chat_stream():
    if not logged_in():
        def err(): yield f"data: {json.dumps({'error': 'Not logged in.'})}\n\n"
        return Response(stream_with_context(err()), mimetype="text/event-stream")

    data       = request.get_json() or {}
    user_msg   = data.get("message", "").strip()
    model      = data.get("model", "openai/gpt-4o-mini")
    sys_prompt = data.get("system_prompt", "You are Smartex, a helpful, friendly, and knowledgeable AI assistant.")
    chat_id    = data.get("chat_id", "")
    attachments= data.get("attachments", [])

    if not user_msg and not attachments:
        def err(): yield f"data: {json.dumps({'error': 'Empty message.'})}\n\n"
        return Response(stream_with_context(err()), mimetype="text/event-stream")

    username = session["username"]
    chats    = load_chats(username)

    # Build content (multimodal if attachments)
    if attachments:
        parts = []
        if user_msg:
            parts.append({"type": "text", "text": user_msg})
        for att in attachments:
            if att.get("isImg") and att.get("dataUrl"):
                parts.append({"type": "image_url", "image_url": {"url": att["dataUrl"]}})
            elif att.get("dataUrl") and "," in att["dataUrl"]:
                try:
                    b64  = att["dataUrl"].split(",", 1)[1]
                    text = base64.b64decode(b64).decode("utf-8", errors="replace")[:12000]
                    parts.append({"type": "text", "text": f"\n--- File: {att['name']} ---\n{text}\n---"})
                except:
                    parts.append({"type": "text", "text": f"[Attached: {att['name']}]"})
        new_content = parts if len(parts) > 1 else (parts[0] if parts else user_msg)
    else:
        new_content = user_msg

    # Load chat history for context memory
    history = chats.get(chat_id, {}).get("messages", []) if chat_id else []
    messages = build_context(history, sys_prompt, new_content)

    # Stream response
    def generate():
        full_reply = ""
        had_error  = False
        for chunk in call_api_stream(model, messages):
            yield chunk
            # Parse to collect full reply
            if chunk.startswith("data: "):
                try:
                    d = json.loads(chunk[6:])
                    if "token" in d:
                        full_reply += d["token"]
                    elif "done" in d:
                        full_reply = d.get("full", full_reply)
                    elif "error" in d:
                        had_error = True
                except: pass

        # Persist to history after streaming completes
        if not had_error and full_reply:
            chats2 = load_chats(username)  # reload fresh
            if chat_id and chat_id in chats2:
                hist = chats2[chat_id].get("messages", [])
                hist.append({"role": "user", "content": user_msg or "[file]"})
                hist.append({"role": "assistant", "content": full_reply})
                if len(hist) > 100: hist = hist[-100:]
                chats2[chat_id]["messages"] = hist
                chats2[chat_id]["updatedAt"] = int(time.time() * 1000)
                save_chats(username, chats2)

    return Response(stream_with_context(generate()),
                    mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ── Chats persistence ──────────────────────────────────────────────────────────
@app.route("/chats/save", methods=["POST"])
def save_chats_ep():
    if not logged_in(): return jsonify({"error": "Not logged in."}), 401
    save_chats(session["username"], request.get_json().get("chats", {}))
    return jsonify({"ok": True})

@app.route("/chats/load")
def load_chats_ep():
    if not logged_in(): return jsonify({"error": "Not logged in."}), 401
    return jsonify({"chats": load_chats(session["username"])})

@app.route("/models")
def models_ep():
    return jsonify({"models": get_all_models()})

if __name__ == "__main__":
    app.run(debug=True, port=5000, threaded=True)
