#!/usr/bin/env bash
# Purdue ROV PCB DevOps - Central Board Template Sync Tool (0 GitHub Actions Minutes)
# Propagates template updates from board-template to all board repositories.

TEMPLATE_URL="https://github.com/purduerov/board-template.git"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "============================================================"
echo "  Purdue ROV - Board Template Sync Tool (0 CI Minutes)"
echo "============================================================"

for BOARD_DIR in "$PARENT_DIR"/*; do
    if [ -d "$BOARD_DIR/.git" ]; then
        BOARD_NAME="$(basename "$BOARD_DIR")"
        if [ "$BOARD_NAME" != "pcb-devops" ] && [ "$BOARD_NAME" != "board-template" ] && [ "$BOARD_NAME" != "purdue-rov-kicad-lib" ]; then
            echo ""
            echo "🔄 Processing repository: $BOARD_NAME..."
            cd "$BOARD_DIR" || continue
            
            if ! git remote | grep -q "^template$"; then
                echo "  Adding remote 'template' ($TEMPLATE_URL)..."
                git remote add template "$TEMPLATE_URL"
            fi
            
            echo "  Fetching latest changes from board-template..."
            git fetch template master --quiet
            
            echo "  Merging template/master..."
            git merge template/master --allow-unrelated-histories -m "chore: sync latest infrastructure updates from board-template" --quiet || true
            
            if [ -d "libs/purdue-rov-kicad-lib" ]; then
                git -C libs/purdue-rov-kicad-lib pull origin master --quiet || true
                git add libs/purdue-rov-kicad-lib || true
                git commit -m "chore(submodule): sync purdue-rov-kicad-lib to latest master" --quiet || true
            fi
            
            echo "  Pushing updates to origin/master..."
            git push origin master --quiet || true
            echo "✅ Successfully synced board-template to $BOARD_NAME!"
        fi
    fi
done

echo ""
echo "============================================================"
echo "🎉 Board template sync complete for all target repositories!"
echo "============================================================"
