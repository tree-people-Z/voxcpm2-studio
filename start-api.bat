@echo off
cd /d "%~dp0"
echo VoxCPM2 API 服务（无界面，供 Tree / 脚本调用）
echo   http://127.0.0.1:7810/api/health
echo.
echo 注意：这个服务和 run.bat 起的界面共用一块 GPU，
echo       两者不要同时开（各会加载一份模型，约 7.9 GB）。
echo 日常使用请运行 run.bat。
echo.
python -X utf8 server\main.py
if errorlevel 1 pause
