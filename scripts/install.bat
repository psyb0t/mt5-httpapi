@echo off
setlocal enabledelayedexpansion
set SHARED=C:\Users\Docker\Desktop\Shared
set SCRIPTS=%SHARED%\scripts
set CONFIG=%SHARED%\config
set BROKERS=%SHARED%\terminals
set LOGDIR=%SHARED%\logs
set INSTALL_LOG=%LOGDIR%\install.log
set "LOCKDIR=%SHARED%\install.running"
mkdir "%LOGDIR%" 2>nul

:: ── Atomic lock via mkdir (only one process can create a dir) ────
mkdir "%LOCKDIR%" 2>nul
if !errorlevel! neq 0 (
    call :log "Another install.bat instance is already running, exiting."
    exit /b 0
)

set NEEDS_REBOOT=0

call :log "============================================"
call :log " MetaTrader 5 + Python Automated Setup"
call :log "============================================"

:: ── Disable Windows Store app execution aliases (fake python) ────
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\App Paths\python.exe" /f >nul 2>&1
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\App Paths\python3.exe" /f >nul 2>&1
if exist "%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe" del "%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe" >nul 2>&1
if exist "%LOCALAPPDATA%\Microsoft\WindowsApps\python3.exe" del "%LOCALAPPDATA%\Microsoft\WindowsApps\python3.exe" >nul 2>&1

:: ── Disable UAC + admin consent prompts (headless VM) ─────────────
:: EnableLUA=0 disables UAC entirely (needs reboot)
:: ConsentPromptBehaviorAdmin=0 auto-elevates without prompting
:: PromptOnSecureDesktop=0 prevents the secure desktop dimming
set "UAC_DONE=%SHARED%\uac-disabled.done"
reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" /v ConsentPromptBehaviorAdmin /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" /v PromptOnSecureDesktop /t REG_DWORD /d 0 /f >nul 2>&1
if not exist "%UAC_DONE%" (
    reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" /v EnableLUA /t REG_DWORD /d 0 /f >nul 2>&1
    echo done > "%UAC_DONE%"
    call :log "UAC disabled — rebooting to apply..."
    rmdir "%LOCKDIR%" 2>nul
    shutdown /r /t 5 /f
    exit /b 3
)

:: ── Append custom hosts entries (idempotent) ─────────────────────
set "HOSTS=%SystemRoot%\System32\drivers\etc\hosts"
set "CUSTOM_HOSTS=%CONFIG%\hosts"
if exist "%CUSTOM_HOSTS%" (
    findstr /c:"# MT5-CUSTOM-HOSTS" "%HOSTS%" >nul 2>&1
    if !errorlevel! neq 0 (
        call :log "Appending custom hosts entries..."
        echo.>> "%HOSTS%"
        echo # MT5-CUSTOM-HOSTS>> "%HOSTS%"
        type "%CUSTOM_HOSTS%" >> "%HOSTS%"
        call :log "Custom hosts entries applied."
    )
)

:: ── Disable Windows Update service ───────────────────────────────
sc config wuauserv start= disabled >nul 2>&1
sc stop wuauserv >nul 2>&1

:: ── Ensure elevated scheduled task exists (idempotent) ───────────
schtasks /query /tn "MT5Start" >nul 2>&1
if !errorlevel! neq 0 (
    call :log "Creating MT5Start scheduled task..."
    schtasks /create /tn "MT5Start" /tr "cmd /c \"%SCRIPTS%\start.bat\"" /sc onlogon /ru "Docker" /rl HIGHEST /f >nul 2>&1
    call :log "MT5Start task created — rebooting to apply..."
    rmdir "%LOCKDIR%" 2>nul
    shutdown /r /t 5 /f
    exit /b 3
)

:: ── Remove legacy startup folder entries (prevent double-launch) ─
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\start.bat" 2>nul
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\start-mt5.bat" 2>nul
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\start-mt5.lnk" 2>nul
del "%ALLUSERSPROFILE%\Microsoft\Windows\Start Menu\Programs\StartUp\start.bat" 2>nul
del "%ALLUSERSPROFILE%\Microsoft\Windows\Start Menu\Programs\StartUp\start-mt5.bat" 2>nul
del "%ALLUSERSPROFILE%\Microsoft\Windows\Start Menu\Programs\StartUp\start-mt5.lnk" 2>nul

:: ── Step 1: Install Python 3.12 ─────────────────────────────────
set "PYDIR=C:\Program Files\Python312"
set "PATH=%PYDIR%;%PYDIR%\Scripts;%PATH%"
if exist "%PYDIR%\python.exe" (
    for /f "delims=" %%V in ('"%PYDIR%\python.exe" --version 2^>^&1') do call :log "[1/5] Python already installed: %%V"
) else (
    call :log "[1/5] Downloading Python 3.12..."
    curl -L -o C:\python-installer.exe https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe
    if not exist C:\python-installer.exe (
        call :log "ERROR: Failed to download Python installer"
        rmdir "%LOCKDIR%" 2>nul
        exit /b 1
    )
    call :log "[1/5] Installing Python 3.12..."
    C:\python-installer.exe /quiet InstallAllUsers=1 PrependPath=1 Include_pip=1 Include_test=0
    if !errorlevel! neq 0 (
        call :log "ERROR: Python installer failed (exit code !errorlevel!)"
        del C:\python-installer.exe 2>nul
        rmdir "%LOCKDIR%" 2>nul
        exit /b 1
    )
    timeout /t 10 /nobreak >nul
    del C:\python-installer.exe
    if not exist "%PYDIR%\python.exe" (
        call :log "ERROR: Python not found after installation"
        rmdir "%LOCKDIR%" 2>nul
        exit /b 1
    )
    for /f "delims=" %%V in ('"%PYDIR%\python.exe" --version 2^>^&1') do call :log "[1/5] Python installed: %%V"
)

:: ── Step 2: Install MetaTrader5 Python package ──────────────────
call :log "[2/5] Installing MetaTrader5 Python package..."
python -m pip install --upgrade pip >> "%INSTALL_LOG%" 2>&1
if !errorlevel! neq 0 (
    call :log "ERROR: pip upgrade failed (exit code !errorlevel!)"
    rmdir "%LOCKDIR%" 2>nul
    exit /b 1
)
python -m pip install MetaTrader5 >> "%INSTALL_LOG%" 2>&1
if !errorlevel! neq 0 (
    call :log "ERROR: Failed to install MetaTrader5 package (exit code !errorlevel!)"
    rmdir "%LOCKDIR%" 2>nul
    exit /b 1
)
call :log "[2/5] MetaTrader5 package installed."

:: ── Step 2.5: Migrate old broker dirs to base\ layout ─────────
mkdir "%BROKERS%" 2>nul
for /d %%D in ("%BROKERS%\*") do (
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
    call "%SCRIPTS%\debloat.bat"
    echo done > "%DEBLOAT_DONE%"
    call :log "[3/5] Debloat done — rebooting before MT5 install..."
    rmdir "%LOCKDIR%" 2>nul
    shutdown /r /t 5 /f
    exit /b 3
)

:: ── Step 4: Install MetaTrader 5 terminals ──────────────────────
call :log "[4/5] Installing MetaTrader 5 terminal(s)..."

set BROKER_COUNT=0
for %%F in ("%BROKERS%\mt5setup-*.exe") do (
    set /a BROKER_COUNT+=1
    set "FNAME=%%~nF"
    set "BNAME=!FNAME:mt5setup-=!"
    call :log "  found installer: %%~nxF (broker: !BNAME!)"
    call :install_one "!BNAME!" "%%~F"
    if !errorlevel! neq 0 (
        rmdir "%LOCKDIR%" 2>nul
        exit /b 1
    )
)

if !BROKER_COUNT! equ 0 (
    call :log "============================================"
    call :log " WARNING: No MT5 installers found!"
    call :log " Place mt5setup-BROKER.exe in mt5installers/"
    call :log " then restart the VM."
    call :log "============================================"
)

call :log "[4/5] MetaTrader 5 terminal installation complete."

:: ── Step 5: Firewall ──────────────────────────────────────────────
call :log "[5/5] Configuring firewall..."
:: Delete old rule first (idempotent), then create with current ports
netsh advfirewall firewall delete rule name="MT5 HTTP API" >nul 2>&1
set "FW_PORTS=6542"
if exist "%CONFIG%\terminals.json" (
    for /f "delims=" %%P in ('python -c "import json;ports=[t['port'] for t in json.load(open(r'%CONFIG%\terminals.json'))];print(str(min(ports))+'-'+str(max(ports)) if min(ports)!=max(ports) else str(min(ports)))" 2^>nul') do (
        set "FW_PORTS=%%P"
    )
) else (
    set "FW_PORTS=6542"
)
call :log "Opening firewall ports: !FW_PORTS!"
netsh advfirewall firewall add rule name="MT5 HTTP API" dir=in action=allow protocol=TCP localport=!FW_PORTS! >> "%INSTALL_LOG%" 2>&1
if !errorlevel! neq 0 (
    call :log "WARNING: Failed to add firewall rule (exit code !errorlevel!)"
)

:: ── Reboot if MT5 was freshly installed ─────────────────────────
if !NEEDS_REBOOT! equ 1 (
    call :log "Rebooting to apply changes..."
    rmdir "%LOCKDIR%" 2>nul
    shutdown /r /t 5 /f
    exit /b 3
)

call :log "============================================"
call :log " Setup complete!"
call :log "============================================"
rmdir "%LOCKDIR%" 2>nul
exit /b 0

:: ══════════════════════════════════════════════════════════════════
:: Subroutines below — only reached via call
:: ══════════════════════════════════════════════════════════════════

:install_one
:: %~1 = broker name, %~2 = path to installer exe
set "BROKER=%~1"
set "BROKER_DIR=%BROKERS%\%BROKER%\base"

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
