@echo off
setlocal enabledelayedexpansion
echo ============================================
echo  Windows GUI Debloat (tiny11)
echo ============================================

:: ── Visual Effects: "Adjust for best performance" ──────────────
echo Disabling visual effects...
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects" /v VisualFXSetting /t REG_DWORD /d 2 /f >nul 2>&1
reg add "HKCU\Control Panel\Desktop" /v UserPreferencesMask /t REG_BINARY /d 9012038010000000 /f >nul 2>&1
reg add "HKCU\Control Panel\Desktop\WindowMetrics" /v MinAnimate /t REG_SZ /d "0" /f >nul 2>&1
reg add "HKCU\Control Panel\Desktop" /v DragFullWindows /t REG_SZ /d "0" /f >nul 2>&1
reg add "HKCU\Control Panel\Desktop" /v FontSmoothing /t REG_SZ /d "0" /f >nul 2>&1

:: ── Disable transparency + wallpaper ───────────────────────────
echo Disabling transparency and wallpaper...
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize" /v EnableTransparency /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKCU\Control Panel\Desktop" /v Wallpaper /t REG_SZ /d "" /f >nul 2>&1
reg add "HKCU\Control Panel\Colors" /v Background /t REG_SZ /d "0 0 0" /f >nul 2>&1

:: ── Disable background apps ────────────────────────────────────
echo Disabling background apps...
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications" /v GlobalUserDisabled /t REG_DWORD /d 1 /f >nul 2>&1

:: ── Disable SysMain (Superfetch) if still running ──────────────
echo Disabling SysMain...
sc config SysMain start= disabled >nul 2>&1
net stop SysMain >nul 2>&1

:: ── Lower DWM priority ─────────────────────────────────────────
echo Lowering DWM priority...
reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options\dwm.exe\PerfOptions" /v CpuPriorityClass /t REG_DWORD /d 1 /f >nul 2>&1

:: ── High performance power plan ────────────────────────────────
echo Setting high performance power plan...
powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c >nul 2>&1
powercfg /hibernate off >nul 2>&1
powercfg /change standby-timeout-ac 0 >nul 2>&1
powercfg /change monitor-timeout-ac 0 >nul 2>&1

:: ── Disable Windows Update ─────────────────────────────────────
echo Disabling Windows Update...
sc config wuauserv start= disabled >nul 2>&1
net stop wuauserv >nul 2>&1
sc config UsoSvc start= disabled >nul 2>&1
net stop UsoSvc >nul 2>&1

:: ── Disable useless services ───────────────────────────────────
echo Disabling Audio, Print Spooler, Error Reporting...
for %%S in (Audiosrv AudioEndpointBuilder Spooler WerSvc) do (
    sc config %%S start= disabled >nul 2>&1
    net stop %%S >nul 2>&1
)

:: ── Disable Windows Error Reporting ────────────────────────────
echo Disabling error reporting UI...
reg add "HKLM\SOFTWARE\Microsoft\Windows\Windows Error Reporting" /v Disabled /t REG_DWORD /d 1 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Microsoft\Windows\Windows Error Reporting" /v DontShowUI /t REG_DWORD /d 1 /f >nul 2>&1

:: ── Disable NTFS last access timestamps ────────────────────────
echo Disabling NTFS last access timestamps...
fsutil behavior set disablelastaccess 1 >nul 2>&1

:: ── Processor scheduling: foreground priority ──────────────────
echo Setting processor scheduling to foreground...
reg add "HKLM\SYSTEM\CurrentControlSet\Control\PriorityControl" /v Win32PrioritySeparation /t REG_DWORD /d 38 /f >nul 2>&1

echo ============================================
echo  Debloat complete.
echo ============================================
