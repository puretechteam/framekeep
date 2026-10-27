@echo off
REM -------------------------------
REM FrameKeep Dependency Installer
REM -------------------------------

REM --- License / Source Notice ---
echo FrameKeep - (C) 2025 Tanner Lucier
echo Licensed under the MIT License - see LICENSE file for details
echo.

cd /d "%~dp0"

REM --- Check if Python is installed ---
python --version >nul 2>&1
IF ERRORLEVEL 1 (
    echo Python is NOT installed or not in PATH.
    echo Please install Python 3.10 or newer before running this script.
    echo Or skip all of this and download FrameKeep.exe from the Releases page.
    pause
    exit /b
)

echo Installing required packages...
echo.
python -m pip install -r requirements.txt
IF ERRORLEVEL 1 (
    echo Package install failed.
    pause
    exit /b
)

echo.
echo Downloading FFmpeg (H.266, AV1 and the built-in player)...
python tools\fetch_ffmpeg.py
IF ERRORLEVEL 1 (
    echo FFmpeg download failed. You can also point FrameKeep at your own ffmpeg.exe in Settings.
    pause
    exit /b
)

echo.
echo All dependencies installed. Double-click FrameKeep.pyw to start.
pause
