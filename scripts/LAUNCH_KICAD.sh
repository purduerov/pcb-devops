#!/usr/bin/env bash
# Master KiCad Launcher for Purdue ROV Boards (macOS / Linux)
set -e

TARGET_DIR="${1:-$(pwd)}"
cd "$TARGET_DIR"

echo "⚙️ Configuring local Git hooks and filters..."
git config core.hooksPath .githooks
git config submodule.recurse true

echo "🔄 Pulling latest board design updates..."
git pull --rebase --autostash --quiet || true

echo "🔄 Auto-fetching latest Purdue ROV component library..."
git -C libs/purdue-rov-kicad-lib pull origin master --quiet || true
echo "✅ Everything up to date! Launching KiCad..."

# Find the first .kicad_pro project file
PROJ=""
for f in *.kicad_pro; do
    if [ -f "$f" ]; then
        PROJ="$f"
        break
    fi
done

if [ -z "$PROJ" ]; then
    echo "⚠️  No .kicad_pro project file found in $TARGET_DIR."
    exit 1
fi

# Detect OS and launch KiCad appropriately
if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS: Check standard KiCad application bundle locations or use default system handler
    if [ -d "/Applications/KiCad/KiCad.app" ]; then
        open -a "/Applications/KiCad/KiCad.app" "$PROJ"
    elif [ -d "/Applications/KiCad.app" ]; then
        open -a "/Applications/KiCad.app" "$PROJ"
    elif [ -d "$HOME/Applications/KiCad/KiCad.app" ]; then
        open -a "$HOME/Applications/KiCad/KiCad.app" "$PROJ"
    elif [ -d "$HOME/Applications/KiCad.app" ]; then
        open -a "$HOME/Applications/KiCad.app" "$PROJ"
    elif command -v kicad >/dev/null 2>&1; then
        kicad "$PROJ" &
    else
        open "$PROJ"
    fi
elif [[ "$OSTYPE" == "linux-gnu"* ]] || [[ "$OSTYPE" == "linux"* ]]; then
    # Linux: Check standard command, Flatpak, or desktop opener
    if command -v kicad >/dev/null 2>&1; then
        kicad "$PROJ" &
    elif command -v flatpak >/dev/null 2>&1 && flatpak info org.kicad.KiCad >/dev/null 2>&1; then
        flatpak run org.kicad.KiCad "$PROJ" &
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$PROJ" &
    else
        echo "⚠️  Could not automatically launch KiCad."
        echo "Please open '$PROJ' directly in KiCad."
    fi
else
    # Fallback / Windows Git Bash / Cygwin
    if command -v kicad >/dev/null 2>&1; then
        kicad "$PROJ" &
    elif command -v cmd.exe >/dev/null 2>&1; then
        cmd.exe /c start "" "$PROJ"
    else
        echo "⚠️  Please open '$PROJ' directly in KiCad."
    fi
fi
