#!/usr/bin/env bash
# Runs local KiCad validation and fabrication package generation using Docker.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(pwd)"

# 1. Ensure Git clean filters are configured
SCH_CLEAN=$(git config --get filter.kicad_sch_cleaner.clean || true)
if [ -z "$SCH_CLEAN" ]; then
    echo "Configuring Git clean filters..."
    if [ -f "$SCRIPT_DIR/setup_git_filters.sh" ]; then
        bash "$SCRIPT_DIR/setup_git_filters.sh"
    fi
fi

# 2. Run symbol library linter on libs/
PYTHON_CMD="python3"
if ! command -v python3 &> /dev/null; then
    PYTHON_CMD="python"
fi

SYM_FILES=$(find "${PROJECT_DIR}/libs" -name "*.kicad_sym" 2>/dev/null || true)
if [ -n "$SYM_FILES" ]; then
    sym_files_array=()
    while IFS= read -r file; do
        if [ -n "$file" ]; then
            sym_files_array+=("$file")
        fi
    done <<< "$SYM_FILES"

    LINTER_SCRIPT="$SCRIPT_DIR/linter_validator.py"
    if [ ! -f "$LINTER_SCRIPT" ]; then
        LINTER_SCRIPT="${PROJECT_DIR}/libs/purdue-rov-kicad-lib/scripts/linter_validator.py"
    fi
    $PYTHON_CMD "$LINTER_SCRIPT" "${sym_files_array[@]}"
else
    echo "No symbol files found in libs/ to lint."
fi

# 3. Run KiBot container if docker is running
if command -v docker &> /dev/null && docker info &> /dev/null; then
    echo "Starting KiBot Local Validation..."
    docker run --rm -v "${PROJECT_DIR}:/workspace" -w /workspace setsoft/kicad_auto:ki10@sha256:493666a06d900ed3352c50b0f75a76ccdfe194999c097d455021cab9e3c723fa kibot -c ".pcb-devops-cache/kibot_master.yaml" -s all -d Generated_Outputs
    echo "Validation completed successfully! Outputs are in 'Generated_Outputs/'."
else
    echo "Docker engine is not running. Skipping KiBot container validation."
fi
