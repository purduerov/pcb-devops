# Task 1 Report: Update board-template Local Validation Scripts

**Target Repository:** `C:\Users\aman\Documents\PCB Design\purdue automation\board-template`

## Summary of Changes
1. **PowerShell Script (`run_validation.ps1`):**
   - Automatically clones or pulls `https://github.com/purduerov/pcb-devops.git` into `.pcb-devops-cache` at script root.
   - Executes central symbol library linter script (`.pcb-devops-cache\scripts\linter_validator.py`) on all `*.kicad_sym` files in `libs/`.
   - Delegates KiBot hardware validation to `.pcb-devops-cache/kibot_master.yaml`.
   - Safely checks Docker daemon availability before starting container execution.

2. **Bash Script (`run_validation.sh`):**
   - Implements equivalent `.pcb-devops-cache` clone/pull logic and central linter invocation for Linux/macOS/Bash environments.

3. **Repository Cleanup:**
   - Deleted duplicate `local_kibot.yaml`.
   - Deleted duplicate `tests/test_linter_validator.py`.
   - Updated `.gitignore` to ignore `.pcb-devops-cache/`.

## Verification Results
- Ran `pwsh -File .\run_validation.ps1` in `board-template`.
- Central linter output:
  ```text
  Linting KiCad symbol file: libs/purdue-rov-kicad-lib/Symbols/rov_connectors.kicad_sym
  Linting KiCad symbol file: libs/purdue-rov-kicad-lib/Symbols/rov_logic.kicad_sym
  Linting KiCad symbol file: libs/purdue-rov-kicad-lib/Symbols/rov_mech.kicad_sym
  Linting KiCad symbol file: libs/purdue-rov-kicad-lib/Symbols/rov_passives.kicad_sym
  Linting KiCad symbol file: libs/purdue-rov-kicad-lib/Symbols/rov_power.kicad_sym
  Linting KiCad symbol file: libs/purdue-rov-kicad-lib/Symbols/rov_sensors.kicad_sym

  ✅ Library verified. All components compliant with structural guidelines.
  ```

## Git Commit
- **Commit Message:** `refactor(devops): delegate local validation to central pcb-devops repo`
- **Changes Committed:** `run_validation.ps1`, `run_validation.sh`, `.gitignore`, deleted `local_kibot.yaml`, deleted `tests/test_linter_validator.py`.

**Status:** DONE
