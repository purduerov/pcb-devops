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

# 1. Git hooks & submodule configuration
echo "[1/5] ⚙️  Configuring Git environment..."
git config core.hooksPath .githooks >/dev/null 2>&1 || true
git config submodule.recurse true >/dev/null 2>&1 || true

# 2. Check internet connectivity (fast ping/curl)
IS_ONLINE=0
if ping -c 1 -W 2 8.8.8.8 >/dev/null 2>&1 || curl -s --head --connect-timeout 2 https://github.com >/dev/null 2>&1; then
    IS_ONLINE=1
fi

# 3. Pull & Sync
if [ "$IS_ONLINE" -eq 1 ]; then
    echo "[2/5] 📥 Pulling latest board design updates..."
    if git pull --rebase --autostash --quiet >/dev/null 2>&1 || git pull --no-rebase --quiet >/dev/null 2>&1; then
        echo "     ✅ Board repository up to date."
    else
        echo "     ⚠️  Note: Could not automatically pull board updates (check local changes)."
    fi

    echo "[3/5] 📚 Updating Purdue ROV component library (submodule)..."
    git submodule sync --quiet >/dev/null 2>&1 || true
    git submodule update --init --recursive --quiet >/dev/null 2>&1 || true
    if [ -d "libs/purdue-rov-kicad-lib" ]; then
        git -C libs/purdue-rov-kicad-lib config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*" >/dev/null 2>&1 || true
        git -C libs/purdue-rov-kicad-lib fetch origin master --quiet >/dev/null 2>&1 || true
        git -C libs/purdue-rov-kicad-lib checkout master --quiet >/dev/null 2>&1 || true
        git -C libs/purdue-rov-kicad-lib pull origin master --ff-only --quiet >/dev/null 2>&1 || true
        echo "     ✅ Component library updated to latest master."
    else
        echo "     ℹ️  No submodule found at libs/purdue-rov-kicad-lib."
    fi
else
    echo "[2/5] 🌐 Offline mode detected: Skipping remote sync."
    echo "[3/5] 📦 Checking local library submodule..."
    git submodule sync --quiet >/dev/null 2>&1 || true
    git submodule update --init --recursive --quiet >/dev/null 2>&1 || true
fi

# 4. Verify & Synchronize sym-lib-table and fp-lib-table
echo "[4/5] 🔧 Verifying KiCad symbol and footprint library tables..."
SYNC_SCRIPT="$SCRIPT_DIR/sync_project_libs.py"
if [ -f "$SYNC_SCRIPT" ]; then
    if command -v python3 >/dev/null 2>&1; then
        python3 "$SYNC_SCRIPT" "$TARGET_DIR" || true
    elif command -v python >/dev/null 2>&1; then
        python "$SYNC_SCRIPT" "$TARGET_DIR" || true
    else
        echo "     ℹ️  Python not found; skipping automated library table check."
    fi
fi

# 5. Locate and Launch KiCad Project
echo "[5/5] 🚀 Launching KiCad..."
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
