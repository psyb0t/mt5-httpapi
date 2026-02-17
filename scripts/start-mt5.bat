@echo off
setlocal enabledelayedexpansion
set SHARED=C:\Users\Docker\Desktop\Shared
set LOGDIR=%SHARED%\logs
set INSTALL_LOG=%LOGDIR%\install.log
set PIP_LOG=%LOGDIR%\pip.log
set API_LOG=%LOGDIR%\api.log

mkdir "%LOGDIR%" 2>nul

call :log "%INSTALL_LOG%" "====== Boot ======"
call "%SHARED%\install.bat"
if !errorlevel! neq 0 (
    call :log "%INSTALL_LOG%" "ERROR: install.bat failed (exit code !errorlevel!)"
    pause
    exit /b 1
)

call :log "%PIP_LOG%" "Installing pip packages..."
python -m pip install --quiet -r "%SHARED%\requirements.txt" >> "%PIP_LOG%" 2>&1
if !errorlevel! neq 0 (
    call :log "%PIP_LOG%" "ERROR: pip install failed (exit code !errorlevel!)"
) else (
    call :log "%PIP_LOG%" "pip done."
)

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

call :log "%API_LOG%" "Using broker: !BROKER!, account: !ACCOUNT!"
call :log "%API_LOG%" "Terminal dir: !MT5DIR!"

if not exist "!MT5DIR!\terminal64.exe" (
    call :log "%API_LOG%" "ERROR: terminal64.exe not found in !MT5DIR!"
    goto :start_api
)

:: Write MT5 startup config with algo trading enabled + account from account.json
set MT5CFG=!MT5DIR!\mt5start.ini
python -c "import json,os;d=json.load(open(os.path.join(r'%SHARED%','account.json')));b=d.get('!BROKER!',{});a='!ACCOUNT!';c=b.get(a) if a else next(iter(b.values()),None) if b else None;f=open(r'!MT5CFG!','w');f.write('[Common]\nLogin='+str(c['login'])+'\nServer='+c['server']+'\nPassword='+c['password']+'\n[Experts]\nAllowLiveTrading=1\nAllowDllImport=1\nEnabled=1\n') if c else f.write('[Experts]\nAllowLiveTrading=1\nAllowDllImport=1\nEnabled=1\n');f.close()" >> "%API_LOG%" 2>&1
if errorlevel 1 (
    call :log "%API_LOG%" "WARNING: account.json not found or invalid, starting without login"
    (
    echo [Experts]
    echo AllowLiveTrading=1
    echo AllowDllImport=1
    echo Enabled=1
    ) > "!MT5CFG!"
)

call :log "%API_LOG%" "Starting MetaTrader 5..."
start "" "!MT5DIR!\terminal64.exe" /portable /config:"!MT5CFG!"

:: Give MT5 time to start before the API connects
timeout /t 10 /nobreak >nul

:start_api
call :log "%API_LOG%" "Starting HTTP API server..."
cd /d "%SHARED%"
python -m mt5api >> "%API_LOG%" 2>&1
exit /b 0

:: ══════════════════════════════════════════════════════════════════
:log
:: %~1 = log file, %~2 = message
echo [%date% %time%] %~2
echo [%date% %time%] %~2 >> "%~1"
exit /b 0
