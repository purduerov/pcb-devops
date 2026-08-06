#!/usr/bin/env bash
set -e

echo "Configuring local Git clean/smudge filters for KiCad..."

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
if [ -d ".githooks" ]; then
    git config core.hooksPath .githooks
    echo "Git hooks path configured to .githooks"
fi

# 5. Automatic Submodule Updates Configuration
git config submodule.recurse true
git config checkout.recurse true
echo "Git configured to automatically pull and update submodules recursively."

echo "Git filters, hooks, and submodules configured successfully!"
