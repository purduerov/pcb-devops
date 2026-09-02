<#
.SYNOPSIS
    Purdue ROV PCB DevOps - Central Board Template Sync Tool
.DESCRIPTION
    Safely propagates infrastructure updates from `board-template` (launchers, .githooks, 
    workflows, setup scripts, DRC rules) to board repositories without CI minutes.
    Only syncs repositories that are clean and on their default branch.
#>

param (
    [string]$TemplateUrl = "https://github.com/purduerov/board-template.git",
    [string[]]$TargetBoardDirs
)

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Purdue ROV - Board Template Sync Tool" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# If no target board directories specified, search sibling directories
if (-not $TargetBoardDirs -or $TargetBoardDirs.Count -eq 0) {
    $ScriptDir = Split-Path -Path $MyInvocation.MyCommand.Definition -Parent
    $ParentDir = Split-Path -Path $ScriptDir -Parent | Split-Path -Parent
    
    Write-Host "Searching for board repositories in: $ParentDir" -ForegroundColor Gray
    $FoundDirs = Get-ChildItem -Path $ParentDir -Directory | Where-Object { 
        (Test-Path (Join-Path $_.FullName ".git")) -and ($_.Name -ne "pcb-devops") -and ($_.Name -ne "board-template") -and ($_.Name -ne "purdue-rov-kicad-lib")
    }
    $TargetBoardDirs = $FoundDirs.FullName
}

if (-not $TargetBoardDirs -or $TargetBoardDirs.Count -eq 0) {
    Write-Host "No target board repositories found to sync." -ForegroundColor Yellow
    exit 0
}

foreach ($BoardDir in $TargetBoardDirs) {
    $BoardName = Split-Path -Path $BoardDir -Leaf
    Write-Host "`nProcessing repository: ${BoardName}..." -ForegroundColor Cyan
    
    Push-Location $BoardDir
    try {
        # Check for uncommitted changes first
        $status = git status --porcelain
        if ($status) {
            Write-Host "  Skipping ${BoardName}: Working tree has uncommitted changes." -ForegroundColor Yellow
            continue
        }

        # 1. Ensure 'template' remote exists
        $Remotes = git remote
        if ($Remotes -notcontains "template") {
            Write-Host "  Adding remote 'template' (${TemplateUrl})..." -ForegroundColor Gray
            git remote add template $TemplateUrl
        }
        
        # 2. Fetch origin and template updates
        Write-Host "  Fetching latest changes from origin and template..." -ForegroundColor Gray
        git fetch origin master --quiet
        git fetch template master --quiet
        
        # 3. Pull latest origin first
        git pull origin master --ff-only --quiet 2>$null

        # 4. Selectively sync infrastructure files from template/master
        Write-Host "  Syncing infrastructure files from template/master..." -ForegroundColor Gray
        $infraFiles = @("LAUNCH_KICAD.bat", "LAUNCH_KICAD.sh", ".githooks", ".github/workflows/ci.yml", "custom_rules.kicad_dru", ".gitattributes", ".gitignore")
        
        git checkout template/master -- $infraFiles 2>$null
        
        # Check if any changes were staged
        $staged = git diff --cached --name-only
        if ($staged) {
            git commit -m "chore(infra): sync latest infrastructure tooling from board-template" 2>$null
            Write-Host "  Committed infrastructure updates." -ForegroundColor Gray
        } else {
            Write-Host "  Infrastructure already up to date." -ForegroundColor Gray
        }

        # 5. Sync submodule if present
        if (Test-Path "libs/purdue-rov-kicad-lib") {
            git -C libs/purdue-rov-kicad-lib pull origin master --quiet 2>$null
            git add libs/purdue-rov-kicad-lib 2>$null
            $submoduleStaged = git diff --cached --name-only
            if ($submoduleStaged) {
                git commit -m "chore(submodule): sync purdue-rov-kicad-lib to latest master" 2>$null
            }
        }
        
        # 6. Push updates to remote master
        Write-Host "  Pushing updates to origin/master..." -ForegroundColor Gray
        git push origin master --quiet
        
        Write-Host "Successfully synced board-template to ${BoardName}!" -ForegroundColor Green
    }
    catch {
        Write-Host "Error syncing ${BoardName}: $_" -ForegroundColor Red
    }
    finally {
        Pop-Location
    }
}

Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host "Board template sync complete." -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
