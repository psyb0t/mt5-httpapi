@echo off
setlocal enabledelayedexpansion
set SHARED=C:\Users\Docker\Desktop\Shared
set SCRIPTS=%SHARED%\scripts
set CONFIG=%SHARED%\config
set BROKERS=%SHARED%\terminals
set LOGDIR=%SHARED%\logs
set INSTALL_LOG=%LOGDIR%\install.log
set PIP_LOG=%LOGDIR%\pip.log
set START_LOG=%LOGDIR%\start.log
set FULL_LOG=%LOGDIR%\full.log
set "PYDIR=C:\Program Files\Python312"
set "PATH=%PYDIR%;%PYDIR%\Scripts;%PATH%"
set "LOCKDIR=%SHARED%\start.running"

mkdir "%LOGDIR%" 2>nul
rmdir "%FULL_LOG%.lock" 2>nul

:: ── Atomic lock (only one start.bat instance at a time) ──────────
mkdir "%LOCKDIR%" 2>nul
if !errorlevel! neq 0 (
    echo [%date% %time%] Another start.bat is already running, exiting.
    echo [%date% %time%] Another start.bat is already running, exiting. >> "%START_LOG%"
    echo [%date% %time%] [start] Another start.bat is already running, exiting. >> "%FULL_LOG%"
    exit /b 0
)

call :log "%START_LOG%" "====== Boot ======"
call :log "%INSTALL_LOG%" "====== Boot ======"

:: ── Run install ──────────────────────────────────────────────────
call :log "%START_LOG%" "Running install.bat..."
call "%SCRIPTS%\install.bat"
if !errorlevel! equ 3 (
    call :log "%START_LOG%" "Reboot scheduled by install.bat, stopping."
    rmdir "%LOCKDIR%" 2>nul
    exit /b 0
)
if !errorlevel! neq 0 (
    call :log "%START_LOG%" "ERROR: install.bat failed (exit code !errorlevel!)"
    rmdir "%LOCKDIR%" 2>nul
    exit /b 1
)
call :log "%START_LOG%" "install.bat done."

:: ── Pip install ──────────────────────────────────────────────────
call :log "%START_LOG%" "Installing pip packages..."
call :log "%PIP_LOG%" "Installing pip packages..."
"%PYDIR%\python.exe" -m pip install --quiet -r "%CONFIG%\requirements.txt" >> "%PIP_LOG%" 2>&1
if !errorlevel! neq 0 (
    call :log "%START_LOG%" "ERROR: pip install failed (exit code !errorlevel!), aborting."
    call :log "%PIP_LOG%" "ERROR: pip install failed (exit code !errorlevel!)"
    rmdir "%LOCKDIR%" 2>nul
    exit /b 1
)
call :log "%START_LOG%" "pip done."
call :log "%PIP_LOG%" "pip done."

:: ── Kill lingering MT5 terminals ────────────────────────────────
call :log "%START_LOG%" "Killing lingering MT5 terminals..."
tasklist /fi "imagename eq terminal64.exe" 2>nul | find /i "terminal64.exe" >nul && (
    taskkill /f /im terminal64.exe >nul 2>&1
    timeout /t 2 /nobreak >nul
)

:: ── Verify terminals.json exists ────────────────────────────────
if not exist "%CONFIG%\terminals.json" (
    call :log "%START_LOG%" "ERROR: terminals.json not found! Create config/terminals.json and re-run."
    rmdir "%LOCKDIR%" 2>nul
    exit /b 1
)

:: ── Parse terminals.json once ───────────────────────────────────
set "TERM_LIST=%TEMP%\mt5_terminals.txt"
"%PYDIR%\python.exe" -c "import json;[print(t['broker'],t['account'],t['port']) for t in json.load(open(r'%CONFIG%\terminals.json'))]" > "%TERM_LIST%" 2>"%TEMP%\mt5_parse_err.txt"
if !errorlevel! neq 0 (
    call :log "%START_LOG%" "ERROR: Failed to parse terminals.json:"
    type "%TEMP%\mt5_parse_err.txt" >> "%START_LOG%"
    del "%TERM_LIST%" 2>nul
    rmdir "%LOCKDIR%" 2>nul
    exit /b 1
)

:: ── Launch MT5 terminals ─────────────────────────────────────────
call :log "%START_LOG%" "Launching MT5 terminals..."
set TERM_COUNT=0
for /f "usebackq delims=" %%L in ("%TERM_LIST%") do (
    call :launch_terminal %%L
    if !errorlevel! neq 0 (
        call :log "%START_LOG%" "ERROR: Failed to launch terminal, aborting."
        del "%TERM_LIST%" 2>nul
        rmdir "%LOCKDIR%" 2>nul
        exit /b 1
    )
    set /a TERM_COUNT+=1
)

if !TERM_COUNT! equ 0 (
    call :log "%START_LOG%" "ERROR: No terminals configured in terminals.json"
    del "%TERM_LIST%" 2>nul
    rmdir "%LOCKDIR%" 2>nul
    exit /b 1
)

call :log "%START_LOG%" "Launched !TERM_COUNT! terminal(s), waiting 30s to initialize..."
timeout /t 30 /nobreak >nul

:: ── Launch API processes ─────────────────────────────────────────
call :log "%START_LOG%" "Launching API processes..."
set API_IDX=0
for /f "usebackq delims=" %%L in ("%TERM_LIST%") do (
    set /a API_IDX+=1
    if !API_IDX! equ !TERM_COUNT! (
        call :log "%START_LOG%" "Running last API in foreground..."
        del "%TERM_LIST%" 2>nul
        rmdir "%LOCKDIR%" 2>nul
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
set "LT_BASEDIR=%BROKERS%\!LT_BROKER!\base"
set "LT_DIR=%BROKERS%\!LT_BROKER!\!LT_ACCOUNT!"

if not exist "!LT_BASEDIR!\terminal64.exe" (
    call :log "%START_LOG%" "ERROR: No base install for !LT_BROKER! at !LT_BASEDIR!"
    exit /b 1
)

if not exist "!LT_DIR!\terminal64.exe" (
    call :log "%START_LOG%" "Copying !LT_BROKER!\base to !LT_BROKER!\!LT_ACCOUNT!..."
    xcopy "!LT_BASEDIR!\*" "!LT_DIR!\" /E /I /H /Y /Q >nul 2>&1
    if !errorlevel! neq 0 (
        call :log "%START_LOG%" "ERROR: xcopy failed for !LT_BROKER!/!LT_ACCOUNT!"
        exit /b 1
    )
)

del "!LT_DIR!\Config\settings.ini" 2>nul
del "!LT_DIR!\Config\common.ini" 2>nul

call :write_ini "!LT_DIR!" "!LT_BROKER!" "!LT_ACCOUNT!"

rem Save journal log size before launch so we only check NEW content
for /f "delims=" %%D in ('python -c "from datetime import date;print(date.today().strftime('%%Y%%m%%d'))"') do set "LT_LOGDATE=%%D"
set "LT_LOGFILE=!LT_DIR!\logs\!LT_LOGDATE!.log"
set LT_LOGSIZE=0
if exist "!LT_LOGFILE!" (
    for %%A in ("!LT_LOGFILE!") do set LT_LOGSIZE=%%~zA
)

call :log "%START_LOG%" "Starting terminal: !LT_BROKER!/!LT_ACCOUNT! (port !LT_PORT!) [log offset !LT_LOGSIZE!]"
powershell -Command "Start-Process '!LT_DIR!\terminal64.exe' -ArgumentList '/portable','/config:\"!LT_DIR!\mt5start.ini\"' -Verb RunAs -WindowStyle Normal"

rem Wait for 'started for' in journal log (for /L avoids goto inside call)
set LT_STARTED=0
for /L %%N in (1,1,120) do (
    if !LT_STARTED! equ 0 (
        python -c "import sys;f=open(sys.argv[1],'rb');f.seek(int(sys.argv[2]));d=f.read().decode('utf-16-le',errors='ignore');f.close();sys.exit(0 if 'started for' in d else 1)" "!LT_LOGFILE!" !LT_LOGSIZE! 2>nul
        if !errorlevel! equ 0 (
            set LT_STARTED=1
        ) else (
            call :log "%START_LOG%" "  Waiting for !LT_BROKER!/!LT_ACCOUNT! to start (%%N)..."
            timeout /t 5 /nobreak >nul
        )
    )
)
if !LT_STARTED! equ 0 (
    call :log "%START_LOG%" "ERROR: !LT_BROKER!/!LT_ACCOUNT! failed to start after 10 minutes"
    exit /b 1
)
call :log "%START_LOG%" "  !LT_BROKER!/!LT_ACCOUNT! started."
exit /b 0

:: ══════════════════════════════════════════════════════════════════
:launch_api_bg
set "LA_BROKER=%~1"
set "LA_ACCOUNT=%~2"
set "LA_PORT=%~3"
set "LA_LOG=%LOGDIR%\api-!LA_BROKER!-!LA_ACCOUNT!.log"

call :log "%START_LOG%" "Starting API (bg): !LA_BROKER!/!LA_ACCOUNT! on port !LA_PORT!"
start "" cmd /c "cd /d %SHARED% && "%PYDIR%\python.exe" -m mt5api --broker !LA_BROKER! --account !LA_ACCOUNT! --port !LA_PORT! >> "!LA_LOG!" 2>&1"
exit /b 0

:: ══════════════════════════════════════════════════════════════════
:launch_api_fg
set "LA_BROKER=%~1"
set "LA_ACCOUNT=%~2"
set "LA_PORT=%~3"
set "LA_LOG=%LOGDIR%\api-!LA_BROKER!-!LA_ACCOUNT!.log"

call :log "%START_LOG%" "Starting API (fg): !LA_BROKER!/!LA_ACCOUNT! on port !LA_PORT!"
cd /d "%SHARED%"
"%PYDIR%\python.exe" -m mt5api --broker !LA_BROKER! --account !LA_ACCOUNT! --port !LA_PORT! >> "!LA_LOG!" 2>&1
exit /b 0

:: ══════════════════════════════════════════════════════════════════
:write_ini
set "WI_DIR=%~1"
set "WI_BROKER=%~2"
set "WI_ACCOUNT=%~3"
set "WI_CFG=!WI_DIR!\mt5start.ini"
"%PYDIR%\python.exe" -c "import json,os;d=json.load(open(os.path.join(r'%CONFIG%','accounts.json')));b=d.get('!WI_BROKER!',{});a='!WI_ACCOUNT!';c=b.get(a) if a else next(iter(b.values()),None) if b else None;f=open(r'!WI_CFG!','w');f.write('[Common]\nLogin='+str(c['login'])+'\nServer='+c['server']+'\nPassword='+c['password']+'\nKeepPrivate=0\nAutoTrading=1\nNewsEnable=0\n[Experts]\nAllowLiveTrading=1\nAllowDllImport=1\nEnabled=1\n[Email]\nEnable=0\n') if c else f.write('[Common]\nKeepPrivate=0\nAutoTrading=1\nNewsEnable=0\n[Experts]\nAllowLiveTrading=1\nAllowDllImport=1\nEnabled=1\n[Email]\nEnable=0\n');f.close()" >> "%START_LOG%" 2>&1
if errorlevel 1 (
    call :log "%START_LOG%" "WARNING: Could not write ini for !WI_BROKER!/!WI_ACCOUNT!, using defaults"
    echo [Common]> "!WI_CFG!"
    echo KeepPrivate=0>> "!WI_CFG!"
    echo AutoTrading=1>> "!WI_CFG!"
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
echo [%date% %time%] [start] %~2 >> "%FULL_LOG%"
exit /b 0
