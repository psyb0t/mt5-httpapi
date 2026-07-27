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

:: ── Boot-scoped lock (only one start.bat instance per boot) ──────
:: A bare `mkdir %LOCKDIR%` used to deadlock the stack permanently. %LOCKDIR%
:: lives on the host-mounted %SHARED% volume, so it survives a VM reboot; the
:: MT5AutoReboot task fires `shutdown /r /t 0 /f` with no grace period and can
:: land anywhere inside this script's run (which legitimately spans from
:: seconds to over an hour). Killed mid-run, the lock outlived the process and
:: every later boot bailed on the orphan forever.
::
:: acquire_lock.ps1 stamps the lock with the OS boot time, so a lock from a
:: previous boot is provably ownerless and gets cleared automatically.
:: Exit 0 = acquired, exit 1 = a live instance from THIS boot holds it.
::
:: Deliberately does NOT consume %SHARED%\rebooting.flag — install.bat (called
:: below) owns that flag for its own lock, and eating it here would break it.
:: Tempfile rather than `for /f` because errorlevel after a for/f loop is the
:: loop body's exit code, not the invoked command's -- the lock verdict would
:: be silently lost. Same reason the API-token block below uses a tempfile.
set "LOCK_OUT=%TEMP%\mt5_lock_result.txt"
del "%LOCK_OUT%" 2>nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPTS%\acquire_lock.ps1" -LockDir "%LOCKDIR%" > "%LOCK_OUT%" 2>&1
set "LOCK_EC=!errorlevel!"
if exist "%LOCK_OUT%" (
    for /f "usebackq delims=" %%A in ("%LOCK_OUT%") do (
        echo [%date% %time%] [start] lock: %%A >> "%FULL_LOG%"
        echo [%date% %time%] lock: %%A >> "%START_LOG%"
    )
)
del "%LOCK_OUT%" 2>nul
rem Exit 10 means a live instance from THIS boot holds the lock -- bail.
if !LOCK_EC! equ 10 (
    echo [%date% %time%] start.bat already running this boot, exiting.
    echo [%date% %time%] start.bat already running this boot, exiting. >> "%START_LOG%"
    echo [%date% %time%] [start] start.bat already running this boot, exiting. >> "%FULL_LOG%"
    exit /b 0
)
rem Any other non-zero means acquire_lock.ps1 ITSELF failed (parse error,
rem missing cmdlet, unwritable mount). Do NOT treat that as "held" -- that is
rem precisely what deadlocked the stack when a syntax error in the helper made
rem PowerShell exit 1 and every boot concluded the lock was taken. Fall back to
rem the plain mkdir lock so boot still proceeds with single-instance safety.
if !LOCK_EC! neq 0 (
    echo [%date% %time%] WARN acquire_lock.ps1 failed ^(exit !LOCK_EC!^), falling back to mkdir lock >> "%START_LOG%"
    echo [%date% %time%] [start] WARN acquire_lock.ps1 failed ^(exit !LOCK_EC!^), falling back to mkdir lock >> "%FULL_LOG%"
    mkdir "%LOCKDIR%" 2>nul
    if !errorlevel! neq 0 (
        echo [%date% %time%] fallback lock held, exiting. >> "%START_LOG%"
        echo [%date% %time%] [start] fallback lock held, exiting. >> "%FULL_LOG%"
        exit /b 0
    )
)

call :log "%START_LOG%" "====== Boot ======"
call :log "%INSTALL_LOG%" "====== Boot ======"

:: ── Run install ──────────────────────────────────────────────────
call :log "%START_LOG%" "Running install.bat..."
call "%SCRIPTS%\install.bat"
if !errorlevel! equ 3 (
    call :log "%START_LOG%" "Reboot scheduled by install.bat, stopping."
    call :release_lock
    exit /b 0
)
if !errorlevel! neq 0 (
    call :log "%START_LOG%" "ERROR: install.bat failed (exit code !errorlevel!)"
    call :release_lock
    exit /b 1
)
call :log "%START_LOG%" "install.bat done."

:: ── Pip install ──────────────────────────────────────────────────
:: Install base deps first (pyyaml required for config_helper.py below).
:: numpy<2 pin: MetaTrader5 5.0.5735 was built against numpy 1.x and breaks
:: silently with numpy 2.x — reads still work but order_send fails immediately
:: with (-2, 'Unnamed arguments not allowed'). Drop the pin once MetaQuotes
:: ships a numpy-2-compatible wheel.
::
:: Each pip command writes to a per-call temp file so we can detect whether
:: anything ACTUALLY got installed/upgraded (presence of "Successfully
:: installed" in pip's output). If so, the python processes already running
:: from the previous boot are stale → reboot to pick up the new libs.
set "PIP_TMP=%TEMP%\mt5-pip-%RANDOM%-%RANDOM%.txt"
set "PIP_CHANGED=0"

call :log "%START_LOG%" "Installing pip packages..."
call :log "%PIP_LOG%" "Installing pip packages..."
"%PYDIR%\python.exe" -m pip install pyyaml MetaTrader5 "numpy<2" flask waitress flask-compress psutil mcp a2wsgi > "%PIP_TMP%" 2>&1
set "PIP_EC=!errorlevel!"
type "%PIP_TMP%" >> "%PIP_LOG%"
findstr /C:"Successfully installed" "%PIP_TMP%" >nul 2>&1 && set "PIP_CHANGED=1"
del "%PIP_TMP%" 2>nul
if !PIP_EC! neq 0 (
    call :log "%START_LOG%" "ERROR: pip install (base) failed (exit code !PIP_EC!), aborting."
    call :log "%PIP_LOG%" "ERROR: pip install (base) failed"
    call :release_lock
    exit /b 1
)
:: Extra packages from config.yaml requirements list.
:: NOTE: no `usebackq` — with usebackq, single-quoted strings are LITERAL,
:: not commands. Without usebackq, ('cmd') executes the command. This is
:: the same pattern install.bat uses for the `ports` lookup.
for /f "delims=" %%R in ('"%PYDIR%\python.exe" "%SCRIPTS%\config_helper.py" requirements 2^>nul') do (
    "%PYDIR%\python.exe" -m pip install "%%R" > "%PIP_TMP%" 2>&1
    type "%PIP_TMP%" >> "%PIP_LOG%"
    findstr /C:"Successfully installed" "%PIP_TMP%" >nul 2>&1 && set "PIP_CHANGED=1"
    del "%PIP_TMP%" 2>nul
)
call :log "%START_LOG%" "pip done."
call :log "%PIP_LOG%" "pip done."

:: Routed through reboot.bat so the flag write + lock release happen in one
:: place. Previously this inlined its own flag+shutdown+rmdir sequence, which
:: was correct but meant three separate reboot implementations to keep in sync.
if "!PIP_CHANGED!"=="1" (
    call :log "%START_LOG%" "pip changed packages -> rebooting so api_runners pick up new libs"
    call "%SCRIPTS%\reboot.bat" pip-changed
    exit /b 0
)

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
    call :release_lock
    exit /b 1
)

:: ── Parse config.yaml terminals once ────────────────────────────
set "TERM_LIST=%TEMP%\mt5_terminals.txt"
"%PYDIR%\python.exe" "%SCRIPTS%\config_helper.py" terminals > "%TERM_LIST%" 2>"%TEMP%\mt5_parse_err.txt"
if !errorlevel! neq 0 (
    call :log "%START_LOG%" "ERROR: Failed to parse config.yaml:"
    type "%TEMP%\mt5_parse_err.txt" >> "%START_LOG%"
    del "%TERM_LIST%" 2>nul
    call :release_lock
    exit /b 1
)

:: ── Periodic auto-reboot scheduled task ─────────────────────────
:: MT5 terminals share a desktop with DWM, and DWM/VirtIO-GPU crashes
:: under sustained load wedge the SDK pipe (terminal64.exe stops
:: responding to GDI/IPC). Cheapest mitigation: hard-reboot every N
:: minutes to flush GPU/desktop state before it rots.
:: Configured via config.yaml reboot_interval (minutes). 0 = disabled.
:: Default: 30. /f on schtasks is idempotent -- overwrites existing task.
::
:: The task calls reboot.bat, NOT `shutdown` directly. Calling shutdown here
:: was the root cause of the permanent-deadlock outage: it killed start.bat
:: mid-run without writing rebooting.flag and without releasing
:: %SHARED%\start.running, and that orphaned lock (living on the host mount)
:: blocked every subsequent boot. reboot.bat always does both.
::
:: No inner quotes in /tr -- schtasks quote-escaping is fragile, and %SCRIPTS%
:: has no spaces (C:\Users\Docker\Desktop\Shared\scripts).
set "REBOOT_INTERVAL=30"
"%PYDIR%\python.exe" "%SCRIPTS%\config_helper.py" reboot_interval > "%SHARED%\mt5_ri.tmp" 2>nul
for /f "usebackq delims=" %%V in ("%SHARED%\mt5_ri.tmp") do set "REBOOT_INTERVAL=%%V"
del "%SHARED%\mt5_ri.tmp" 2>nul
if "!REBOOT_INTERVAL!"=="0" (
    schtasks /delete /tn "MT5AutoReboot" /f >nul 2>&1
    call :log "%START_LOG%" "Auto-reboot disabled (reboot_interval=0)."
) else (
    schtasks /create /tn "MT5AutoReboot" /tr "cmd.exe /c %SCRIPTS%\reboot.bat scheduled" /sc minute /mo !REBOOT_INTERVAL! /ru "SYSTEM" /rl HIGHEST /f >nul 2>&1
    if !errorlevel! equ 0 (
        call :log "%START_LOG%" "MT5AutoReboot task ensured (every !REBOOT_INTERVAL! min)."
    ) else (
        call :log "%START_LOG%" "WARN: failed to create MT5AutoReboot task (errorlevel !errorlevel!)."
    )
)

:: ── Compile chartctl loader EA (zero-touch bootstrap) ────────────
:: Compiles MT5ChartLoader in every broker base and propagates the .ex5
:: into existing terminal instances, so the [StartUp] Expert= line in
:: mt5start.ini can auto-attach it at launch. Skipped when chartctl is
:: disabled globally in config.yaml. Non-fatal: a compile failure only
:: means chart deployments stay unavailable until fixed.
:: Tempfile read, NOT for /f ('command') — with both python.exe and the
:: script path quoted, cmd's quote-stripping mangles the subshell command
:: and it silently outputs nothing (same failure the api_token block
:: documents; also why install.bat's ports lookup falls back to 6542).
set "CHARTCTL_ON="
"%PYDIR%\python.exe" "%SCRIPTS%\config_helper.py" chartctl_enabled > "%SHARED%\mt5_cc.tmp" 2>nul
for /f "usebackq delims=" %%C in ("%SHARED%\mt5_cc.tmp") do set "CHARTCTL_ON=%%C"
del "%SHARED%\mt5_cc.tmp" 2>nul
if "!CHARTCTL_ON!"=="1" (
    call :log "%START_LOG%" "Compiling chartctl loader EA (MT5ChartLoader)..."
    call "%SCRIPTS%\compile-chartctl-loader.bat" >> "%START_LOG%" 2>&1
    if !errorlevel! neq 0 (
        call :log "%START_LOG%" "WARN: chartctl loader compile failed -- chart deployments unavailable. See logs\compile-chartctl-loader.log"
    ) else (
        call :log "%START_LOG%" "chartctl loader compiled and propagated."
    )
) else (
    call :log "%START_LOG%" "chartctl disabled in config.yaml -- skipping loader compile."
)

:: ── Launch MT5 terminals ─────────────────────────────────────────
call :log "%START_LOG%" "Launching MT5 terminals..."
set TERM_COUNT=0
for /f "usebackq delims=" %%L in ("%TERM_LIST%") do (
    call :launch_terminal %%L
    if !errorlevel! neq 0 (
        call :log "%START_LOG%" "ERROR: Failed to launch terminal, aborting."
        del "%TERM_LIST%" 2>nul
        call :release_lock
        exit /b 1
    )
    set /a TERM_COUNT+=1
)

if !TERM_COUNT! equ 0 (
    call :log "%START_LOG%" "ERROR: No terminals configured in config.yaml"
    del "%TERM_LIST%" 2>nul
    call :release_lock
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
call :release_lock
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
:: %1=broker %2=account %3=instance %4=port %5=utc_offset %6=mode (live|backtest)
set "LT_BROKER=%~1"
set "LT_ACCOUNT=%~2"
set "LT_INSTANCE=%~3"
set "LT_PORT=%~4"
set "LT_MODE=%~6"
if "!LT_INSTANCE!"=="" set "LT_INSTANCE=default"
if "!LT_MODE!"=="" set "LT_MODE=live"
set "LT_BASEDIR=%BROKERS%\!LT_BROKER!\base"
set "LT_DIR=%BROKERS%\!LT_BROKER!\!LT_ACCOUNT!\!LT_INSTANCE!"

if not exist "!LT_BASEDIR!\terminal64.exe" (
    call :log "%START_LOG%" "ERROR: No base install for !LT_BROKER! at !LT_BASEDIR!"
    exit /b 1
)

if not exist "!LT_DIR!\terminal64.exe" (
    call :log "%START_LOG%" "Copying !LT_BROKER!\base to !LT_BROKER!\!LT_ACCOUNT!\!LT_INSTANCE!..."
    xcopy "!LT_BASEDIR!\*" "!LT_DIR!\" /E /I /H /Y /Q >nul 2>&1
    if !errorlevel! neq 0 (
        call :log "%START_LOG%" "ERROR: xcopy failed for !LT_BROKER!/!LT_ACCOUNT!/!LT_INSTANCE!"
        exit /b 1
    )
)

del "!LT_DIR!\Config\settings.ini" 2>nul
del "!LT_DIR!\Config\common.ini" 2>nul

call :write_ini "!LT_DIR!" "!LT_BROKER!" "!LT_ACCOUNT!" "!LT_INSTANCE!" "!LT_MODE!"

rem Save journal log size before launch so we only check NEW content
for /f "delims=" %%D in ('python -c "from datetime import date;print(date.today().strftime('%%Y%%m%%d'))"') do set "LT_LOGDATE=%%D"
set "LT_LOGFILE=!LT_DIR!\logs\!LT_LOGDATE!.log"
set LT_LOGSIZE=0
if exist "!LT_LOGFILE!" (
    for %%A in ("!LT_LOGFILE!") do set LT_LOGSIZE=%%~zA
)

if /i "!LT_MODE!"=="backtest" (
    call :log "%START_LOG%" "  !LT_BROKER!/!LT_ACCOUNT!/!LT_INSTANCE! mode=backtest -- portable dir prepared, terminal NOT launched (tester will spawn it on demand)."
    exit /b 0
)

call :log "%START_LOG%" "Starting terminal: !LT_BROKER!/!LT_ACCOUNT!/!LT_INSTANCE! (port !LT_PORT!) [log offset !LT_LOGSIZE!]"
powershell -Command "Start-Process '!LT_DIR!\terminal64.exe' -ArgumentList '/portable','/config:\"!LT_DIR!\mt5start.ini\"' -Verb RunAs -WindowStyle Normal"

rem Wait for 'started for' in journal log (for /L avoids goto inside call)
set LT_STARTED=0
for /L %%N in (1,1,120) do (
    if !LT_STARTED! equ 0 (
        python -c "import sys;f=open(sys.argv[1],'rb');f.seek(int(sys.argv[2]));d=f.read().decode('utf-16-le',errors='ignore');f.close();sys.exit(0 if 'started for' in d else 1)" "!LT_LOGFILE!" !LT_LOGSIZE! 2>nul
        if !errorlevel! equ 0 (
            set LT_STARTED=1
        ) else (
            call :log "%START_LOG%" "  Waiting for !LT_BROKER!/!LT_ACCOUNT!/!LT_INSTANCE! to start (%%N)..."
            timeout /t 5 /nobreak >nul
        )
    )
)
if !LT_STARTED! equ 0 (
    call :log "%START_LOG%" "ERROR: !LT_BROKER!/!LT_ACCOUNT!/!LT_INSTANCE! failed to start after 10 minutes"
    exit /b 1
)
call :log "%START_LOG%" "  !LT_BROKER!/!LT_ACCOUNT!/!LT_INSTANCE! started."
exit /b 0

:: ══════════════════════════════════════════════════════════════════
:launch_api_bg
set "LA_BROKER=%~1"
set "LA_ACCOUNT=%~2"
set "LA_INSTANCE=%~3"
set "LA_PORT=%~4"
set "LA_OFFSET=%~5"
set "LA_MODE=%~6"
if "!LA_INSTANCE!"=="" set "LA_INSTANCE=default"
if "!LA_OFFSET!"=="" set "LA_OFFSET=0"
if "!LA_MODE!"=="" set "LA_MODE=live"

call :log "%START_LOG%" "Starting API (bg): !LA_BROKER!/!LA_ACCOUNT!/!LA_INSTANCE! on port !LA_PORT! (utc_offset=!LA_OFFSET! mode=!LA_MODE!)"
if "!LA_INSTANCE!"=="default" (
    start "MT5 API !LA_BROKER!/!LA_ACCOUNT!" cmd /c ""%SCRIPTS%\api_runner.bat" !LA_BROKER! !LA_ACCOUNT! !LA_INSTANCE! !LA_PORT! !API_TOKEN! !LA_OFFSET! !LA_MODE!"
) else (
    start "MT5 API !LA_BROKER!/!LA_ACCOUNT!/!LA_INSTANCE!" cmd /c ""%SCRIPTS%\api_runner.bat" !LA_BROKER! !LA_ACCOUNT! !LA_INSTANCE! !LA_PORT! !API_TOKEN! !LA_OFFSET! !LA_MODE!"
)
exit /b 0

:: ══════════════════════════════════════════════════════════════════
:write_ini
set "WI_DIR=%~1"
set "WI_BROKER=%~2"
set "WI_ACCOUNT=%~3"
set "WI_INSTANCE=%~4"
set "WI_MODE=%~5"
if "!WI_INSTANCE!"=="" set "WI_INSTANCE=default"
if "!WI_MODE!"=="" set "WI_MODE=live"
set "WI_CFG=!WI_DIR!\mt5start.ini"
"%PYDIR%\python.exe" "%SCRIPTS%\config_helper.py" write_ini "!WI_BROKER!" "!WI_ACCOUNT!" "!WI_CFG!" "!WI_INSTANCE!" "!WI_MODE!" >> "%START_LOG%" 2>&1
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
:release_lock
:: /s /q is REQUIRED: the lock dir contains acquire_lock.ps1's boot.id stamp,
:: so a bare `rmdir` fails on a non-empty directory and would leave the lock
:: behind -- reintroducing the deadlock this whole mechanism removes.
rmdir /s /q "%LOCKDIR%" 2>nul
exit /b 0

:: ══════════════════════════════════════════════════════════════════
:log
echo [%date% %time%] %~2
echo [%date% %time%] %~2 >> "%~1"
echo [%date% %time%] [start] %~2 >> "%FULL_LOG%"
exit /b 0
