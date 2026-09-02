@echo off
title Purdue ROV - Launch KiCad Project

if not "%~1"=="" (
    cd /d "%~1"
)

echo ⚙️ Configuring local Git hooks and filters...
git config core.hooksPath .githooks
git config submodule.recurse true

echo 🔄 Pulling latest board design updates...
git pull --rebase --autostash --quiet

echo 🔄 Auto-fetching latest Purdue ROV component library...
git -C libs/purdue-rov-kicad-lib pull origin master --quiet

echo ✅ Everything up to date! Launching KiCad...
for %%f in (*.kicad_pro) do (
    start "" "%%f"
    exit /b 0
)

echo ⚠️ No .kicad_pro project file found in this directory.
