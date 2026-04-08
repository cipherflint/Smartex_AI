# Smartex AI — Render Deployment Guide

## What's different from the local version

| Feature | Local | Render |
|---|---|---|
| Storage | JSON files | PostgreSQL database |
| API keys | `data/config.json` | Stored in DB via admin panel |
| Admin panel | `localhost:5001` | `yourapp.onrender.com/admin` |
| Port | 5000 / 5001 | Auto-assigned by Render |
| Persistence | Files on disk | PostgreSQL (survives restarts) |

---

## Step 1 — Push to GitHub

1. Create a new **private** repository on GitHub (private keeps your code safe)
2. Upload all files from this folder to the repo root:
   ```
   app.py
   requirements.txt
   gunicorn.conf.py
   render.yaml
   templates/
     auth.html
     index.html
     admin.html
   ```
3. Make sure `data/` folder is NOT pushed (no sensitive data in git)

Add a `.gitignore`:
```
data/
__pycache__/
*.pyc
venv/
.env
```

---

## Step 2 — Create Render account

Go to **https://render.com** → sign up free with GitHub.

---

## Step 3 — Create PostgreSQL database

1. In Render dashboard → **New** → **PostgreSQL**
2. Name: `smartex-db`
3. Plan: **Free**
4. Region: Choose closest to you (Singapore for Bangladesh)
5. Click **Create Database**
6. Wait ~1 minute, then copy the **Internal Database URL**

---

## Step 4 — Create Web Service

1. In Render dashboard → **New** → **Web Service**
2. Connect your GitHub repo
3. Settings:
   - **Name**: `smartex-ai`
   - **Region**: Same as your database
   - **Branch**: `main`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app -c gunicorn.conf.py`
   - **Plan**: **Free**

4. Click **Advanced** → **Add Environment Variable**:

   | Key | Value |
   |-----|-------|
   | `DATABASE_URL` | Paste the Internal Database URL from Step 3 |
   | `SECRET_KEY` | Any random string (e.g. `myrandomsecret123abc`) |

5. Click **Create Web Service**

---

## Step 5 — Wait for deploy

Render will:
1. Install Python packages (~1-2 min)
2. Start the app
3. Auto-create database tables on first run
4. Give you a URL like `https://smartex-ai.onrender.com`

You can watch logs in real-time in the Render dashboard.

---

## Step 6 — Set up Admin Panel

1. Go to `https://your-app.onrender.com/admin`
2. Default password: **`admin123`**
3. **Change password immediately** in Settings tab
4. Go to **API Keys** tab and add your OpenRouter/Groq keys
5. Optionally add custom models

---

## Step 7 — Done!

Share `https://your-app.onrender.com` with anyone.
They register an account and start chatting.

---

## Important Notes

### Free tier limits
- Render free web services **spin down after 15 minutes of inactivity**
- First request after spin-down takes ~30 seconds to wake up
- Free PostgreSQL has 1GB storage limit
- To avoid spin-down, use a free uptime monitor like **UptimeRobot** to ping your URL every 5 minutes

### UptimeRobot setup (keep app awake)
1. Go to https://uptimerobot.com → sign up free
2. Add new monitor → HTTP(s)
3. URL: `https://your-app.onrender.com`
4. Interval: 5 minutes
5. This prevents the 30-second cold start

### Streaming on Render
The app uses SSE (Server-Sent Events) for real-time streaming.
Render supports this but the free tier may add a small buffer delay.
This is normal — streaming still works, just may have a tiny initial delay.

### Admin security
The admin panel is at `/admin` — there's no IP restriction on Render free tier.
Your password is the only protection, so choose a strong one.

---

## Updating the app

Any push to your GitHub `main` branch will automatically redeploy on Render
(if you enabled Auto-Deploy, which is on by default).

---

## Local development (still works)

```bash
pip install -r requirements.txt
python app.py   # Runs on localhost:5000 using JSON files
# Admin panel: localhost:5000/admin
```

No DATABASE_URL = local JSON file mode automatically.
