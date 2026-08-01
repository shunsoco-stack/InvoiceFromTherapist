@echo off
setlocal
cd /d "%~dp0"

if not exist "deploy-local.bat" (
  echo ERROR: deploy-local.bat not found.
  echo Copy deploy-local.example.bat to deploy-local.bat and set PEM, HOST, BRANCH.
  exit /b 1
)
call deploy-local.bat
if errorlevel 1 exit /b 1

if not exist "%LIGHTSAIL_PEM%" (
  echo ERROR: PEM file not found: %LIGHTSAIL_PEM%
  exit /b 1
)

echo Deploying to %LIGHTSAIL_USER%@%LIGHTSAIL_HOST% ...
echo Remote: %REMOTE_APP_DIR% branch %GIT_BRANCH%
echo.

ssh -i "%LIGHTSAIL_PEM%" -o BatchMode=yes -o StrictHostKeyChecking=accept-new "%LIGHTSAIL_USER%@%LIGHTSAIL_HOST%" "bash -lc 'cd %REMOTE_APP_DIR% && git fetch origin && git pull origin %GIT_BRANCH% && . venv/bin/activate && pip install -r requirements.txt -q && sudo systemctl restart invoice.service && sudo systemctl is-active invoice.service'"

if errorlevel 1 (
  echo.
  echo ERROR: remote deploy failed. Try SSH manually and run commands from WORKFLOW.md
  exit /b 1
)

echo.
echo Done.
exit /b 0
