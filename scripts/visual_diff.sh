#!/usr/bin/env bash
# Generates visual overlay diffs between two Git refs for KiCad PCBs using kicad-cli and ImageMagick.
# Usage: ./visual_diff.sh <base_ref> <head_ref> <pcb_file_path> [project_name]

set -e

BASE_REF=$1
HEAD_REF=$2
PCB_PATH=$3
PROJECT_NAME=${4:-"board"}
DIFF_OUT="out/diff"

if [ -z "$BASE_REF" ] || [ -z "$HEAD_REF" ] || [ -z "$PCB_PATH" ]; then
    echo "Usage: $0 <base_ref> <head_ref> <pcb_file_path> [project_name]"
    exit 1
fi

if ! command -v kicad-cli &> /dev/null; then
    echo "Error: kicad-cli is not installed or not in PATH."
    exit 1
fi

COMPARE_CMD="compare"
if command -v magick &> /dev/null; then
    COMPARE_CMD="magick compare"
elif ! command -v compare &> /dev/null; then
    echo "Error: ImageMagick (compare or magick command) is not installed."
    exit 1
fi

mkdir -p "$DIFF_OUT/base"
mkdir -p "$DIFF_OUT/head"
mkdir -p "$DIFF_OUT/compare"

TEMP_DIR=$(mktemp -d 2>/dev/null || mktemp -d -t 'pcb_diff')
cleanup() {
    rm -rf "$TEMP_DIR"
}
trap cleanup EXIT

BASE_PCB="$TEMP_DIR/base.kicad_pcb"
HEAD_PCB="$TEMP_DIR/head.kicad_pcb"

echo "Extracting non-destructive layout snapshots using git show..."
git show "$BASE_REF:$PCB_PATH" > "$BASE_PCB"
git show "$HEAD_REF:$PCB_PATH" > "$HEAD_PCB"

# Export base branch layout layers
echo "Exporting base layout layers from $BASE_REF..."
kicad-cli pcb export svg --output "$DIFF_OUT/base/" --layers "F.Cu,B.Cu,Edge.Cuts" "$BASE_PCB"

# Export head branch layout layers
echo "Exporting head layout layers from $HEAD_REF..."
kicad-cli pcb export svg --output "$DIFF_OUT/head/" --layers "F.Cu,B.Cu,Edge.Cuts" "$HEAD_PCB"

# Generate diff overlays using ImageMagick
# KiCad exports SVGs named like: <pcb_name>-<layer_name>.svg
echo "Generating pixel-by-pixel diff overlays..."
BASE_PREFIX="base"
HEAD_PREFIX="head"

for layer in F_Cu B_Cu Edge_Cuts; do
    layer_name=$(echo "$layer" | tr '.' '_')
    
    BASE_SVG="$DIFF_OUT/base/${BASE_PREFIX}-${layer_name}.svg"
    HEAD_SVG="$DIFF_OUT/head/${HEAD_PREFIX}-${layer_name}.svg"
    DIFF_PNG="$DIFF_OUT/compare/${layer_name}_diff.png"
    
    if [ -f "$BASE_SVG" ] && [ -f "$HEAD_SVG" ]; then
        # Rasterize and compare using ImageMagick
        $COMPARE_CMD -metric AE -fuzz 5% -highlight-color red -lowlight-color white "$BASE_SVG" "$HEAD_SVG" "$DIFF_PNG" || true
        echo "Diff generated for layer $layer: $DIFF_PNG"
    else
        echo "Warning: Skipped $layer, files not found."
    fi
done

echo "Visual diff generation completed."
