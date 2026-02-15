@echo off
setlocal enabledelayedexpansion
set SHARED=C:\Users\Docker\Desktop\Shared
set LOGDIR=%SHARED%\logs
set SETUP_LOG=%LOGDIR%\setup.log
set PIP_LOG=%LOGDIR%\pip.log
set API_LOG=%LOGDIR%\api.log

mkdir "%LOGDIR%" 2>nul

:: Re-run install if flag file is present
if exist "%SHARED%\reinstall.flag" (
    echo [%date% %time%] reinstall.flag detected, running install.bat... >> "%SETUP_LOG%"
    del "%SHARED%\reinstall.flag"
    call "%SHARED%\install.bat"
)

echo [%date% %time%] ====== Boot ====== >> "%SETUP_LOG%"
echo [%date% %time%] Running setup.bat... >> "%SETUP_LOG%"
call "%SHARED%\setup.bat" >> "%SETUP_LOG%" 2>&1
echo [%date% %time%] setup.bat done. >> "%SETUP_LOG%"

echo [%date% %time%] Installing pip packages... >> "%PIP_LOG%"
python -m pip install --quiet -r "%SHARED%\requirements.txt" >> "%PIP_LOG%" 2>&1
echo [%date% %time%] pip done. >> "%PIP_LOG%"

:: Read broker and account from terminal.json
set BROKER=default
set ACCOUNT=
for /f "usebackq delims=" %%L in (`python -c "import json;t=json.load(open(r'%SHARED%\terminal.json'));print(t.get('broker','default'));print(t.get('account',''))" 2^>nul`) do (
    if not defined _GOT_BROKER (
        set "BROKER=%%L"
        set _GOT_BROKER=1
    ) else (
        set "ACCOUNT=%%L"
    )
)
set _GOT_BROKER=
set "MT5DIR=%SHARED%\!BROKER!"

echo [%date% %time%] Using broker: !BROKER!, account: !ACCOUNT! >> "%API_LOG%"
echo [%date% %time%] Terminal dir: !MT5DIR! >> "%API_LOG%"

if not exist "!MT5DIR!\terminal64.exe" (
    echo [%date% %time%] ERROR: terminal64.exe not found in !MT5DIR! >> "%API_LOG%"
    echo ERROR: terminal64.exe not found in !MT5DIR! >> "%API_LOG%" 2>&1
    goto :start_api
)

:: Write MT5 startup config with algo trading enabled + account from account.json
set MT5CFG=!MT5DIR!\mt5start.ini
python -c "import json,os;d=json.load(open(os.path.join(r'%SHARED%','account.json')));b=d.get('!BROKER!',{});a='!ACCOUNT!';c=b.get(a) if a else next(iter(b.values()),None) if b else None;f=open(r'!MT5CFG!','w');f.write('[Common]\nLogin='+str(c['login'])+'\nServer='+c['server']+'\nPassword='+c['password']+'\n[Experts]\nAllowLiveTrading=1\nAllowDllImport=1\nEnabled=1\n') if c else f.write('[Experts]\nAllowLiveTrading=1\nAllowDllImport=1\nEnabled=1\n');f.close()" >> "%API_LOG%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] account.json not found or invalid, starting without login >> "%API_LOG%"
    (
    echo [Experts]
    echo AllowLiveTrading=1
    echo AllowDllImport=1
    echo Enabled=1
    ) > "!MT5CFG!"
)

echo [%date% %time%] Starting MetaTrader 5... >> "%API_LOG%"
start "" "!MT5DIR!\terminal64.exe" /portable /config:"!MT5CFG!"

:: Give MT5 time to start before the API connects
timeout /t 10 /nobreak >nul

:start_api
echo [%date% %time%] Starting HTTP API server... >> "%API_LOG%"
cd /d "%SHARED%"
python -m mt5api >> "%API_LOG%" 2>&1
