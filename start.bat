@echo off
echo Starting Smartex AI...
echo.
echo Main app:   http://localhost:5000
echo Admin panel: http://localhost:5001
echo.
start "Smartex Main" python app.py
timeout /t 2 >nul
start "Smartex Admin" python admin.py
pause
