@echo off
rem Builds dist\LauncherScheduler.exe. Requires Python 3.10+ (python.org) in PATH.
cd /d "%~dp0"
python -m pip install -r requirements.txt pyinstaller || goto :error
python -m PyInstaller --noconfirm --clean --onefile --windowed --name LauncherScheduler ^
  --icon assets\icon.ico --add-data "assets;assets" --hidden-import pystray._win32 main.py || goto :error
echo.
echo Done: dist\LauncherScheduler.exe
pause
exit /b 0
:error
echo.
echo Build failed.
pause
exit /b 1
