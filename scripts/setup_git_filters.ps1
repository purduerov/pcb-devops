<#
.SYNOPSIS
    Central Purdue ROV script: Sets up advanced Git clean/smudge filters for KiCad project files.
.DESCRIPTION
    This script configures Git to scrub zoom levels, scrolls, viewports, and update timestamps
    before staging commits. This eliminates GUI-only change diffs and prevents merge conflicts.
#>

Write-Host "Configuring local Git clean/smudge filters for KiCad..." -ForegroundColor Cyan

# 1. Schematic Cleaner
git config filter.kicad_sch_cleaner.clean "sed -E -e 's/\(zoom [0-9.]+\)/\(zoom 1.0\)/g' -e 's/\(scroll -?[0-9.]+ -?[0-9.]+\)/\(scroll 0 0\)/g'"
git config filter.kicad_sch_cleaner.smudge "cat"

# 2. PCB Layout Cleaner
git config filter.kicad_pcb_cleaner.clean "sed -E -e 's/\(viewport -?[0-9.]+ -?[0-9.]+ [0-9.]+ [0-9.]+\)/\(viewport 0 0 1 1\)/g'"
git config filter.kicad_pcb_cleaner.smudge "cat"

# 3. Project Cleaner
git config filter.kicad_project_cleaner.clean "sed -E -e 's/^update=.*$/update=Date/g'"
git config filter.kicad_project_cleaner.smudge "cat"

# 4. Git Hooks Configuration
if (Test-Path ".githooks") {
    git config core.hooksPath .githooks
    Write-Host "Git hooks path configured to .githooks" -ForegroundColor Green
}

# 5. Automatic Submodule Updates Configuration
git config submodule.recurse true
git config checkout.recurse true
Write-Host "Git configured to automatically pull and update submodules recursively." -ForegroundColor Green

Write-Host "Git filters, hooks, and submodules configured successfully! You can verify them with 'git config --list'." -ForegroundColor Green
