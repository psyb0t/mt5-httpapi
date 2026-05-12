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
:: Install base deps first (pyyaml required for config_helper.py below).
call :log "%START_LOG%" "Installing pip packages..."
call :log "%PIP_LOG%" "Installing pip packages..."
"%PYDIR%\python.exe" -m pip install --quiet pyyaml MetaTrader5 flask waitress flask-compress psutil >> "%PIP_LOG%" 2>&1
if !errorlevel! neq 0 (
    call :log "%START_LOG%" "ERROR: pip install (base) failed (exit code !errorlevel!), aborting."
    call :log "%PIP_LOG%" "ERROR: pip install (base) failed"
    rmdir "%LOCKDIR%" 2>nul
    exit /b 1
)
:: Extra packages from config.yaml requirements list.
:: NOTE: no `usebackq` — with usebackq, single-quoted strings are LITERAL,
:: not commands. Without usebackq, ('cmd') executes the command. This is
:: the same pattern install.bat uses for the `ports` lookup.
for /f "delims=" %%R in ('"%PYDIR%\python.exe" "%SCRIPTS%\config_helper.py" requirements 2^>nul') do (
    "%PYDIR%\python.exe" -m pip install --quiet "%%R" >> "%PIP_LOG%" 2>&1
)
call :log "%START_LOG%" "pip done."
call :log "%PIP_LOG%" "pip done."

:: ── Start Windows event log tailer (background) ────────────────
:: Streams Warning/Error/Critical from System + Application logs into
:: %LOGDIR%\windows-events.log so OOM kills, BSODs, terminal64 crashes,
:: etc. show up alongside the API logs. Single-instance via lock file
:: inside the script.
call :log "%START_LOG%" "Starting Windows event log tailer..."
start "Win Event Tailer" /B powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%SCRIPTS%\event-log-tailer.ps1"

:: ── Kill lingering MT5 terminals ────────────────────────────────
call :log "%START_LOG%" "Killing lingering MT5 terminals..."
tasklist /fi "imagename eq terminal64.exe" 2>nul | find /i "terminal64.exe" >nul && (
    taskkill /f /im terminal64.exe >nul 2>&1
    timeout /t 2 /nobreak >nul
)

:: ── Verify config.yaml exists ───────────────────────────────────
if not exist "%CONFIG%\config.yaml" (
    call :log "%START_LOG%" "ERROR: config.yaml not found! Copy config/config.yaml.example and re-run."
    rmdir "%LOCKDIR%" 2>nul
    exit /b 1
)

:: ── Parse config.yaml terminals once ────────────────────────────
set "TERM_LIST=%TEMP%\mt5_terminals.txt"
"%PYDIR%\python.exe" "%SCRIPTS%\config_helper.py" terminals > "%TERM_LIST%" 2>"%TEMP%\mt5_parse_err.txt"
if !errorlevel! neq 0 (
    call :log "%START_LOG%" "ERROR: Failed to parse config.yaml:"
    type "%TEMP%\mt5_parse_err.txt" >> "%START_LOG%"
    del "%TERM_LIST%" 2>nul
    rmdir "%LOCKDIR%" 2>nul
    exit /b 1
)

:: ── Periodic auto-reboot scheduled task ─────────────────────────
:: MT5 terminals share a desktop with DWM, and DWM/VirtIO-GPU crashes
:: under sustained load wedge the SDK pipe (terminal64.exe stops
:: responding to GDI/IPC). Cheapest mitigation: hard-reboot every N
:: minutes to flush GPU/desktop state before it rots.
:: Configured via config.yaml reboot_interval (minutes). 0 = disabled.
:: Default: 30. /f on schtasks is idempotent -- overwrites existing task.
set "REBOOT_INTERVAL=30"
for /f "delims=" %%V in ('"%PYDIR%\python.exe" "%SCRIPTS%\config_helper.py" reboot_interval 2^>nul') do set "REBOOT_INTERVAL=%%V"
if "!REBOOT_INTERVAL!"=="0" (
    schtasks /delete /tn "MT5AutoReboot" /f >nul 2>&1
    call :log "%START_LOG%" "Auto-reboot disabled (reboot_interval=0)."
) else (
    schtasks /create /tn "MT5AutoReboot" /tr "shutdown /r /t 0 /f /d p:0:0" /sc minute /mo !REBOOT_INTERVAL! /ru "SYSTEM" /rl HIGHEST /f >nul 2>&1
    if !errorlevel! equ 0 (
        call :log "%START_LOG%" "MT5AutoReboot task ensured (every !REBOOT_INTERVAL! min)."
    ) else (
        call :log "%START_LOG%" "WARN: failed to create MT5AutoReboot task (errorlevel !errorlevel!)."
    )
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
    call :log "%START_LOG%" "ERROR: No terminals configured in config.yaml"
    del "%TERM_LIST%" 2>nul
    rmdir "%LOCKDIR%" 2>nul
    exit /b 1
)

call :log "%START_LOG%" "Launched !TERM_COUNT! terminal(s), waiting 30s to initialize..."
timeout /t 30 /nobreak >nul

:: ── Load API token from config.yaml (optional) ──────────────────
:: Tempfile path is more robust than `for /f`'s subshell+quoting dance —
:: any python crash, pyyaml fallback install, or stdout buffering quirk
:: showed up as "API_TOKEN empty" through the for/f path.
set "API_TOKEN="
set "TOKEN_TMP=%TEMP%\mt5_api_token.txt"
del "%TOKEN_TMP%" 2>nul
"%PYDIR%\python.exe" "%SCRIPTS%\config_helper.py" api_token > "%TOKEN_TMP%" 2>nul
if exist "%TOKEN_TMP%" set /p API_TOKEN=<"%TOKEN_TMP%"
del "%TOKEN_TMP%" 2>nul
if defined API_TOKEN (
    call :log "%START_LOG%" "API token loaded."
) else (
    call :log "%START_LOG%" "WARNING: api_token empty in config.yaml, API running without auth."
)

:: ── Launch API processes (all background) ────────────────────────
call :log "%START_LOG%" "Launching API processes..."
for /f "usebackq delims=" %%L in ("%TERM_LIST%") do (
    call :launch_api_bg %%L
)
del "%TERM_LIST%" 2>nul
rmdir "%LOCKDIR%" 2>nul
call :log "%START_LOG%" "All !TERM_COUNT! API(s) running in background."

:: ── Foreground: status + health monitor ──────────────────────────
:status_loop
cls
echo.
echo  =====================================================
echo    MT5 HTTP API RUNNING  --  %DATE% %TIME%
echo  =====================================================
echo.
"%PYDIR%\python.exe" "%SCRIPTS%\check_health.py"
echo.
timeout /t 60 /nobreak >nul
goto status_loop

:: ══════════════════════════════════════════════════════════════════
:launch_terminal
:: %1=broker %2=account %3=port %4=utc_offset %5=mode (live|backtest)
set "LT_BROKER=%~1"
set "LT_ACCOUNT=%~2"
set "LT_PORT=%~3"
set "LT_MODE=%~5"
if "!LT_MODE!"=="" set "LT_MODE=live"
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

if /i "!LT_MODE!"=="backtest" (
    call :log "%START_LOG%" "  !LT_BROKER!/!LT_ACCOUNT! mode=backtest -- portable dir prepared, terminal NOT launched (tester will spawn it on demand)."
    exit /b 0
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
set "LA_OFFSET=%~4"
set "LA_MODE=%~5"
if "!LA_OFFSET!"=="" set "LA_OFFSET=0"
if "!LA_MODE!"=="" set "LA_MODE=live"

call :log "%START_LOG%" "Starting API (bg): !LA_BROKER!/!LA_ACCOUNT! on port !LA_PORT! (utc_offset=!LA_OFFSET! mode=!LA_MODE!)"
start "MT5 API !LA_BROKER!/!LA_ACCOUNT!" cmd /c ""%SCRIPTS%\api_runner.bat" !LA_BROKER! !LA_ACCOUNT! !LA_PORT! !API_TOKEN! !LA_OFFSET! !LA_MODE!"
exit /b 0

:: ══════════════════════════════════════════════════════════════════
:write_ini
set "WI_DIR=%~1"
set "WI_BROKER=%~2"
set "WI_ACCOUNT=%~3"
set "WI_CFG=!WI_DIR!\mt5start.ini"
"%PYDIR%\python.exe" "%SCRIPTS%\config_helper.py" write_ini "!WI_BROKER!" "!WI_ACCOUNT!" "!WI_CFG!" >> "%START_LOG%" 2>&1
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
