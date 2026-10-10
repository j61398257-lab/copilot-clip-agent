@echo off
cd /d "%~dp0"
python "%~dp0clip_agent.py" %*
echo.
pause
