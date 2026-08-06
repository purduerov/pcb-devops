#!/usr/bin/env bash
set -e

echo "🔄 Auto-fetching latest Purdue ROV component library..."
if [ -d "libs/purdue-rov-kicad-lib" ]; then
    git -C libs/purdue-rov-kicad-lib pull origin master --quiet
fi
echo "✅ Library up to date! Launching KiCad..."
for f in *.kicad_pro; do
    if [ -f "$f" ]; then
        kicad "$f" &
    fi
done
