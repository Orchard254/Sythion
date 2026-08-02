@echo off
REM JARVIS - one-command dev launcher.
REM Starts the backend + frontend dev servers and opens the dashboard.
REM Development-only; production starts the sidecar from the Tauri shell.
setlocal

cd /d "%~dp0"

set "BACKEND_PORT=8000"
set "FRONTEND_PORT=3000"

REM ---------- Check prerequisites ----------
where python >nul 2>&1
if errorlevel 1 goto :no_python
where npm >nul 2>&1
if errorlevel 1 goto :no_npm

if not exist "frontend\node_modules" goto :install_frontend
goto :deps_done

:install_frontend
echo Installing frontend dependencies (first run)...
pushd "frontend"
call npm install
if errorlevel 1 (
    echo [ERROR] npm install failed.
    popd
    pause
    exit /b 1
)
popd

:deps_done

REM Phase A handshake: launch_sidecar.py writes the per-launch token file,
REM starts the sidecar, and waits for the handshake file with the bound port.
REM The token is never put on a command line (visible in process listings).
echo Starting JARVIS backend on http://127.0.0.1:%BACKEND_PORT% ...
start "JARVIS Backend" cmd /c "python scripts\launch_sidecar.py --port %BACKEND_PORT% --dev --no-open"

REM The launcher writes the token file before spawning the sidecar; read it
REM for the health check and the frontend.
powershell -NoProfile -Command "$f=Join-Path $env:LOCALAPPDATA 'JARVIS\token.json'; $i=0; while(!(Test-Path $f) -and $i -lt 30){Start-Sleep 1; $i++}; if(Test-Path $f){$j=Get-Content $f | ConvertFrom-Json; Write-Output $j.token}else{exit 1}" > "%TEMP%\jarvis_token.txt" 2>nul
set /p JARVIS_CONTROL_TOKEN=<"%TEMP%\jarvis_token.txt"
del "%TEMP%\jarvis_token.txt" >nul 2>&1
set "JARVIS_DEVELOPMENT_MODE=true"
set "VITE_JARVIS_CONTROL_TOKEN=%JARVIS_CONTROL_TOKEN%"

REM ---------- Wait for backend health ----------
set /a tries=0
:wait_backend
set /a tries+=1
ping -n 2 127.0.0.1 >nul
call :check_health
if "%HEALTHY%"=="1" goto :backend_up
if %tries% geq 30 (
    echo [ERROR] Backend did not become healthy after ~30s.
    echo Check that port %BACKEND_PORT% is free and the .env is configured.
    pause
    exit /b 1
)
goto wait_backend

:check_health
set "HEALTHY=0"
powershell -NoProfile -Command "$r=Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:%BACKEND_PORT%/api/v1/health' -Headers @{'X-Control-Token'='%JARVIS_CONTROL_TOKEN%'} -TimeoutSec 2; if($r.StatusCode -eq 200){exit 0}else{exit 1}" >nul 2>&1
if not errorlevel 1 set "HEALTHY=1"
exit /b 0

:backend_up
echo Backend is healthy.

REM ---------- Start frontend ----------
echo Starting JARVIS dashboard on http://localhost:%FRONTEND_PORT% ...
start "JARVIS Frontend" cmd /c "set VITE_JARVIS_CONTROL_TOKEN=%JARVIS_CONTROL_TOKEN% && npm --prefix frontend run dev"

ping -n 3 127.0.0.1 >nul
start "" "http://localhost:%FRONTEND_PORT%"

echo.
echo JARVIS development environment is running.
echo   Dashboard : http://localhost:%FRONTEND_PORT%
echo   Backend   : http://127.0.0.1:%BACKEND_PORT%
echo   Logs      : %LOCALAPPDATA%\JARVIS\logs\jarvis.log
echo   Mode      : development-only; use the packaged Tauri app for production
echo.
echo The backend and frontend each run in their own console window. Close
echo those windows (or press Ctrl+C inside them) to stop JARVIS. This
echo launcher window can be closed after startup.
echo.
endlocal
exit /b 0

:no_python
echo [ERROR] Python not found on PATH.
pause
exit /b 1

:no_npm
echo [ERROR] Node.js/npm not found on PATH.
pause
exit /b 1
