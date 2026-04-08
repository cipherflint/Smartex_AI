"""
Smartex AI — Admin Panel  (localhost:5001)
Manages: API keys (OpenRouter + Groq), custom AI models, user accounts.
Only accessible from localhost:5001.
"""
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import json, os, hashlib, secrets, time

admin_app = Flask(__name__, template_folder="templates")
admin_app.secret_key = "smartex_admin_secret_2025"

BASE    = os.path.dirname(__file__)
DATA    = os.path.join(BASE, "data")
USERS_F = os.path.join(DATA, "users.json")
CHATS_D = os.path.join(DATA, "chats")
CFG_F   = os.path.join(DATA, "config.json")

ADMIN_PASSWORD_FILE = os.path.join(DATA, "admin.json")

# ── Admin credential management ────────────────────────────────────────────────
def load_admin():
    try:
        with open(ADMIN_PASSWORD_FILE) as f: return json.load(f)
    except:
        # Default admin password: "admin123" — change on first login
        salt = "defaultsalt"
        h = hashlib.sha256((salt + "admin123").encode()).hexdigest()
        return {"password_hash": f"{salt}:{h}"}

def verify_admin_pw(given):
    a = load_admin()
    stored = a.get("password_hash", "")
    try:
        salt, h = stored.split(":", 1)
        return hashlib.sha256((salt + given).encode()).hexdigest() == h
    except: return False

def set_admin_pw(new_pw):
    salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + new_pw).encode()).hexdigest()
    with open(ADMIN_PASSWORD_FILE, "w") as f:
        json.dump({"password_hash": f"{salt}:{h}"}, f)

# ── Config helpers ─────────────────────────────────────────────────────────────
def load_config():
    try:
        with open(CFG_F) as f: c = json.load(f)
    except: c = {}
    c.setdefault("openrouter_keys", [])
    c.setdefault("groq_keys", [])
    c.setdefault("custom_models", [])
    return c

def save_config(c):
    with open(CFG_F, "w") as f: json.dump(c, f, indent=2)

# ── Users helpers ──────────────────────────────────────────────────────────────
def load_users():
    try:
        with open(USERS_F) as f: return json.load(f)
    except: return {}

def save_users(u):
    with open(USERS_F, "w") as f: json.dump(u, f, indent=2)

def del_user_data(username):
    safe = "".join(c for c in username if c.isalnum() or c in "_-")
    p = os.path.join(CHATS_D, f"{safe}.json")
    if os.path.exists(p): os.remove(p)

def count_user_chats(username):
    safe = "".join(c for c in username if c.isalnum() or c in "_-")
    p = os.path.join(CHATS_D, f"{safe}.json")
    try:
        with open(p) as f: d = json.load(f)
        return len(d)
    except: return 0

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated

# ════════════════════════════════════════════════════════════════════════════════
# Routes
# ════════════════════════════════════════════════════════════════════════════════

@admin_app.route("/")
@admin_required
def admin_index():
    return redirect(url_for("admin_dashboard"))

@admin_app.route("/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        pw = request.form.get("password", "")
        if verify_admin_pw(pw):
            session["admin"] = True
            return redirect(url_for("admin_dashboard"))
        error = "Wrong password."
    return render_template("admin.html", page="login", error=error)

@admin_app.route("/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))

@admin_app.route("/dashboard")
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

# ── API Keys ───────────────────────────────────────────────────────────────────
@admin_app.route("/api/keys/openrouter", methods=["POST"])
@admin_required
def set_or_keys():
    keys = request.get_json().get("keys", [])
    keys = [k.strip() for k in keys if k.strip()]
    cfg = load_config()
    cfg["openrouter_keys"] = keys
    save_config(cfg)
    return jsonify({"ok": True, "count": len(keys)})

@admin_app.route("/api/keys/groq", methods=["POST"])
@admin_required
def set_groq_keys():
    keys = request.get_json().get("keys", [])
    keys = [k.strip() for k in keys if k.strip()]
    cfg = load_config()
    cfg["groq_keys"] = keys
    save_config(cfg)
    return jsonify({"ok": True, "count": len(keys)})

# ── Custom Models ──────────────────────────────────────────────────────────────
@admin_app.route("/api/models/add", methods=["POST"])
@admin_required
def add_model():
    d = request.get_json()
    model_id   = (d.get("id") or "").strip()
    model_name = (d.get("name") or "").strip()
    provider   = (d.get("provider") or "openrouter").strip()
    tag        = (d.get("tag") or "").strip()
    if not model_id or not model_name:
        return jsonify({"error": "Model ID and Name are required."}), 400
    cfg = load_config()
    # Check for duplicate
    if any(m["id"] == model_id for m in cfg["custom_models"]):
        return jsonify({"error": "Model ID already exists."}), 409
    cfg["custom_models"].append({"id": model_id, "name": model_name, "provider": provider, "tag": tag})
    save_config(cfg)
    return jsonify({"ok": True})

@admin_app.route("/api/models/remove", methods=["POST"])
@admin_required
def remove_model():
    model_id = (request.get_json().get("id") or "").strip()
    cfg = load_config()
    cfg["custom_models"] = [m for m in cfg["custom_models"] if m["id"] != model_id]
    save_config(cfg)
    return jsonify({"ok": True})

# ── User Management ────────────────────────────────────────────────────────────
@admin_app.route("/api/users/delete", methods=["POST"])
@admin_required
def delete_user():
    username = (request.get_json().get("username") or "").strip().lower()
    if not username: return jsonify({"error": "Username required."}), 400
    users = load_users()
    if username in users:
        del users[username]
        save_users(users)
    del_user_data(username)
    return jsonify({"ok": True})

@admin_app.route("/api/users/list")
@admin_required
def list_users():
    users = load_users()
    result = []
    for uname, udata in users.items():
        result.append({
            "username": uname,
            "created_at": udata.get("created_at", 0),
            "chats": count_user_chats(uname)
        })
    result.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    return jsonify({"users": result})

@admin_app.route("/api/config/get")
@admin_required
def get_config():
    cfg = load_config()
    # Mask keys for display
    def mask(k): return k[:8] + "..." + k[-4:] if len(k) > 12 else "****"
    return jsonify({
        "openrouter_keys": [mask(k) for k in cfg["openrouter_keys"]],
        "groq_keys": [mask(k) for k in cfg["groq_keys"]],
        "custom_models": cfg["custom_models"],
        "or_key_count": len(cfg["openrouter_keys"]),
        "groq_key_count": len(cfg["groq_keys"]),
    })

@admin_app.route("/api/admin/change-password", methods=["POST"])
@admin_required
def change_admin_pw():
    d = request.get_json()
    new_pw = (d.get("password") or "").strip()
    if len(new_pw) < 6: return jsonify({"error": "Password min 6 chars."}), 400
    set_admin_pw(new_pw)
    return jsonify({"ok": True})

if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    print("🔐 Admin panel: http://localhost:5001/login")
    print("   Default password: admin123  (change immediately!)")
    admin_app.run(debug=True, port=5001, host="127.0.0.1", threaded=True)
