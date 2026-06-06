@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

python cleanup_reqable_capture.py --keep-hours 24 --delete

pause
