@echo off
title Sector Says What — LIVE
color 0A
echo.
echo  ╔══════════════════════════════════════════╗
echo  ║       SECTOR SAYS WHAT — LAUNCHING        ║
echo  ╚══════════════════════════════════════════╝
echo.

:: ── Move into the python folder ──
cd /d "%~dp0python"

:: ── Launch engine (connects to iRacing, starts dashboard) ──
echo [ENGINE] Starting Sector Says What engine...
echo [ENGINE] Dashboard will be at http://localhost:8080
echo [ENGINE] Press Ctrl+C to stop.
echo.

python sector_says_engine.py

echo.
echo [ENGINE] Sector Says What has stopped.
pause
