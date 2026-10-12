@echo off
chcp 65001 >nul
title 加权（仓权）合约监控面板 · 曲合/天勤 双数据源
cd /d "%~dp0"
where python >nul 2>nul && (set PY=python) || (set PY=C:\Python314\python.exe)
echo 正在启动「加权（仓权）合约监控面板」...
echo 数据源可在页面顶栏随时切换（曲合网 / 天勤）。
%PY% 服务端.py --port 8689
pause
