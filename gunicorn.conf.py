# gunicorn.conf.py
# Render deployment configuration

import os

# Bind to the port Render assigns
port = os.environ.get("PORT", "10000")
bind = f"0.0.0.0:{port}"

# Workers — 1 sync worker is best for SSE streaming on free tier
workers = 1
worker_class = "sync"

# Increase timeout for long AI streaming responses (90s)
timeout = 120

# Keep connections alive
keepalive = 5

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Don't use preload_app (causes issues with DB init timing)
preload_app = False
