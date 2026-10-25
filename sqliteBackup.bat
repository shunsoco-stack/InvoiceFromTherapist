@echo off
setlocal EnableExtensions

rem --- Lightsail から salon.sqlite3 を data\ に取得 ---
set "SSH_KEY=%USERPROFILE%\Downloads\LightsailDefaultKey-ap-northeast-1.pem"
set "REMOTE_HOST=35.75.205.230"
set "REMOTE_USER=ubuntu"
set "REMOTE_PATH=/home/ubuntu/InvoiceFromTherapist/data/salon.sqlite3"

rem このバッチがあるフォルダ直下の data\ へ保存（どこから実行しても同じ）
set "SCRIPT_DIR=%~dp0"
set "DEST_DIR=%SCRIPT_DIR%data"

if not exist "%SSH_KEY%" (
  echo [エラー] 秘密鍵が見つかりません:
  echo   %SSH_KEY%
  pause
  exit /b 1
)

if not exist "%DEST_DIR%" mkdir "%DEST_DIR%"

echo 取得中: %REMOTE_USER%@%REMOTE_HOST%:%REMOTE_PATH%
echo 保存先: %DEST_DIR%\
echo.

scp -i "%SSH_KEY%" "%REMOTE_USER%@%REMOTE_HOST%:%REMOTE_PATH%" "%DEST_DIR%\"

if errorlevel 1 (
  echo.
  echo [エラー] scp に失敗しました。
  pause
  exit /b 1
)

echo.
echo 完了: %DEST_DIR%\salon.sqlite3
pause
exit /b 0
