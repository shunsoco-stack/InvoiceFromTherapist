@echo off
REM Copy this file to deploy-local.bat and edit values (deploy-local.bat is gitignored).

set "LIGHTSAIL_PEM=C:\Users\YOUR_NAME\Downloads\LightsailDefaultKey-ap-northeast-1.pem"
set "LIGHTSAIL_USER=ubuntu"
set "LIGHTSAIL_HOST=35.75.205.230"

REM Branch tracked on the server (see: ssh ... "cd ~/InvoiceFromTherapist && git branch")
set "GIT_BRANCH=cursor/-bc-0ffebd94-1a8c-4abc-9df2-4ddb63e659b9-828c"

REM App directory on server (full path avoids ~ expansion issues in ssh -lc)
set "REMOTE_APP_DIR=/home/ubuntu/InvoiceFromTherapist"
