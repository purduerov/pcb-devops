<#
.SYNOPSIS
    Runs local KiCad symbol validation and KiBot hardware generation.
.DESCRIPTION
    Central validation runner from pcb-devops.
.EXAMPLE
    irm https://raw.githubusercontent.com/purduerov/pcb-devops/master/scripts/run_local_validation.ps1 | iex
#>

$projectDir = Get-Location

# 1. Ensure filters are configured
$schClean = git config --get filter.kicad_sch_cleaner.clean
if (-not $schClean) {
    Write-Host "Configuring Git clean filters..." -ForegroundColor Yellow
    $setupScript = Join-Path $PSScriptRoot "setup_git_filters.ps1"
    if (Test-Path $setupScript) {
        & $setupScript
    }
}

# 2. Run symbol linter on all *.kicad_sym in libs/
$symFiles = Get-ChildItem -Path (Join-Path $projectDir "libs") -Recurse -Filter "*.kicad_sym" -ErrorAction SilentlyContinue
if ($symFiles) {
    Write-Host "`nRunning central KiCad symbol library linter..." -ForegroundColor Cyan
    $linterScript = Join-Path $PSScriptRoot "linter_validator.py"
    if (-not (Test-Path $linterScript)) {
        $linterScript = Join-Path $projectDir "libs\purdue-rov-kicad-lib\scripts\linter_validator.py"
    }
    python $linterScript $symFiles.FullName
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Symbol library linter failed."
        exit $LASTEXITCODE
    }
} else {
    Write-Host "No symbol files found in libs/ to lint." -ForegroundColor Yellow
}

# 3. Run KiBot container if docker engine is running
$dockerAvailable = $false
try {
    $null = docker info 2>&1
    if ($LASTEXITCODE -eq 0) {
        $dockerAvailable = $true
    }
} catch {}

if ($dockerAvailable) {
    $pcbFiles = Get-ChildItem -Path $projectDir -Filter "*.kicad_pcb"
    $schFiles = Get-ChildItem -Path $projectDir -Filter "*.kicad_sch"

    $schName = if ($schFiles.Count -gt 0) { $schFiles[0].Name } else { "*.kicad_sch" }
    $pcbName = if ($pcbFiles.Count -gt 0) { $pcbFiles[0].Name } else { "*.kicad_pcb" }

    $kibotConfig = Join-Path $PSScriptRoot "..\kibot_master.yaml"
    if (-not (Test-Path $kibotConfig)) {
        $kibotConfig = Join-Path $projectDir ".pcb-devops-cache\kibot_master.yaml"
    }

    Write-Host "`nStarting KiBot Local Validation for: $schName / $pcbName" -ForegroundColor Cyan
    docker run --rm -v "${projectDir}:/workspace" -w /workspace setsoft/kicad_auto:ki10@sha256:493666a06d900ed3352c50b0f75a76ccdfe194999c097d455021cab9e3c723fa kibot -c ".pcb-devops-cache/kibot_master.yaml" -s all -d Generated_Outputs

    if ($LASTEXITCODE -eq 0) {
        Write-Host "Validation and generation completed successfully! Outputs are in 'Generated_Outputs/'." -ForegroundColor Green
    } else {
        Write-Error "KiBot validation failed. Check logs above for ERC/DRC failures."
        exit $LASTEXITCODE
    }
} else {
    Write-Host "`nDocker engine is not running. Skipping KiBot container validation." -ForegroundColor Yellow
}
