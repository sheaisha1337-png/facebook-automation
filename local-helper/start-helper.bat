@echo off
title Kashif Local YouTube Downloader
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set "PYTHON_CMD=py") else (set "PYTHON_CMD=python")
%PYTHON_CMD% -m pip install --upgrade yt-dlp
if errorlevel 1 (echo Python 3 and pip are required.& pause & exit /b 1)
%PYTHON_CMD% helper.py
pause
