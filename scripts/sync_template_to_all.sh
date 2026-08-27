#!/usr/bin/env bash
# Purdue ROV PCB DevOps - Central Board Template Sync Tool
# Safely propagates infrastructure updates from board-template to board repositories.

set -e

TEMPLATE_URL="https://github.com/purduerov/board-template.git"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "============================================================"
echo "  Purdue ROV - Board Template Sync Tool"
echo "============================================================"

for BOARD_DIR in "$PARENT_DIR"/*; do
    if [ -d "$BOARD_DIR/.git" ]; then
        BOARD_NAME="$(basename "$BOARD_DIR")"
        if [ "$BOARD_NAME" != "pcb-devops" ] && [ "$BOARD_NAME" != "board-template" ] && [ "$BOARD_NAME" != "purdue-rov-kicad-lib" ]; then
            echo ""
            echo "Processing repository: $BOARD_NAME..."
            cd "$BOARD_DIR" || continue
            
            # Check for dirty working tree
            if [ -n "$(git status --porcelain)" ]; then
                echo "  Skipping $BOARD_NAME: Working tree has uncommitted changes."
                continue
            fi
            
            if ! git remote | grep -q "^template$"; then
                echo "  Adding remote 'template' ($TEMPLATE_URL)..."
                git remote add template "$TEMPLATE_URL"
            fi
            
            echo "  Fetching latest changes from origin and template..."
            git fetch origin master --quiet || true
            git fetch template master --quiet || true
            
            git pull origin master --ff-only --quiet || true
            
            echo "  Merging template/master..."
            if ! git merge template/master --allow-unrelated-histories -m "chore: sync latest infrastructure updates from board-template" --quiet; then
                echo "  Merge conflict encountered in $BOARD_NAME. Aborting merge..."
                git merge --abort || true
                continue
            fi
            
            if [ -d "libs/purdue-rov-kicad-lib" ]; then
                git -C libs/purdue-rov-kicad-lib pull origin master --quiet || true
                git add libs/purdue-rov-kicad-lib || true
                git commit -m "chore(submodule): sync purdue-rov-kicad-lib to latest master" --quiet 2>/dev/null || true
            fi
            
            echo "  Pushing updates to origin/master..."
            git push origin master --quiet || true
            echo "Successfully synced board-template to $BOARD_NAME!"
        fi
    fi
done

echo ""
echo "============================================================"
echo "Board template sync complete."
echo "============================================================"
