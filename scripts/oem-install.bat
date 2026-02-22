@echo off
:: OEM first-boot script — registers start-mt5.bat as an elevated scheduled task.
:: All real work happens via start-mt5.bat -> install.bat on the shared drive.
set SHARED=C:\Users\Docker\Desktop\Shared

schtasks /create /tn "MT5Start" /tr "cmd /c \"%SHARED%\start-mt5.bat\"" /sc onlogon /ru "Docker" /rl HIGHEST /f >nul 2>&1
