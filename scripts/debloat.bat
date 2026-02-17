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

:: ── Nuke Windows Defender / Antimalware ────────────────────────
:: Based on https://github.com/ionuttbara/windows-defender-remover
echo Removing Windows Defender...

:: Step 1: Kill Tamper Protection
echo   Killing Tamper Protection...
reg add "HKLM\SOFTWARE\Microsoft\Windows Defender\Features" /v TamperProtection /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Microsoft\Windows Defender\Features" /v TamperProtectionSource /t REG_DWORD /d 2 /f >nul 2>&1

:: Step 2: Group policy - disable everything
echo   Disabling via group policy...
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows Defender" /v DisableAntiSpyware /t REG_DWORD /d 1 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows Defender" /v DisableAntiVirus /t REG_DWORD /d 1 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows Defender" /v DisableRoutinelyTakingAction /t REG_DWORD /d 1 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows Defender" /v ServiceKeepAlive /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows Defender" /v AllowFastServiceStartup /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows Defender\Real-Time Protection" /v DisableRealtimeMonitoring /t REG_DWORD /d 1 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows Defender\Real-Time Protection" /v DisableBehaviorMonitoring /t REG_DWORD /d 1 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows Defender\Real-Time Protection" /v DisableOnAccessProtection /t REG_DWORD /d 1 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows Defender\Real-Time Protection" /v DisableScanOnRealtimeEnable /t REG_DWORD /d 1 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows Defender\Real-Time Protection" /v DisableIOAVProtection /t REG_DWORD /d 1 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows Defender\Real-Time Protection" /v DisableIntrusionPreventionSystem /t REG_DWORD /d 1 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows Defender\Real-Time Protection" /v DisableRawWriteNotification /t REG_DWORD /d 1 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows Defender\Real-Time Protection" /v DisableInformationProtectionControl /t REG_DWORD /d 1 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows Defender\Spynet" /v DisableBlockAtFirstSeen /t REG_DWORD /d 1 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows Defender\Spynet" /v SpynetReporting /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows Defender\Spynet" /v SubmitSamplesConsent /t REG_DWORD /d 2 /f >nul 2>&1

:: Step 3: Disable all defender services + drivers via registry (Start=4 = disabled)
echo   Disabling services and drivers...
for %%S in (WinDefend WdNisSvc WdNisDrv WdFilter WdBoot SecurityHealthService MsSecCore MsSecFlt MsSecWfp SgrmAgent SgrmBroker webthreatdefsvc webthreatdefusersvc wscsvc) do (
    reg add "HKLM\SYSTEM\CurrentControlSet\Services\%%S" /v Start /t REG_DWORD /d 4 /f >nul 2>&1
    sc config %%S start= disabled >nul 2>&1
    net stop %%S >nul 2>&1
)

:: Step 4: Remove startup entries
echo   Removing startup entries...
reg delete "HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" /v "Windows Defender" /f >nul 2>&1
reg delete "HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" /v "SecurityHealth" /f >nul 2>&1
reg delete "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" /v "WindowsDefender" /f >nul 2>&1
reg delete "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" /v "SecurityHealth" /f >nul 2>&1
reg delete "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" /v "Windows Defender" /f >nul 2>&1
reg delete "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" /v "SecurityHealth" /f >nul 2>&1

:: Step 5: Kill scheduled tasks
echo   Removing scheduled tasks...
for %%T in ("Windows Defender Scheduled Scan" "Windows Defender Cache Maintenance" "Windows Defender Cleanup" "Windows Defender Verification") do (
    schtasks /change /tn "Microsoft\Windows\Windows Defender\%%~T" /disable >nul 2>&1
    schtasks /delete /tn "Microsoft\Windows\Windows Defender\%%~T" /f >nul 2>&1
)

:: Step 6: Kill WMI autologgers that respawn defender
echo   Removing WMI autologgers...
reg delete "HKLM\SYSTEM\CurrentControlSet\Control\WMI\Autologger\DefenderAuditLogger" /f >nul 2>&1
reg delete "HKLM\SYSTEM\CurrentControlSet\Control\WMI\Autologger\DefenderApiLogger" /f >nul 2>&1

:: Step 7: Disable VBS (Virtualization-Based Security) that protects defender
echo   Disabling Virtualization-Based Security...
bcdedit /set hypervisorlaunchtype off >nul 2>&1

:: Step 8: Take ownership and nuke binaries
echo   Nuking Defender binaries...
takeown /f "C:\ProgramData\Microsoft\Windows Defender" /r /d y >nul 2>&1
icacls "C:\ProgramData\Microsoft\Windows Defender" /grant Administrators:F /t >nul 2>&1
rd /s /q "C:\ProgramData\Microsoft\Windows Defender\Platform" >nul 2>&1
takeown /f "C:\Program Files\Windows Defender" /r /d y >nul 2>&1
icacls "C:\Program Files\Windows Defender" /grant Administrators:F /t >nul 2>&1
ren "C:\Program Files\Windows Defender\MsMpEng.exe" "MsMpEng.exe.dead" >nul 2>&1
ren "C:\Program Files\Windows Defender\NisSrv.exe" "NisSrv.exe.dead" >nul 2>&1

:: Step 9: Remove Security Health app
echo   Removing Security Health app...
powershell -Command "Get-AppxPackage *SecHealthUI* | Remove-AppxPackage" >nul 2>&1
powershell -Command "Get-AppxProvisionedPackage -Online | Where-Object {$_.PackageName -like '*SecHealthUI*'} | Remove-AppxProvisionedPackage -Online" >nul 2>&1

:: Step 10: Kill it
echo   Killing remaining processes...
taskkill /f /im MsMpEng.exe >nul 2>&1
taskkill /f /im NisSrv.exe >nul 2>&1
taskkill /f /im SecurityHealthSystray.exe >nul 2>&1
taskkill /f /im SecurityHealthService.exe >nul 2>&1

:: ── Processor scheduling: foreground priority ──────────────────
echo Setting processor scheduling to foreground...
reg add "HKLM\SYSTEM\CurrentControlSet\Control\PriorityControl" /v Win32PrioritySeparation /t REG_DWORD /d 38 /f >nul 2>&1

echo ============================================
echo  Debloat complete.
echo ============================================
