<#
.SYNOPSIS
    Generates non-destructive visual overlay diffs between two Git refs for KiCad PCBs.
.DESCRIPTION
    Uses kicad-cli and ImageMagick to render and compare PCB copper layers
    without modifying or switching the current Git working tree.
.EXAMPLE
    .\visual_diff.ps1 -BaseRef "master" -HeadRef "HEAD" -PcbPath "X19-Control-Board.kicad_pcb"
#>

param(
    [Parameter(Mandatory=$true)]
    [string]$BaseRef,

    [Parameter(Mandatory=$true)]
    [string]$HeadRef,

    [Parameter(Mandatory=$true)]
    [string]$PcbPath,

    [string]$DiffOut = "out/diff"
)

# 1. Verify dependencies
if (-not (Get-Command "kicad-cli" -ErrorAction SilentlyContinue)) {
    Write-Error "kicad-cli is not installed or not in PATH."
    exit 1
}

$compareCmd = "compare"
if (Get-Command "magick" -ErrorAction SilentlyContinue) {
    $compareCmd = "magick compare"
} elseif (-not (Get-Command "compare" -ErrorAction SilentlyContinue)) {
    Write-Error "ImageMagick (magick or compare command) is not installed."
    exit 1
}

# 2. Setup output directories
$baseDir = Join-Path $DiffOut "base"
$headDir = Join-Path $DiffOut "head"
$compDir = Join-Path $DiffOut "compare"

New-Item -ItemType Directory -Force -Path $baseDir, $headDir, $compDir | Out-Null

# 3. Create temporary files for non-destructive inspection
$tempDir = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString())
New-Item -ItemType Directory -Force -Path $tempDir | Out-Null

$basePcb = Join-Path $tempDir "base.kicad_pcb"
$headPcb = Join-Path $tempDir "head.kicad_pcb"

try {
    Write-Host "Extracting non-destructive layout snapshots using git show..." -ForegroundColor Cyan
    git show "${BaseRef}:${PcbPath}" | Out-File -FilePath $basePcb -Encoding utf8
    git show "${HeadRef}:${PcbPath}" | Out-File -FilePath $headPcb -Encoding utf8

    Write-Host "Exporting base layout layers from $BaseRef..." -ForegroundColor Gray
    kicad-cli pcb export svg --output "$baseDir\" --layers "F.Cu,B.Cu,Edge.Cuts" $basePcb

    Write-Host "Exporting head layout layers from $HeadRef..." -ForegroundColor Gray
    kicad-cli pcb export svg --output "$headDir\" --layers "F.Cu,B.Cu,Edge.Cuts" $headPcb

    Write-Host "Generating pixel-by-pixel diff overlays..." -ForegroundColor Cyan
    $layers = @("F_Cu", "B_Cu", "Edge_Cuts")

    foreach ($layer in $layers) {
        $baseSvg = Join-Path $baseDir "base-${layer}.svg"
        $headSvg = Join-Path $headDir "head-${layer}.svg"
        $diffPng = Join-Path $compDir "${layer}_diff.png"

        if ((Test-Path $baseSvg) -and (Test-Path $headSvg)) {
            if ($compareCmd -eq "magick compare") {
                magick compare -metric AE -fuzz 5% -highlight-color red -lowlight-color white $baseSvg $headSvg $diffPng 2>$null
            } else {
                compare -metric AE -fuzz 5% -highlight-color red -lowlight-color white $baseSvg $headSvg $diffPng 2>$null
            }
            Write-Host "Diff generated for layer ${layer}: $diffPng" -ForegroundColor Green
        } else {
            Write-Host "Warning: Skipped $layer, exported SVG not found." -ForegroundColor Yellow
        }
    }

    Write-Host "Visual diff generation completed successfully. Results in $DiffOut." -ForegroundColor Green
}
finally {
    if (Test-Path $tempDir) {
        Remove-Item -Recurse -Force $tempDir
    }
}
