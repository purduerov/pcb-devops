#!/usr/bin/env bash
# Master KiCad Launcher for Purdue ROV Boards (macOS / Linux)
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -n "$1" ]; then
    cd "$1"
fi
TARGET_DIR="$(pwd)"

echo ""
echo "============================================================"
echo "  🚀 Purdue ROV - KiCad Launch & Sync System"
echo "============================================================"
echo ""

# 1. Git environment configuration. The Git hook path is owned by
# "rov board bootstrap", which installs the untracked .rov-hooks directory.
echo "[1/3] Configuring Git environment..."
git config submodule.recurse true >/dev/null 2>&1 || true

# 2. Prepare the board through the shared bootstrap: rename starter design
# files, write rov.project.json, add standard library table entries, prepare
# the library submodule, and install the untracked hook. The bootstrap never
# resets, stashes, or overwrites local work.
echo "[2/3] Preparing board project, library tables, submodule, and hooks..."
ROV_CLI="$SCRIPT_DIR/rov.py"
if command -v python3 >/dev/null 2>&1; then
    python3 "$ROV_CLI" board bootstrap --project-dir "$TARGET_DIR" --non-interactive
elif command -v python >/dev/null 2>&1; then
    python "$ROV_CLI" board bootstrap --project-dir "$TARGET_DIR" --non-interactive
else
    echo "Python is required to prepare this board." >&2
    exit 2
fi

# 3. Locate and Launch KiCad Project
echo "[3/3] Launching KiCad..."
PROJ=""
for f in *.kicad_pro; do
    if [ -f "$f" ]; then
        PROJ="$f"
        break
    fi
done

if [ -z "$PROJ" ]; then
    echo ""
    echo "==========================================================="
    echo "⚠️  No .kicad_pro project file found in $TARGET_DIR!"
    echo "==========================================================="
    exit 1
fi

echo "     Opening: $PROJ"
echo ""

# Detect OS and launch KiCad appropriately
if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS: Check standard KiCad application bundle locations
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
        echo "⚠️  KiCad EDA was not found in your standard PATH or Flatpak."
        echo "👉 Please install KiCad: https://kicad.org/download/"
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
