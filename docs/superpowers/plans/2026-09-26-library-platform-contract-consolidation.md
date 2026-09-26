# Library Platform Contract Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove dead Library Manager code and route all validation through the shared `rov` CLI, without changing CLI, GUI, workflow, exit-code, or marker behavior.

**Architecture:** `KiCad/DevOps` stays the policy and invocation layer. `KiCad/Libraries` keeps its GUI, importer UX, parser, and file operations, but validation, build, sync, and contribution go through `scripts/rov_bridge.py`. Dead one-shot scripts move to `scripts/legacy/` with a README. Board propagation is out of scope for this plan and travels through normal template-sync pull requests later.

**Tech Stack:** Python 3.10+, standard library plus `tkinter` for the GUI, `unittest`, `pyyaml` for workflow contract tests only.

**Spec:** `KiCad/DevOps/docs/superpowers/specs/2026-09-26-library-platform-contract-consolidation-design.md`

## Global Constraints

- Python floor is 3.10.
- The platform core runs on the standard library; do not add third-party runtime dependencies to `scripts/`.
- Never commit or push to a protected branch (`master`, `main`, `develop`, `development`, `release`).
- Tests must not use real network access, pushes, submodule initialization, or destructive repository operations.
- Support Windows, Linux, and macOS developer workflows.
- KiCad baseline is 10.x.
- No emojis in code, comments, docs, commits, or output.
- Do not modify teammate-authored code; inspect and review only. Board and template changes travel through normal pull requests, which are out of scope here.

## Review Focus

- A missing DevOps checkout must produce an actionable dialog, never a crash or direct push. Pinned by the existing GUI delegation tests in Task 2.
- A hung CLI must surface as a bounded timeout failure, never freeze the window. Pinned by the existing threading tests in Task 2.
- A non-URL contribution summary must never open a browser. Pinned by the existing browser-guard tests in Task 2.
- The import-dialog pull-request path must pass the application root, never a destroyed dialog. Pinned by the existing import-dialog tests in Task 2.
- A direct `linter_validator.py` subprocess in GUI or importer code is drift. Pinned by the new delegation tests in Task 2 and Task 3.

---

## File structure

- `KiCad/Libraries/scripts/legacy/README.md` (create): explains quarantined one-shot scripts.
- `KiCad/Libraries/scripts/legacy/` (create by move): `categorize_library.py`, `port_external_parts.py`, `port_usb_hub_parts.py`, `fix_usb_hub_project.py`, `benchmark.py`.
- `KiCad/Libraries/tests/test_legacy_quarantine.py` (create): asserts quarantine and no live imports.
- `KiCad/Libraries/scripts/library_manager_gui.py` (modify, `run_linter` at lines 1343-1364): delegate to `_run_rov_action`.
- `KiCad/Libraries/tests/test_library_manager_gui.py` (modify, `test_run_linter_success` at lines 153-163 and `test_run_linter_failure` at lines 165-177): assert delegation instead of direct subprocess.
- `KiCad/Libraries/scripts/import_part.py` (modify, linter blocks at lines 244-255 and 316-318): use a shared-validation helper through `rov_bridge`.
- `KiCad/Libraries/tests/test_library_manager.py` (modify): add shared-validation delegation tests.
- `KiCad/Libraries/scripts/linter_validator.py` (modify, line 17): import allowed categories from `kicad_sym_utils` instead of a duplicate literal.
- `KiCad/Libraries/tests/test_category_contract.py` (create): asserts one category source.
- `KiCad/Libraries/README.md` and `CONTRIBUTING.md` (modify): fix board-cache candidate lists and footprint naming.
- `KiCad/Libraries/tests/test_workflow_contracts.py` (modify): assert the docs list the five resolver candidates.

---

### Task 1: Quarantine dead Library scripts

**Files:**
- Create: `KiCad/Libraries/scripts/legacy/README.md`
- Create by move: `KiCad/Libraries/scripts/legacy/categorize_library.py`, `port_external_parts.py`, `port_usb_hub_parts.py`, `fix_usb_hub_project.py`, `benchmark.py`
- Create: `KiCad/Libraries/tests/test_legacy_quarantine.py`

**Interfaces:**
- Consumes: nothing; these files have no live importers.
- Produces: a `scripts/legacy/` directory whose contents are never imported by live code.

- [ ] **Step 1: Write the failing quarantine test**

```python
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEGACY = ROOT / "scripts" / "legacy"
DEAD = [
    "categorize_library.py",
    "port_external_parts.py",
    "port_usb_hub_parts.py",
    "fix_usb_hub_project.py",
    "benchmark.py",
]


class TestLegacyQuarantine(unittest.TestCase):
    def test_dead_scripts_are_quarantined(self):
        missing = [name for name in DEAD if not (LEGACY / name).is_file()]
        self.assertEqual([], missing)

    def test_live_code_does_not_import_dead_scripts(self):
        offenders = []
        for base in (ROOT / "scripts", ROOT / "tests"):
            for path in base.rglob("*.py"):
                if "scripts/legacy" in path.as_posix():
                    continue
                text = path.read_text(encoding="utf-8")
                for name in DEAD:
                    stem = Path(name).stem
                    if f"import {stem}" in text or f"from {stem}" in text:
                        offenders.append(f"{path}:{stem}")
        self.assertEqual([], offenders)
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `KiCad/Libraries`:
```powershell
python -m unittest tests.test_legacy_quarantine -v
```
Expected: FAIL because `scripts/legacy/` does not exist yet.

- [ ] **Step 3: Move the dead scripts and add the legacy README**

Run from `KiCad/Libraries`:
```powershell
New-Item -ItemType Directory -Force -Path scripts/legacy
git mv scripts/categorize_library.py scripts/legacy/categorize_library.py
git mv scripts/port_external_parts.py scripts/legacy/port_external_parts.py
git mv scripts/port_usb_hub_parts.py scripts/legacy/port_usb_hub_parts.py
git mv scripts/fix_usb_hub_project.py scripts/legacy/fix_usb_hub_project.py
git mv scripts/benchmark.py scripts/legacy/benchmark.py
```

Create `KiCad/Libraries/scripts/legacy/README.md` with exactly:
```markdown
# Legacy one-shot scripts

These scripts are quarantined, not live code. They were one-shot migrations,
obsolete import paths, or ad-hoc benchmarks. Nothing in `scripts/`, `tests/`,
or CI imports them. Do not add new callers. Delete them outright only with
owner approval.
```

- [ ] **Step 4: Run the quarantine test and the full Libraries suite**

Run from `KiCad/Libraries`:
```powershell
python -m unittest tests.test_legacy_quarantine -v
python -m unittest discover -s tests -v
```
Expected: PASS, all tests green.

- [ ] **Step 5: Commit**

```powershell
git add -- scripts/legacy tests/test_legacy_quarantine.py
git commit -m "refactor: quarantine dead one-shot library scripts"
```
Do not push.

### Task 2: Route GUI Validate All through the shared CLI

**Files:**
- Modify: `KiCad/Libraries/scripts/library_manager_gui.py:1343-1364`
- Modify: `KiCad/Libraries/tests/test_library_manager_gui.py:153-177`
- Test: `KiCad/Libraries/tests/test_library_manager_gui.py`

**Interfaces:**
- Consumes: `library_manager_gui._run_rov_action(title, arguments, root, success_title)` and `rov_bridge.run_rov`.
- Produces: `LibraryManagerApp.run_linter()` delegating to `["library", "validate"]` with identical dialogs.

- [ ] **Step 1: Replace the direct-subprocess linter tests with delegation tests**

Replace `test_run_linter_success` (lines 153-163) with:
```python
    @patch("library_manager_gui._run_rov_action")
    def test_run_linter_success(self, mock_run):
        """Verifies run_linter delegates to the shared CLI."""
        self.app.run_linter()
        mock_run.assert_called_once_with(
            "Linter Validation Failed",
            ["library", "validate"],
            root=self.app.root,
            success_title="Linter Validation",
        )
```

Replace `test_run_linter_failure` (lines 165-177) with:
```python
    @patch("library_manager_gui._run_rov_action")
    def test_run_linter_failure(self, mock_run):
        """Verifies run_linter reports through the shared action path."""
        self.app.run_linter()
        mock_run.assert_called_once_with(
            "Linter Validation Failed",
            ["library", "validate"],
            root=self.app.root,
            success_title="Linter Validation",
        )
```

- [ ] **Step 2: Run the delegation tests to verify they fail**

Run from `KiCad/Libraries`:
```powershell
python -m unittest tests.test_library_manager_gui.TestLibraryManagerGui.test_run_linter_success tests.test_library_manager_gui.TestLibraryManagerGui.test_run_linter_failure -v
```
Expected: FAIL because `run_linter` still shells out to `linter_validator.py` directly.

- [ ] **Step 3: Implement the delegation**

Replace the body of `run_linter` (lines 1343-1364) with:
```python
    def run_linter(self):
        return _run_rov_action(
            "Linter Validation Failed",
            ["library", "validate"],
            root=self.root,
            success_title="Linter Validation",
        )
```

- [ ] **Step 4: Run the delegation tests and the GUI suite**

Run from `KiCad/Libraries`:
```powershell
python -m unittest tests.test_library_manager_gui -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add -- scripts/library_manager_gui.py tests/test_library_manager_gui.py
git commit -m "refactor: validate through the shared CLI in the GUI linter action"
```
Do not push.

### Task 3: Route importer validation through the shared CLI

**Files:**
- Modify: `KiCad/Libraries/scripts/import_part.py:244-255,316-318`
- Modify: `KiCad/Libraries/tests/test_library_manager.py`
- Test: `KiCad/Libraries/tests/test_library_manager.py`

**Interfaces:**
- Consumes: `import_part.rov_bridge.run_rov(BASE_DIR, ["library", "validate"])`.
- Produces: `import_part.run_shared_validation()` returning `(returncode, output)` used by both import paths.

- [ ] **Step 1: Point the existing import tests at the shared seam**

In `test_non_interactive_import_prints_the_same_hint_after_validation` (lines 201-225), replace the `fake_linter` helper and its patch with:
```python
        def fake_validate(*_args, **_kwargs):
            calls.append(True)
            return subprocess.CompletedProcess([], 0, "", "")
```
and replace `patch.object(import_part.subprocess, "run", fake_linter)` with:
```python
        ), patch.object(
            import_part.rov_bridge, "run_rov", fake_validate
```

In `test_non_interactive_import_reports_a_failing_linter_without_a_hint` (lines 227-246), replace the `fake_linter` helper with:
```python
        def fake_validate(*_args, **_kwargs):
            return subprocess.CompletedProcess([], 1, "", "missing mandatory field: MPN")
```
and replace `patch.object(import_part.subprocess, "run", fake_linter)` with:
```python
        ), patch.object(
            import_part.rov_bridge, "run_rov", fake_validate
```

- [ ] **Step 2: Run the updated tests to verify they fail**

Run from `KiCad/Libraries`:
```powershell
python -m unittest tests.test_library_manager.TestImportPartContributionHint.test_non_interactive_import_prints_the_same_hint_after_validation tests.test_library_manager.TestImportPartContributionHint.test_non_interactive_import_reports_a_failing_linter_without_a_hint -v
```
Expected: FAIL because the implementation still shells out to `linter_validator.py` directly.

- [ ] **Step 3: Write the failing delegation tests**

Append to `KiCad/Libraries/tests/test_library_manager.py`:
```python
class TestSharedValidation(unittest.TestCase):
    def test_success_returns_zero_and_output(self):
        from unittest.mock import MagicMock

        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = "Validation successful!"
        proc.stderr = ""
        with patch.object(import_part.rov_bridge, "run_rov", return_value=proc) as mock_run:
            code, output = import_part.run_shared_validation()
        mock_run.assert_called_once_with(import_part.BASE_DIR, ["library", "validate"])
        self.assertEqual(0, code)
        self.assertIn("Validation successful!", output)

    def test_failure_returns_nonzero(self):
        from unittest.mock import MagicMock

        proc = MagicMock()
        proc.returncode = 1
        proc.stdout = ""
        proc.stderr = "Rule violation"
        with patch.object(import_part.rov_bridge, "run_rov", return_value=proc):
            code, output = import_part.run_shared_validation()
        self.assertEqual(1, code)
        self.assertIn("Rule violation", output)
```

- [ ] **Step 4: Run the new tests to verify they fail**

Run from `KiCad/Libraries`:
```powershell
python -m unittest tests.test_library_manager.TestSharedValidation -v
```
Expected: FAIL with `run_shared_validation` not defined.

- [ ] **Step 5: Implement the helper and replace both direct linter blocks**

Add after the `BASE_DIR` constants near line 56:
```python
def run_shared_validation():
    """Validate the library through the shared CLI and return code and output."""
    result = rov_bridge.run_rov(BASE_DIR, ["library", "validate"])
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode, output
```

In `interactive_mode`, replace lines 244-255 with:
```python
    print("\n[INFO] Running Linter Verification...")
    returncode, _ = run_shared_validation()

    if returncode == 0:
        print("\n[OK] Part imported successfully and verified compliant!")
        print_contribution_hint(mpn, category)
    else:
        print("\n[FAIL] Linter check failed. Please correct fields.")
```

In `main`, replace lines 316-318 with:
```python
    # Run linter
    returncode, _ = run_shared_validation()
    if returncode == 0:
        print("[OK] Part imported successfully and verified compliant!")
        print_contribution_hint(field_updates["MPN"], category)
    else:
        print("[FAIL] Linter check failed. Please correct fields.")
```

- [ ] **Step 6: Run the importer tests and the full Libraries suite**

Run from `KiCad/Libraries`:
```powershell
python -m unittest tests.test_library_manager -v
python -m unittest discover -s tests -v
```
Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add -- scripts/import_part.py tests/test_library_manager.py
git commit -m "refactor: validate imports through the shared CLI"
```
Do not push.

### Task 4: Single-source category names in Libraries

**Files:**
- Modify: `KiCad/Libraries/scripts/linter_validator.py:17`
- Create: `KiCad/Libraries/tests/test_category_contract.py`
- Test: `KiCad/Libraries/tests/test_category_contract.py`

**Interfaces:**
- Consumes: `kicad_sym_utils.CATEGORIES`.
- Produces: `linter_validator.ALLOWED_CATEGORIES` equal to that single source.

- [ ] **Step 1: Write the failing category test**

```python
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import kicad_sym_utils
import linter_validator


class TestCategoryContract(unittest.TestCase):
    def test_allowed_categories_come_from_one_source(self):
        self.assertEqual(
            set(kicad_sym_utils.CATEGORIES), linter_validator.ALLOWED_CATEGORIES
        )

    def test_all_six_short_names_present(self):
        self.assertEqual(
            {"Passives", "Power", "Logic", "Connectors", "Sensors", "Mech"},
            linter_validator.ALLOWED_CATEGORIES,
        )
```

- [ ] **Step 2: Run the test to verify it passes already**

Run from `KiCad/Libraries`:
```powershell
python -m unittest tests.test_category_contract -v
```
Expected: PASS. The values already agree; the test pins the agreement so the refactor below cannot drift.

- [ ] **Step 3: Remove the duplicate literal**

In `KiCad/Libraries/scripts/linter_validator.py`, replace line 17:
```python
ALLOWED_CATEGORIES = {"Passives", "Power", "Logic", "Connectors", "Sensors", "Mech"}
```
with:
```python
from kicad_sym_utils import CATEGORIES as _CATEGORIES

ALLOWED_CATEGORIES = set(_CATEGORIES)
```
Keep the existing stdlib imports; `kicad_sym_utils` lives in the same `scripts/` directory, which is on `sys.path` whenever the linter runs as a script or through the CLI.

- [ ] **Step 4: Run the category test, the codepage tests, and the full suite**

Run from `KiCad/Libraries`:
```powershell
python -m unittest tests.test_category_contract tests.test_codepage_safety -v
python -m unittest discover -s tests -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add -- scripts/linter_validator.py tests/test_category_contract.py
git commit -m "refactor: single-source allowed categories in the library linter"
```
Do not push.

### Task 5: Fix resolver and footprint docs parity

**Files:**
- Modify: `KiCad/Libraries/CONTRIBUTING.md:32-40,182,191`
- Modify: `KiCad/Libraries/tests/test_workflow_contracts.py`
- Test: `KiCad/Libraries/tests/test_workflow_contracts.py`

**Interfaces:**
- Consumes: `rov_bridge.devops_candidates` order and `kicad_sym_utils.link_3d_model_to_footprint` output format.
- Produces: docs that name all five resolver candidates and the real `rov_<category>:` footprint and `${KIPRJMOD}` model formats.

- [ ] **Step 1: Write the failing docs test**

Append to the library CI contract class in `KiCad/Libraries/tests/test_workflow_contracts.py`:
```python
    def test_docs_name_every_resolver_candidate(self):
        contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        for candidate in (
            "ROV_DEVOPS_DIR",
            ".pcb-devops-cache",
            "../DevOps",
            "../pcb-devops",
            "board root",
        ):
            with self.subTest(candidate=candidate):
                self.assertIn(candidate, contributing)

    def test_docs_use_the_real_footprint_and_model_formats(self):
        contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        self.assertIn("rov_<category>:", contributing)
        self.assertIn("${KIPRJMOD}/libs/purdue-rov-kicad-lib/3D_Models/", contributing)
        self.assertNotIn("ROV_Footprints:[", contributing)
        self.assertNotIn("${KICAD_PROJECT_DIR}/libs/", contributing)
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `KiCad/Libraries`:
```powershell
python -m unittest tests.test_workflow_contracts.TestLibraryCiContract.test_docs_name_every_resolver_candidate tests.test_workflow_contracts.TestLibraryCiContract.test_docs_use_the_real_footprint_and_model_formats -v
```
Expected: FAIL. Adjust the class name to wherever the test was appended if different.

- [ ] **Step 3: Fix the docs**

In `CONTRIBUTING.md` lines 32-40, replace with:
```markdown
1. the `ROV_DEVOPS_DIR` environment variable, which always wins;
2. `.pcb-devops-cache/` inside the library directory;
3. a sibling `../DevOps`, the multi-repository workspace layout;
4. a sibling `../pcb-devops`, the older single-repository sibling name;
5. the board root cache, two levels above the library, which is where a
   board's `LAUNCH_KICAD` puts its cache.

`ROV_DEVOPS_DIR` is the only setting that works in every layout.
```

In `CONTRIBUTING.md` line 182, replace:
```
    ${KICAD_PROJECT_DIR}/libs/purdue-rov-kicad-lib/3D_Models/[your-part-name].step
```
with:
```
    ${KIPRJMOD}/libs/purdue-rov-kicad-lib/3D_Models/[your-part-name].step
```

In `CONTRIBUTING.md` line 191 and the Step 4 prose at line 298, replace:
```
    ROV_Footprints:[exact_footprint_name_you_saved]
```
with:
```
    rov_<category>:[exact_footprint_name_you_saved]
```
and replace `the **Footprint** field points to `ROV_Footprints:[footprint_name]`` with `the **Footprint** field points to `rov_<category>:[footprint_name]``.

- [ ] **Step 4: Run the docs tests and the full suite**

Run from `KiCad/Libraries`:
```powershell
python -m unittest tests.test_workflow_contracts -v
python -m unittest discover -s tests -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add -- CONTRIBUTING.md tests/test_workflow_contracts.py
git commit -m "docs: fix resolver candidates and footprint naming in contributing guide"
```
Do not push.