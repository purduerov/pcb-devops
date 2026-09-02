@echo off
title Purdue ROV - Launch KiCad Project

if not "%~1"=="" (
    cd /d "%~1"
)

echo ⚙️ Configuring local Git hooks and filters...
git config core.hooksPath .githooks >nul 2>&1
git config submodule.recurse true >nul 2>&1

echo 🔄 Pulling latest board design updates...
git pull --rebase --autostash --quiet >nul 2>&1

echo 🔄 Auto-fetching latest Purdue ROV component library...
git -C libs/purdue-rov-kicad-lib pull origin master --quiet >nul 2>&1

echo ✅ Everything up to date! Launching KiCad...
for %%f in (*.kicad_pro) do (
    start "" "%%f"
    exit /b 0
)

echo.
echo ===========================================================
echo ⚠️ No .kicad_pro project file found in this directory!
echo ===========================================================
pause
