@echo off
setlocal enabledelayedexpansion
set SHARED=C:\Users\Docker\Desktop\Shared
set LOGDIR=%SHARED%\logs
set INSTALL_LOG=%LOGDIR%\install.log
set PIP_LOG=%LOGDIR%\pip.log
set API_LOG=%LOGDIR%\api.log
set START_LOG=%LOGDIR%\start-mt5.log

mkdir "%LOGDIR%" 2>nul

call :log "%START_LOG%" "====== Boot ======"
call :log "%INSTALL_LOG%" "====== Boot ======"

:: ── Run install ──────────────────────────────────────────────────
call :log "%START_LOG%" "Running install.bat..."
call "%SHARED%\install.bat"
if !errorlevel! equ 3 (
    call :log "%START_LOG%" "Reboot scheduled by install.bat, stopping."
    exit /b 0
)
if !errorlevel! neq 0 (
    call :log "%START_LOG%" "ERROR: install.bat failed (exit code !errorlevel!)"
    pause
    exit /b 1
)
call :log "%START_LOG%" "install.bat done."

:: ── Kill MT5 auto-updater to prevent surprise reboots ───────────
call :log "%START_LOG%" "Killing MT5 updater processes..."
taskkill /f /im liveupdate.exe >nul 2>&1
taskkill /f /im mtupdate.exe >nul 2>&1

:: ── Pip install ──────────────────────────────────────────────────
call :log "%START_LOG%" "Installing pip packages..."
call :log "%PIP_LOG%" "Installing pip packages..."
python -m pip install --quiet -r "%SHARED%\requirements.txt" >> "%PIP_LOG%" 2>&1
if !errorlevel! neq 0 (
    call :log "%START_LOG%" "WARNING: pip install failed (exit code !errorlevel!)"
    call :log "%PIP_LOG%" "ERROR: pip install failed (exit code !errorlevel!)"
) else (
    call :log "%START_LOG%" "pip done."
    call :log "%PIP_LOG%" "pip done."
)

:: ── Kill lingering MT5 terminals ────────────────────────────────
call :log "%START_LOG%" "Killing lingering MT5 terminals..."
tasklist /fi "imagename eq terminal64.exe" 2>nul | find /i "terminal64.exe" >nul && (
    taskkill /f /im terminal64.exe >nul 2>&1
    timeout /t 2 /nobreak >nul
)

:: ── Verify terminals.json exists ────────────────────────────────
if not exist "%SHARED%\terminals.json" (
    call :log "%START_LOG%" "============================================"
    call :log "%START_LOG%" " ERROR: terminals.json not found!"
    call :log "%START_LOG%" " Create config/terminals.json and re-run."
    call :log "%START_LOG%" "============================================"
    exit /b 1
)

:: ── Launch MT5 terminals ─────────────────────────────────────────
call :log "%START_LOG%" "Launching MT5 terminals..."
set TERM_COUNT=0
for /f "usebackq delims=" %%L in (`python -c "import json;[print(t['broker'],t['account'],t['port']) for t in json.load(open(r'%SHARED%\terminals.json'))]" 2^>nul`) do (
    call :launch_terminal %%L
    set /a TERM_COUNT+=1
)

if !TERM_COUNT! equ 0 (
    call :log "%START_LOG%" "ERROR: No terminals configured in terminals.json"
    exit /b 1
)

call :log "%START_LOG%" "Launched !TERM_COUNT! terminal(s), waiting 10s to initialize..."
timeout /t 10 /nobreak >nul

:: Kill updater again after terminals had a chance to start ───────
taskkill /f /im liveupdate.exe >nul 2>&1
taskkill /f /im mtupdate.exe >nul 2>&1

:: ── Launch API processes ─────────────────────────────────────────
call :log "%START_LOG%" "Launching API processes..."
set API_IDX=0
for /f "usebackq delims=" %%L in (`python -c "import json;[print(t['broker'],t['account'],t['port']) for t in json.load(open(r'%SHARED%\terminals.json'))]" 2^>nul`) do (
    set /a API_IDX+=1
    if !API_IDX! equ !TERM_COUNT! (
        call :log "%START_LOG%" "All APIs launched. Running last one in foreground..."
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
    call :log "%START_LOG%" "ERROR: No base install for !LT_BROKER! at !LT_BASEDIR!"
    exit /b 1
)

if not exist "!LT_DIR!\terminal64.exe" (
    call :log "%START_LOG%" "Copying !LT_BROKER!\base to !LT_BROKER!\!LT_ACCOUNT!..."
    xcopy "!LT_BASEDIR!\*" "!LT_DIR!\" /E /I /H /Y /Q >nul 2>&1
)

call :write_ini "!LT_DIR!" "!LT_BROKER!" "!LT_ACCOUNT!"

call :log "%START_LOG%" "Starting terminal: !LT_BROKER!/!LT_ACCOUNT! (port !LT_PORT!)"
start "" "!LT_DIR!\terminal64.exe" /portable /config:"!LT_DIR!\mt5start.ini"
exit /b 0

:: ══════════════════════════════════════════════════════════════════
:launch_api_bg
set "LA_BROKER=%~1"
set "LA_ACCOUNT=%~2"
set "LA_PORT=%~3"
set "LA_LOG=%LOGDIR%\api-!LA_BROKER!-!LA_ACCOUNT!.log"

call :log "%START_LOG%" "Starting API (bg): !LA_BROKER!/!LA_ACCOUNT! on port !LA_PORT!"
start "" cmd /c "cd /d %SHARED% && python -m mt5api --broker !LA_BROKER! --account !LA_ACCOUNT! --port !LA_PORT! >> "!LA_LOG!" 2>&1"
exit /b 0

:: ══════════════════════════════════════════════════════════════════
:launch_api_fg
set "LA_BROKER=%~1"
set "LA_ACCOUNT=%~2"
set "LA_PORT=%~3"
set "LA_LOG=%LOGDIR%\api-!LA_BROKER!-!LA_ACCOUNT!.log"

call :log "%START_LOG%" "Starting API (fg): !LA_BROKER!/!LA_ACCOUNT! on port !LA_PORT!"
cd /d "%SHARED%"
python -m mt5api --broker !LA_BROKER! --account !LA_ACCOUNT! --port !LA_PORT! >> "!LA_LOG!" 2>&1
exit /b 0

:: ══════════════════════════════════════════════════════════════════
:write_ini
set "WI_DIR=%~1"
set "WI_BROKER=%~2"
set "WI_ACCOUNT=%~3"
set "WI_CFG=!WI_DIR!\mt5start.ini"
python -c "import json,os;d=json.load(open(os.path.join(r'%SHARED%','accounts.json')));b=d.get('!WI_BROKER!',{});a='!WI_ACCOUNT!';c=b.get(a) if a else next(iter(b.values()),None) if b else None;f=open(r'!WI_CFG!','w');f.write('[Common]\nLogin='+str(c['login'])+'\nServer='+c['server']+'\nPassword='+c['password']+'\nNewsEnable=0\n[Experts]\nAllowLiveTrading=1\nAllowDllImport=1\nEnabled=1\n[Email]\nEnable=0\n') if c else f.write('[Common]\nNewsEnable=0\n[Experts]\nAllowLiveTrading=1\nAllowDllImport=1\nEnabled=1\n[Email]\nEnable=0\n');f.close()" >> "%START_LOG%" 2>&1
if errorlevel 1 (
    call :log "%START_LOG%" "WARNING: Could not write ini for !WI_BROKER!/!WI_ACCOUNT!, using defaults"
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
echo [%date% %time%] %~2
echo [%date% %time%] %~2 >> "%~1"
exit /b 0
