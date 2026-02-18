@echo off
setlocal enabledelayedexpansion
set SHARED=C:\Users\Docker\Desktop\Shared
set LOGDIR=%SHARED%\logs
set INSTALL_LOG=%LOGDIR%\install.log
mkdir "%LOGDIR%" 2>nul

set NEEDS_REBOOT=0

call :log "============================================"
call :log " MetaTrader 5 + Python Automated Setup"
call :log "============================================"

:: ── Step 1: Install Python 3.12 ─────────────────────────────────
set "PATH=C:\Program Files\Python312;C:\Program Files\Python312\Scripts;%PATH%"
python --version >nul 2>&1
if !errorlevel! equ 0 (
    for /f "delims=" %%V in ('python --version 2^>^&1') do call :log "[1/5] Python already installed: %%V"
) else (
    call :log "[1/5] Downloading Python 3.12..."
    curl -L -o C:\python-installer.exe https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe
    if not exist C:\python-installer.exe (
        call :log "ERROR: Failed to download Python installer"
        exit /b 1
    )
    call :log "[1/5] Installing Python 3.12..."
    C:\python-installer.exe /quiet InstallAllUsers=1 PrependPath=1 Include_pip=1 Include_test=0
    if !errorlevel! neq 0 (
        call :log "ERROR: Python installer failed (exit code !errorlevel!)"
        del C:\python-installer.exe 2>nul
        exit /b 1
    )
    timeout /t 10 /nobreak >nul
    del C:\python-installer.exe
    python --version >nul 2>&1
    if !errorlevel! neq 0 (
        call :log "ERROR: Python not found after installation"
        exit /b 1
    )
    for /f "delims=" %%V in ('python --version 2^>^&1') do call :log "[1/5] Python installed: %%V"
)

:: ── Step 2: Install MetaTrader5 Python package ──────────────────
call :log "[2/5] Installing MetaTrader5 Python package..."
python -m pip install --upgrade pip >> "%INSTALL_LOG%" 2>&1
if !errorlevel! neq 0 (
    call :log "ERROR: pip upgrade failed (exit code !errorlevel!)"
    exit /b 1
)
python -m pip install MetaTrader5 >> "%INSTALL_LOG%" 2>&1
if !errorlevel! neq 0 (
    call :log "ERROR: Failed to install MetaTrader5 package (exit code !errorlevel!)"
    exit /b 1
)
call :log "[2/5] MetaTrader5 package installed."

:: ── Step 3: Install MetaTrader 5 terminals ──────────────────────
call :log "[3/5] Installing MetaTrader 5 terminal(s)..."

:: Build a list of brokers to install
set BROKER_COUNT=0
for %%F in ("%SHARED%\mt5setup-*.exe") do (
    set /a BROKER_COUNT+=1
    set "INSTALLER_!BROKER_COUNT!=%%F"
    set "FNAME=%%~nF"
    set "BNAME_!BROKER_COUNT!=!FNAME:mt5setup-=!"
    call :log "  found installer: %%~nxF (broker: !FNAME:mt5setup-=!)"
)
call :log "Detected !BROKER_COUNT! MT5 installer(s)."

if !BROKER_COUNT! equ 0 (
    :: No custom installers — use default MetaQuotes
    if exist "%SHARED%\default\terminal64.exe" (
        call :log "Default terminal already installed, skipping."
    ) else (
        call :log "No custom installers found, downloading standard MetaQuotes MT5..."
        curl -L -o C:\mt5setup.exe https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe
        if not exist C:\mt5setup.exe (
            call :log "ERROR: Failed to download MT5 installer"
            exit /b 1
        )
        call :install_one "default" "C:\mt5setup.exe"
        if !errorlevel! neq 0 (
            del C:\mt5setup.exe 2>nul
            exit /b 1
        )
        del C:\mt5setup.exe
        set NEEDS_REBOOT=1
    )
) else (
    :: Process each broker sequentially
    for /l %%I in (1,1,!BROKER_COUNT!) do (
        call :install_one "!BNAME_%%I!" "!INSTALLER_%%I!"
        if !errorlevel! neq 0 exit /b 1
    )
)

call :log "[3/5] MetaTrader 5 terminal installation complete."

:: ── Step 4: Firewall + Startup ──────────────────────────────────
call :log "[4/5] Configuring firewall and startup..."
netsh advfirewall firewall add rule name="MT5 HTTP API" dir=in action=allow protocol=TCP localport=6542 >> "%INSTALL_LOG%" 2>&1
if !errorlevel! neq 0 (
    call :log "WARNING: Failed to add firewall rule (exit code !errorlevel!)"
)

set STARTUP=C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Startup\start-mt5.bat
echo @echo off> "%STARTUP%"
echo call "%SHARED%\start-mt5.bat">> "%STARTUP%"

:: ── Step 5: Debloat Windows GUI ─────────────────────────────────
set "DEBLOAT_DONE=%SHARED%\debloat.done"
set "DEBLOAT_FLAG=%SHARED%\debloat.flag"

if exist "%DEBLOAT_FLAG%" (
    call :log "[5/5] debloat.flag detected, forcing re-debloat..."
    del "%DEBLOAT_FLAG%"
    del "%DEBLOAT_DONE%" 2>nul
)

if exist "%DEBLOAT_DONE%" (
    call :log "[5/5] Already debloated, skipping."
) else (
    call :log "[5/5] Running Windows debloat..."
    call "%SHARED%\debloat.bat"
    echo done > "%DEBLOAT_DONE%"
    call :log "[5/5] Debloat done."
    set NEEDS_REBOOT=1
)

:: ── Reboot if needed ────────────────────────────────────────────
if !NEEDS_REBOOT! equ 1 (
    call :log "Rebooting to apply changes..."
    shutdown /r /t 5 /f
    exit /b 0
)

call :log "============================================"
call :log " Setup complete!"
call :log "============================================"
exit /b 0

:: ══════════════════════════════════════════════════════════════════
:: Subroutines below — only reached via call
:: ══════════════════════════════════════════════════════════════════

:install_one
:: %~1 = broker name, %~2 = path to installer exe
set "BROKER=%~1"
set "BROKER_DIR=%SHARED%\%BROKER%"

call :log "Processing broker: %BROKER%"

if exist "%BROKER_DIR%\terminal64.exe" (
    call :log "Broker %BROKER% already installed, skipping."
    exit /b 0
)

call :log "Installing %BROKER% to %BROKER_DIR%..."
"%~2" /auto /path:"%BROKER_DIR%"

set WAIT_COUNT=0
set MAX_WAIT=60
:wait_loop
if exist "%BROKER_DIR%\terminal64.exe" goto :wait_done
set /a WAIT_COUNT+=1
if !WAIT_COUNT! gtr !MAX_WAIT! (
    call :log "ERROR: Timed out waiting for %BROKER% installer after !MAX_WAIT! attempts (~5 min)"
    taskkill /f /im mt5setup.exe >nul 2>&1
    exit /b 1
)
call :log "Waiting for %BROKER% installer to finish (!WAIT_COUNT!/!MAX_WAIT!)..."
timeout /t 5 /nobreak >nul
goto :wait_loop

:wait_done
call :log "terminal64.exe ready for %BROKER%."
taskkill /f /im terminal64.exe >nul 2>&1
taskkill /f /im mt5setup.exe >nul 2>&1
timeout /t 2 /nobreak >nul
call :log "Broker %BROKER% installed successfully."
set NEEDS_REBOOT=1
exit /b 0

:log
echo [%date% %time%] %~1
echo [%date% %time%] %~1 >> "%INSTALL_LOG%"
exit /b 0
