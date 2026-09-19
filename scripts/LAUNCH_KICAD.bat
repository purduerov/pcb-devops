@echo off
chcp 65001 >nul 2>&1
title Purdue ROV - KiCad Launch System

if not "%~1"=="" cd /d "%~1"
set "TARGET_DIR=%CD%"

echo.
echo ============================================================
echo   🚀 Purdue ROV - KiCad Launch ^& Sync System
echo ============================================================
echo.

REM 1. Git hooks & submodule configuration
echo [1/5] ⚙️  Configuring Git environment...
git config core.hooksPath .githooks >nul 2>&1
git config submodule.recurse true >nul 2>&1

REM 2. Check network connectivity
set "IS_ONLINE=0"
ping -n 1 -w 1000 8.8.8.8 >nul 2>&1
if %ERRORLEVEL% EQU 0 set "IS_ONLINE=1"
if "%IS_ONLINE%"=="1" goto :check_online_done
where curl >nul 2>&1
if %ERRORLEVEL% NEQ 0 goto :check_online_done
curl -s --head --connect-timeout 2 https://github.com >nul 2>&1
if %ERRORLEVEL% EQU 0 set "IS_ONLINE=1"

:check_online_done

REM 3. Pull & Sync
if "%IS_ONLINE%"=="0" goto :offline_sync

echo [2/5] 📥 Pulling latest board design updates...
git pull --rebase --autostash --quiet >nul 2>&1
if %ERRORLEVEL% NEQ 0 git pull --no-rebase --quiet >nul 2>&1
if %ERRORLEVEL% EQU 0 echo      ✅ Board repository up to date.
if %ERRORLEVEL% NEQ 0 echo      ⚠️  Note: Could not pull board updates - check local changes.

echo [3/5] 📚 Updating Purdue ROV component library submodule...
git submodule sync --quiet >nul 2>&1
git submodule update --init --recursive --quiet >nul 2>&1
if not exist "libs\purdue-rov-kicad-lib" echo      ℹ️  No submodule at libs\purdue-rov-kicad-lib.
if not exist "libs\purdue-rov-kicad-lib" goto :sync_tables

git -C libs/purdue-rov-kicad-lib config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*" >nul 2>&1
git -C libs/purdue-rov-kicad-lib fetch origin master --quiet >nul 2>&1
git -C libs/purdue-rov-kicad-lib checkout -B master origin/master --quiet >nul 2>&1
git -C libs/purdue-rov-kicad-lib reset --hard origin/master --quiet >nul 2>&1
echo      ✅ Component library updated to latest master.
goto :sync_tables


:offline_sync
echo [2/5] 🌐 Offline mode: Skipping board remote sync.
echo [3/5] 📦 Checking local library submodule...
git submodule sync --quiet >nul 2>&1
git submodule update --init --recursive --quiet >nul 2>&1

:sync_tables
REM 4. Verify & Synchronize sym-lib-table and fp-lib-table
echo [4/5] 🔧 Verifying KiCad symbol and footprint library tables...
set "SYNC_SCRIPT=%~dp0sync_project_libs.py"
if not exist "%SYNC_SCRIPT%" echo      ℹ️  Library sync utility not found.
if not exist "%SYNC_SCRIPT%" goto :launch_kicad

where python >nul 2>&1
if %ERRORLEVEL% EQU 0 python "%SYNC_SCRIPT%" "%TARGET_DIR%" & goto :launch_kicad

where py >nul 2>&1
if %ERRORLEVEL% EQU 0 py -3 "%SYNC_SCRIPT%" "%TARGET_DIR%" & goto :launch_kicad

echo      ℹ️  Python not found; skipping automated library table check.

:launch_kicad
REM 5. Locate and Launch KiCad Project
echo [5/5] 🚀 Launching KiCad project...
set "PROJ_FILE="
for %%f in (*.kicad_pro) do (
    set "PROJ_FILE=%%f"
    goto :found_proj
)

:found_proj
if "%PROJ_FILE%"=="" (
    echo.
    echo ============================================================
    echo ⚠️ No .kicad_pro project file found in %TARGET_DIR%!
    echo ============================================================
    pause
    exit /b 1
)

echo      Opening: %PROJ_FILE%
echo.

REM Try system association first
start "" "%PROJ_FILE%" >nul 2>&1
if %ERRORLEVEL% EQU 0 exit /b 0

REM Fallback to standard installation paths
if exist "%ProgramFiles%\KiCad\10.0\bin\kicad.exe" start "" "%ProgramFiles%\KiCad\10.0\bin\kicad.exe" "%PROJ_FILE%" & exit /b 0
if exist "%ProgramFiles%\KiCad\9.0\bin\kicad.exe" start "" "%ProgramFiles%\KiCad\9.0\bin\kicad.exe" "%PROJ_FILE%" & exit /b 0
if exist "%ProgramFiles%\KiCad\8.0\bin\kicad.exe" start "" "%ProgramFiles%\KiCad\8.0\bin\kicad.exe" "%PROJ_FILE%" & exit /b 0
if exist "%ProgramFiles%\KiCad\bin\kicad.exe" start "" "%ProgramFiles%\KiCad\bin\kicad.exe" "%PROJ_FILE%" & exit /b 0

echo ⚠️ KiCad opened via file association.
exit /b 0