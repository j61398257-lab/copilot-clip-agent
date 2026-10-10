@echo off
cd /d "%~dp0"
where pythonw >nul 2>&1
if errorlevel 1 (
  echo 找不到 pythonw，无法打开窗口。
  echo 控制台版仍可使用：python "%~dp0clip_agent.py"
  pause
  exit /b 1
)
start "" pythonw "%~dp0clip_agent_gui.py" %*
