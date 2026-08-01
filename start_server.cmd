@echo off
REM Starts the JARVIS backend with logs captured to a file that opencode can read.
cd /d C:\Ai\jarvis
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 >> C:\Users\ADMINI~1\AppData\Local\Temp\opencode\jarvis_out.log 2>&1
