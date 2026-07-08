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

if ! command -v compare &> /dev/null; then
    echo "Error: ImageMagick (compare command) is not installed."
    exit 1
fi

mkdir -p "$DIFF_OUT/base"
mkdir -p "$DIFF_OUT/head"
mkdir -p "$DIFF_OUT/compare"

# Export base branch layout layers
echo "Exporting base layout layers from $BASE_REF..."
git checkout "$BASE_REF"
kicad-cli pcb export svg --output "$DIFF_OUT/base/" --layers "F.Cu,B.Cu,Edge.Cuts" "$PCB_PATH"

# Export head branch layout layers
echo "Exporting head layout layers from $HEAD_REF..."
git checkout "$HEAD_REF"
kicad-cli pcb export svg --output "$DIFF_OUT/head/" --layers "F.Cu,B.Cu,Edge.Cuts" "$PCB_PATH"

# Generate diff overlays using ImageMagick
# KiCad exports SVGs named like: <pcb_name>-<layer_name>.svg
echo "Generating pixel-by-pixel diff overlays..."
# Normalize path names for the exported SVGs
BASE_PCB_NAME=$(basename "$PCB_PATH" .kicad_pcb)

for layer in F_Cu B_Cu Edge_Cuts; do
    # Convert layer names to match exported file names
    # KiCad v7+ exports layer names with underscores instead of dots (e.g. F_Cu instead of F.Cu)
    layer_name=$(echo "$layer" | tr '.' '_')
    
    BASE_SVG="$DIFF_OUT/base/${BASE_PCB_NAME}-${layer_name}.svg"
    HEAD_SVG="$DIFF_OUT/head/${BASE_PCB_NAME}-${layer_name}.svg"
    DIFF_PNG="$DIFF_OUT/compare/${layer_name}_diff.png"
    
    if [ -f "$BASE_SVG" ] && [ -f "$HEAD_SVG" ]; then
        # Rasterize and compare using ImageMagick
        # compare returns exit code 1 if differences are found, so we append || true
        compare -metric AE -fuzz 5% -highlight-color red -lowlight-color white "$BASE_SVG" "$HEAD_SVG" "$DIFF_PNG" || true
        echo "Diff generated for layer $layer: $DIFF_PNG"
    else
        echo "Warning: Skipped $layer, files not found."
    fi
done

echo "Visual diff generation completed."
