@echo off
REM Launches the podcast-to-notion Telegram bot hidden (no console window).
REM Used by the auto-start task. Reads config from .env in this folder.
cd /d "%~dp0"
start "" /b "<HOME_PATH>\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe" "%~dp0telegram_bot.py"
