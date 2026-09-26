# ROV PCB Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a simple cross-platform `rov` CLI and shared core that bootstrap boards, validate projects and libraries, synchronize the approved ROV KiCad library through update PRs, and power both `LAUNCH_KICAD` and the existing Library Manager without duplicating Git logic.

**Architecture:** `KiCad/DevOps` owns a standard-library Python core and a thin `argparse` CLI. Board repositories consume a small `rov.project.json` manifest and reusable workflows; the GitHub board template supplies `bootstrap.py`; `KiCad/Libraries` keeps its existing GUI but invokes the central CLI through a small bridge. The work is one integrated plan because the template, GUI bridge, CI, and update workflow all depend on the interfaces created in Tasks 1-4.

**Tech Stack:** Python 3.10+ standard library, `unittest`, Git, GitHub CLI, GitHub Actions, KiCad 10.x, KiBot, Tkinter, Bash, Windows batch.

**Spec:** `KiCad/DevOps/docs/superpowers/specs/2026-09-25-rov-pcb-platform-design.md`

## Global Constraints

- Keep `KiCad/DevOps`, `KiCad/Board_Template`, `KiCad/Libraries`, and individual boards as separate Git repositories.
- Keep GitHub **Use this template** as the primary board-creation entry point.
- Keep `LAUNCH_KICAD.bat`, `LAUNCH_KICAD.sh`, and the existing Library Manager GUI.
- New platform core code must use Python 3.10+ and the standard library; do not add a package-manager dependency to the new `rov` code.
- Support Windows, Linux, and macOS; run full KiCad validation only against the current KiCad 10.x baseline.
- Treat the protected ROV library `master` branch as the approved source.
- Never reset, stash, or overwrite a developer worktree automatically.
- Never push directly to a protected library or board branch from the CLI, launchers, or GUI.
- Environment setup diagnoses and guides; it does not install software without an explicit user action.
- Do not add emojis to code, comments, documentation, commits, or user-facing output.
- Do not modify teammate-authored code. In particular, do not edit the currently staged `KiCad/Board_Template/.githooks/*` or `KiCad/Board_Template/LAUNCH_KICAD.sh`; use the untracked `.rov-hooks/` mechanism instead.
- Do not push commits or post GitHub comments without explicit user permission.
- Preserve unrelated staged and unstaged changes in every repository.

## Review Focus

1. **Dirty design worktree:** any uncommitted board or library change must produce `BLOCKED`, not an automatic reset, stash, or overwrite — covered by Tasks 1, 2, 4, and 5.
2. **Offline or missing tools:** missing network, KiCad, Docker, or GitHub CLI must produce an explicit `WARN` or `BLOCKED` result and must never be reported as a successful full validation — covered by Tasks 3 and 4.
3. **Incomplete board/library wiring:** missing `rov.project.json`, standard library table entries, project files, or an initialized submodule must fail with an actionable message — covered by Tasks 1, 2, and 3.
4. **Invalid or unrelated library contribution:** validation must run before commit, and only `Symbols/`, `Footprints/`, `3D_Models/`, and `Design_Blocks/` may be staged — covered by Task 5.
5. **Concurrent or duplicate updates:** an existing update branch/PR, non-fast-forward remote, or dirty submodule must stop without force-push — covered by Tasks 4 and 6.

---

## File Structure

### `KiCad/DevOps`

- Create `scripts/rov_core.py`: shared status types, manifest validation, board checks, safe Git helpers, bootstrap, library sync, and contribution preparation.
- Create `scripts/rov.py`: command-line parsing and exit codes only.
- Create `tests/test_rov_core.py`: manifest, table, submodule, and Git-state tests.
- Create `tests/test_rov.py`: CLI behavior and status/exit-code tests.
- Create `tests/test_bootstrap.py`: bootstrap and generated hook tests.
- Create `tests/test_library_update.py`: local bare-remote/submodule update tests.
- Create `tests/test_library_contribution.py`: safe library branch/PR tests.
- Create `tests/test_workflow_contracts.py`: central workflow text contracts.
- Modify `scripts/LAUNCH_KICAD.bat` and `scripts/LAUNCH_KICAD.sh`: call shared bootstrap, then retain only OS-specific KiCad opening.
- Modify `scripts/setup_git_filters.ps1` and `scripts/setup_git_filters.sh`: configure filters but do not force the tracked `.githooks` directory.
- Create `.github/workflows/update-library.yml`: reusable validated update-PR workflow.
- Modify `.github/workflows/run-kicad-ci.yml` in Task 8: run shared fast validation before KiBot after all board manifests exist.
- Modify `.github/workflows/devops-ci.yml`: test the new core on Windows, Linux, and macOS.
- Modify `README.md`: one-command quickstart and command reference.

### `KiCad/Board_Template`

- Create `bootstrap.py`: thin resolver/invoker for the central `rov.py`.
- Create `rov.project.json`: minimal board contract.
- Modify `.gitignore`: ignore generated `.rov-hooks/`.
- Delete `.github/workflows/initialize-template.yml`: bootstrap becomes the single initialization path.
- Replace `.github/workflows/auto-update-submodule.yml` with a reusable-workflow call.
- Keep `LAUNCH_KICAD.*` and existing `.githooks/*` untouched in this plan.
- Modify `README.md`: document template creation and bootstrap.

### `KiCad/Libraries`

- Create `scripts/rov_bridge.py`: locate the central DevOps scripts and invoke them.
- Modify `scripts/library_manager_gui.py`: delegate Git sync and contribution flows to the bridge.
- Modify `scripts/import_part.py`: remove direct commit/push-to-master behavior.
- Create `tests/test_rov_bridge.py`: resolver and subprocess contract tests.
- Modify `tests/test_library_manager_gui.py`: assert bridge delegation.
- Modify `tests/test_library_manager.py`: assert contribution preparation behavior without touching the protected branch.
- Modify `.github/workflows/notify-boards.yml`: dispatch only after library CI succeeds.
- Create `tests/test_workflow_contracts.py`: assert notification gating.
- Modify `README.md` and `CONTRIBUTING.md`: use the safe contribution flow.

### Board repositories

Apply the same contract to:

- `KiCad/Boards/X19-Control-Board`
- `KiCad/Boards/X19-Electrical-New-Member-Board`
- `KiCad/Boards/X19-Float-Board`
- `KiCad/Boards/X19-Pi-Shield-Board`
- `KiCad/Boards/X19-Power-Slab-Board`
- `KiCad/Boards/X19-Pressure-Chamber-Board`
- `KiCad/Boards/X19-USB-Hub-Board`

For each repository:

- Create `rov.project.json`.
- Replace `.github/workflows/auto-update-submodule.yml` with a reusable-workflow call.
- Modify `.gitignore` to ignore `.rov-hooks/`.
- Keep existing `ci.yml` and `sync-template.yml` behavior unless a validation failure proves a change is required.

---

### Task 1: Shared Contracts, Manifest Validation, and Safe Git Primitives

**Files:**
- Create: `KiCad/DevOps/scripts/rov_core.py`
- Create: `KiCad/DevOps/tests/test_rov_core.py`
- Modify: `KiCad/DevOps/scripts/sync_project_libs.py`

**Interfaces:**
- Consumes: existing `sync_project_libs.sync_project()` and `sync_gitmodules()` behavior.
- Produces:
  - `STANDARD_LIBS: list[dict[str, str]]`
  - `CheckResult(name: str, status: str, message: str)`
  - `BootstrapResult(changed_files: tuple[Path, ...], messages: tuple[str, ...])`
  - `LibraryUpdatePlan(current_commit: str, target_commit: str, changed_files: tuple[str, ...], blocked_reason: str | None)`
  - `load_project_config(project_dir: Path) -> dict[str, object]`
  - `validate_project_config(config: dict[str, object]) -> list[CheckResult]`
  - `find_project_file(project_dir: Path) -> Path | None`
  - `validate_library_tables(project_dir: Path) -> list[CheckResult]`
  - `check_submodule(project_dir: Path, config: dict[str, object]) -> CheckResult`
  - `run_git(repo_dir: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]`
  - `is_git_worktree(repo_dir: Path) -> bool`
  - `is_clean_worktree(repo_dir: Path) -> bool`
  - `current_branch(repo_dir: Path) -> str | None`

- [x] **Step 1: Write failing contract tests**
Create `KiCad/DevOps/tests/test_rov_core.py` with tests that use `tempfile.TemporaryDirectory()` and do not access the network:

```python
import json
import sys
import tempfile
import unittest
from pathlib import Path

DEVOPS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DEVOPS_DIR / "scripts"))

import rov_core


VALID_CONFIG = {
    "schema": 1,
    "project_name": "X19-Control-Board",
    "kicad_version": "10",
    "platform_ref": "master",
    "library": {
        "path": "libs/purdue-rov-kicad-lib",
        "branch": "master",
        "update_policy": "pull-request",
    },
    "ci_profile": "standard",
}


class TestRovCoreContracts(unittest.TestCase):
    def test_valid_manifest_has_no_failures(self):
        failures = [r for r in rov_core.validate_project_config(VALID_CONFIG) if r.status == "FAIL"]
        self.assertEqual(failures, [])

    def test_manifest_rejects_unsupported_kicad_version(self):
        config = dict(VALID_CONFIG)
        config["kicad_version"] = "9"
        messages = [r.message for r in rov_core.validate_project_config(config) if r.status == "FAIL"]
        self.assertTrue(any("kicad_version" in message for message in messages))

    def test_missing_manifest_reports_actionable_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "rov.project.json"):
                rov_core.load_project_config(Path(tmp))

    def test_missing_standard_library_table_entry_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sym-lib-table").write_text('(sym_lib_table)\n', encoding="utf-8")
            (root / "fp-lib-table").write_text('(fp_lib_table)\n', encoding="utf-8")
            failures = [r for r in rov_core.validate_library_tables(root) if r.status == "FAIL"]
            self.assertEqual(len(failures), 2)

    def test_project_file_ignores_prl(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "demo.kicad_prl").write_text("{}", encoding="utf-8")
            (root / "demo.kicad_pro").write_text("{}", encoding="utf-8")
            self.assertEqual(rov_core.find_project_file(root), root / "demo.kicad_pro")


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run the tests and confirm the expected failure**
Run from `KiCad/DevOps`:

```powershell
python -m unittest tests.test_rov_core -v
```

Expected: FAIL because `scripts/rov_core.py` does not exist.

- [x] **Step 3: Implement the shared types and pure validators**
Create `KiCad/DevOps/scripts/rov_core.py` with these exact status values and dataclasses:

```python
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

STATUS_PASS = "PASS"
STATUS_WARN = "WARN"
STATUS_FAIL = "FAIL"
STATUS_BLOCKED = "BLOCKED"

KICAD_BASELINE = "10"
LIBRARY_BRANCH = "master"
PROJECT_CONFIG_NAME = "rov.project.json"

STANDARD_LIB_LABELS = (
    ("rov_passives", "Passives"),
    ("rov_power", "Power"),
    ("rov_logic", "Logic"),
    ("rov_connectors", "Connectors"),
    ("rov_sensors", "Sensors"),
    ("rov_mech", "Mechanical"),
)

STANDARD_LIBS = [
    {
        "name": name,
        "sym_uri": f"${{KIPRJMOD}}/libs/purdue-rov-kicad-lib/Symbols/{name}.kicad_sym",
        "fp_uri": f"${{KIPRJMOD}}/libs/purdue-rov-kicad-lib/Footprints/{name}.pretty",
        "sym_descr": f"Purdue ROV {label} Symbols",
        "fp_descr": f"Purdue ROV {label} Footprints",
    }
    for name, label in STANDARD_LIB_LABELS
]


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    message: str


@dataclass(frozen=True)
class BootstrapResult:
    changed_files: tuple[Path, ...]
    messages: tuple[str, ...]


@dataclass(frozen=True)
class LibraryUpdatePlan:
    current_commit: str
    target_commit: str
    changed_files: tuple[str, ...]
    blocked_reason: str | None
```

Implement `load_project_config()` so missing or malformed JSON raises `ValueError` containing the absolute config path. Implement `validate_project_config()` so it reports `FAIL` for a missing/wrong `schema`, empty `project_name`, `kicad_version != "10"`, empty `platform_ref`, missing library `path`/`branch`, an `update_policy` other than `pull-request`, and a missing `ci_profile`.

Implement `validate_library_tables()` by reading `sym-lib-table` and `fp-lib-table` as text and returning exactly one `CheckResult` per missing or unreadable table file; each result message lists every missing nickname in that table. Implement `check_submodule()` by resolving the manifest path and returning:

- `PASS` when `<path>/.git` exists or the path is a valid initialized submodule worktree.
- `BLOCKED` when the directory is absent.
- `FAIL` when `.gitmodules` does not contain the configured path.

Implement `find_project_file()` to return the lexicographically first `*.kicad_pro`, never a `.kicad_prl`.

Implement Git helpers without shell=True:

```python
def is_git_worktree(repo_dir: Path) -> bool:
    return run_git(repo_dir, "rev-parse", "--is-inside-work-tree").stdout.strip() == "true"


def run_git(repo_dir: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed"
        raise RuntimeError(detail)
    return result


def is_clean_worktree(repo_dir: Path) -> bool:
    return run_git(repo_dir, "status", "--porcelain").stdout.strip() == ""


def current_branch(repo_dir: Path) -> str | None:
    result = run_git(repo_dir, "symbolic-ref", "--quiet", "--short", "HEAD")
    return result.stdout.strip() or None
```

Raise a clear `RuntimeError("Git is required but was not found")` from `run_git()` when `shutil.which("git")` is `None`.

- [x] **Step 4: Make `sync_project_libs.py` consume the shared library contract**
Replace its local `STANDARD_LIBS` list with:

```python
from rov_core import STANDARD_LIBS
```

Keep `sync_lib_table()`, `sync_gitmodules()`, and `sync_project()` signatures unchanged so the existing tests and launchers continue to work.

- [x] **Step 5: Run focused and full DevOps tests**
```powershell
python -m unittest tests.test_rov_core -v
python -m unittest discover -s tests -v
```

Expected: all tests PASS, including the four pre-existing DevOps test modules.

- [x] **Step 6: Commit DevOps changes**
```powershell
git add -- scripts/rov_core.py scripts/sync_project_libs.py tests/test_rov_core.py
git commit -m "feat: add shared ROV PCB contracts and safe git primitives"
```

---

### Task 2: Idempotent Board Bootstrap and Untracked Hook Installation

**Files:**
- Modify: `KiCad/DevOps/scripts/rov_core.py`
- Create: `KiCad/DevOps/tests/test_bootstrap.py`
- Modify: `KiCad/DevOps/scripts/LAUNCH_KICAD.bat`
- Modify: `KiCad/DevOps/scripts/LAUNCH_KICAD.sh`
- Modify: `KiCad/DevOps/scripts/setup_git_filters.ps1`
- Modify: `KiCad/DevOps/scripts/setup_git_filters.sh`
- Create: `KiCad/Board_Template/bootstrap.py`
- Create: `KiCad/Board_Template/rov.project.json`
- Modify: `KiCad/Board_Template/.gitignore`

**Interfaces:**
- Consumes: Task 1 `CheckResult`, `BootstrapResult`, `STANDARD_LIBS`, `find_project_file()`, `run_git()`.
- Produces:
  - `bootstrap_project(project_dir: Path, project_name: str) -> BootstrapResult`
  - `install_project_hook(project_dir: Path) -> Path`
  - `KiCad/Board_Template/bootstrap.py --project-dir PATH --project-name NAME --non-interactive`

- [x] **Step 1: Write failing bootstrap tests**
Create `KiCad/DevOps/tests/test_bootstrap.py`:

```python
import json
import sys
import tempfile
import unittest
from pathlib import Path

DEVOPS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DEVOPS_DIR / "scripts"))

import rov_core


class TestBootstrap(unittest.TestCase):
    def make_template(self, root: Path) -> None:
        (root / "board-template.kicad_pro").write_text(
            json.dumps({"meta": {"version": 1}, "project": {"title": "Purdue ROV Subsystem Board"}}),
            encoding="utf-8",
        )
        (root / "board-template.kicad_sch").write_text("(kicad_sch)\n", encoding="utf-8")
        (root / "board-template.kicad_pcb").write_text("(kicad_pcb)\n", encoding="utf-8")
        (root / ".gitmodules").write_text(
            '[submodule "libs/purdue-rov-kicad-lib"]\n'
            "\tpath = libs/purdue-rov-kicad-lib\n"
            "\turl = ../purdue-rov-kicad-lib.git\n"
            "\tbranch = master\n",
            encoding="utf-8",
        )

    def test_bootstrap_renames_and_writes_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_template(root)
            result = rov_core.bootstrap_project(root, "Demo-Board")
            self.assertTrue((root / "Demo-Board.kicad_pro").exists())
            self.assertTrue((root / "Demo-Board.kicad_sch").exists())
            config = json.loads((root / "rov.project.json").read_text(encoding="utf-8"))
            self.assertEqual(config["project_name"], "Demo-Board")
            self.assertTrue((root / "sym-lib-table").exists())
            self.assertTrue((root / "fp-lib-table").exists())
            self.assertIn("Demo-Board.kicad_pro", [p.name for p in result.changed_files])

    def test_bootstrap_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_template(root)
            rov_core.bootstrap_project(root, "Demo-Board")
            before = sorted(p.name for p in root.iterdir())
            rov_core.bootstrap_project(root, "Demo-Board")
            self.assertEqual(before, sorted(p.name for p in root.iterdir()))

    def test_bootstrap_does_not_overwrite_existing_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_template(root)
            (root / "Demo-Board.kicad_pro").write_text('{"project": {"title": "Mine"}}', encoding="utf-8")
            result = rov_core.bootstrap_project(root, "Demo-Board")
            self.assertEqual(json.loads((root / "Demo-Board.kicad_pro").read_text(encoding="utf-8"))["project"]["title"], "Mine")
            self.assertTrue(any("not overwritten" in message for message in result.messages))

    def test_bootstrap_updates_template_manifest_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_template(root)
            (root / "rov.project.json").write_text(
                '{"schema": 1, "project_name": "board-template", "kicad_version": "10", '
                '"platform_ref": "master", "library": {"path": "libs/purdue-rov-kicad-lib", '
                '"branch": "master", "update_policy": "pull-request"}, "ci_profile": "standard"}',
                encoding="utf-8",
            )
            rov_core.bootstrap_project(root, "Demo-Board")
            config = json.loads((root / "rov.project.json").read_text(encoding="utf-8"))
            self.assertEqual(config["project_name"], "Demo-Board")

    def test_hook_is_untracked_and_does_not_modify_githooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tracked = root / ".githooks" / "pre-commit"
            tracked.parent.mkdir()
            tracked.write_text("original\n", encoding="utf-8")
            hook = rov_core.install_project_hook(root)
            self.assertEqual(hook, root / ".rov-hooks" / "pre-commit")
            self.assertEqual(tracked.read_text(encoding="utf-8"), "original\n")
            self.assertIn("board validate", hook.read_text(encoding="utf-8"))

    def test_missing_submodule_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".gitmodules").write_text(
                '[submodule "libs/purdue-rov-kicad-lib"]\n'
                "\tpath = libs/purdue-rov-kicad-lib\n"
                "\turl = ../purdue-rov-kicad-lib.git\n",
                encoding="utf-8",
            )
            config = {
                "library": {"path": "libs/purdue-rov-kicad-lib", "branch": "master"}
            }
            self.assertEqual(rov_core.check_submodule(root, config).status, "BLOCKED")

    def test_dirty_submodule_is_not_fast_forwarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "libs" / "purdue-rov-kicad-lib"
            library.mkdir(parents=True)
            (library / "local.txt").write_text("local change", encoding="utf-8")
            result = rov_core.bootstrap_project(root, "Demo-Board")
            self.assertEqual((library / "local.txt").read_text(encoding="utf-8"), "local change")
            self.assertTrue(any("submodule" in message.lower() for message in result.messages))

    def test_filter_scripts_do_not_select_tracked_hooks(self):
        for filename in ("setup_git_filters.sh", "setup_git_filters.ps1"):
            text = (DEVOPS_DIR / "scripts" / filename).read_text(encoding="utf-8")
            self.assertNotIn("core.hooksPath .githooks", text)

    def test_central_launchers_do_not_reset_user_worktrees(self):
        for filename in ("LAUNCH_KICAD.sh", "LAUNCH_KICAD.bat"):
            text = (DEVOPS_DIR / "scripts" / filename).read_text(encoding="utf-8")
            self.assertNotIn("reset --hard", text)
            self.assertNotIn("checkout -B", text)


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run the tests and confirm they fail**
```powershell
python -m unittest tests.test_bootstrap -v
```

Expected: FAIL because `bootstrap_project()` and `install_project_hook()` are undefined.

- [x] **Step 3: Implement idempotent bootstrap**
Add these behaviors to `rov_core.py`:

1. Sanitize `project_name` to a filename-safe stem; reject empty names and path separators.
2. Rename `board-template.kicad_pro`, `.kicad_sch`, and `.kicad_pcb` to `<project_name>.*` only when the destination does not exist.
3. If a destination exists, leave it unchanged and add a message containing `not overwritten`.
4. Update only the `project.title` value in an existing generated `.kicad_pro`; do not rewrite the whole file as text.
5. Create `rov.project.json` when absent. If it exists with the starter value `board-template`, update only `project_name`; if it already has a custom name, preserve it and add a message.
6. Inside `bootstrap_project()`, import `sync_project_libs` lazily and call `sync_project_libs.sync_project(project_dir)` to add missing standard library table entries while preserving custom entries; record changed table files in `BootstrapResult`. Do not import `sync_project_libs` at module import time because it consumes `STANDARD_LIBS` from this module.
7. If the library submodule directory is absent and the project is a Git worktree, run `git submodule update --init --recursive` once. If it exists and is a Git worktree, check `is_clean_worktree()` first, then fetch the configured library branch and fast-forward it with `git fetch origin <branch>` followed by `git merge --ff-only origin/<branch>`; never reset it. If it has local changes, do not touch it and add a `BLOCKED` message. If the directory is not a Git worktree, leave it untouched and add a warning. If the remote is unavailable, keep the cached revision and add a warning that the library may be stale.
8. Create `.rov-hooks/pre-commit` as an untracked local file and run `git config core.hooksPath .rov-hooks` when the project is a Git worktree. If the directory is not a Git worktree, still create the hook file but skip the Git config call.
9. Never call `git checkout`, `git reset`, `git stash`, or `git clean` from bootstrap.

The generated hook must be a small POSIX shell script:

```sh
#!/usr/bin/env sh
repo_root=$(git rev-parse --show-toplevel) || exit 0
cli="$repo_root/.pcb-devops-cache/scripts/rov.py"
[ -f "$cli" ] || exit 0
python "$cli" board validate --project-dir "$repo_root" --hook
```

The `--hook` behavior defined in Task 3 exits `1` only for `FAIL`; missing tools/offline state exits `0` with a warning so a club member is not locked out of Git.

- [x] **Step 4: Make the central launchers thin bootstrap wrappers**
In `KiCad/DevOps/scripts/LAUNCH_KICAD.bat`, keep target-directory detection and the final KiCad-open block, but remove the `git config core.hooksPath .githooks` line and replace the current board `git pull`, submodule `checkout -B`/`reset --hard`, and direct table-mutation block with:

```bat
set "ROV_CLI=%~dp0rov.py"
where python >nul 2>&1
if not errorlevel 1 (
    python "%ROV_CLI%" board bootstrap --project-dir "%TARGET_DIR%" --non-interactive
    exit /b %ERRORLEVEL%
)
where py >nul 2>&1
if not errorlevel 1 (
    py -3 "%ROV_CLI%" board bootstrap --project-dir "%TARGET_DIR%" --non-interactive
    exit /b %ERRORLEVEL%
)
echo Python is required to prepare this board. Open KiCad manually or install Python.
exit /b 2
```

The Python bootstrap command derives the project name from `Path(project_dir).name`; the launcher does not pass a shell-expanded directory substring.

In `KiCad/DevOps/scripts/LAUNCH_KICAD.sh`, remove the `git config core.hooksPath .githooks` line and replace the automatic board `git pull` and submodule `reset --hard` with:

```bash
ROV_CLI="$SCRIPT_DIR/rov.py"
if command -v python3 >/dev/null 2>&1; then
    python3 "$ROV_CLI" board bootstrap --project-dir "$TARGET_DIR" --non-interactive
elif command -v python >/dev/null 2>&1; then
    python "$ROV_CLI" board bootstrap --project-dir "$TARGET_DIR" --non-interactive
else
    echo "Python is required to prepare this board." >&2
    exit 2
fi
```

Retain the existing OS-specific KiCad location/open logic.

- [x] **Step 5: Stop forcing the tracked `.githooks` directory**
Modify both filter setup scripts to keep the KiCad clean/smudge filters and recursive submodule config, but delete the commands that set `core.hooksPath` to `.githooks`. Bootstrap now owns the untracked `.rov-hooks` path.

- [x] **Step 6: Add the template bootstrap wrapper and manifest**
Create `KiCad/Board_Template/bootstrap.py`:

```python
#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def find_rov_script() -> Path:
    candidates = [
        Path(os.environ["ROV_DEVOPS_DIR"]) / "scripts" / "rov.py" if os.environ.get("ROV_DEVOPS_DIR") else None,
        ROOT / ".pcb-devops-cache" / "scripts" / "rov.py",
        ROOT.parent / "DevOps" / "scripts" / "rov.py",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    raise FileNotFoundError("rov.py not found; run LAUNCH_KICAD once or set ROV_DEVOPS_DIR")


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap a Purdue ROV board from the board template")
    parser.add_argument("--project-dir", type=Path, default=ROOT)
    parser.add_argument("--project-name")
    parser.add_argument("--non-interactive", action="store_true")
    args = parser.parse_args()
    command = [sys.executable, str(find_rov_script()), "board", "bootstrap",
               "--project-dir", str(args.project_dir.resolve())]
    if args.project_name:
        command.extend(["--project-name", args.project_name])
    if args.non_interactive:
        command.append("--non-interactive")
    return subprocess.run(command, cwd=args.project_dir).returncode


if __name__ == "__main__":
    raise SystemExit(main())
```

Create `KiCad/Board_Template/rov.project.json`:

```json
{
  "schema": 1,
  "project_name": "board-template",
  "kicad_version": "10",
  "platform_ref": "master",
  "library": {
    "path": "libs/purdue-rov-kicad-lib",
    "branch": "master",
    "update_policy": "pull-request"
  },
  "ci_profile": "standard"
}
```

Append this exact entry to `KiCad/Board_Template/.gitignore`:

```gitignore
# Generated local ROV hooks
.rov-hooks/
```

- [x] **Step 7: Run tests and inspect protected worktrees**
```powershell
python -m unittest tests.test_bootstrap -v
python -m unittest discover -s tests -v
bash -n scripts/LAUNCH_KICAD.sh
bash -n scripts/setup_git_filters.sh
```

From `KiCad/Board_Template`, run `git status --short`. Confirm the pre-existing staged `.githooks/*` and `LAUNCH_KICAD.sh` entries are unchanged. Do not stage them.

- [x] **Step 8: Commit in each repository**
In `KiCad/DevOps`:

```powershell
git add -- scripts/rov_core.py scripts/LAUNCH_KICAD.bat scripts/LAUNCH_KICAD.sh scripts/setup_git_filters.ps1 scripts/setup_git_filters.sh tests/test_bootstrap.py
git commit -m "feat: add safe idempotent board bootstrap"
```

In `KiCad/Board_Template`, stage only the new/clean files:

```powershell
git add -- bootstrap.py rov.project.json .gitignore
git commit -m "feat: add ROV board bootstrap entry point"
```

Do not push either commit.

---

### Task 3: Cross-Platform `rov` CLI for Doctor, Validation, and Library Commands

**Files:**
- Create: `KiCad/DevOps/scripts/rov.py`
- Create: `KiCad/DevOps/tests/test_rov.py`
- Modify: `KiCad/DevOps/scripts/rov_core.py`

**Interfaces:**
- Consumes: all Task 1 core validators.
- Produces:
  - `run_doctor(project_dir: Path) -> list[CheckResult]`
  - `run_board_validate(project_dir: Path, full: bool = False, hook: bool = False) -> list[CheckResult]`
  - `run_library_validate(library_dir: Path) -> list[CheckResult]`
  - `run_library_build(library_dir: Path) -> CheckResult`
  - `run_library_sync(library_dir: Path, branch: str = "master") -> CheckResult`
  - `main(argv: list[str] | None = None) -> int`
  - Exit codes: `0` for no `FAIL`/`BLOCKED`, `1` for `FAIL`, `2` for `BLOCKED`.

- [x] **Step 1: Write failing CLI tests**
Create `KiCad/DevOps/tests/test_rov.py` with direct `main()` calls and mocks; do not launch KiCad or access the network:

```python
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

DEVOPS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DEVOPS_DIR / "scripts"))

import rov
import rov_core


class TestRovCli(unittest.TestCase):
    def test_doctor_missing_kicad_is_warning(self):
        with patch.object(rov.shutil, "which", side_effect=lambda name: "/usr/bin/git" if name == "git" else None):
            results = rov.run_doctor(Path.cwd())
        kicad = next(r for r in results if r.name == "kicad-cli")
        self.assertEqual(kicad.status, "WARN")

    def test_full_validation_without_kicad_is_blocked(self):
        with patch.object(rov.shutil, "which", return_value=None):
            results = rov.run_board_validate(Path.cwd(), full=True)
        self.assertTrue(any(r.status == "BLOCKED" for r in results))

    @patch.object(rov, "run_board_validate")
    def test_validate_returns_one_for_fail(self, mock_validate):
        mock_validate.return_value = [rov_core.CheckResult("manifest", "FAIL", "bad manifest")]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(rov.main(["board", "validate"]), 1)

    @patch.object(rov, "run_doctor")
    def test_doctor_missing_tool_returns_zero(self, mock_doctor):
        mock_doctor.return_value = [rov_core.CheckResult("docker", "WARN", "not installed")]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(rov.main(["doctor"]), 0)


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run the tests and confirm they fail**
```powershell
python -m unittest tests.test_rov -v
```

Expected: FAIL because `rov.py` does not exist.

- [x] **Step 3: Implement status rendering and exit-code rules**
Add a single renderer in `rov.py`:

```python
def print_results(results: list[rov_core.CheckResult]) -> None:
    for result in results:
        print(f"[{result.status}] {result.name}: {result.message}")


def exit_code_for(results: list[rov_core.CheckResult]) -> int:
    statuses = {result.status for result in results}
    if "FAIL" in statuses:
        return 1
    if "BLOCKED" in statuses:
        return 2
    return 0
```

Never use color-only output or emoji status markers.

- [x] **Step 4: Implement `doctor`**
`run_doctor()` must check and report:

- `git`: `FAIL` if missing; include `git --version` when present.
- `python`: `PASS` using `sys.version`.
- `github-cli`: `WARN` if missing, `PASS` if `gh --version` succeeds.
- `kicad-cli`: `WARN` if missing, otherwise `PASS` with the first output line.
- `docker`: `WARN` if missing or `docker info` fails.
- `project-config`: `WARN` outside a board, otherwise result from Task 1.
- `submodule`: result from Task 1.

Missing optional tools are warnings. Git absence is the only doctor failure.

- [x] **Step 5: Implement fast and full board validation**
Fast validation must include:

1. Manifest load/validation.
2. Exactly one preferred `.kicad_pro` selected by `find_project_file()`; multiple project files are `WARN`, not automatic selection failure.
3. `sym-lib-table` and `fp-lib-table` standard entries.
4. Submodule initialization.
5. Merge-conflict markers in `*.kicad_*` and `*-lib-table`, matching the existing CI rule.
6. Central linter against `libs/purdue-rov-kicad-lib/Symbols` when the directory exists.

Full validation additionally requires `kicad-cli`. The severity flag is
`--severity-error`, so `--full` fails a board only where `kibot_master.yaml`
(`severity: error`) would. Run:

```text
kicad-cli sch erc --severity-error --exit-code-violations -o <temp>/erc.rpt <schematic>
kicad-cli pcb drc --severity-error --exit-code-violations -o <temp>/drc.rpt <board>
```

Return `BLOCKED` if `kicad-cli` or the project files are missing. Do not silently skip full validation. Manufacturing export remains the responsibility of the existing KiBot workflow; `doctor` reports whether Docker is available for it.

For `--hook`, run fast validation only, print warnings, and return `1` only when a result has status `FAIL`.

- [x] **Step 6: Implement library subcommands**
- `library validate`: execute `<library>/scripts/linter_validator.py <library>/Symbols` with `sys.executable`; return `FAIL` on a non-zero exit.
- `library build`: execute `<library>/scripts/build_symbol_libs.py`; return `PASS`/`FAIL` from its exit code.
- `library sync`: require a clean library worktree, run `git fetch origin <branch>` and `git merge --ff-only origin/<branch>`; return `BLOCKED` for a dirty worktree or unreachable/failed remote, and `PASS` for a successful fast-forward or already-current checkout. It must never push.
- `library list` and `library search`: import the library's `kicad_sym_utils.extract_top_symbols()` and `parse_symbol_properties()` by file path; print `name<TAB>category<TAB>MPN<TAB>manufacturer`.
- `library gui`: launch `<library>/scripts/library_manager_gui.py` with `sys.executable` and return its exit code.
- `library import`: execute `<library>/scripts/import_part.py` with the remaining arguments and return its exit code. It must not add a separate import implementation.
- `library contribute` is added in Task 5; until then, argparse must return a clear `BLOCKED` message rather than `AttributeError`.

- [x] **Step 7: Implement the `argparse` command tree**
Use subparsers with these exact commands:

```text
rov doctor
rov board bootstrap [--project-name NAME] [--non-interactive]
rov board validate [--full] [--hook]
rov board sync-library [--library-path PATH] [--library-branch BRANCH] [--apply] [--branch NAME] [--push] [--pr]
rov library list
rov library search QUERY
rov library sync
rov library validate
rov library build
rov library import
rov library contribute [--name NAME] [--category CATEGORY] [--push] [--pr]
rov library gui
```

Default `--project-dir` is `Path.cwd()`. Default `--library-dir` is `<project-dir>/libs/purdue-rov-kicad-lib` for board commands and the current directory for library commands.

- [x] **Step 8: Run focused and full tests**
```powershell
python -m unittest tests.test_rov -v
python -m unittest discover -s tests -v
python -m py_compile scripts/rov.py scripts/rov_core.py
```

Expected: all tests PASS and compilation succeeds.

- [x] **Step 9: Commit**
```powershell
git add -- scripts/rov.py scripts/rov_core.py tests/test_rov.py
git commit -m "feat: add cross-platform ROV PCB CLI"
```

---

### Task 4: Safe Library Update Planning and Update-Branch Preparation

**Files:**
- Modify: `KiCad/DevOps/scripts/rov_core.py`
- Modify: `KiCad/DevOps/scripts/rov.py`
- Create: `KiCad/DevOps/tests/test_library_update.py`

**Interfaces:**
- Consumes: `LibraryUpdatePlan`, `run_git()`, `is_clean_worktree()` from Task 1.
- Produces:
  - `plan_library_update(project_dir: Path, remote: str = "origin", branch: str = "master") -> LibraryUpdatePlan`
  - `apply_library_update(project_dir: Path, plan: LibraryUpdatePlan, branch_name: str, commit_message: str, push: bool = False, create_pr: bool = False) -> CheckResult`
  - `rov board sync-library --library-path PATH --library-branch BRANCH --apply --branch NAME --commit-message TEXT --push --pr`

- [x] **Step 1: Write failing tests using only local Git repositories**
Create `KiCad/DevOps/tests/test_library_update.py`. The test helper must create a bare remote, a source repository, a consumer repository, and a real submodule using local filesystem paths:

```python
def run_git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def make_repo(path: Path) -> None:
    path.mkdir(parents=True)
    run_git(path, "init", "-b", "master")
    run_git(path, "config", "user.name", "PCB Test")
    run_git(path, "config", "user.email", "pcb-test@example.invalid")
```

Build the fixture with these operations:

```python
run_git(remote, "init", "--bare", "-b", "master")
run_git(library, "init", "-b", "master")
(library / "Symbols").mkdir(parents=True)
(library / "Symbols" / "part.txt").write_text("initial", encoding="utf-8")
run_git(library, "add", "Symbols/part.txt")
run_git(library, "commit", "-m", "initial library")
run_git(library, "remote", "add", "origin", str(remote))
run_git(library, "push", "-u", "origin", "master")
run_git(board, "init", "-b", "master")
run_git(board, "config", "user.name", "PCB Test")
run_git(board, "config", "user.email", "pcb-test@example.invalid")
run_git(board, "-c", "protocol.file.allow=always", "submodule", "add", str(library), "libs/purdue-rov-kicad-lib")
run_git(board, "commit", "-am", "add library")
```

Add these tests:

- `test_dirty_board_blocks_update`
- `test_dirty_submodule_blocks_update`
- `test_plan_reports_no_change_when_remote_is_current`
- `test_apply_updates_submodule_and_creates_branch_commit`
- `test_apply_never_pushes_base_branch`
- `test_existing_update_pr_is_reused_without_duplicate_push`
- `test_conflicting_remote_commit_is_blocked`

For the push assertion, add a second commit to the consumer's local `master` after the plan is created. Assert `apply_library_update()` returns `BLOCKED` and leaves the local `master` commit unchanged.

- [x] **Step 2: Run the tests and confirm they fail**
```powershell
python -m unittest tests.test_library_update -v
```

Expected: FAIL because the update functions are undefined.

- [x] **Step 3: Implement update planning without changing files**
`plan_library_update()` must:

1. Load and validate `rov.project.json`; a missing/invalid manifest returns a blocked plan.
2. Verify the board and submodule worktrees are clean.
3. Run `git submodule sync -- <path>`.
4. Run `git fetch <remote> <branch>` with `repo_dir` set to the submodule path and a 60-second subprocess timeout.
5. Resolve `current_commit` with `git rev-parse HEAD` and `target_commit` with `git rev-parse FETCH_HEAD`.
6. If commits match, return a plan with empty `changed_files` and no blocked reason.
7. If the target is not a descendant of the current commit, return `blocked_reason="remote library history diverged"`.
8. Otherwise report `git diff --name-only <current>..<target>` as `changed_files`.

A network failure returns a blocked plan containing the Git error, not an empty successful plan.

- [x] **Step 4: Implement safe apply and branch preparation**
`apply_library_update()` must:

1. Return `BLOCKED` if `plan.blocked_reason` is set or the board is dirty.
2. Return `PASS` with no changes if `current_commit == target_commit`.
3. Verify the submodule is clean.
4. Create or switch to `branch_name` only when it does not exist; if it exists and differs from the requested base, return `BLOCKED`.
5. In the submodule, run `git checkout --detach <target_commit>`; never use `reset --hard`.
6. Stage only the configured submodule path in the board repository.
7. Commit with `commit_message` when a diff exists.
8. Push only `branch_name` and only when the caller explicitly requests `push=True`; never push `master`/`main`. Before pushing, query `gh pr view <branch> --json url`; if a PR already exists for the branch, do not create a duplicate and return `PASS` with the existing URL. If the existing branch has a different commit than the planned target, return `BLOCKED` without pushing.
9. When `create_pr=True`, require `gh` in `PATH`, run `gh pr create --base <base> --head <branch> --title <title> --body <body>`, and return the PR URL.
10. Return a `PASS` result containing the branch, commit, changed file count, and optional PR URL.

- [x] **Step 5: Wire the CLI**
`rov board sync-library` is dry-run by default. It prints the current commit, target commit, changed files, and blocked reason. It changes files only with `--apply`. It creates a branch only when `--branch` is supplied. It pushes only with `--push`; it opens a PR only when both `--push` and `--pr` are supplied.

Default branch name: `chore/library-update`. Default commit message:

```text
chore(library): update Purdue ROV component library
```

Default PR title:

```text
chore: update Purdue ROV component library
```

- [x] **Step 6: Run tests**
```powershell
python -m unittest tests.test_library_update -v
python -m unittest discover -s tests -v
```

Expected: all tests PASS. Confirm no test contacts the network.

- [x] **Step 7: Commit**
```powershell
git add -- scripts/rov_core.py scripts/rov.py tests/test_library_update.py
git commit -m "feat: add safe library update planning and pr preparation"
```

---

### Task 5: Library Manager Bridge, CLI Parity, and Safe Contribution Flow

**Files:**
- Create: `KiCad/DevOps/tests/test_library_contribution.py`
- Modify: `KiCad/DevOps/scripts/rov_core.py`
- Modify: `KiCad/DevOps/scripts/rov.py`
- Create: `KiCad/Libraries/scripts/rov_bridge.py`
- Create: `KiCad/Libraries/tests/test_rov_bridge.py`
- Modify: `KiCad/Libraries/scripts/library_manager_gui.py`
- Modify: `KiCad/Libraries/scripts/import_part.py`
- Modify: `KiCad/Libraries/tests/test_library_manager_gui.py`
- Modify: `KiCad/Libraries/tests/test_library_manager.py`

**Interfaces:**
- Consumes: `rov` commands from Tasks 3 and 4.
- Produces:
  - `resolve_devops_dir(library_dir: Path) -> Path`
  - `run_rov(library_dir: Path, args: list[str], check: bool = False, devops_dir: Path | None = None) -> subprocess.CompletedProcess[str]`
  - Library Manager Git Sync calls `rov library sync`.
  - Library Manager PR preparation calls `rov library contribute --name NAME --category CATEGORY --push --pr`.

- [x] **Step 1: Write failing bridge tests**
Create `KiCad/Libraries/tests/test_rov_bridge.py`:

```python
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))

import rov_bridge


class TestRovBridge(unittest.TestCase):
    def test_environment_path_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = root / "env"
            sibling = root / "sibling"
            (env / "scripts").mkdir(parents=True)
            (env / "scripts" / "rov.py").write_text("", encoding="utf-8")
            (sibling / "scripts").mkdir(parents=True)
            (sibling / "scripts" / "rov.py").write_text("", encoding="utf-8")
            with patch.dict(os.environ, {"ROV_DEVOPS_DIR": str(env)}):
                self.assertEqual(rov_bridge.resolve_devops_dir(sibling), env)

    def test_sibling_path_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "Libraries"
            devops = root / "DevOps"
            library.mkdir()
            (devops / "scripts").mkdir(parents=True)
            (devops / "scripts" / "rov.py").write_text("", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(rov_bridge.resolve_devops_dir(library), devops)

    def test_run_rov_uses_explicit_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            devops = root / "DevOps"
            (devops / "scripts").mkdir(parents=True)
            script = devops / "scripts" / "rov.py"
            script.write_text("import sys\nprint('|'.join(sys.argv[1:]))\n", encoding="utf-8")
            result = rov_bridge.run_rov(root, ["library", "validate"], devops_dir=devops)
            self.assertEqual(result.stdout.strip(), "library|validate")


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Implement `rov_bridge.py`**
Resolution order must be:

1. `ROV_DEVOPS_DIR` environment variable.
2. `<library_dir>/.pcb-devops-cache`.
3. `<library_dir>.parent/DevOps` for the current multi-repository workspace.
4. `<library_dir>/../pcb-devops`.

A candidate is valid only when `<candidate>/scripts/rov.py` exists. Raise `FileNotFoundError` with all checked paths when none is valid.

`run_rov()` must call `[sys.executable, str(devops_dir / "scripts" / "rov.py"), *args]` with `cwd=library_dir`, captured UTF-8 output, and no `shell=True`.

- [x] **Step 3: Run bridge tests**
```powershell
python -m unittest tests.test_rov_bridge -v
```

Expected: PASS.

- [x] **Step 4: Add failing GUI delegation tests**
In `KiCad/Libraries/tests/test_library_manager_gui.py`, replace the existing mocks for `git_sync` and PR creation with:

```python
@patch("library_manager_gui.rov_bridge.run_rov")
def test_git_sync_delegates_to_rov(self, mock_run):
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = "library is current"
    mock_run.return_value.stderr = ""
    self.app.git_sync()
    self.assertIn("library", mock_run.call_args.args[1])
    self.assertIn("sync", mock_run.call_args.args[1])

@patch("library_manager_gui.rov_bridge.run_rov")
def test_create_pr_delegates_to_rov(self, mock_run):
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = "https://github.com/purduerov/purdue-rov-kicad-lib/pull/1"
    mock_run.return_value.stderr = ""
    library_manager_gui.create_pull_request_flow("TPS54302", "Power")
    args = mock_run.call_args.args[1]
    self.assertIn("contribute", args)
    self.assertIn("--name", args)
    self.assertIn("--push", args)
    self.assertIn("--pr", args)
```

- [x] **Step 5: Replace direct GUI Git logic with bridge calls**
In `library_manager_gui.py`:

1. Import `rov_bridge`.
2. Replace the body of `create_pull_request_flow()` with a bridge call to:

```python
["library", "contribute", "--name", component_name, "--category", category, "--push", "--pr"]
```

3. Replace `git_sync()` with:

```python
result = rov_bridge.run_rov(BASE_DIR, ["library", "sync"])
```

4. Remove the second direct `git add -A`, `git commit`, and `git push origin master` block in the Git Sync handler.
5. Keep all browse, filter, metadata edit, import, and E2E dialog behavior unchanged.
6. If `rov_bridge` cannot find DevOps, show an actionable error telling the user to run `LAUNCH_KICAD` once or set `ROV_DEVOPS_DIR`; do not fall back to direct protected-branch pushes.

- [x] **Step 6: Implement `rov library contribute` in the central CLI**
Add the constant and function signature to `rov_core.py`:

```python
LIBRARY_CONTRIBUTION_PATHS = ("Symbols", "Footprints", "3D_Models", "Design_Blocks")


def prepare_library_contribution(
    library_dir: Path,
    component_name: str,
    category: str,
    push: bool = False,
    create_pr: bool = False,
) -> CheckResult:
```

Implement the function body with these exact operations:

1. Run `library_dir/scripts/linter_validator.py library_dir/Symbols` with `sys.executable`; return `FAIL` when it exits non-zero.
2. Read `git status --porcelain`; allow only paths under `LIBRARY_CONTRIBUTION_PATHS`; return `BLOCKED` for unrelated changes.
3. Create branch `add-part-<slug>-<timestamp>` from the current branch; do not check out or modify `master` after creating it.
4. Stage only the four allowed directories.
5. Commit with `f"feat(parts): add {component_name} to {category}"`.
6. Push only the new branch when `push=True`.
7. Use `gh pr create --base master --head <branch>` when `create_pr=True`.
8. Return `PASS` with the branch and optional PR URL.

Add `--name`, `--category`, `--push`, and `--pr` to the CLI subcommand and delegate to this function.

- [x] **Step 7: Remove direct push behavior from `import_part.py`**
Delete the interactive prompt that runs `git push origin master`. After successful linter validation, resolve the DevOps script through `rov_bridge.resolve_devops_dir(BASE_DIR)` and print the concrete command:

```python
devops_script = rov_bridge.resolve_devops_dir(BASE_DIR) / "scripts" / "rov.py"
print(
    f"Library changes validated. Run: {sys.executable} {devops_script} "
    f"library contribute --name {mpn} --category {category} --push --pr"
)
```

If DevOps cannot be resolved, print `Set ROV_DEVOPS_DIR or run LAUNCH_KICAD once, then run rov library contribute --push --pr` instead. Do not call `git commit` or `git push` from `import_part.py`.

- [x] **Step 8: Add contribution tests in the central repository**
Create `KiCad/DevOps/tests/test_library_contribution.py` with a temporary Git library containing one compliant symbol file and a committed baseline. Mock the linter subprocess and the Git push subprocess. Assert:

- `prepare_library_contribution()` returns `PASS` for an allowed `Symbols/parts/power/new-part.kicad_sym` change.
- It returns `BLOCKED` when an unrelated `notes.txt` change is present.
- The recorded push command starts with `git push -u origin add-part-` and is never `git push origin master`.
- `create_pr=True` calls `gh pr create --base master --head` with the same generated branch name.

Run:

```powershell
python -m unittest tests.test_library_contribution -v
```

- [x] **Step 9: Run library and DevOps tests**
From `KiCad/Libraries`:

```powershell
python -m unittest tests.test_rov_bridge -v
python -m unittest tests.test_library_manager -v
python -m unittest tests.test_library_manager_gui -v
python -m unittest discover -s tests -v
git diff --check
```

On headless Linux CI, run the full suite with `xvfb-run -a python -m unittest discover -s tests -v`.

- [x] **Step 10: Commit DevOps and Libraries separately**
In `KiCad/DevOps`:

```powershell
git add -- scripts/rov.py scripts/rov_core.py tests/test_library_contribution.py
git commit -m "feat: add safe library contribution command"
```

In `KiCad/Libraries`:

```powershell
git add -- scripts/rov_bridge.py scripts/library_manager_gui.py scripts/import_part.py tests/test_rov_bridge.py tests/test_library_manager_gui.py tests/test_library_manager.py
git commit -m "feat: delegate library manager git actions to rov cli"
```

Do not push either commit.

---

### Task 6: Reusable CI Workflows and Post-Validation Library Notification

**Files:**
- Create: `KiCad/DevOps/.github/workflows/update-library.yml`
- Modify: `KiCad/DevOps/.github/workflows/devops-ci.yml`
- Create: `KiCad/DevOps/tests/test_workflow_contracts.py`
- Modify: `KiCad/Libraries/.github/workflows/notify-boards.yml`
- Create: `KiCad/Libraries/tests/test_workflow_contracts.py`

**Interfaces:**
- Consumes: `rov board validate` and `rov board sync-library --apply --push --pr` from Tasks 3-4.
- Produces: reusable update workflow inputs `library-path`, `library-branch`, `update-branch`, `auto-merge`, and `platform-ref`.

- [x] **Step 1: Write failing workflow contract tests**
Create `KiCad/DevOps/tests/test_workflow_contracts.py`:

```python
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestWorkflowContracts(unittest.TestCase):
    def test_update_workflow_is_reusable_and_creates_pr(self):
        text = (ROOT / ".github/workflows/update-library.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_call:", text)
        self.assertIn("gh pr create", text)
        self.assertNotIn("git push origin master", text)
        self.assertNotIn("git push origin main", text)

    def test_update_workflow_uses_concurrency(self):
        text = (ROOT / ".github/workflows/update-library.yml").read_text(encoding="utf-8")
        self.assertIn("concurrency:", text)
        self.assertIn("cancel-in-progress: false", text)


if __name__ == "__main__":
    unittest.main()
```

Create `KiCad/Libraries/tests/test_workflow_contracts.py`:

```python
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestNotifyWorkflowContract(unittest.TestCase):
    def test_notification_waits_for_library_ci(self):
        text = (ROOT / ".github/workflows/notify-boards.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_run:", text)
        self.assertIn("Cross-Platform Library CI Matrix", text)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", text)


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run the tests and confirm they fail**
```powershell
python -m unittest tests.test_workflow_contracts -v
```

Expected: FAIL because `update-library.yml` does not exist and current workflows do not contain the required calls.

- [x] **Step 3: Create `update-library.yml`**
Use `on: workflow_call` with these inputs and defaults:

```yaml
inputs:
  library-path:
    required: false
    type: string
    default: libs/purdue-rov-kicad-lib
  library-branch:
    required: false
    type: string
    default: master
  update-branch:
    required: false
    type: string
    default: chore/library-update
  auto-merge:
    required: false
    type: boolean
    default: false
  platform-ref:
    required: false
    type: string
    default: master
```

The job must:

1. Use `concurrency` keyed on repository/ref with `cancel-in-progress: false`.
2. Request only `contents: write` and `pull-requests: write`.
3. Check out the board with `submodules: recursive` and `fetch-depth: 0`.
4. Check out `purduerov/pcb-devops` into `pcb-devops-tools` with `ref: ${{ inputs.platform-ref }}`.
5. Configure the bot identity.
6. Set `GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}` for the PR steps.
7. Run one update step and capture its machine-readable final line:

```yaml
- name: Prepare library update PR
  id: update
  env:
    GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
  run: |
    PR_OUTPUT=$(python3 pcb-devops-tools/scripts/rov.py board sync-library \
      --project-dir . \
      --library-path "${{ inputs.library-path }}" \
      --library-branch "${{ inputs.library-branch }}" \
      --branch "${{ inputs.update-branch }}" \
      --commit-message "chore(library): update Purdue ROV component library" \
      --apply --push --pr 2>&1)
    printf '%s\n' "$PR_OUTPUT"
    PR_URL=$(printf '%s\n' "$PR_OUTPUT" | sed -n 's/^PR_URL=//p')
    if [ -n "$PR_URL" ]; then
      echo "url=$PR_URL" >> "$GITHUB_OUTPUT"
    fi
```

The CLI must print a final line beginning with `PR_URL=` when a PR was created, and `NO_CHANGE` when the library was already current.

8. Run auto-merge only when a PR was created and the input is true:

```yaml
- name: Enable auto-merge
  if: ${{ inputs.auto-merge && steps.update.outputs.url != '' }}
  env:
    GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
  run: gh pr merge --auto --merge "${{ steps.update.outputs.url }}"
```

9. Never push the base branch and never use force flags.

- [x] **Step 4: Defer `run-kicad-ci.yml` integration until board manifests exist**
Do not modify `KiCad/DevOps/.github/workflows/run-kicad-ci.yml` in this task. Adding required-manifest validation before the seven boards receive `rov.project.json` would make existing board CI fail during rollout. Task 8 adds the shared validation call after Task 7 has migrated every board.

The existing sourcing-secret behavior and artifact upload steps remain unchanged in this task.

- [x] **Step 5: Align DevOps CI across operating systems**
Modify `devops-ci.yml` so the validation job uses this exact matrix:

```yaml
jobs:
  validate-tools:
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]
```

Run Python 3.10, install only the existing YAML parser needed to validate workflow syntax, compile `scripts/rov.py`, `scripts/rov_core.py`, and existing scripts, then run:

```text
python -m unittest discover -s tests -v
```

Do not add a new Python package requirement to the CLI itself.

- [x] **Step 6: Gate library notifications on successful library CI**
Replace the `push` trigger in `KiCad/Libraries/.github/workflows/notify-boards.yml` with:

```yaml
on:
  workflow_run:
    workflows: ["Cross-Platform Library CI Matrix"]
    types: [completed]
    branches: [master]
  workflow_dispatch:
```

Add a job-level condition:

```yaml
if: github.event_name == 'workflow_dispatch' || github.event.workflow_run.conclusion == 'success'
```

Keep the eight-repository dispatch matrix and `continue-on-error: true`.

- [x] **Step 7: Run tests and workflow syntax checks**
From `KiCad/DevOps`:

```powershell
python -m unittest tests.test_workflow_contracts -v
python -m unittest discover -s tests -v
python -c "import yaml, pathlib; [yaml.safe_load(p.read_text(encoding='utf-8')) for p in pathlib.Path('.github/workflows').glob('*.yml')]"
```

From `KiCad/Libraries`:

```powershell
python -m unittest tests.test_workflow_contracts -v
python -c "import yaml, pathlib; yaml.safe_load(pathlib.Path('.github/workflows/notify-boards.yml').read_text(encoding='utf-8'))"
```

- [x] **Step 8: Commit in each repository**
In `KiCad/DevOps`:

```powershell
git add -- .github/workflows/update-library.yml .github/workflows/devops-ci.yml tests/test_workflow_contracts.py
git commit -m "ci: add safe reusable library update workflow"
```

In `KiCad/Libraries`:

```powershell
git add -- .github/workflows/notify-boards.yml tests/test_workflow_contracts.py
git commit -m "ci: notify boards after library validation passes"
```

Do not push either commit.

---

### Task 7: Apply the Manifest and Update Workflow to the Template and Seven Boards

**Files:**
- Modify: `KiCad/Board_Template/rov.project.json`
- Delete: `KiCad/Board_Template/.github/workflows/initialize-template.yml`
- Modify: `KiCad/Board_Template/.github/workflows/auto-update-submodule.yml`
- For each of the seven board repositories:
  - Create `rov.project.json`
  - Modify `.github/workflows/auto-update-submodule.yml`
  - Modify `.gitignore`

**Interfaces:**
- Consumes: `purduerov/pcb-devops/.github/workflows/update-library.yml@master` from Task 6.
- Produces: identical update workflow wrappers and board-specific manifests.

- [x] **Step 1: Run preflight repository checks**
For every repository, run:

```text
git status --short --branch
git submodule status
```

Record existing changes. Do not edit a repository that has unrelated staged or unstaged changes; report it and continue with clean repositories. In `KiCad/Board_Template`, do not stage the pre-existing `.githooks/*` or `LAUNCH_KICAD.sh` changes.

- [x] **Step 2: Finalize the template manifest**
Change only `project_name` in `KiCad/Board_Template/rov.project.json`:

```json
"project_name": "board-template"
```

Keep every other field unchanged.

- [x] **Step 3: Remove the competing automatic template initializer**
Delete `KiCad/Board_Template/.github/workflows/initialize-template.yml`. `bootstrap.py` becomes the only first-run initialization path, preventing a direct bot push that races with a member's local bootstrap.

- [x] **Step 4: Replace the template library update workflow**
Replace `KiCad/Board_Template/.github/workflows/auto-update-submodule.yml` with:

```yaml
name: Library Update

on:
  workflow_dispatch:
  repository_dispatch:
    types: [update-library]
  schedule:
    - cron: "17 3 * * 1"

jobs:
  update-library:
    uses: purduerov/pcb-devops/.github/workflows/update-library.yml@master
    with:
      library-path: libs/purdue-rov-kicad-lib
      library-branch: master
      update-branch: chore/library-update
      auto-merge: false
      platform-ref: master
    secrets: inherit
```

- [x] **Step 5: Create the seven board manifests**
Write the following JSON shape in each repository, using the exact `project_name` value from the table:

```json
{
  "schema": 1,
  "project_name": "X19-Control-Board",
  "kicad_version": "10",
  "platform_ref": "master",
  "library": {
    "path": "libs/purdue-rov-kicad-lib",
    "branch": "master",
    "update_policy": "pull-request"
  },
  "ci_profile": "standard"
}
```

| Repository path | `project_name` |
| :--- | :--- |
| `KiCad/Boards/X19-Control-Board` | `X19-Control-Board` |
| `KiCad/Boards/X19-Electrical-New-Member-Board` | `X19-Electrical-New-Member-Board` |
| `KiCad/Boards/X19-Float-Board` | `X19-Float-Board` |
| `KiCad/Boards/X19-Pi-Shield-Board` | `X19-Pi-Shield-Board` |
| `KiCad/Boards/X19-Power-Slab-Board` | `X19-Power-Slab-Board` |
| `KiCad/Boards/X19-Pressure-Chamber-Board` | `X19-Pressure-Chamber-Board` |
| `KiCad/Boards/X19-USB-Hub-Board` | `X19-USB-Hub-Board` |

- [x] **Step 6: Replace each board library update workflow**
Use the exact YAML from Step 4 in each of the seven board repositories. Do not change `ci.yml` or `sync-template.yml` in this task.

- [x] **Step 7: Ignore generated hooks in every board and the template**
Append to each `.gitignore`:

```gitignore
# Generated local ROV hooks
.rov-hooks/
```

- [x] **Step 8: Validate manifests and workflows locally**
For each of the eight repositories, run from its root:

```text
python -c "import json, pathlib, yaml; c=json.loads(pathlib.Path('rov.project.json').read_text(encoding='utf-8')); assert c['schema']==1; assert c['kicad_version']=='10'; yaml.safe_load(pathlib.Path('.github/workflows/auto-update-submodule.yml').read_text(encoding='utf-8'))"
git diff --check
```

Expected: every command exits `0`.

- [x] **Step 9: Commit each repository separately**
Use one commit per repository. Stage only the three files named for that repository:

```text
feat: adopt ROV PCB platform contract
```

For `KiCad/Board_Template`, stage only `rov.project.json`, `.gitignore`, `.github/workflows/auto-update-submodule.yml`, and the deletion of `.github/workflows/initialize-template.yml`. Do not stage `.githooks/*` or `LAUNCH_KICAD.sh`.

Do not push any commit.

- [x] **Step 10: Record integration status**
Create a checklist in the final implementation report listing each repository, commit hash, manifest validation result, and whether any repository was skipped because of pre-existing changes. Do not claim cross-repository completion while a required wrapper is still uncommitted.

---

### Task 8: Documentation, Full Verification, and Handoff

**Files:**
- Modify: `KiCad/DevOps/.github/workflows/run-kicad-ci.yml`
- Modify: `KiCad/DevOps/README.md`
- Modify: `KiCad/Board_Template/README.md`
- Modify: `KiCad/Libraries/README.md`
- Modify: `KiCad/Libraries/CONTRIBUTING.md`
- Modify: `KiCad/DevOps/docs/superpowers/plans/2026-09-25-rov-pcb-platform.md` to mark completed checkboxes only after verification.

**Interfaces:**
- Consumes: the final verified command names and workflow behavior from Tasks 1-7.
- Produces: one short quickstart per repository and a verification record.

**Task 8 verification record (2026-09-25):**

Steps 1-6 and 8-10 were executed and are checked. Step 7 is the only step left
unchecked, because its command ran but did not reach the expected result. The
full evidence is in
`.superpowers/sdd/2026-09-25-rov-pcb-platform/task-8-report.md`.

- Step 6, executed with a sanctioned `BLOCKED`: `bootstrap.py` on a temporary
  template copy renamed and wrote the expected files and exited `0`. Fast
  validation on that plain-directory copy exited `2` with `BLOCKED`, because the
  copy carries no usable submodule Git metadata. That is the outcome this step
  explicitly allows to be recorded as `BLOCKED` in place of a pass, so the step
  counts as executed. The same fast-validation command on the real
  `Board_Template` worktree returned `0` with every check `PASS`, and the
  eight-repository manifest and workflow check returned `0` everywhere.
- Step 7, not reached: KiCad 10.0.1 was available, so full validation ran for
  real. ERC is `PASS` for `Board_Template` and `X19-Pressure-Chamber-Board`;
  every board reports a pre-existing `FAIL` in DRC, and six of eight also report
  a pre-existing ERC `FAIL`. No design file was changed to make this pass, so
  the step's expected `PASS` result was not reached and the box stays empty.
- Blocking follow-up raised by the verification and still open: `X19-Control-Board`
  fails fast validation because its `sym-lib-table` still points at the removed
  `rov_parts.kicad_sym` instead of the six standard category entries, so the
  shared validation step in `run-kicad-ci.yml` will fail that board's CI. The
  owner must rule on either the targeted one-file
  `python KiCad/DevOps/scripts/sync_project_libs.py KiCad/Boards/X19-Control-Board`
  migration or making the gate non-blocking. `rov board bootstrap` is not the
  remedy, because it performs the full board preparation rather than a one-file
  table fix. No board design file was changed by this plan.

- [x] **Step 1: Update DevOps documentation**
Replace the current repository overview with a short operator guide containing:

```text
python scripts/rov.py doctor
python scripts/rov.py board validate
python scripts/rov.py board validate --full
python scripts/rov.py board sync-library
python scripts/rov.py library validate
python scripts/rov.py library sync
python scripts/rov.py library gui
```

Document exit codes `0`, `1`, and `2`, the dry-run default for `sync-library`, and the rule that no command pushes a protected branch.

- [x] **Step 2: Update the board template quickstart**
Document this exact new-board flow:

```text
1. Create the repository with GitHub "Use this template".
2. Clone the repository.
3. Run LAUNCH_KICAD once, or run python bootstrap.py.
4. Open future sessions with LAUNCH_KICAD.
```

Remove the old manual instruction to rename `board-template.kicad_*` by hand. Keep the existing library categories, design-rule notes, and Library Manager launch instructions.

- [x] **Step 3: Update library contribution documentation**
In `KiCad/Libraries/README.md` and `CONTRIBUTING.md`:

- Keep the GUI import/search/edit workflow.
- Replace direct commit/push-to-master steps with branch/PR preparation.
- Document `rov library validate`, `rov library build`, and `rov library contribute --push --pr`.
- State that the GUI and CLI use the same validation and Git safety rules.
- Preserve the six mandatory symbol fields and the footprint/3D-model best practices.

- [x] **Step 4: Run complete DevOps verification**
Before running the commands, add the deferred shared validation step to `KiCad/DevOps/.github/workflows/run-kicad-ci.yml` after the DevOps checkout and before KiBot:

```yaml
- name: Run Shared ROV Board Validation
  run: python3 pcb-devops-tools/scripts/rov.py board validate --project-dir .
```

Do not change the existing sourcing-secret behavior or artifact upload steps. Add this test to `KiCad/DevOps/tests/test_workflow_contracts.py`:

```python
def test_run_kicad_ci_calls_shared_validation(self):
    text = (ROOT / ".github/workflows/run-kicad-ci.yml").read_text(encoding="utf-8")
    self.assertIn("pcb-devops-tools/scripts/rov.py board validate", text)
```

Run the focused workflow test after adding the method. From `KiCad/DevOps`:

```powershell
python -m py_compile scripts/rov.py scripts/rov_core.py scripts/linter_validator.py scripts/sync_project_libs.py scripts/generate_portal_page.py scripts/fetch_sourcing_bom.py
python -m unittest discover -s tests -v
python -c "import yaml, pathlib; [yaml.safe_load(p.read_text(encoding='utf-8')) for p in pathlib.Path('.github/workflows').glob('*.yml')]"
bash -n scripts/LAUNCH_KICAD.sh
bash -n scripts/setup_git_filters.sh
git diff --check
```

Expected: compilation, tests, YAML parsing, shell syntax, and whitespace checks all exit `0`.

- [x] **Step 5: Run complete library verification**
From `KiCad/Libraries`:

```powershell
python -m py_compile scripts/rov_bridge.py scripts/library_manager_gui.py scripts/import_part.py scripts/build_symbol_libs.py scripts/kicad_sym_utils.py
python -m unittest discover -s tests -v
python -c "import yaml, pathlib; yaml.safe_load(pathlib.Path('.github/workflows/notify-boards.yml').read_text(encoding='utf-8'))"
git diff --check
git status --short
```

Expected: all commands pass. Existing tests that create temporary library parts must leave no untracked or modified library files; if they do, clean only the test artifacts created by this run and report the test that leaked state.

- [x] **Step 6: Run board and template smoke checks**

From the workspace root, create a temporary copy and run the smoke test:

```powershell
$copy = Join-Path $env:TEMP "rov-template-smoke"
if (Test-Path $copy) { Remove-Item -Recurse -Force $copy }
Copy-Item -Recurse "KiCad\Board_Template" $copy
$devops = (Resolve-Path "KiCad\DevOps").Path
python (Join-Path $copy "bootstrap.py") --project-dir $copy --project-name Smoke-Test-Board --non-interactive
python (Join-Path $devops "scripts\rov.py") board validate --project-dir $copy
```

Expected: bootstrap reports the files it renamed/wrote, and fast validation passes if the copied submodule is initialized. If the submodule is unavailable offline, record `BLOCKED` with the missing path; do not claim a pass.

For each of the eight board/template repositories, parse `rov.project.json` and the update workflow using the Step 8 command from Task 7.

- [ ] **Step 7: Run full KiCad validation when the current KiCad toolchain exists**

Run:

```text
python KiCad/DevOps/scripts/rov.py board validate --full --project-dir KiCad/Board_Template
```

Expected on a machine with KiCad 10.x: ERC and DRC both return `PASS`. If `kicad-cli` or required project dependencies are unavailable, record the exact `BLOCKED` message. Do not substitute CI expectations for local evidence.

- [x] **Step 8: Review repository status and diffs**
For `KiCad/DevOps`, `KiCad/Board_Template`, `KiCad/Libraries`, and all seven boards, run:

```text
git status --short --branch
git log -1 --oneline
git diff --check
```

Confirm:

- No generated `Generated_Outputs/`, `.pcb-devops-cache/`, `.rov-hooks/`, or `__pycache__/` files are staged.
- The pre-existing staged Board_Template hook/launcher changes remain unstaged by this work.
- No commit was pushed.

- [x] **Step 9: Mark the plan checkboxes and commit documentation**
In each repository, stage only the documentation files changed in Steps 1-3 and commit:

```text
docs: document the ROV PCB platform workflow
```

In `KiCad/DevOps`, also stage `docs/superpowers/plans/2026-09-25-rov-pcb-platform.md` after its checkboxes accurately reflect the verification results:

```powershell
git add -- docs/superpowers/plans/2026-09-25-rov-pcb-platform.md
git commit -m "docs: record ROV PCB platform plan progress"
```

Update this plan's completed checkboxes only for tasks whose commands actually passed. Leave blocked or skipped steps unchecked and explain them in the final report.

- [x] **Step 10: Produce the final handoff**
Report:

- Commands implemented and their verified exit codes.
- Local commit hashes per repository, without pushing.
- Any repository skipped because of pre-existing changes.
- Any full KiCad validation that was unavailable and its exact `BLOCKED` reason.
- The manual owner-approved follow-up still needed for the staged `KiCad/Board_Template/.githooks/*` cleanup, if any.
