#!/usr/bin/env bash
# Master KiCad Launcher for Purdue ROV Boards (macOS / Linux)
set -e

TARGET_DIR="${1:-$(pwd)}"
cd "$TARGET_DIR"

echo "⚙️ Configuring local Git hooks and filters..."
git config core.hooksPath .githooks >/dev/null 2>&1 || true
git config submodule.recurse true >/dev/null 2>&1 || true

# Check internet connectivity
IS_ONLINE=0
if ping -c 1 -W 2 8.8.8.8 >/dev/null 2>&1 || curl -s --head --connect-timeout 2 https://github.com >/dev/null 2>&1; then
    IS_ONLINE=1
fi

if [ "$IS_ONLINE" -eq 1 ]; then
    echo "🔄 Pulling latest board design updates..."
    git pull --rebase --autostash --quiet >/dev/null 2>&1 || true

    echo "🔄 Auto-fetching latest Purdue ROV component library..."
    git -C libs/purdue-rov-kicad-lib pull origin master --quiet >/dev/null 2>&1 || true
else
    echo "ℹ️ Offline mode: Skipping remote sync..."
fi

echo "✅ Everything ready! Launching KiCad..."

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
        echo ""
        echo "==========================================================="
        echo "⚠️  KiCad EDA was not found in your standard PATH or Flatpak."
        echo "👉 Please install KiCad: https://kicad.org/download/"
        echo "==========================================================="
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
