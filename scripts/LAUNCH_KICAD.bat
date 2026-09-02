@echo off
title Purdue ROV - Launch KiCad Project

if not "%~1"=="" (
    cd /d "%~1"
)

echo ⚙️ Configuring local Git hooks and filters...
git config core.hooksPath .githooks >nul 2>&1
git config submodule.recurse true >nul 2>&1

:: Check network connectivity with low timeout (ping with HTTP fallback for campus networks)
set "IS_ONLINE=0"
ping -n 1 -w 1000 8.8.8.8 >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set "IS_ONLINE=1"
) else (
    where curl >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        curl -s --head --connect-timeout 2 https://github.com >nul 2>&1
        if %ERRORLEVEL% EQU 0 set "IS_ONLINE=1"
    )
)

if "%IS_ONLINE%"=="1" (
    echo 🔄 Pulling latest board design updates...
    git pull --rebase --autostash --quiet >nul 2>&1

    echo 🔄 Auto-fetching latest Purdue ROV component library...
    git -C libs/purdue-rov-kicad-lib pull origin master --quiet >nul 2>&1
) else (
    echo ℹ️ Offline mode: Skipping remote sync...
)

echo ✅ Everything ready! Launching KiCad...
for %%f in (*.kicad_pro) do (
    start "" "%%f"
    exit /b 0
)

echo.
echo ===========================================================
echo ⚠️ No .kicad_pro project file found in this directory!
echo ===========================================================
pause
