# Centralize PCB DevOps Tooling & Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Centralize all KiCad linter scripts, KiBot configurations, and GitHub Actions workflows into `purduerov/pcb-devops`, replacing duplicated scripts in board repositories with dynamic wrappers.

**Architecture:** Board repositories reference `purduerov/pcb-devops/.github/workflows/run-kicad-ci.yml@master` for CI/CD and execute auto-updating scripts from `pcb-devops` for local validation.

**Tech Stack:** Python 3, PowerShell, Bash, GitHub Actions, KiCad, KiBot.

## Global Constraints

- Python versions: 3.x
- KiCad format: KiCad v6+ symbol and PCB format
- Workspace location: `C:\Users\aman\Documents\PCB Design\purdue automation`

---

### Task 1: Update `board-template` Local Validation Scripts

**Files:**
- Modify: `board-template/run_validation.ps1`
- Modify: `board-template/run_validation.sh`
- Delete: `board-template/local_kibot.yaml`
- Delete: `board-template/tests/test_linter_validator.py`

**Interfaces:**
- Consumes: `pcb-devops/scripts/linter_validator.py`, `pcb-devops/kibot_master.yaml`
- Produces: Dynamic local validation runner in `board-template`

- [ ] **Step 1: Update `board-template/run_validation.ps1` to pull `pcb-devops` dynamically**

```powershell
# Auto-fetch/pull pcb-devops tools into .pcb-devops-cache
$cacheDir = Join-Path $PSScriptRoot ".pcb-devops-cache"
if (-not (Test-Path $cacheDir)) {
    Write-Host "Cloning central pcb-devops tools..." -ForegroundColor Cyan
    git clone --depth 1 https://github.com/purduerov/pcb-devops.git $cacheDir
} else {
    Write-Host "Updating central pcb-devops tools..." -ForegroundColor Cyan
    git -C $cacheDir pull origin master --quiet
}

$linterScript = Join-Path $cacheDir "scripts\linter_validator.py"
Write-Host "`nRunning central KiCad symbol library linter..." -ForegroundColor Cyan
python $linterScript (Get-ChildItem -Path "libs" -Recurse -Filter "*.kicad_sym").FullName
```

- [ ] **Step 2: Update `board-template/run_validation.sh` for Bash compatibility**

```bash
#!/usr/bin/env bash
set -e

CACHE_DIR="$(dirname "$0")/.pcb-devops-cache"
if [ ! -d "$CACHE_DIR" ]; then
    echo "Cloning central pcb-devops tools..."
    git clone --depth 1 https://github.com/purduerov/pcb-devops.git "$CACHE_DIR"
else
    echo "Updating central pcb-devops tools..."
    git -C "$CACHE_DIR" pull origin master --quiet
fi

echo "Running central KiCad symbol library linter..."
find libs -name "*.kicad_sym" -exec python3 "$CACHE_DIR/scripts/linter_validator.py" {} +
```

- [ ] **Step 3: Delete duplicate `local_kibot.yaml` and `tests/test_linter_validator.py` from `board-template`**

- [ ] **Step 4: Verify `run_validation.ps1` in `board-template`**

Run: `pwsh -File .\run_validation.ps1` in `board-template`
Expected: Successfully clones/updates `.pcb-devops-cache` and runs symbol linter cleanly.

- [ ] **Step 5: Commit changes in `board-template`**

```bash
git add run_validation.ps1 run_validation.sh
git rm -f local_kibot.yaml tests/test_linter_validator.py || true
git commit -m "refactor(devops): delegate local validation to central pcb-devops repo"
```

---

### Task 2: Refactor `X19-Electrical-New-Member-Board` Validation Scripts & Clean Up Duplicates

**Files:**
- Modify: `X19-Electrical-New-Member-Board/run_validation.ps1`
- Modify: `X19-Electrical-New-Member-Board/run_validation.sh`
- Delete: `X19-Electrical-New-Member-Board/local_kibot.yaml`
- Delete: `X19-Electrical-New-Member-Board/tests/test_linter_validator.py`

**Interfaces:**
- Consumes: `pcb-devops/scripts/linter_validator.py`
- Produces: Dynamic local validation runner in `X19-Electrical-New-Member-Board`

- [ ] **Step 1: Update `X19-Electrical-New-Member-Board/run_validation.ps1`**

Sync content with `board-template/run_validation.ps1`.

- [ ] **Step 2: Update `X19-Electrical-New-Member-Board/run_validation.sh`**

Sync content with `board-template/run_validation.sh`.

- [ ] **Step 3: Remove duplicate `local_kibot.yaml` and `tests/test_linter_validator.py`**

- [ ] **Step 4: Verify `run_validation.ps1` in `X19-Electrical-New-Member-Board`**

Run: `pwsh -File .\run_validation.ps1` in `X19-Electrical-New-Member-Board`
Expected: Output reports linter validation results using `.pcb-devops-cache`.

- [ ] **Step 5: Commit changes in `X19-Electrical-New-Member-Board`**

```bash
git add run_validation.ps1 run_validation.sh
git rm -f local_kibot.yaml tests/test_linter_validator.py || true
git commit -m "refactor(devops): delegate local validation to central pcb-devops repo"
```

---

### Task 3: Ensure Reusable GitHub Actions CI Workflow in Board Repositories

**Files:**
- Modify: `board-template/.github/workflows/ci.yml`
- Modify: `X19-Electrical-New-Member-Board/.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `purduerov/pcb-devops/.github/workflows/run-kicad-ci.yml@master`

- [ ] **Step 1: Update `board-template/.github/workflows/ci.yml`**

```yaml
name: Hardware CI/CD Pipeline

on:
  push:
    branches: [ master, main, develop ]
  pull_request:
    branches: [ master, main, develop ]

jobs:
  run-validation:
    uses: purduerov/pcb-devops/.github/workflows/run-kicad-ci.yml@master
    secrets: inherit
```

- [ ] **Step 2: Update `X19-Electrical-New-Member-Board/.github/workflows/ci.yml`**

Sync content with `board-template/.github/workflows/ci.yml`.

- [ ] **Step 3: Commit and Push workflow changes**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: reference central run-kicad-ci.yml@master from pcb-devops"
git push
```

---

### Task 4: Final End-to-End Verification

- [ ] **Step 1: Run unit tests in `pcb-devops`**
Run: `python -m unittest discover -s scripts -p "test_*.py"` in `pcb-devops`
Expected: PASS (10 tests)

- [ ] **Step 2: Test `run_validation.ps1` in `board-template` and `X19-Electrical-New-Member-Board`**
Run: `pwsh -File .\run_validation.ps1`
Expected: PASS

- [ ] **Step 3: Check `gh run list` across repos**
Verify no syntax errors in GitHub Actions workflows.
