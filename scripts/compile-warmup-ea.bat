@echo off
setlocal enabledelayedexpansion

set "SHARED=C:\Users\Docker\Desktop\Shared"
set "ASSETS=C:\Users\Docker\Desktop\Assets"
if not exist "%ASSETS%\experts" set "ASSETS=%SHARED%\assets"

set "SOURCE_NAME=MT5SystemWarmup.mq5"
set "OUTPUT_NAME=MT5SystemWarmup.ex5"
set "SOURCE_PATH=%ASSETS%\experts\%SOURCE_NAME%"

if not exist "%SOURCE_PATH%" (
  echo ERROR: source not found: %SOURCE_PATH%
  exit /b 1
)

set "BASE_DIR="
for /d %%D in ("%SHARED%\terminals\*") do (
  if exist "%%~fD\base\MetaEditor64.exe" (
    set "BASE_DIR=%%~fD\base"
    goto :base_found
  )
)

:base_found
if "%BASE_DIR%"=="" (
  echo ERROR: could not find any broker base terminal under %SHARED%\terminals
  exit /b 1
)

set "TARGET_DIR=%BASE_DIR%\MQL5\Experts\Advisors"
set "TARGET_SOURCE=%TARGET_DIR%\%SOURCE_NAME%"
set "TARGET_EX5=%TARGET_DIR%\%OUTPUT_NAME%"
set "COMPILE_LOG=%SHARED%\logs\compile-warmup-ea.log"

copy /Y "%SOURCE_PATH%" "%TARGET_SOURCE%" >nul
if errorlevel 1 (
  echo ERROR: failed to copy source into %TARGET_DIR%
  exit /b 1
)

echo Compiling %TARGET_SOURCE%
"%BASE_DIR%\MetaEditor64.exe" /compile:"%TARGET_SOURCE%" /log:"%COMPILE_LOG%"
if errorlevel 1 (
  echo ERROR: MetaEditor compile failed. See %COMPILE_LOG%
  exit /b 1
)

if not exist "%TARGET_EX5%" (
  echo ERROR: compile finished without producing %TARGET_EX5%
  exit /b 1
)

copy /Y "%TARGET_EX5%" "%ASSETS%\experts\%OUTPUT_NAME%" >nul
if errorlevel 1 (
  echo ERROR: failed to copy compiled ex5 back to assets pool
  exit /b 1
)

echo OK: wrote %ASSETS%\experts\%OUTPUT_NAME%
echo Compile log: %COMPILE_LOG%
exit /b 0