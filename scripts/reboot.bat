@echo off
setlocal enabledelayedexpansion
::
:: The ONLY reboot path for the whole stack. Every caller goes through here.
::
:: WHY THIS EXISTS
:: ---------------
:: The MT5AutoReboot scheduled task used to run `shutdown /r /t 0 /f`
:: directly. That skipped both halves of the reboot protocol:
::
::   1. It never wrote rebooting.flag, which install.bat consumes on the
::      next boot to clear its own stale lock.
::   2. It gave zero grace period, so it killed start.bat mid-run while
::      start.bat still held %SHARED%\start.running.
::
:: Because %SHARED% is the host-mounted volume, that orphaned lock survived
:: the reboot and every subsequent boot bailed on it -- a permanent outage
:: that only a human running run.sh could clear.
::
:: Routing every reboot through this one script means the flag is always
:: written and the locks are always released, no matter who asks to reboot.
::
:: Usage: reboot.bat [reason]
::
set "SHARED=C:\Users\Docker\Desktop\Shared"
set "LOGDIR=%SHARED%\logs"
set "FULL_LOG=%LOGDIR%\full.log"
set "REBOOT_FLAG=%SHARED%\rebooting.flag"
set "START_LOCK=%SHARED%\start.running"
set "INSTALL_LOCK=%SHARED%\install.running"
:: Grace period so the log write and the lock removals land on the mounted
:: volume before the OS tears the filesystem down.
set "GRACE_SECONDS=5"

set "REASON=%~1"
if "!REASON!"=="" set "REASON=unspecified"

mkdir "%LOGDIR%" 2>nul
echo [%date% %time%] [reboot] reboot requested (reason=!REASON!) >> "%FULL_LOG%"

:: Tell the next boot that any lock it finds belongs to a dead instance.
:: install.bat consumes this flag; start.bat uses the boot-stamp check in
:: acquire_lock.ps1 instead, so it deliberately does NOT consume the flag --
:: doing so would delete it before install.bat (which start.bat calls) sees it.
echo rebooting > "%REBOOT_FLAG%"

:: Belt AND braces: drop the locks now rather than relying only on the flag.
:: /s /q is REQUIRED -- the lock dir holds acquire_lock.ps1's boot.id stamp
:: file, so a bare `rmdir` would fail on a non-empty directory.
rmdir /s /q "%START_LOCK%" 2>nul
rmdir /s /q "%INSTALL_LOCK%" 2>nul

echo [%date% %time%] [reboot] locks released, rebooting in !GRACE_SECONDS!s >> "%FULL_LOG%"
shutdown /r /t !GRACE_SECONDS! /f
exit /b 0
