@echo off
title Sector Says What — Dashboard Only
color 0E
echo.
echo  ╔══════════════════════════════════════════╗
echo  ║    SECTOR SAYS WHAT — DASHBOARD ONLY      ║
echo  ║    (No iRacing connection required)        ║
echo  ╚══════════════════════════════════════════╝
echo.

cd /d "%~dp0python\sector_says_oversee"

echo [DASHBOARD] Starting Flask server on http://localhost:8080
echo [DASHBOARD] Press Ctrl+C to stop.
echo.

python app.py

echo.
echo [DASHBOARD] Server stopped.
pause
