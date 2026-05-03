@echo off
:: api_runner.bat  broker  account  port  token  utc_offset
:: Wraps the Python API process so start/exit/exitcode are logged.
setlocal enabledelayedexpansion
set "AR_BROKER=%~1"
set "AR_ACCOUNT=%~2"
set "AR_PORT=%~3"
set "AR_TOKEN=%~4"
set "AR_OFFSET=%~5"
if "!AR_OFFSET!"=="" set "AR_OFFSET=0"
set "SHARED=C:\Users\Docker\Desktop\Shared"
set "LOGDIR=%SHARED%\logs"
set "AR_LOG=%LOGDIR%\api-!AR_BROKER!-!AR_ACCOUNT!.log"
set "FULL_LOG=%LOGDIR%\full.log"
set "PYDIR=C:\Program Files\Python312"

mkdir "%LOGDIR%" 2>nul

echo [%DATE% %TIME%] [api:!AR_BROKER!/!AR_ACCOUNT!] === PROCESS STARTED on port !AR_PORT! (utc_offset=!AR_OFFSET!) === >> "!AR_LOG!"
echo [%DATE% %TIME%] [start] [api:!AR_BROKER!/!AR_ACCOUNT!] PROCESS STARTED on port !AR_PORT! utc_offset=!AR_OFFSET! >> "%FULL_LOG%"

cd /d "%SHARED%"
"%PYDIR%\python.exe" -m mt5api --broker !AR_BROKER! --account !AR_ACCOUNT! --port !AR_PORT! --token "!AR_TOKEN!" --utc-offset "!AR_OFFSET!" >> "!AR_LOG!" 2>&1
set "AR_EC=!ERRORLEVEL!"

echo [%DATE% %TIME%] [api:!AR_BROKER!/!AR_ACCOUNT!] === PROCESS EXITED exit_code=!AR_EC! === >> "!AR_LOG!"
echo [%DATE% %TIME%] [start] [api:!AR_BROKER!/!AR_ACCOUNT!] PROCESS EXITED exit_code=!AR_EC! >> "%FULL_LOG%"
endlocal
