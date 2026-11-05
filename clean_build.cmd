@echo off
python "%~dp0scripts\clean_source_build.py" %*
exit /b %ERRORLEVEL%