@echo off
setlocal enabledelayedexpansion
set SHARED=C:\Users\Docker\Desktop\Shared
set SCRIPTS=%SHARED%\scripts
set CONFIG=%SHARED%\config
set BROKERS=%SHARED%\terminals
set LOGDIR=%SHARED%\logs
set INSTALL_LOG=%LOGDIR%\install.log
set "PYDIR=C:\Program Files\Python312"
set "PATH=%PYDIR%;%PYDIR%\Scripts;%PATH%"
set "LOCKDIR=%SHARED%\install.running"
set "DEBLOAT_DONE=%SHARED%\debloat.done"
mkdir "%LOGDIR%" 2>nul

:: ── Stale lock cleanup after reboot ────────────────────────────────
if exist "%SHARED%\rebooting.flag" (
    del "%SHARED%\rebooting.flag" 2>nul
    rmdir "%SHARED%\install.running" 2>nul
)

:: ── Atomic lock ─────────────────────────────────────────────────────
mkdir "%LOCKDIR%" 2>nul
if !errorlevel! neq 0 (
    call :log "Another install.bat is already running, exiting."
    exit /b 0
)

call :log "============================================"
call :log " MT5 Setup"
call :log "============================================"

:: ── Always: idempotent prereqs (safe every boot) ───────────────────
call :prereqs

:: ── Force re-debloat if flag dropped ───────────────────────────────
if exist "%SHARED%\debloat.flag" (
    del "%SHARED%\debloat.flag" 2>nul
    del "%DEBLOAT_DONE%" 2>nul
    call :log "  debloat.flag detected, will re-debloat."
)

:: ── Stage detection ─────────────────────────────────────────────────
:: Stage 1: schtask doesn't exist yet
schtasks /query /tn "MT5Start" >nul 2>&1
if !errorlevel! neq 0 goto :stage1

:: Stage 2: schtask exists, debloat not done
if not exist "%DEBLOAT_DONE%" goto :stage2

:: Stage 3: debloat done, Python not installed
if not exist "%PYDIR%\python.exe" goto :stage3

:: Stage 4: Python ready — check/install terminals, then done
goto :stage4


:: ══════════════════════════════════════════════════════════════════
:stage1
:: Runs via startup folder entry (may not be elevated yet).
:: Creates schtask with HIGHEST privilege for all future boots.
:: Disables EnableLUA (requires reboot to take effect).
call :log "[1/4] Creating scheduled task and disabling UAC..."

schtasks /create /tn "MT5Start" /tr "cmd /c \"%SCRIPTS%\start.bat\"" /sc onlogon /ru "Docker" /rl HIGHEST /f >nul 2>&1
if !errorlevel! neq 0 (
    call :log "ERROR: Failed to create MT5Start scheduled task"
    rmdir "%LOCKDIR%" 2>nul
    exit /b 1
)
call :log "  MT5Start task created."

:: Remove startup folder entries — schtask takes over from next boot
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\start.bat" 2>nul
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\start-mt5.bat" 2>nul
del "%ALLUSERSPROFILE%\Microsoft\Windows\Start Menu\Programs\StartUp\start.bat" 2>nul
del "%ALLUSERSPROFILE%\Microsoft\Windows\Start Menu\Programs\StartUp\start-mt5.bat" 2>nul
call :log "  Startup folder entries removed."

:: Disable EnableLUA fully (requires reboot)
reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" /v EnableLUA /t REG_DWORD /d 0 /f >nul 2>&1
call :log "  EnableLUA disabled (takes effect after reboot)."

call :log "[1/4] Done. Rebooting..."
call :do_reboot
exit /b 3


:: ══════════════════════════════════════════════════════════════════
:stage2
:: Runs elevated via schtask. UAC is now fully disabled.
:: Runs debloat — kills services, Defender, firewall, telemetry.
call :log "[2/4] Running Windows debloat..."
call "%SCRIPTS%\debloat.bat" >> "%INSTALL_LOG%" 2>&1
echo done > "%DEBLOAT_DONE%"
call :log "[2/4] Debloat done. Rebooting..."
call :do_reboot
exit /b 3


:: ══════════════════════════════════════════════════════════════════
:stage3
:: Runs after debloat reboot. Defender is gone. Install Python + pip.
call :log "[3/4] Installing Python 3.12..."
curl -L -o C:\python-installer.exe https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe >> "%INSTALL_LOG%" 2>&1
if not exist C:\python-installer.exe (
    call :log "ERROR: Failed to download Python installer"
    rmdir "%LOCKDIR%" 2>nul
    exit /b 1
)
C:\python-installer.exe /quiet InstallAllUsers=1 PrependPath=1 Include_pip=1 Include_test=0
if !errorlevel! neq 0 (
    call :log "ERROR: Python installer failed (exit code !errorlevel!)"
    del C:\python-installer.exe 2>nul
    rmdir "%LOCKDIR%" 2>nul
    exit /b 1
)
timeout /t 10 /nobreak >nul
del C:\python-installer.exe 2>nul
if not exist "%PYDIR%\python.exe" (
    call :log "ERROR: python.exe not found after installation"
    rmdir "%LOCKDIR%" 2>nul
    exit /b 1
)
for /f "delims=" %%V in ('"%PYDIR%\python.exe" --version 2^>^&1') do call :log "  %%V installed."

call :log "  Installing MetaTrader5 pip package..."
"%PYDIR%\python.exe" -m pip install --upgrade pip >> "%INSTALL_LOG%" 2>&1
if !errorlevel! neq 0 (
    call :log "ERROR: pip upgrade failed"
    rmdir "%LOCKDIR%" 2>nul
    exit /b 1
)
"%PYDIR%\python.exe" -m pip install MetaTrader5 >> "%INSTALL_LOG%" 2>&1
if !errorlevel! neq 0 (
    call :log "ERROR: Failed to install MetaTrader5"
    rmdir "%LOCKDIR%" 2>nul
    exit /b 1
)
call :log "  MetaTrader5 installed."
call :log "[3/4] Done. Rebooting..."
call :do_reboot
exit /b 3


:: ══════════════════════════════════════════════════════════════════
:stage4
:: Normal operational stage. Runs every boot.
:: Installs any new MT5 terminals found, then exits 0 so start.bat
:: can continue to launch terminals and APIs.
call :log "[4/4] Checking MT5 terminals..."
mkdir "%BROKERS%" 2>nul

:: Migrate old flat-layout broker dirs to base\ layout
for /d %%D in ("%BROKERS%\*") do (
    if exist "%%D\terminal64.exe" if not exist "%%D\base\terminal64.exe" (
        call :log "  Migrating %%~nxD to %%~nxD\base..."
        mkdir "%%D\base" 2>nul
        robocopy "%%D" "%%D\base" /E /XD base /MOVE /NFL /NDL /NJH /NJS >nul 2>&1
    )
)

:: Install any new mt5setup-*.exe installers
set NEEDS_REBOOT=0
for %%F in ("%BROKERS%\mt5setup-*.exe") do (
    set "FNAME=%%~nF"
    set "BNAME=!FNAME:mt5setup-=!"
    call :install_one "!BNAME!" "%%~F"
    if !errorlevel! neq 0 (
        rmdir "%LOCKDIR%" 2>nul
        exit /b 1
    )
)

:: Verify at least one terminal exists
set HAS_TERMINALS=0
for /d %%D in ("%BROKERS%\*") do (
    if exist "%%D\base\terminal64.exe" set HAS_TERMINALS=1
)
if !HAS_TERMINALS! equ 0 (
    call :log "ERROR: No MT5 terminals installed and no installers found!"
    rmdir "%LOCKDIR%" 2>nul
    exit /b 1
)

:: Firewall
call :log "  Configuring firewall..."
netsh advfirewall firewall delete rule name="MT5 HTTP API" >nul 2>&1
netsh advfirewall firewall delete rule name="MT5 Python" >nul 2>&1
set "FW_PORTS=6542"
if exist "%CONFIG%\terminals.json" (
    for /f "delims=" %%P in ('"%PYDIR%\python.exe" -c "import json;ports=[t['port'] for t in json.load(open(r'%CONFIG%\terminals.json'))];print(str(min(ports))+'-'+str(max(ports)) if min(ports)!=max(ports) else str(min(ports)))" 2^>nul') do (
        set "FW_PORTS=%%P"
    )
)
netsh advfirewall firewall add rule name="MT5 HTTP API" dir=in action=allow protocol=TCP localport=!FW_PORTS! >> "%INSTALL_LOG%" 2>&1
netsh advfirewall firewall add rule name="MT5 Python" dir=in action=allow program="%PYDIR%\python.exe" enable=yes >> "%INSTALL_LOG%" 2>&1
call :log "  Firewall: ports !FW_PORTS! + python.exe allowed."

if !NEEDS_REBOOT! equ 1 (
    call :log "[4/4] New terminals installed. Rebooting..."
    call :do_reboot
    exit /b 3
)

call :log "[4/4] All terminals present."
call :log "============================================"
call :log " Setup complete!"
call :log "============================================"
rmdir "%LOCKDIR%" 2>nul
exit /b 0


:: ══════════════════════════════════════════════════════════════════
:prereqs
:: Idempotent setup — runs on every boot before stage detection.
:: Fast, safe to repeat, no reboots.

:: Ensure Docker user is admin
net localgroup Administrators Docker /add >nul 2>&1

:: UAC: auto-elevate without prompt (no reboot needed for these two)
reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" /v ConsentPromptBehaviorAdmin /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" /v PromptOnSecureDesktop /t REG_DWORD /d 0 /f >nul 2>&1

:: Kill Defender real-time immediately (before anything it might flag)
powershell -Command "Set-MpPreference -DisableRealtimeMonitoring $true -ErrorAction SilentlyContinue" >nul 2>&1

:: Remove fake python App Execution Aliases
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\App Paths\python.exe" /f >nul 2>&1
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\App Paths\python3.exe" /f >nul 2>&1
if exist "%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe" del /q /f "%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe" >nul 2>&1
if exist "%LOCALAPPDATA%\Microsoft\WindowsApps\python3.exe" del /q /f "%LOCALAPPDATA%\Microsoft\WindowsApps\python3.exe" >nul 2>&1

:: Custom hosts entries
set "HOSTS=%SystemRoot%\System32\drivers\etc\hosts"
set "CUSTOM_HOSTS=%CONFIG%\hosts"
if exist "%CUSTOM_HOSTS%" (
    findstr /c:"# MT5-CUSTOM-HOSTS" "%HOSTS%" >nul 2>&1
    if !errorlevel! neq 0 (
        echo.>> "%HOSTS%"
        echo # MT5-CUSTOM-HOSTS>> "%HOSTS%"
        type "%CUSTOM_HOSTS%" >> "%HOSTS%"
        call :log "  Custom hosts appended."
    )
)

:: Disable Windows Update
sc config wuauserv start= disabled >nul 2>&1
sc stop wuauserv >nul 2>&1

:: Set timezone to UTC
tzutil /s "UTC" >nul 2>&1

exit /b 0


:: ══════════════════════════════════════════════════════════════════
:do_reboot
echo rebooting > "%SHARED%\rebooting.flag"
shutdown /r /t 5 /f
:: lock intentionally NOT released — rebooting.flag cleans it on next boot
exit /b 0


:: ══════════════════════════════════════════════════════════════════
:install_one
:: %~1 = broker name, %~2 = path to installer exe
set "BROKER=%~1"
set "BROKER_DIR=%BROKERS%\%BROKER%\base"

if exist "%BROKER_DIR%\terminal64.exe" (
    call :log "  %BROKER% already installed, skipping."
    exit /b 0
)

call :log "  Installing %BROKER% to %BROKER_DIR%..."
"%~2" /auto /path:"%BROKER_DIR%"

set WAIT_COUNT=0
set MAX_WAIT=60
:wait_loop
if exist "%BROKER_DIR%\terminal64.exe" goto :wait_done
set /a WAIT_COUNT+=1
if !WAIT_COUNT! gtr !MAX_WAIT! (
    call :log "ERROR: Timed out waiting for !BROKER! installer (~5 min)"
    taskkill /f /im mt5setup.exe >nul 2>&1
    exit /b 1
)
call :log "  Waiting for %BROKER% installer (!WAIT_COUNT!/!MAX_WAIT!)..."
timeout /t 5 /nobreak >nul
goto :wait_loop

:wait_done
call :log "  %BROKER% installed successfully."
taskkill /f /im terminal64.exe >nul 2>&1
taskkill /f /im mt5setup.exe >nul 2>&1
timeout /t 2 /nobreak >nul
del "%BROKER_DIR%\Config\settings.ini" 2>nul
del "%BROKER_DIR%\Config\common.ini" 2>nul
set NEEDS_REBOOT=1
exit /b 0


:: ══════════════════════════════════════════════════════════════════
:log
echo [%date% %time%] %~1
echo [%date% %time%] %~1 >> "%INSTALL_LOG%"
echo [%date% %time%] [install] %~1 >> "%LOGDIR%\full.log"
exit /b 0
