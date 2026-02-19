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
if !errorlevel! equ 3 (
    call :log "%INSTALL_LOG%" "Reboot scheduled by install.bat, stopping."
    exit /b 0
)
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

:: Kill lingering MT5 terminals
tasklist /fi "imagename eq terminal64.exe" 2>nul | find /i "terminal64.exe" >nul && (
    call :log "%API_LOG%" "Killing lingering MT5 terminals..."
    taskkill /f /im terminal64.exe >nul 2>&1
    timeout /t 2 /nobreak >nul
)

if not exist "%SHARED%\terminals.json" (
    call :log "%API_LOG%" "============================================"
    call :log "%API_LOG%" " ERROR: terminals.json not found!"
    call :log "%API_LOG%" " Create config/terminals.json and re-run."
    call :log "%API_LOG%" "============================================"
    exit /b 1
)

:: Read terminals.json and launch each terminal
set TERM_COUNT=0
for /f "usebackq delims=" %%L in (`python -c "import json;[print(t['broker'],t['account'],t['port']) for t in json.load(open(r'%SHARED%\terminals.json'))]" 2^>nul`) do (
    call :launch_terminal %%L
    set /a TERM_COUNT+=1
)

if !TERM_COUNT! equ 0 (
    call :log "%API_LOG%" "ERROR: No terminals configured in terminals.json"
    exit /b 1
)

:: Wait for terminals to initialize
call :log "%API_LOG%" "Waiting 10s for !TERM_COUNT! MT5 terminal(s) to initialize..."
timeout /t 10 /nobreak >nul

:: Launch all API processes (all but last as background, last in foreground)
set API_IDX=0
for /f "usebackq delims=" %%L in (`python -c "import json;[print(t['broker'],t['account'],t['port']) for t in json.load(open(r'%SHARED%\terminals.json'))]" 2^>nul`) do (
    set /a API_IDX+=1
    if !API_IDX! equ !TERM_COUNT! (
        call :launch_api_fg %%L
    ) else (
        call :launch_api_bg %%L
    )
)
exit /b 0

:: ══════════════════════════════════════════════════════════════════
:launch_terminal
:: %1=broker %2=account %3=port
set "LT_BROKER=%~1"
set "LT_ACCOUNT=%~2"
set "LT_PORT=%~3"
set "LT_BASEDIR=%SHARED%\!LT_BROKER!\base"
set "LT_DIR=%SHARED%\!LT_BROKER!\!LT_ACCOUNT!"

if not exist "!LT_BASEDIR!\terminal64.exe" (
    call :log "%API_LOG%" "ERROR: No base install for !LT_BROKER! at !LT_BASEDIR!"
    exit /b 1
)

:: Copy base to account dir if it doesn't exist
if not exist "!LT_DIR!\terminal64.exe" (
    call :log "%API_LOG%" "Copying !LT_BROKER!\base to !LT_BROKER!\!LT_ACCOUNT!..."
    xcopy "!LT_BASEDIR!\*" "!LT_DIR!\" /E /I /H /Y /Q >nul 2>&1
)

call :write_ini "!LT_DIR!" "!LT_BROKER!" "!LT_ACCOUNT!"

call :log "%API_LOG%" "Starting terminal: !LT_BROKER!/!LT_ACCOUNT! (port !LT_PORT!)"
start "" "!LT_DIR!\terminal64.exe" /portable /config:"!LT_DIR!\mt5start.ini"
exit /b 0

:: ══════════════════════════════════════════════════════════════════
:launch_api_bg
:: %1=broker %2=account %3=port — background API process
set "LA_BROKER=%~1"
set "LA_ACCOUNT=%~2"
set "LA_PORT=%~3"
set "LA_LOG=%LOGDIR%\api-!LA_BROKER!-!LA_ACCOUNT!.log"

call :log "%API_LOG%" "Starting API (bg): !LA_BROKER!/!LA_ACCOUNT! on port !LA_PORT!"
start "" cmd /c "cd /d %SHARED% && python -m mt5api --broker !LA_BROKER! --account !LA_ACCOUNT! --port !LA_PORT! >> "!LA_LOG!" 2>&1"
exit /b 0

:: ══════════════════════════════════════════════════════════════════
:launch_api_fg
:: %1=broker %2=account %3=port — foreground API process (keeps script alive)
set "LA_BROKER=%~1"
set "LA_ACCOUNT=%~2"
set "LA_PORT=%~3"
set "LA_LOG=%LOGDIR%\api-!LA_BROKER!-!LA_ACCOUNT!.log"

call :log "%API_LOG%" "Starting API (fg): !LA_BROKER!/!LA_ACCOUNT! on port !LA_PORT!"
cd /d "%SHARED%"
python -m mt5api --broker !LA_BROKER! --account !LA_ACCOUNT! --port !LA_PORT! >> "!LA_LOG!" 2>&1
exit /b 0

:: ══════════════════════════════════════════════════════════════════
:write_ini
:: %1=mt5dir %2=broker %3=account
set "WI_DIR=%~1"
set "WI_BROKER=%~2"
set "WI_ACCOUNT=%~3"
set "WI_CFG=!WI_DIR!\mt5start.ini"
python -c "import json,os;d=json.load(open(os.path.join(r'%SHARED%','accounts.json')));b=d.get('!WI_BROKER!',{});a='!WI_ACCOUNT!';c=b.get(a) if a else next(iter(b.values()),None) if b else None;f=open(r'!WI_CFG!','w');f.write('[Common]\nLogin='+str(c['login'])+'\nServer='+c['server']+'\nPassword='+c['password']+'\nNewsEnable=0\n[Experts]\nAllowLiveTrading=1\nAllowDllImport=1\nEnabled=1\n[Email]\nEnable=0\n') if c else f.write('[Common]\nNewsEnable=0\n[Experts]\nAllowLiveTrading=1\nAllowDllImport=1\nEnabled=1\n[Email]\nEnable=0\n');f.close()" >> "%API_LOG%" 2>&1
if errorlevel 1 (
    call :log "%API_LOG%" "WARNING: Could not write ini for !WI_BROKER!/!WI_ACCOUNT!, using defaults"
    echo [Common]> "!WI_CFG!"
    echo NewsEnable=0>> "!WI_CFG!"
    echo [Experts]>> "!WI_CFG!"
    echo AllowLiveTrading=1>> "!WI_CFG!"
    echo AllowDllImport=1>> "!WI_CFG!"
    echo Enabled=1>> "!WI_CFG!"
    echo [Email]>> "!WI_CFG!"
    echo Enable=0>> "!WI_CFG!"
)
exit /b 0

:: ══════════════════════════════════════════════════════════════════
:log
:: %~1 = log file, %~2 = message
echo [%date% %time%] %~2
echo [%date% %time%] %~2 >> "%~1"
exit /b 0
