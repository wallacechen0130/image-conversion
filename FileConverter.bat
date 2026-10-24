@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0converter.py"
) else (
    python "%~dp0converter.py"
)
