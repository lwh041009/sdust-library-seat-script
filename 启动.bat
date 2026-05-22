@echo off
chcp 65001
:: 切换到当前文件所在的目录
cd /d %~dp0

echo 正在启动 图书馆抢座引擎...
"D:\anaconda3\python.exe" main.py

pause