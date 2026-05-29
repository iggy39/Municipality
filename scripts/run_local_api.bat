@echo off
setlocal
set ROOT=%~dp0..
cd /d "%ROOT%"
python -m uvicorn municipality.api:app --app-dir src --host 127.0.0.1 --port 8000
