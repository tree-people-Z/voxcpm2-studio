@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   VoxCPM2 Studio
echo ============================================
python main.py
if errorlevel 1 pause
