@echo off
setlocal enabledelayedexpansion
set SHARED=C:\Users\Docker\Desktop\Shared
set LOGDIR=%SHARED%\logs
set INSTALL_LOG=%LOGDIR%\install.log
set "LOCKFILE=%SHARED%\install.lock"
mkdir "%LOGDIR%" 2>nul

:: ── Lock to prevent concurrent runs ─────────────────────────────
if exist "%LOCKFILE%" (
    call :log "Another install.bat instance is already running, exiting."
    exit /b 0
)
echo %date% %time% > "%LOCKFILE%"

set NEEDS_REBOOT=0

call :log "============================================"
call :log " MetaTrader 5 + Python Automated Setup"
call :log "============================================"

:: ── Disable UAC (headless VM, no need for it) ────────────────────
reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" /v EnableLUA /t REG_DWORD /d 0 /f >nul 2>&1

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
        del "%LOCKFILE%" 2>nul
        exit /b 1
    )
    call :log "[1/5] Installing Python 3.12..."
    C:\python-installer.exe /quiet InstallAllUsers=1 PrependPath=1 Include_pip=1 Include_test=0
    if !errorlevel! neq 0 (
        call :log "ERROR: Python installer failed (exit code !errorlevel!)"
        del C:\python-installer.exe 2>nul
        del "%LOCKFILE%" 2>nul
        exit /b 1
    )
    timeout /t 10 /nobreak >nul
    del C:\python-installer.exe
    python --version >nul 2>&1
    if !errorlevel! neq 0 (
        call :log "ERROR: Python not found after installation"
        del "%LOCKFILE%" 2>nul
        exit /b 1
    )
    for /f "delims=" %%V in ('python --version 2^>^&1') do call :log "[1/5] Python installed: %%V"
)

:: ── Step 2: Install MetaTrader5 Python package ──────────────────
call :log "[2/5] Installing MetaTrader5 Python package..."
python -m pip install --upgrade pip >> "%INSTALL_LOG%" 2>&1
if !errorlevel! neq 0 (
    call :log "ERROR: pip upgrade failed (exit code !errorlevel!)"
    del "%LOCKFILE%" 2>nul
    exit /b 1
)
python -m pip install MetaTrader5 >> "%INSTALL_LOG%" 2>&1
if !errorlevel! neq 0 (
    call :log "ERROR: Failed to install MetaTrader5 package (exit code !errorlevel!)"
    del "%LOCKFILE%" 2>nul
    exit /b 1
)
call :log "[2/5] MetaTrader5 package installed."

:: ── Step 2.5: Migrate old broker dirs to base\ layout ─────────
for /d %%D in ("%SHARED%\*") do (
    if exist "%%D\terminal64.exe" (
        if not exist "%%D\base\terminal64.exe" (
            call :log "Migrating %%~nxD to %%~nxD\base..."
            mkdir "%%D\base" 2>nul
            robocopy "%%D" "%%D\base" /E /XD base /MOVE /NFL /NDL /NJH /NJS >nul 2>&1
            call :log "Migration done for %%~nxD."
        )
    )
)

:: ── Step 3: Debloat Windows (must happen before MT5 install) ────
set "DEBLOAT_DONE=%SHARED%\debloat.done"
set "DEBLOAT_FLAG=%SHARED%\debloat.flag"

if exist "%DEBLOAT_FLAG%" (
    call :log "[3/5] debloat.flag detected, forcing re-debloat..."
    del "%DEBLOAT_FLAG%"
    del "%DEBLOAT_DONE%" 2>nul
)

if exist "%DEBLOAT_DONE%" (
    call :log "[3/5] Already debloated, skipping."
) else (
    call :log "[3/5] Running Windows debloat..."
    call "%SHARED%\debloat.bat"
    echo done > "%DEBLOAT_DONE%"
    call :log "[3/5] Debloat done — rebooting before MT5 install..."
    del "%LOCKFILE%" 2>nul
    shutdown /r /t 5 /f
    exit /b 3
)

:: ── Step 4: Install MetaTrader 5 terminals ──────────────────────
call :log "[4/5] Installing MetaTrader 5 terminal(s)..."

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
    call :log "============================================"
    call :log " WARNING: No MT5 installers found!"
    call :log " Place mt5setup-BROKER.exe in mt5installers/"
    call :log " then restart the VM."
    call :log "============================================"
) else (
    :: Process each broker sequentially
    for /l %%I in (1,1,!BROKER_COUNT!) do (
        call :install_one "!BNAME_%%I!" "!INSTALLER_%%I!"
        if !errorlevel! neq 0 (
            del "%LOCKFILE%" 2>nul
            exit /b 1
        )
    )
)

call :log "[4/5] MetaTrader 5 terminal installation complete."

:: ── Step 5: Firewall ──────────────────────────────────────────────
call :log "[5/5] Configuring firewall..."
netsh advfirewall firewall add rule name="MT5 HTTP API" dir=in action=allow protocol=TCP localport=6542-6552 >> "%INSTALL_LOG%" 2>&1
if !errorlevel! neq 0 (
    call :log "WARNING: Failed to add firewall rule (exit code !errorlevel!)"
)

:: ── Reboot if MT5 was freshly installed ─────────────────────────
if !NEEDS_REBOOT! equ 1 (
    call :log "Rebooting to apply changes..."
    del "%LOCKFILE%" 2>nul
    shutdown /r /t 5 /f
    exit /b 3
)

call :log "============================================"
call :log " Setup complete!"
call :log "============================================"
del "%LOCKFILE%" 2>nul
exit /b 0

:: ══════════════════════════════════════════════════════════════════
:: Subroutines below — only reached via call
:: ══════════════════════════════════════════════════════════════════

:install_one
:: %~1 = broker name, %~2 = path to installer exe
set "BROKER=%~1"
set "BROKER_DIR=%SHARED%\%BROKER%\base"

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
