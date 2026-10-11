@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>&1
if errorlevel 1 (
  echo python not found
  pause
  exit /b 1
)
python -X utf8 "%~dp0install_deps.py"
echo.
pause
