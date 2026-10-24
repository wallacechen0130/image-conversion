@echo off
cd /d "%~dp0"

if not exist ".venv-build\Scripts\python.exe" (
    echo [1/3] Creating build environment...
    python -m venv .venv-build
    .venv-build\Scripts\python.exe -m pip install --disable-pip-version-check -q -r requirements.txt pyinstaller
)

echo [2/3] Building ImageConverter.exe...
.venv-build\Scripts\pyinstaller.exe --noconfirm --clean --onefile --windowed ^
    --name ImageConverter ^
    --icon assets\icon.ico ^
    --add-data "assets\icon.ico;assets" ^
    --collect-all tkinterdnd2 ^
    converter.py

if errorlevel 1 (
    echo Build failed.
    exit /b 1
)

echo [3/3] Done: dist\ImageConverter.exe
