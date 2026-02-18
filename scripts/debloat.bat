@echo off
setlocal enabledelayedexpansion
echo ============================================
echo  Unfucking this shithole of an OS so MT5 doesn't choke on
echo  Microsoft's steaming pile of dogshit services and bloatware
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
sc stop SysMain >nul 2>&1

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
sc stop wuauserv >nul 2>&1
sc config UsoSvc start= disabled >nul 2>&1
sc stop UsoSvc >nul 2>&1

:: ── Disable all useless services ───────────────────────────────
:: Based on https://github.com/LeDragoX/Win-Debloat-Tools
echo Disabling useless services (this may take a minute)...
for %%S in (
    Audiosrv AudioEndpointBuilder
    Spooler
    WerSvc
    WSearch
    DiagTrack
    diagnosticshub.standardcollector.service
    dmwappushservice
    Fax fhsvc
    GraphicsPerfSvc
    lfsvc
    MapsBroker
    PcaSvc
    RemoteAccess RemoteRegistry
    RetailDemo
    TrkWks
    BITS
    FontCache
    PhoneSvc
    WbioSrvc
    wisvc
    WMPNetworkSvc
    WpnService
    BTAGService BthAvctpSvc bthserv
    DPS WdiServiceHost WdiSystemHost
    iphlpsvc lmhosts SharedAccess
    Wecsvc
    XblAuthManager XblGameSave XboxGipSvc XboxNetApiSvc
    TabletInputService
    SCardSvr stisvc
    DoSvc
    DusmSvc
    SEMgrSvc
    Ndu
    CamSvc
    cbdhsvc
    CDPSvc
    DeviceAssociationService DeviceInstall
    DispBrokerDesktopSvc
    MessagingService
    NcbService
    OneSyncSvc
    PimIndexMaintenanceSvc
    SensorDataService SensorService SensrSvc
    UserDataSvc UnistoreSvc
    WalletService
    WpcMonSvc
    AJRouter
    FrameServer FrameServerMonitor
    icssvc
    InstallService
    edgeupdate edgeupdatem
    gupdate gupdatem
    RtkBtManServ
    SSDPSRV upnphost
    TapiSrv
    NetTcpPortSharing
    EFS
    WinRM
    TermService SessionEnv UmRdpService
    spectrum perceptionsimulation
    ClipSVC LicenseManager
    wlidsvc
    TokenBroker
    WFDSConMgrSvc
    MixedRealityOpenXRSvc
    wercplsupport
) do (
    echo   %%S
    sc config %%S start= disabled >nul 2>&1
    sc stop %%S >nul 2>&1
)

:: ── Disable Security and Maintenance notifications ───────────
echo Disabling Security and Maintenance...
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Notifications\Settings\Windows.SystemToast.SecurityAndMaintenance" /v Enabled /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\ControlPanel\NameSpace" /v SecurityAndMaintenance /t REG_SZ /d "" /f >nul 2>&1
reg delete "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\ControlPanel\NameSpace\{BB64F8A7-BEE7-4E1A-AB8D-7D8273F7FDB6}" /f >nul 2>&1
reg add "HKCU\Software\Policies\Microsoft\Windows\Explorer" /v DisableNotificationCenter /t REG_DWORD /d 1 /f >nul 2>&1
sc config wscsvc start= disabled >nul 2>&1
sc stop wscsvc >nul 2>&1

:: ── Disable Windows Error Reporting ────────────────────────────
echo Disabling error reporting UI...
reg add "HKLM\SOFTWARE\Microsoft\Windows\Windows Error Reporting" /v Disabled /t REG_DWORD /d 1 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Microsoft\Windows\Windows Error Reporting" /v DontShowUI /t REG_DWORD /d 1 /f >nul 2>&1

:: ── Disable NTFS last access timestamps ────────────────────────
echo Disabling NTFS last access timestamps...
fsutil behavior set disablelastaccess 1 >nul 2>&1

:: ── Nuke Windows Defender / Antimalware ────────────────────────
:: Using https://github.com/ionuttbara/windows-defender-remover
echo Removing Windows Defender...
set "DEFREM=%SHARED%\defender-remover"

:: Step 1: Apply all .reg files via PowerRun (runs as SYSTEM to bypass Tamper Protection)
echo   Applying registry patches as SYSTEM...
for %%f in ("%DEFREM%\*.reg") do (
    echo     %%~nxf
    "%DEFREM%\PowerRun.exe" regedit.exe /s "%%f"
)

:: Step 2: Remove Security Health UWP app
echo   Removing Security Health app...
"%DEFREM%\PowerRun.exe" powershell.exe -noprofile -executionpolicy bypass -file "%DEFREM%\RemoveSecHealthApp.ps1"

:: Step 3: Disable VBS (Virtualization-Based Security) that protects defender
echo   Disabling Virtualization-Based Security...
bcdedit /set hypervisorlaunchtype off >nul 2>&1

:: Step 4: Nuke Defender binaries
echo   Nuking Defender binaries...
"%DEFREM%\PowerRun.exe" cmd.exe /c "%DEFREM%\files_removal.bat"

:: Step 5: Kill remaining processes
echo   Killing remaining processes...
taskkill /f /im MsMpEng.exe >nul 2>&1
taskkill /f /im NisSrv.exe >nul 2>&1
taskkill /f /im SecurityHealthSystray.exe >nul 2>&1
taskkill /f /im SecurityHealthService.exe >nul 2>&1

:: ── Nuke all privacy/telemetry/spying bullshit ────────────────
:: Based on https://github.com/LeDragoX/Win-Debloat-Tools Optimize-Privacy.ps1
echo Nuking privacy and telemetry...
:: Disable advertising ID
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\AdvertisingInfo" /v Enabled /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\AdvertisingInfo" /v DisabledByGroupPolicy /t REG_DWORD /d 1 /f >nul 2>&1
:: Disable activity history
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\System" /v EnableActivityFeed /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\System" /v PublishUserActivities /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\System" /v UploadUserActivities /t REG_DWORD /d 0 /f >nul 2>&1
:: Disable clipboard history and sync
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\System" /v AllowClipboardHistory /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\System" /v AllowCrossDeviceClipboard /t REG_DWORD /d 0 /f >nul 2>&1
:: Disable inking and typing data collection
reg add "HKCU\Software\Microsoft\Input\TIPC" /v Enabled /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\TextInput" /v AllowLinguisticDataCollection /t REG_DWORD /d 0 /f >nul 2>&1
:: Disable diagnostic data viewer
reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Diagnostics\DiagTrack\EventTranscriptKey" /v EnableEventTranscript /t REG_DWORD /d 0 /f >nul 2>&1
:: Disable feedback
reg add "HKCU\Software\Microsoft\Siuf\Rules" /v NumberOfSIUFInPeriod /t REG_DWORD /d 0 /f >nul 2>&1
:: Disable tailored experiences
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Privacy" /v TailoredExperiencesWithDiagnosticDataEnabled /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKCU\Software\Policies\Microsoft\Windows\CloudContent" /v DisableTailoredExperiencesWithDiagnosticData /t REG_DWORD /d 1 /f >nul 2>&1
:: Disable third party suggestions and consumer features
reg add "HKCU\Software\Policies\Microsoft\Windows\CloudContent" /v DisableThirdPartySuggestions /t REG_DWORD /d 1 /f >nul 2>&1
reg add "HKCU\Software\Policies\Microsoft\Windows\CloudContent" /v DisableWindowsConsumerFeatures /t REG_DWORD /d 1 /f >nul 2>&1
:: Disable telemetry autologgers
reg add "HKLM\SYSTEM\CurrentControlSet\Control\WMI\AutoLogger\AutoLogger-Diagtrack-Listener" /v Start /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\SYSTEM\CurrentControlSet\Control\WMI\AutoLogger\SQMLogger" /v Start /t REG_DWORD /d 0 /f >nul 2>&1
:: Disable WiFi Sense hotspot sharing
reg add "HKLM\Software\Microsoft\PolicyManager\default\WiFi\AllowWiFiHotSpotReporting" /v value /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\Software\Microsoft\PolicyManager\default\WiFi\AllowAutoConnectToWiFiSenseHotspots" /v value /t REG_DWORD /d 0 /f >nul 2>&1
:: Disable CEIP and app compatibility telemetry
reg add "HKLM\SOFTWARE\Policies\Microsoft\SQMClient\Windows" /v CEIPEnable /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\AppCompat" /v AITEnable /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\AppCompat" /v DisableUAR /t REG_DWORD /d 1 /f >nul 2>&1
:: Deny CapabilityAccessManager permissions (camera, mic, location, etc.)
for %%P in (
    webcam microphone location userNotificationListener
    appDiagnostics userAccountInformation contacts appointments
    phoneCallHistory email chat radios bluetoothSync
    broadFileSystemAccess documentsLibrary picturesLibrary
    videosLibrary activity cellularData gazeInput graphicsCaptureProgrammatic
) do (
    reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\%%P" /v Value /t REG_SZ /d "Deny" /f >nul 2>&1
    reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\%%P" /v Value /t REG_SZ /d "Deny" /f >nul 2>&1
)
:: Disable content delivery (app suggestions, preinstalled crap, lock screen tips)
for %%N in (
    SubscribedContent-310093Enabled SubscribedContent-314559Enabled
    SubscribedContent-314563Enabled SubscribedContent-338387Enabled
    SubscribedContent-338388Enabled SubscribedContent-338389Enabled
    SubscribedContent-338393Enabled SubscribedContent-353698Enabled
    RotatingLockScreenOverlayEnabled RotatingLockScreenEnabled
    ContentDeliveryAllowed FeatureManagementEnabled
    OemPreInstalledAppsEnabled PreInstalledAppsEnabled
    PreInstalledAppsEverEnabled SilentInstalledAppsEnabled
    SoftLandingEnabled SubscribedContentEnabled SystemPaneSuggestionsEnabled
) do (
    reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager" /v %%N /t REG_DWORD /d 0 /f >nul 2>&1
)
:: Disable Game Bar/Game DVR
reg add "HKCU\Software\Microsoft\GameBar" /v AutoGameModeEnabled /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKCU\System\GameConfigStore" /v GameDVR_Enabled /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\GameDVR" /v AllowGameDVR /t REG_DWORD /d 0 /f >nul 2>&1

:: ── Processor scheduling: foreground priority ──────────────────
echo Setting processor scheduling to foreground...
reg add "HKLM\SYSTEM\CurrentControlSet\Control\PriorityControl" /v Win32PrioritySeparation /t REG_DWORD /d 38 /f >nul 2>&1

:: ── Performance tweaks ───────────────────────────────────────
:: Based on https://github.com/LeDragoX/Win-Debloat-Tools
echo Applying performance tweaks...
:: Disable Ndu (Network Data Usage) high RAM usage
reg add "HKLM\SYSTEM\ControlSet001\Services\Ndu" /v Start /t REG_DWORD /d 4 /f >nul 2>&1
:: Reduce service kill timeout from 20s to 2s
reg add "HKLM\SYSTEM\CurrentControlSet\Control" /v WaitToKillServiceTimeout /t REG_DWORD /d 2000 /f >nul 2>&1
:: Don't clear page file at shutdown (saves time)
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management" /v ClearPageFileAtShutdown /t REG_DWORD /d 0 /f >nul 2>&1
:: Auto-end tasks on shutdown without prompting
reg add "HKCU\Control Panel\Desktop" /v AutoEndTasks /t REG_DWORD /d 1 /f >nul 2>&1
:: Reduce app kill timeout from 20s to 5s
reg add "HKCU\Control Panel\Desktop" /v WaitToKillAppTimeout /t REG_DWORD /d 5000 /f >nul 2>&1
:: Speed up menu animations to 1ms
reg add "HKCU\Control Panel\Desktop" /v MenuShowDelay /t REG_DWORD /d 1 /f >nul 2>&1
:: Remove network bandwidth throttling
reg add "HKLM\SOFTWARE\Policies\Microsoft\Psched" /v NonBestEffortLimit /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile" /v NetworkThrottlingIndex /t REG_DWORD /d 0xffffffff /f >nul 2>&1
:: Disable remote assistance
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Remote Assistance" /v fAllowToGetHelp /t REG_DWORD /d 0 /f >nul 2>&1
:: Disable reserved storage
DISM /Online /Set-ReservedStorageState /State:Disabled >nul 2>&1

:: ── Kill useless scheduled tasks ─────────────────────────────
echo Disabling scheduled tasks...
for %%T in (
    "Microsoft\Windows\Application Experience\Microsoft Compatibility Appraiser"
    "Microsoft\Windows\Application Experience\ProgramDataUpdater"
    "Microsoft\Windows\Application Experience\StartupAppTask"
    "Microsoft\Windows\Autochk\Proxy"
    "Microsoft\Windows\Customer Experience Improvement Program\Consolidator"
    "Microsoft\Windows\Customer Experience Improvement Program\KernelCeipTask"
    "Microsoft\Windows\Customer Experience Improvement Program\UsbCeip"
    "Microsoft\Windows\DiskDiagnostic\Microsoft-Windows-DiskDiagnosticDataCollector"
    "Microsoft\Windows\Maps\MapsToastTask"
    "Microsoft\Windows\Maps\MapsUpdateTask"
    "Microsoft\Windows\Power Efficiency Diagnostics\AnalyzeSystem"
    "Microsoft\Windows\Shell\FamilySafetyMonitor"
    "Microsoft\Windows\Shell\FamilySafetyRefreshTask"
    "Microsoft\Windows\Windows Media Sharing\UpdateLibrary"
    "Microsoft\Windows\Maintenance\WinSAT"
) do (
    schtasks /change /tn %%T /disable >nul 2>&1
)

echo ============================================
echo  Debloat complete.
echo ============================================
