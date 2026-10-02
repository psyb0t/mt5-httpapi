@echo off
:: install-mt4-compiler.bat
:: One-time, compile-only MetaTrader 4 install: gives POST /compile the MT4
:: MetaEditor it needs for .mq4 sources. Nothing here manages an MT4 terminal;
:: the terminal is started once only so it unpacks the stock MQL4 tree.
::
:: Idempotent: does nothing unless compile-mt4\mt4setup.exe is present and no
:: MetaEditor is installed beside it yet. NEVER fatal -- start.bat calls this
:: on every boot of every VM, so a failed install only logs and the boot goes on.
::
:: Runs before any MT5 terminal is launched (start.bat guarantees that). The
:: kills below name terminal.exe, MT4's terminal; MT5's is terminal64.exe and is
:: never touched.
::
:: Findings this is built on (2026-09-28, BlackBull MT4 build):
::   * mt4setup.exe /auto installs silently but IGNORES /path:, installing to
::     %APPDATA%\<Broker> MT4. So it is installed there and copied over.
::   * The installer leaves NO MQL4 folder. Starting terminal.exe /portable from
::     the copy once makes it unpack MQL4\Include (stdlib.mqh, stderror.mqh...),
::     without which `#include <stdlib.mqh>` fails with error 106.
setlocal enabledelayedexpansion
set "SHARED=C:\Users\Docker\Desktop\Shared"
set "DIR=%SHARED%\compile-mt4"
set "LOG=%SHARED%\logs\install-mt4-compiler.log"

if not exist "%DIR%\mt4setup.exe" exit /b 0
if exist "%DIR%\metaeditor64.exe" exit /b 0
if exist "%DIR%\metaeditor.exe" exit /b 0

echo [%date% %time%] installing MT4 compiler into %DIR% >> "%LOG%"
start "" "%DIR%\mt4setup.exe" /auto

:: Installed once both MetaEditor and the terminal (written last) exist.
set "SRC="
set /a WAIT=0
:wait_install
for /d %%D in ("%APPDATA%\*MT4*") do (
    if exist "%%D\metaeditor.exe" if exist "%%D\terminal.exe" set "SRC=%%D"
)
if defined SRC goto :installed
set /a WAIT+=1
if !WAIT! gtr 60 goto :fail_install
timeout /t 5 /nobreak >nul
goto :wait_install

:installed
echo [%date% %time%] installed at !SRC! after !WAIT! poll(s) >> "%LOG%"
:: Let the installer finish writing, then stop it and the terminal it launches.
timeout /t 10 /nobreak >nul
taskkill /f /im mt4setup.exe >nul 2>&1
taskkill /f /im terminal.exe >nul 2>&1
robocopy "!SRC!" "%DIR%" /E /XF uninstall.exe /NFL /NDL /NJH /NJS >> "%LOG%" 2>&1

:: Unpack MQL4 once.
start "" "%DIR%\terminal.exe" /portable
set /a WAIT=0
:wait_mql4
if exist "%DIR%\MQL4\Include\stdlib.mqh" goto :mql4_done
set /a WAIT+=1
if !WAIT! gtr 24 goto :mql4_timeout
timeout /t 5 /nobreak >nul
goto :wait_mql4

:mql4_done
:: Give it a moment to finish writing the rest of the tree.
timeout /t 5 /nobreak >nul
taskkill /f /im terminal.exe >nul 2>&1
echo [%date% %time%] MQL4 tree unpacked; MT4 compiler ready >> "%LOG%"
goto :listing

:mql4_timeout
taskkill /f /im terminal.exe >nul 2>&1
echo [%date% %time%] WARN: no MQL4\Include after ~2 min; .mq4 files with #include will fail >> "%LOG%"
goto :listing

:fail_install
echo [%date% %time%] WARN: MT4 did not appear under %APPDATA% after ~5 min; installer stopped >> "%LOG%"
taskkill /f /im mt4setup.exe >nul 2>&1
taskkill /f /im terminal.exe >nul 2>&1

:listing
dir /b "%DIR%" >> "%LOG%" 2>&1
exit /b 0
