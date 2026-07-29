<#
.SYNOPSIS
    Purdue ROV PCB DevOps - Central Board Template Sync Tool (0 GitHub Actions Minutes)
.DESCRIPTION
    Propagates all infrastructure updates from `board-template` (launchers, .githooks, 
    workflows, setup scripts, DRC rules) to all board repositories without using cloud CI minutes.
#>

param (
    [string]$TemplateUrl = "https://github.com/purduerov/board-template.git",
    [string[]]$TargetBoardDirs
)

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Purdue ROV - Board Template Sync Tool (0 CI Minutes)" -ForegroundColor Cyan
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
    Write-Host "⚠️ No target board repositories found to sync!" -ForegroundColor Yellow
    exit 0
}

foreach ($BoardDir in $TargetBoardDirs) {
    $BoardName = Split-Path -Path $BoardDir -Leaf
    Write-Host "`n🔄 Processing repository: ${BoardName}..." -ForegroundColor Cyan
    
    Push-Location $BoardDir
    try {
        # 1. Ensure 'template' remote exists
        $Remotes = git remote
        if ($Remotes -notcontains "template") {
            Write-Host "  Adding remote 'template' (${TemplateUrl})..." -ForegroundColor Gray
            git remote add template $TemplateUrl
        }
        
        # 2. Fetch template updates
        Write-Host "  Fetching latest changes from board-template..." -ForegroundColor Gray
        git fetch template master --quiet
        
        # 3. Merge template changes
        Write-Host "  Merging template/master..." -ForegroundColor Gray
        $MergeOutput = git merge template/master --allow-unrelated-histories -m "chore: sync latest infrastructure updates from board-template" 2>&1
        
        # 4. Sync submodule if present
        if (Test-Path "libs/purdue-rov-kicad-lib") {
            git -C libs/purdue-rov-kicad-lib pull origin master --quiet 2>$null
            git add libs/purdue-rov-kicad-lib 2>$null
            git commit -m "chore(submodule): sync purdue-rov-kicad-lib to latest master" 2>$null
        }
        
        # 5. Push updates to remote master
        Write-Host "  Pushing updates to origin/master..." -ForegroundColor Gray
        git push origin master --quiet
        
        Write-Host "✅ Successfully synced board-template to ${BoardName}!" -ForegroundColor Green
    }
    catch {
        Write-Host "❌ Failed to sync repo" -ForegroundColor Red
    }
    finally {
        Pop-Location
    }
}

Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host "🎉 Board template sync complete for all target repositories!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
