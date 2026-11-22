@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在常驻监听，每轮间隔见 config.json 的 interval_minutes（Ctrl+C 退出）
python monitor.py --loop
pause
