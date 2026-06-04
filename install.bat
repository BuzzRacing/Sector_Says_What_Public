@echo off
title Sector Says What — Installer
color 0A
echo.
echo  ╔══════════════════════════════════════════╗
echo  ║        SECTOR SAYS WHAT — INSTALL        ║
echo  ║      Live AI Race Commentary System       ║
echo  ╚══════════════════════════════════════════╝
echo.

:: ── Check Python ──
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found on PATH.
    echo         Install Python 3.10+ from https://python.org
    echo         Make sure "Add to PATH" is checked during install.
    pause
    exit /b 1
)
echo [OK] Python found:
python --version
echo.

:: ── Install pip packages ──
echo [INSTALL] Installing Python dependencies...
pip install --upgrade pip
pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo [ERROR] pip install failed. Check the output above.
    pause
    exit /b 1
)
echo.
echo [OK] Python packages installed.
echo.

:: ── Check FFmpeg ──
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo [WARNING] FFmpeg not found on PATH.
    echo           pydub needs FFmpeg for MP3 processing.
    echo.
    echo   Option A:  winget install ffmpeg
    echo   Option B:  Download from https://ffmpeg.org/download.html
    echo              and add the bin folder to your system PATH.
    echo.
) else (
    echo [OK] FFmpeg found.
)

:: ── Check FMOD DLLs ──
if exist "%~dp0audio\fmod\fmod.dll" (
    echo [OK] FMOD Core DLL found.
) else (
    echo [WARNING] FMOD Core DLL not found at:
    echo           %~dp0audio\fmod\fmod.dll
    echo.
    echo   Download FMOD Engine from https://www.fmod.com/download
    echo   Copy fmod.dll and fmodstudio.dll into:
    echo     %~dp0audio\fmod\
    echo.
)

:: ── Check API Keys ──
echo.
echo ── API Key Check ──
if defined OPENAI_API_KEY (
    echo [OK] OPENAI_API_KEY is set.
) else (
    echo [WARNING] OPENAI_API_KEY not set.
    echo   Set it:  setx OPENAI_API_KEY "sk-your-key-here"
    echo   Or enter it in the dashboard Settings panel at runtime.
)

if defined INWORLD_API_KEY (
    echo [OK] INWORLD_API_KEY is set.
) else (
    echo [WARNING] INWORLD_API_KEY not set.
    echo   Set it:  setx INWORLD_API_KEY "your-inworld-key"
    echo   Or enter it in the dashboard Settings panel at runtime.
)

:: ── Create missing folders ──
if not exist "%~dp0audio\fmod" mkdir "%~dp0audio\fmod"
if not exist "%~dp0audio\broadcast\stings" mkdir "%~dp0audio\broadcast\stings"
if not exist "%~dp0audio\broadcast\loops" mkdir "%~dp0audio\broadcast\loops"
if not exist "%~dp0audio\broadcast\commentary" mkdir "%~dp0audio\broadcast\commentary"
if not exist "%~dp0audio\ambienance" mkdir "%~dp0audio\ambienance"
if not exist "%~dp0python\sector_said" mkdir "%~dp0python\sector_said"
if not exist "%~dp0python\sector_said_exports" mkdir "%~dp0python\sector_said_exports"
echo.
echo [OK] Folder structure verified.

echo.
echo  ╔══════════════════════════════════════════╗
echo  ║          INSTALL COMPLETE                 ║
echo  ║                                           ║
echo  ║  1. Start iRacing and load into a session ║
echo  ║  2. Run:  run.bat                         ║
echo  ║  3. Open: http://localhost:8080            ║
echo  ╚══════════════════════════════════════════╝
echo.
pause
