@echo off
chcp 65001 >nul
cd /d %~dp0

set PY=python
where python >nul 2>nul
if errorlevel 1 (
  echo [!] 没找到 python，请先安装 Python 3.10+ 并勾选 "Add to PATH"
  pause
  exit /b 1
)

echo 正在启动 R1 自建 AI 后端...
%PY% server.py %*
pause
