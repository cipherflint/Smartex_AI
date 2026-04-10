"""
Smartex AI — Render deployment version
- Single Flask app (main chat + admin panel merged)
- PostgreSQL database for users, chats, config
- API keys stored in DB (set via admin panel at /admin/...)
- Falls back to JSON files if no DATABASE_URL (local dev)
- Gunicorn-compatible streaming via SSE
"""
from flask import (Flask, render_template, request, jsonify,
                   session, redirect, url_for, Response, stream_with_context)
import requests, json, uuid, os, hashlib, secrets, base64, time

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "smartex_secret_key_2025_fixed")

# ══════════════════════════════════════════════════════════════════════════════
# DATABASE LAYER  (PostgreSQL on Render, JSON fallback for local dev)
# ══════════════════════════════════════════════════════════════════════════════
DATABASE_URL = os.environ.get("DATABASE_URL", "")
USE_DB = bool(DATABASE_URL)

if USE_DB:
    import psycopg2
    import psycopg2.extras
    # Render gives postgres:// but psycopg2 needs postgresql://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

def get_conn():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    """Create tables if they don't exist."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    created_at BIGINT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chats (
                    username TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT 'New Chat',
                    messages JSONB NOT NULL DEFAULT '[]',
                    created_at BIGINT NOT NULL,
                    updated_at BIGINT NOT NULL,
                    PRIMARY KEY (username, chat_id)
                );
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value JSONB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS admin_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pending_users (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    requested_at BIGINT NOT NULL
                );
            """)
        conn.commit()

# ── Local-dev JSON paths ───────────────────────────────────────────────────────
BASE    = os.path.dirname(os.path.abspath(__file__))
DATA    = os.path.join(BASE, "data")
USERS_F = os.path.join(DATA, "users.json")
CHATS_D = os.path.join(DATA, "chats")
CFG_F    = os.path.join(DATA, "config.json")
ADMIN_F  = os.path.join(DATA, "admin.json")
PENDING_F= os.path.join(DATA, "pending.json")
if not USE_DB:
    os.makedirs(CHATS_D, exist_ok=True)
    os.makedirs(DATA, exist_ok=True)

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG  (API keys + custom models)
# ══════════════════════════════════════════════════════════════════════════════
def load_config():
    if USE_DB:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT key, value FROM config")
                    rows = cur.fetchall()
            c = {r[0]: r[1] for r in rows}
        except Exception:
            c = {}
    else:
        try:
            with open(CFG_F) as f: c = json.load(f)
        except: c = {}
    c.setdefault("openrouter_keys", [])
    c.setdefault("groq_keys", [])
    c.setdefault("custom_models", [])
    return c

def save_config(c):
    if USE_DB:
        with get_conn() as conn:
            with conn.cursor() as cur:
                for k, v in c.items():
                    cur.execute("""
                        INSERT INTO config (key, value) VALUES (%s, %s)
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                    """, (k, json.dumps(v)))
            conn.commit()
    else:
        with open(CFG_F, "w") as f: json.dump(c, f, indent=2)

# ══════════════════════════════════════════════════════════════════════════════
# ADMIN PASSWORD
# ══════════════════════════════════════════════════════════════════════════════
def _default_admin_hash():
    salt = "defaultsalt00000"
    h = hashlib.sha256((salt + "admin123").encode()).hexdigest()
    return f"{salt}:{h}"

def load_admin_hash():
    if USE_DB:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT value FROM admin_config WHERE key='password_hash'")
                    row = cur.fetchone()
            return row[0] if row else _default_admin_hash()
        except: return _default_admin_hash()
    else:
        try:
            with open(ADMIN_F) as f: return json.load(f).get("password_hash", _default_admin_hash())
        except: return _default_admin_hash()

def save_admin_hash(h):
    if USE_DB:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO admin_config (key, value) VALUES ('password_hash', %s)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """, (h,))
            conn.commit()
    else:
        with open(ADMIN_F, "w") as f: json.dump({"password_hash": h}, f)

def verify_admin_pw(given):
    stored = load_admin_hash()
    try:
        salt, h = stored.split(":", 1)
        return hashlib.sha256((salt + given).encode()).hexdigest() == h
    except: return False

def set_admin_pw(new_pw):
    salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + new_pw).encode()).hexdigest()
    save_admin_hash(f"{salt}:{h}")

# ══════════════════════════════════════════════════════════════════════════════
# USERS
# ══════════════════════════════════════════════════════════════════════════════
def load_users():
    if USE_DB:
        try:
            with get_conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT username, password_hash, created_at FROM users")
                    rows = cur.fetchall()
            return {r["username"]: {"password": r["password_hash"], "created_at": r["created_at"]} for r in rows}
        except: return {}
    else:
        try:
            with open(USERS_F) as f: return json.load(f)
        except: return {}

def save_users(users_dict):
    # In DB mode we save individually; this is only used by JSON path
    if not USE_DB:
        with open(USERS_F, "w") as f: json.dump(users_dict, f, indent=2)

def db_create_user(username, pw_hash, created_at):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (username, password_hash, created_at) VALUES (%s, %s, %s)",
                        (username, pw_hash, created_at))
        conn.commit()

def db_delete_user(username):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE username=%s", (username,))
            cur.execute("DELETE FROM chats WHERE username=%s", (username,))
        conn.commit()

def user_exists(username):
    if USE_DB:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM users WHERE username=%s", (username,))
                return bool(cur.fetchone())
    else:
        return username in load_users()

def get_user_pw_hash(username):
    if USE_DB:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT password_hash FROM users WHERE username=%s", (username,))
                row = cur.fetchone()
        return row[0] if row else None
    else:
        users = load_users()
        u = users.get(username)
        return u["password"] if u else None

def count_user_chats(username):
    if USE_DB:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM chats WHERE username=%s", (username,))
                    return cur.fetchone()[0]
        except: return 0
    else:
        try:
            safe = "".join(c for c in username if c.isalnum() or c in "_-")
            with open(os.path.join(CHATS_D, f"{safe}.json")) as f:
                return len(json.load(f))
        except: return 0

# ══════════════════════════════════════════════════════════════════════════════
# CHATS
# ══════════════════════════════════════════════════════════════════════════════
def load_chats(username):
    if USE_DB:
        try:
            with get_conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT chat_id, title, messages, created_at, updated_at FROM chats WHERE username=%s", (username,))
                    rows = cur.fetchall()
            result = {}
            for r in rows:
                result[r["chat_id"]] = {
                    "id": r["chat_id"], "title": r["title"],
                    "messages": r["messages"],
                    "createdAt": r["created_at"], "updatedAt": r["updated_at"]
                }
            return result
        except: return {}
    else:
        safe = "".join(c for c in username if c.isalnum() or c in "_-")
        try:
            with open(os.path.join(CHATS_D, f"{safe}.json")) as f: return json.load(f)
        except: return {}

def save_chats(username, chats_dict):
    if USE_DB:
        with get_conn() as conn:
            with conn.cursor() as cur:
                # Delete chats no longer in dict
                if chats_dict:
                    ids = list(chats_dict.keys())
                    cur.execute("DELETE FROM chats WHERE username=%s AND chat_id != ALL(%s)", (username, ids))
                else:
                    cur.execute("DELETE FROM chats WHERE username=%s", (username,))
                # Upsert all chats
                for cid, chat in chats_dict.items():
                    cur.execute("""
                        INSERT INTO chats (username, chat_id, title, messages, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (username, chat_id) DO UPDATE SET
                            title = EXCLUDED.title,
                            messages = EXCLUDED.messages,
                            updated_at = EXCLUDED.updated_at
                    """, (username, cid,
                          chat.get("title", "New Chat"),
                          json.dumps(chat.get("messages", [])),
                          chat.get("createdAt", int(time.time()*1000)),
                          chat.get("updatedAt", int(time.time()*1000))))
            conn.commit()
    else:
        safe = "".join(c for c in username if c.isalnum() or c in "_-")
        with open(os.path.join(CHATS_D, f"{safe}.json"), "w") as f:
            json.dump(chats_dict, f, indent=2)

def update_chat_after_stream(username, chat_id, user_msg, assistant_reply):
    """Efficient single-chat update after streaming completes."""
    if USE_DB:
        with get_conn() as conn:
            with conn.cursor() as cur:
                # Get current messages
                cur.execute("SELECT messages FROM chats WHERE username=%s AND chat_id=%s", (username, chat_id))
                row = cur.fetchone()
                if row:
                    msgs = row[0] if isinstance(row[0], list) else []
                    msgs.append({"role": "user", "content": user_msg})
                    msgs.append({"role": "assistant", "content": assistant_reply})
                    if len(msgs) > 100: msgs = msgs[-100:]
                    cur.execute("""
                        UPDATE chats SET messages=%s, updated_at=%s WHERE username=%s AND chat_id=%s
                    """, (json.dumps(msgs), int(time.time()*1000), username, chat_id))
            conn.commit()
    else:
        chats = load_chats(username)
        if chat_id in chats:
            hist = chats[chat_id].get("messages", [])
            hist.append({"role": "user", "content": user_msg})
            hist.append({"role": "assistant", "content": assistant_reply})
            if len(hist) > 100: hist = hist[-100:]
            chats[chat_id]["messages"] = hist
            chats[chat_id]["updatedAt"] = int(time.time()*1000)
            save_chats(username, chats)

def get_chat_history(username, chat_id):
    """Get messages for a specific chat."""
    if USE_DB:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT messages FROM chats WHERE username=%s AND chat_id=%s", (username, chat_id))
                row = cur.fetchone()
        return row[0] if row else []
    else:
        chats = load_chats(username)
        return chats.get(chat_id, {}).get("messages", [])

def ensure_chat_exists(username, chat_id, title="New Chat"):
    """Create chat row if it doesn't exist yet."""
    if USE_DB:
        now = int(time.time()*1000)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO chats (username, chat_id, title, messages, created_at, updated_at)
                    VALUES (%s, %s, %s, '[]', %s, %s)
                    ON CONFLICT (username, chat_id) DO NOTHING
                """, (username, chat_id, title, now, now))
            conn.commit()

# ══════════════════════════════════════════════════════════════════════════════
# PASSWORD HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def hash_pw(pw):
    salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + pw).encode()).hexdigest()
    return f"{salt}:{h}"

def verify_pw(stored, given):
    try:
        salt, h = stored.split(":", 1)
        return hashlib.sha256((salt + given).encode()).hexdigest() == h
    except: return False

# ── Display name: capitalize first letter (store lowercase, display capitalized) ──
def display_name(username):
    return username[0].upper() + username[1:] if username else username

# ══════════════════════════════════════════════════════════════════════════════
# PENDING USERS  (awaiting admin approval)
# ══════════════════════════════════════════════════════════════════════════════
def load_pending():
    if USE_DB:
        try:
            with get_conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT username, password_hash, requested_at FROM pending_users ORDER BY requested_at DESC")
                    rows = cur.fetchall()
            return {r["username"]: {"password": r["password_hash"], "requested_at": r["requested_at"]} for r in rows}
        except: return {}
    else:
        try:
            with open(PENDING_F) as f: return json.load(f)
        except: return {}

def save_pending(pending):
    if not USE_DB:
        with open(PENDING_F, "w") as f: json.dump(pending, f, indent=2)

def add_pending(username, pw_hash):
    now = int(time.time())
    if USE_DB:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO pending_users (username, password_hash, requested_at)
                    VALUES (%s, %s, %s) ON CONFLICT (username) DO UPDATE
                    SET password_hash=EXCLUDED.password_hash, requested_at=EXCLUDED.requested_at
                """, (username, pw_hash, now))
            conn.commit()
    else:
        pending = load_pending()
        pending[username] = {"password": pw_hash, "requested_at": now}
        save_pending(pending)

def remove_pending(username):
    if USE_DB:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM pending_users WHERE username=%s", (username,))
            conn.commit()
    else:
        pending = load_pending()
        pending.pop(username, None)
        save_pending(pending)

def get_pending_pw(username):
    if USE_DB:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT password_hash FROM pending_users WHERE username=%s", (username,))
                row = cur.fetchone()
        return row[0] if row else None
    else:
        return load_pending().get(username, {}).get("password")

def logged_in(): return "username" in session
def admin_logged_in(): return session.get("admin") is True

# ══════════════════════════════════════════════════════════════════════════════
# MODELS
# ══════════════════════════════════════════════════════════════════════════════
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

# ══════════════════════════════════════════════════════════════════════════════
# AI API STREAMING
# ══════════════════════════════════════════════════════════════════════════════
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GROQ_URL       = "https://api.groq.com/openai/v1/chat/completions"

def get_provider_for_model(model_id):
    for m in get_all_models():
        if m["id"] == model_id:
            return m.get("provider", "openrouter")
    return "openrouter"

def get_keys(provider):
    cfg = load_config()
    if provider == "groq":
        return [k for k in cfg.get("groq_keys", []) if k.strip()]
    return [k for k in cfg.get("openrouter_keys", []) if k.strip()]

def build_context(history, system_prompt, new_content, max_ctx=40):
    messages = [{"role": "system", "content": system_prompt}]
    recent = history[-max_ctx:] if len(history) > max_ctx else history
    for msg in recent:
        role = "user" if msg["role"] == "user" else "assistant"
        messages.append({"role": role, "content": msg["content"]})
    messages.append({"role": "user", "content": new_content})
    return messages

def stream_ai(model, messages):
    provider = get_provider_for_model(model)
    api_url  = GROQ_URL if provider == "groq" else OPENROUTER_URL
    keys     = get_keys(provider)

    if not keys:
        yield f"data: {json.dumps({'error': 'No API keys configured. Ask the admin to add API keys.'})}\n\n"
        return

    for i, key in enumerate(keys):
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        if provider == "openrouter":
            headers["HTTP-Referer"] = request.host_url.rstrip("/")
            headers["X-Title"] = "Smartex AI"

        payload = {"model": model, "messages": messages,
                   "temperature": 0.7, "max_tokens": 4096, "stream": True}
        try:
            resp = requests.post(api_url, headers=headers, json=payload,
                                 stream=True, timeout=90)
            if resp.status_code == 200:
                full_text = ""
                for line in resp.iter_lines():
                    if not line: continue
                    line = line.decode("utf-8", errors="replace")
                    if not line.startswith("data: "): continue
                    chunk_str = line[6:]
                    if chunk_str.strip() == "[DONE]":
                        yield f"data: {json.dumps({'done': True, 'full': full_text})}\n\n"
                        return
                    try:
                        chunk = json.loads(chunk_str)
                        token = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if token:
                            full_text += token
                            yield f"data: {json.dumps({'token': token})}\n\n"
                    except json.JSONDecodeError:
                        pass
                yield f"data: {json.dumps({'done': True, 'full': full_text})}\n\n"
                return
            elif resp.status_code in (401, 403):
                if i == len(keys)-1:
                    yield f"data: {json.dumps({'error': 'Invalid API key.'})}\n\n"
            elif resp.status_code == 429:
                if i == len(keys)-1:
                    yield f"data: {json.dumps({'error': 'Rate limited on all keys.'})}\n\n"
            elif resp.status_code == 402:
                if i == len(keys)-1:
                    yield f"data: {json.dumps({'error': 'Insufficient API credits.'})}\n\n"
            else:
                try: err = resp.json().get("error", {}).get("message", resp.text[:200])
                except: err = resp.text[:200]
                if i == len(keys)-1:
                    yield f"data: {json.dumps({'error': err})}\n\n"
        except requests.exceptions.Timeout:
            if i == len(keys)-1:
                yield f"data: {json.dumps({'error': 'Request timed out.'})}\n\n"
        except Exception as e:
            if i == len(keys)-1:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

# ══════════════════════════════════════════════════════════════════════════════
# ─────────────────────────── MAIN APP ROUTES ──────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

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
    if len(u) < 3 or len(u) > 20: return jsonify({"error": "Username must be 3-20 chars."}), 400
    if not u.replace("_","").replace("-","").isalnum(): return jsonify({"error": "Username: letters/numbers/- _ only."}), 400
    if len(p) < 6: return jsonify({"error": "Password min 6 chars."}), 400
    if user_exists(u): return jsonify({"error": "Username already taken."}), 409
    if get_pending_pw(u): return jsonify({"error": "Account request already pending approval."}), 409
    add_pending(u, hash_pw(p))
    return jsonify({"pending": True, "message": "Your account request has been sent to the admin for approval. Please wait — you can sign in once approved."})

@app.route("/api/login", methods=["POST"])
def login():
    d = request.get_json()
    u = (d.get("username") or "").strip().lower()
    p = (d.get("password") or "").strip()
    pw_hash = get_user_pw_hash(u)
    if not pw_hash or not verify_pw(pw_hash, p):
        return jsonify({"error": "Invalid username or password."}), 401
    session["username"] = u
    return jsonify({"ok": True, "username": u, "display_name": display_name(u)})

@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})

@app.route("/api/delete-account", methods=["POST"])
def delete_account():
    if not logged_in(): return jsonify({"error": "Not logged in."}), 401
    u = session["username"]
    if USE_DB:
        db_delete_user(u)
    else:
        users = load_users()
        if u in users: del users[u]; save_users(users)
        safe = "".join(c for c in u if c.isalnum() or c in "_-")
        p = os.path.join(CHATS_D, f"{safe}.json")
        if os.path.exists(p): os.remove(p)
    session.clear()
    return jsonify({"ok": True})

# ── Chat streaming ─────────────────────────────────────────────────────────────
@app.route("/chat/stream", methods=["POST"])
def chat_stream():
    if not logged_in():
        def err():
            yield f"data: {json.dumps({'error': 'Not logged in.'})}\n\n"
        return Response(stream_with_context(err()), mimetype="text/event-stream")

    data       = request.get_json() or {}
    user_msg   = data.get("message", "").strip()
    model      = data.get("model", "openai/gpt-4o-mini")
    sys_prompt = data.get("system_prompt", "You are Smartex, a helpful, friendly AI assistant.")
    chat_id    = data.get("chat_id", "")
    attachments= data.get("attachments", [])

    if not user_msg and not attachments:
        def err():
            yield f"data: {json.dumps({'error': 'Empty message.'})}\n\n"
        return Response(stream_with_context(err()), mimetype="text/event-stream")

    username = session["username"]

    # Build multimodal content
    if attachments:
        parts = []
        if user_msg: parts.append({"type": "text", "text": user_msg})
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

    # Ensure chat exists in DB
    if USE_DB and chat_id and chat_id != "__title__":
        ensure_chat_exists(username, chat_id)

    # Load history for context memory
    history = get_chat_history(username, chat_id) if chat_id and chat_id != "__title__" else []
    messages = build_context(history, sys_prompt, new_content)

    def generate():
        full_reply = ""
        had_error  = False
        for chunk in stream_ai(model, messages):
            yield chunk
            if chunk.startswith("data: "):
                try:
                    d = json.loads(chunk[6:])
                    if "token" in d: full_reply += d["token"]
                    elif "done" in d: full_reply = d.get("full", full_reply)
                    elif "error" in d: had_error = True
                except: pass

        if not had_error and full_reply and chat_id and chat_id != "__title__":
            update_chat_after_stream(username, chat_id, user_msg or "[file]", full_reply)

    return Response(stream_with_context(generate()),
                    mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ── Chats ──────────────────────────────────────────────────────────────────────
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


# ══════════════════════════════════════════════════════════════════════════════
# ─────────────────────────── ADMIN PANEL ROUTES ───────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
# Admin is accessed at /admin/* — only accessible to the admin session

ADMIN_PREFIX = "/admin"

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not admin_logged_in():
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated

@app.route("/admin")
@app.route("/admin/")
def admin_root():
    if admin_logged_in(): return redirect(url_for("admin_dashboard"))
    return redirect(url_for("admin_login"))

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        pw = request.form.get("password", "")
        if verify_admin_pw(pw):
            session["admin"] = True
            return redirect(url_for("admin_dashboard"))
        error = "Wrong password."
    return render_template("admin.html", page="login", error=error)

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("admin_login"))

@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    cfg   = load_config()
    users = load_users()
    user_list = []
    for uname, udata in users.items():
        user_list.append({
            "username": uname,
            "created_at": udata.get("created_at", 0),
            "chats": count_user_chats(uname)
        })
    user_list.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    stats = {
        "users": len(users),
        "or_keys": len([k for k in cfg["openrouter_keys"] if k.strip()]),
        "groq_keys": len([k for k in cfg["groq_keys"] if k.strip()]),
        "custom_models": len(cfg["custom_models"]),
    }
    return render_template("admin.html", page="dashboard",
                           cfg=cfg, users=user_list, stats=stats)

@app.route("/admin/api/keys/openrouter", methods=["POST"])
@admin_required
def admin_set_or_keys():
    keys = [k.strip() for k in request.get_json().get("keys", []) if k.strip()]
    cfg = load_config(); cfg["openrouter_keys"] = keys; save_config(cfg)
    return jsonify({"ok": True, "count": len(keys)})

@app.route("/admin/api/keys/groq", methods=["POST"])
@admin_required
def admin_set_groq_keys():
    keys = [k.strip() for k in request.get_json().get("keys", []) if k.strip()]
    cfg = load_config(); cfg["groq_keys"] = keys; save_config(cfg)
    return jsonify({"ok": True, "count": len(keys)})

@app.route("/admin/api/keys/append", methods=["POST"])
@admin_required
def admin_append_keys():
    """Add new keys to existing list without replacing saved ones."""
    d = request.get_json()
    provider = d.get("provider", "openrouter")
    new_keys = [k.strip() for k in d.get("keys", []) if k.strip()]
    cfg = load_config()
    field = "groq_keys" if provider == "groq" else "openrouter_keys"
    existing = cfg.get(field, [])
    # Avoid exact duplicates
    for k in new_keys:
        if k not in existing:
            existing.append(k)
    cfg[field] = existing
    save_config(cfg)
    return jsonify({"ok": True, "count": len(existing)})

@app.route("/admin/api/keys/delete", methods=["POST"])
@admin_required
def admin_delete_key():
    """Delete a key by its index position."""
    d = request.get_json()
    provider = d.get("provider", "openrouter")
    index = d.get("index", -1)
    cfg = load_config()
    field = "groq_keys" if provider == "groq" else "openrouter_keys"
    keys = cfg.get(field, [])
    if 0 <= index < len(keys):
        keys.pop(index)
        cfg[field] = keys
        save_config(cfg)
    return jsonify({"ok": True, "count": len(keys)})

@app.route("/admin/api/models/add", methods=["POST"])
@admin_required
def admin_add_model():
    d = request.get_json()
    model_id = (d.get("id") or "").strip()
    model_name = (d.get("name") or "").strip()
    provider = (d.get("provider") or "openrouter").strip()
    tag = (d.get("tag") or "").strip()
    if not model_id or not model_name:
        return jsonify({"error": "Model ID and Name required."}), 400
    cfg = load_config()
    if any(m["id"] == model_id for m in cfg["custom_models"]):
        return jsonify({"error": "Model ID already exists."}), 409
    cfg["custom_models"].append({"id": model_id, "name": model_name, "provider": provider, "tag": tag})
    save_config(cfg)
    return jsonify({"ok": True})

@app.route("/admin/api/models/remove", methods=["POST"])
@admin_required
def admin_remove_model():
    model_id = (request.get_json().get("id") or "").strip()
    cfg = load_config()
    cfg["custom_models"] = [m for m in cfg["custom_models"] if m["id"] != model_id]
    save_config(cfg)
    return jsonify({"ok": True})

@app.route("/admin/api/users/delete", methods=["POST"])
@admin_required
def admin_delete_user():
    username = (request.get_json().get("username") or "").strip().lower()
    if not username: return jsonify({"error": "Username required."}), 400
    if USE_DB:
        db_delete_user(username)
    else:
        users = load_users()
        if username in users: del users[username]; save_users(users)
        safe = "".join(c for c in username if c.isalnum() or c in "_-")
        p = os.path.join(CHATS_D, f"{safe}.json")
        if os.path.exists(p): os.remove(p)
    return jsonify({"ok": True})

@app.route("/admin/api/users/list")
@admin_required
def admin_list_users():
    users = load_users()
    result = [{"username": u, "created_at": d.get("created_at", 0), "chats": count_user_chats(u)}
              for u, d in users.items()]
    result.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    return jsonify({"users": result})

@app.route("/admin/api/config/get")
@admin_required
def admin_get_config():
    cfg = load_config()
    def mask(k): return k[:8]+"..."+k[-4:] if len(k) > 12 else "****"
    return jsonify({
        "openrouter_keys": [mask(k) for k in cfg["openrouter_keys"]],
        "groq_keys": [mask(k) for k in cfg["groq_keys"]],
        "custom_models": cfg["custom_models"],
        "or_key_count": len(cfg["openrouter_keys"]),
        "groq_key_count": len(cfg["groq_keys"]),
    })

@app.route("/admin/api/admin/change-password", methods=["POST"])
@admin_required
def admin_change_pw():
    new_pw = (request.get_json().get("password") or "").strip()
    if len(new_pw) < 6: return jsonify({"error": "Password min 6 chars."}), 400
    set_admin_pw(new_pw)
    return jsonify({"ok": True})

@app.route("/admin/api/pending/list")
@admin_required
def admin_pending_list():
    pending = load_pending()
    result = [{"username": u, "requested_at": d.get("requested_at", 0)}
              for u, d in pending.items()]
    result.sort(key=lambda x: x["requested_at"], reverse=True)
    return jsonify({"pending": result})

@app.route("/admin/api/pending/approve", methods=["POST"])
@admin_required
def admin_approve_user():
    username = (request.get_json().get("username") or "").strip().lower()
    if not username: return jsonify({"error": "Username required."}), 400
    pw_hash = get_pending_pw(username)
    if not pw_hash: return jsonify({"error": "No pending request found."}), 404
    created = int(time.time())
    if USE_DB:
        try: db_create_user(username, pw_hash, created)
        except Exception as e: return jsonify({"error": str(e)}), 500
    else:
        users = load_users()
        users[username] = {"password": pw_hash, "created_at": created}
        save_users(users)
    remove_pending(username)
    return jsonify({"ok": True})

@app.route("/admin/api/pending/reject", methods=["POST"])
@admin_required
def admin_reject_user():
    username = (request.get_json().get("username") or "").strip().lower()
    if not username: return jsonify({"error": "Username required."}), 400
    remove_pending(username)
    return jsonify({"ok": True})

# ══════════════════════════════════════════════════════════════════════════════
# STARTUP
# ══════════════════════════════════════════════════════════════════════════════
if USE_DB:
    try:
        init_db()
        print("✅ Database initialized")
    except Exception as e:
        print(f"⚠️  Database init error: {e}")
else:
    print("📁 Running in local JSON mode (no DATABASE_URL set)")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, port=port, host="0.0.0.0", threaded=True)
