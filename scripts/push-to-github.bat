@echo off
setlocal
cd /d "%~dp0.."

if not exist ".git" (
  echo ERROR: .git not found. Run this from repo root or keep scripts folder inside the project.
  exit /b 1
)

git status
echo.
set /p MSG=Commit message (empty=cancel): 
if "%MSG%"=="" (
  echo Cancelled.
  exit /b 1
)

git add -A
git commit -m "%MSG%"
if errorlevel 1 (
  echo Nothing to commit or commit failed.
  exit /b 1
)

git push origin HEAD
if errorlevel 1 (
  echo Push failed.
  exit /b 1
)

echo.
echo Push finished.
exit /b 0
