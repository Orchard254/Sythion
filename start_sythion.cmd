@echo off
setlocal
title Sythion Launcher
echo ========================================================
echo               Starting Sythion (Synth)
echo ========================================================
echo.

cd /d "%~dp0"
if errorlevel 1 (
    echo Could not open the Sythion project directory.
    exit /b 1
)

REM Tauri owns the Python sidecar and selects a free loopback port.
REM Never start a second uvicorn instance here: it can contend for the
REM single-instance profile lock and corrupt the launch handshake.
echo Launching the Tauri desktop shell and its managed backend...
echo Do not start a separate Python backend while this desktop app runs.
call npx --prefix frontend tauri dev
set "SYTHION_LAUNCH_EXIT_CODE=%ERRORLEVEL%"

if not "%SYTHION_LAUNCH_EXIT_CODE%"=="0" (
    echo.
    echo Sythion desktop exited with error %SYTHION_LAUNCH_EXIT_CODE%.
    exit /b %SYTHION_LAUNCH_EXIT_CODE%
)
echo.
echo Sythion desktop session finished.
exit /b 0
