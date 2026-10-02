@echo off
setlocal enabledelayedexpansion

rem ════════════════════════════════════════════════════════════════
rem  compile-chartctl-loader.bat
rem
rem  Zero-touch bootstrap for the chartctl loader EA:
rem    1. Copies ChartControl.mqh into MQL5\Include\ of every broker
rem       base terminal and MT5ChartLoader.mq5 into MQL5\Experts\Advisors\.
rem    2. Compiles the loader with each base's MetaEditor64.
rem    3. Copies the compiled .ex5 (+ include + source) into every
rem       already-provisioned terminal instance dir, so existing
rem       terminals pick it up without re-provisioning. New instances
rem       inherit it automatically via the base xcopy.
rem
rem  Combined with the [StartUp] Expert= line that config_helper.py
rem  writes into mt5start.ini for live chartctl terminals, the loader
rem  attaches itself at terminal launch — no RDP, no manual step.
rem
rem  Modeled on compile-warmup-ea.bat (same MetaEditor invocation).
rem ════════════════════════════════════════════════════════════════

set "SHARED=C:\Users\Docker\Desktop\Shared"
set "ASSETS=C:\Users\Docker\Desktop\Assets"
if not exist "%ASSETS%\experts" set "ASSETS=%SHARED%\assets"

set "SRC_EA=%ASSETS%\experts\MT5ChartLoader.mq5"
set "SRC_INC=%ASSETS%\experts\include\ChartControl.mqh"
set "COMPILE_LOG=%SHARED%\logs\compile-chartctl-loader.log"

if not exist "%SRC_EA%" (
  echo ERROR: source not found: %SRC_EA%
  exit /b 1
)
if not exist "%SRC_INC%" (
  echo ERROR: include not found: %SRC_INC%
  exit /b 1
)

set FOUND=0
set FAILED=0

for /d %%B in ("%SHARED%\terminals\*") do (
  if exist "%%~fB\base\MetaEditor64.exe" (
    set /a FOUND+=1
    set "BASE=%%~fB\base"
    echo [%%~nB] compiling loader in base...

    if not exist "!BASE!\MQL5\Include" mkdir "!BASE!\MQL5\Include"
    if not exist "!BASE!\MQL5\Experts\Advisors" mkdir "!BASE!\MQL5\Experts\Advisors"
    copy /Y "%SRC_INC%" "!BASE!\MQL5\Include\ChartControl.mqh" >nul
    copy /Y "%SRC_EA%"  "!BASE!\MQL5\Experts\Advisors\MT5ChartLoader.mq5" >nul

    "!BASE!\MetaEditor64.exe" /compile:"!BASE!\MQL5\Experts\Advisors\MT5ChartLoader.mq5" /inc:"!BASE!\MQL5" /log:"%COMPILE_LOG%" >nul 2>&1

    if exist "!BASE!\MQL5\Experts\Advisors\MT5ChartLoader.ex5" (
      echo [%%~nB]   OK: MT5ChartLoader.ex5

      rem Propagate into every provisioned instance of this broker:
      rem terminals\<broker>\<account>\<instance>\ layout, skip \base.
      for /d %%A in ("%%~fB\*") do (
        if /i not "%%~nxA"=="base" (
          for /d %%I in ("%%~fA\*") do (
            if exist "%%~fI\terminal64.exe" (
              if not exist "%%~fI\MQL5\Include" mkdir "%%~fI\MQL5\Include"
              if not exist "%%~fI\MQL5\Experts\Advisors" mkdir "%%~fI\MQL5\Experts\Advisors"
              copy /Y "!BASE!\MQL5\Experts\Advisors\MT5ChartLoader.ex5" "%%~fI\MQL5\Experts\Advisors\" >nul
              copy /Y "%SRC_INC%" "%%~fI\MQL5\Include\ChartControl.mqh" >nul
              copy /Y "%SRC_EA%"  "%%~fI\MQL5\Experts\Advisors\MT5ChartLoader.mq5" >nul
              echo [%%~nB]   propagated to %%~nA\%%~nI
            )
          )
        )
      )
    ) else (
      set /a FAILED+=1
      echo [%%~nB]   ERROR: compile produced no .ex5 — see %COMPILE_LOG%
    )
  )
)

if %FOUND%==0 (
  echo ERROR: no broker base terminals found under %SHARED%\terminals
  exit /b 1
)
if %FAILED% gtr 0 exit /b 1
exit /b 0
