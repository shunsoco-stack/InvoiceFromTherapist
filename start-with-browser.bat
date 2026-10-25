@echo off
cd /d "%~dp0"

if not exist "venv\Scripts\activate.bat" goto NO_VENV

start "InvoiceFromTherapist_v2" /D "%~dp0" cmd /k "call venv\Scripts\activate.bat && python run.py"
timeout /t 2 /nobreak >nul
start "" "http://localhost:5000/"
goto :EOF

:NO_VENV
echo ERROR: venv not found. In this folder run:
echo   python -m venv venv
echo   venv\Scripts\activate
echo   pip install -r requirements.txt
pause
exit /b 1
