@echo off
title Sythion Launcher
echo ========================================================
echo               Starting Sythion (Synth)
echo ========================================================
echo.

cd /d "%~dp0"

echo [1/2] Starting Python FastAPI Backend on http://127.0.0.1:8000...
set PYTHONPATH=backend
start "Sythion Backend" cmd /k "cd /d "%~dp0" && set PYTHONPATH=backend && python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload"

echo [2/2] Launching Tauri Desktop Shell...
timeout /t 3 /nobreak >nul
npx --prefix frontend tauri dev

echo.
echo Sythion session finished.
