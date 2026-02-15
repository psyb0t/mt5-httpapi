@echo off
setlocal enabledelayedexpansion
set SHARED=C:\Users\Docker\Desktop\Shared
set LOGDIR=%SHARED%\logs
set INSTALL_LOG=%LOGDIR%\install.log
mkdir "%LOGDIR%" 2>nul

call :log "============================================"
call :log " MetaTrader 5 + Python Automated Setup"
call :log "============================================"

:: ── Install Python 3.12 ──────────────────────────────────────────
set "PATH=C:\Program Files\Python312;C:\Program Files\Python312\Scripts;%PATH%"
python --version >nul 2>&1
if !errorlevel! equ 0 (
    for /f "delims=" %%V in ('python --version 2^>^&1') do call :log "[1/4] Python already installed: %%V"
) else (
    call :log "[1/4] Downloading Python 3.12..."
    curl -L -o C:\python-installer.exe https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe
    if not exist C:\python-installer.exe (
        call :log "ERROR: Failed to download Python installer"
        exit /b 1
    )
    call :log "[1/4] Installing Python 3.12..."
    C:\python-installer.exe /quiet InstallAllUsers=1 PrependPath=1 Include_pip=1 Include_test=0
    timeout /t 10 /nobreak >nul
    del C:\python-installer.exe
    for /f "delims=" %%V in ('python --version 2^>^&1') do call :log "[1/4] Python installed: %%V"
)

:: ── Install MetaTrader5 Python package ───────────────────────────
call :log "[2/4] Installing MetaTrader5 Python package..."
python -m pip install --upgrade pip >> "%INSTALL_LOG%" 2>&1
python -m pip install MetaTrader5 >> "%INSTALL_LOG%" 2>&1
call :log "[2/4] MetaTrader5 package installed."

:: ── Install MetaTrader 5 terminals ───────────────────────────────
call :log "[3/4] Installing MetaTrader 5 terminal(s)..."

:: Build a list of brokers to install
set BROKER_COUNT=0
for %%F in ("%SHARED%\mt5setup-*.exe") do (
    set /a BROKER_COUNT+=1
    set "INSTALLER_!BROKER_COUNT!=%%F"
    set "FNAME=%%~nF"
    set "BNAME_!BROKER_COUNT!=!FNAME:mt5setup-=!"
)

if !BROKER_COUNT! equ 0 (
    :: No custom installers — use default MetaQuotes
    if exist "%SHARED%\default\terminal64.exe" (
        call :log "Default terminal already installed, skipping."
    ) else (
        call :log "No custom installers found, downloading standard MetaQuotes MT5..."
        curl -L -o C:\mt5setup.exe https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe
        if exist C:\mt5setup.exe (
            call :install_one "default" "C:\mt5setup.exe"
            del C:\mt5setup.exe
        ) else (
            call :log "ERROR: Failed to download MT5 installer"
        )
    )
) else (
    :: Process each broker sequentially
    for /l %%I in (1,1,!BROKER_COUNT!) do (
        call :install_one "!BNAME_%%I!" "!INSTALLER_%%I!"
    )
)

call :log "[3/4] MetaTrader 5 terminal installation complete."

:: ── Step 4: Firewall + Startup ───────────────────────────────────
call :log "[4/4] Configuring firewall and startup..."
netsh advfirewall firewall add rule name="MT5 HTTP API" dir=in action=allow protocol=TCP localport=6542 >> "%INSTALL_LOG%" 2>&1

(
echo @echo off
echo call "%SHARED%\start-mt5.bat"
) > "C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Startup\start-mt5.bat"

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

:wait_loop
if exist "%BROKER_DIR%\terminal64.exe" goto :wait_done
call :log "Waiting for %BROKER% installer to finish..."
timeout /t 5 /nobreak >nul
goto :wait_loop

:wait_done
call :log "terminal64.exe ready for %BROKER%."
taskkill /f /im terminal64.exe >nul 2>&1
taskkill /f /im metatrader64.exe >nul 2>&1
taskkill /f /im mt5setup.exe >nul 2>&1
timeout /t 2 /nobreak >nul
call :log "Broker %BROKER% installed successfully."
exit /b 0

:log
echo [%date% %time%] %~1
echo [%date% %time%] %~1 >> "%INSTALL_LOG%"
exit /b 0
