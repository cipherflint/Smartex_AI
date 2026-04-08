#!/bin/bash
echo "Starting Smartex AI..."
echo "Main app:    http://localhost:5000"
echo "Admin panel: http://localhost:5001/login"
python3 app.py &
python3 admin.py &
wait
