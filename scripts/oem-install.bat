@echo off
:: OEM first-boot script — creates startup entry to launch start.bat on first logon.
:: install.bat will later replace this with an elevated scheduled task.
set SHARED=C:\Users\Docker\Desktop\Shared
set "STARTUP=C:\ProgramData\Microsoft\Windows\Start Menu\Programs\StartUp\start.bat"

echo @echo off> "%STARTUP%"
echo call "%SHARED%\scripts\start.bat">> "%STARTUP%"
