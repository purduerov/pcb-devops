# Design Specification: Centralize PCB DevOps Tooling & Workflows

**Date:** 2026-08-05  
**Target Repositories:** `purduerov/pcb-devops`, `purduerov/board-template`, `purduerov/X19-Electrical-New-Member-Board`  
**Status:** Approved by User  

---

## 1. Overview & Architecture Goal

Currently, board template repositories (`board-template`, `X19-Electrical-New-Member-Board`, etc.) duplicate validation scripts, unit tests, and KiBot configurations locally (`run_validation.ps1`, `local_kibot.yaml`, `tests/test_linter_validator.py`). Any updates to linter rules or export rules require separate commit updates to every board repository.

This project centralizes all scripts, KiBot configurations, and GitHub Actions workflows into **`purduerov/pcb-devops`**. Individual board repositories will act as lightweight wrappers that dynamically reference `pcb-devops` tools both in CI/CD and in local development environments.

```
                      +-----------------------------+
                      |   purduerov/pcb-devops     |
                      |  (Single Source of Truth)   |
                      +--------------+--------------+
                                     |
           +-------------------------+-------------------------+
           |                                                   |
           v                                                   v
+-----------------------+                           +-----------------------+
|  GitHub Actions CI    |                           |   Local Environment   |
| (run-kicad-ci.yml)    |                           | (run_validation.ps1)  |
+-----------------------+                           +-----------------------+
           |                                                   |
           +-------------------------+-------------------------+
                                     | (Dynamic Pull / Call)
                                     v
                      +-----------------------------+
                      |      Board Repositories     |
                      | (board-template, X19, etc.) |
                      +-----------------------------+
```

---

## 2. Reusable GitHub Actions Workflow

* **File Location:** `pcb-devops/.github/workflows/run-kicad-ci.yml`
* **Trigger:** `workflow_call`
* **Board Integration:** Board repos only need a minimal `.github/workflows/ci.yml`:
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

### Workflow Execution Flow:
1. Checkout target board repository (including submodules recursively).
2. Checkout `purduerov/pcb-devops` to temporary directory `pcb-devops-tools`.
3. Run central linter `pcb-devops-tools/scripts/linter_validator.py` on all `.kicad_sym` files in `libs/`.
4. Copy central `pcb-devops-tools/kibot_master.yaml` into run environment.
5. Execute KiBot (`INTI-CMNB/KiBot@v2_k10`) for ERC, DRC, and manufacturing output exports.
6. Execute central sourcing validator `pcb-devops-tools/scripts/fetch_sourcing_bom.py` on generated XML BOM.
7. Upload generated fabrication artifacts.

---

## 3. Local Validation & Developer Environment Scripts

### Local Validation (`run_validation.ps1` & `run_validation.sh`)
* **Behavior:**
  1. Checks if a cached copy of `pcb-devops` exists in `.pcb-devops-cache/` or sibling folder.
  2. If missing or out of date, fetches/clones the latest `pcb-devops` master branch.
  3. Executes `linter_validator.py` from `pcb-devops` against local symbol libraries.
  4. Executes `fetch_sourcing_bom.py` from `pcb-devops` against local BOMs.

### Developer Tooling Cleanup in `board-template`
* Remove local duplicate file `local_kibot.yaml` from board repos.
* Remove duplicate `tests/test_linter_validator.py` from board repos (unit tests live exclusively in `pcb-devops`).
* Update `LAUNCH_KICAD.bat` / `.sh` and `setup_git_filters.ps1` / `.sh` in `board-template` to delegate setup logic to central `pcb-devops` scripts.

---

## 4. Verification Plan

1. **Local Verification:**
   * Run `python -m unittest discover -s scripts` in `pcb-devops`.
   * Test `run_validation.ps1` in `board-template` and `X19-Electrical-New-Member-Board` to verify auto-fetching of `pcb-devops` tools and clean execution.
2. **CI/CD Verification:**
   * Push changes to `pcb-devops`, `board-template`, and `X19-Electrical-New-Member-Board`.
   * Verify GitHub Action runs complete cleanly using `uses: purduerov/pcb-devops/.github/workflows/run-kicad-ci.yml@master`.

---
