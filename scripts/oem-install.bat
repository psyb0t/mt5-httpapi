@echo off
:: OEM first-boot script — only creates the startup entry.
:: All real work happens via start-mt5.bat -> install.bat on the shared drive.
set SHARED=C:\Users\Docker\Desktop\Shared
set "STARTUP=C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Startup\start-mt5.bat"
echo @echo off> "%STARTUP%"
echo call "%SHARED%\start-mt5.bat">> "%STARTUP%"
