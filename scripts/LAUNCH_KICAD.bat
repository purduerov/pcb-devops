@echo off
title Purdue ROV - Launch KiCad Project
echo 🔄 Auto-fetching latest Purdue ROV component library...
if exist "libs\purdue-rov-kicad-lib" (
    git -C libs/purdue-rov-kicad-lib pull origin master --quiet
)
echo ✅ Library up to date! Launching KiCad...
for %%f in (*.kicad_pro) do start "" "%%f"
