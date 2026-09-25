#!/usr/bin/env python3
"""Cross-platform ROV PCB command-line interface.

This module is the single entry point for environment diagnosis, board
bootstrap, board validation, and the library commands. Every shared operation
comes from ``rov_core``; this file only parses arguments, delegates to the
library scripts that already own that work, renders results, and maps statuses
to exit codes:

* ``0`` - no ``FAIL`` and no ``BLOCKED`` result
* ``1`` - at least one ``FAIL`` result
* ``2`` - no ``FAIL``, but at least one ``BLOCKED`` result

The CLI never installs software, never pushes to a protected branch, and never
discards local work.
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import rov_core
from rov_core import CheckResult

SCRIPTS_DIR = Path(__file__).resolve().parent

KICAD_CLI = "kicad-cli"
LIBRARY_IMPORT_COMMAND = ("library", "import")

# The library scripts are the implementations for build, import, GUI, and
# symbol parsing. The CLI only launches or loads them; it never reimplements
# their behavior.
SYMBOL_UTILS_FILE = "kicad_sym_utils.py"
LINTER_FILE = "linter_validator.py"
BUILD_FILE = "build_symbol_libs.py"
IMPORT_FILE = "import_part.py"
GUI_FILE = "library_manager_gui.py"

# Generated category libraries duplicate the per-part sources, so they are
# excluded from list and search output.
GENERATED_SYMBOL_FILES = frozenset(f"{lib['name']}.kicad_sym" for lib in rov_core.STANDARD_LIBS)
SYMBOL_ROW_COLUMNS = ("name", "category", "MPN", "manufacturer")

DEFAULT_SYNC_BRANCH = "chore/library-update"
DEFAULT_SYNC_COMMIT_MESSAGE = "chore(library): update Purdue ROV component library"

# Tool timeouts in seconds. Doctor probes stay short so a missing or hung
# service cannot stall a diagnosis; the heavy KiCad runs get a full allowance.
PROBE_TIMEOUT_SECONDS = 20
SCRIPT_TIMEOUT_SECONDS = 300
KICAD_TIMEOUT_SECONDS = 900

_EXIT_CODE_BY_STATUS = {
    rov_core.STATUS_PASS: 0,
    rov_core.STATUS_WARN: 0,
    rov_core.STATUS_FAIL: 1,
    rov_core.STATUS_BLOCKED: 2,
}


class LibraryLoadError(RuntimeError):
    """Raised when the library's own symbol utilities cannot be loaded."""


# ---------------------------------------------------------------------------
# Rendering and exit codes
# ---------------------------------------------------------------------------


def print_results(results: list[CheckResult]) -> None:
    """Render every result as plain text with a status tag."""
    for result in results:
        print(f"[{result.status}] {result.name}: {result.message}")


def exit_code_for(results: list[CheckResult]) -> int:
    """Return 0 for no FAIL/BLOCKED, 1 for FAIL, and 2 for BLOCKED."""
    return _EXIT_CODE_BY_STATUS[rov_core.worst_status(results)]


def hook_exit_code_for(results: list[CheckResult]) -> int:
    """Return 1 only for a FAIL result so a commit is never blocked by tools.

    The generated pre-commit hook runs the fast checks. Missing tools, an
    uninitialized submodule, or an unreachable remote must not lock a member
    out of Git, so only an actual failure fails the commit.
    """
    return 1 if any(result.status == rov_core.STATUS_FAIL for result in results) else 0


# ---------------------------------------------------------------------------
# Process helper
# ---------------------------------------------------------------------------


def _run_tool(
    command: list, cwd: Path | None = None, timeout: int = SCRIPT_TIMEOUT_SECONDS
) -> subprocess.CompletedProcess[str]:
    """Run one external command without a shell and capture its output."""
    return subprocess.run(
        [str(part) for part in command],
        cwd=None if cwd is None else str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
    )


def _launch_tool(command: list, cwd: Path) -> int:
    """Run a script in the foreground and return its exit code.

    Standard streams are inherited so an interactive prompt or a GUI window
    stays visible to the person who started it. Captured output would hide the
    questions ``import_part.py`` asks.
    """
    return subprocess.run(
        [str(part) for part in command], cwd=str(cwd), check=False
    ).returncode


def _first_line(text: str) -> str:
    """Return the first non-empty line of ``text``."""
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _last_line(text: str) -> str:
    """Return the last non-empty line of ``text``."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _script_result(
    name: str, script: Path, arguments: list[str], success: str, cwd: Path
) -> CheckResult:
    """Run one library or DevOps script and map its exit code to a status.

    These scripts own their behavior, so the CLI only reports the outcome: a
    zero exit is PASS, a non-zero exit is FAIL with the last output line, and an
    unrunnable script is BLOCKED rather than silently passing.
    """
    try:
        result = _run_tool([sys.executable, str(script), *arguments], cwd=cwd)
    except (OSError, subprocess.SubprocessError) as exc:
        return CheckResult(
            name, rov_core.STATUS_BLOCKED, f"{script.name} could not be run: {exc}"
        )
    if result.returncode == 0:
        return CheckResult(name, rov_core.STATUS_PASS, success)
    detail = _last_line(result.stderr) or _last_line(result.stdout) or "no output"
    return CheckResult(
        name,
        rov_core.STATUS_FAIL,
        f"{script.name} exited with status {result.returncode}: {detail}",
    )


def _tool_result(
    name: str,
    arguments: list[str],
    missing_hint: str,
    warn_only: bool = True,
    timeout: int = PROBE_TIMEOUT_SECONDS,
    executable_name: str | None = None,
) -> CheckResult:
    """Probe an optional tool and report its version line or why it is unusable.

    ``name`` is the reported check name and ``executable_name`` is the command
    on PATH; they differ only for GitHub CLI, which is reported as
    ``github-cli`` and invoked as ``gh``.
    """
    executable = shutil.which(executable_name or name)
    command = executable_name or name
    if executable is None:
        return CheckResult(
            name,
            rov_core.STATUS_WARN if warn_only else rov_core.STATUS_FAIL,
            f"{command} was not found on PATH. {missing_hint}",
        )
    try:
        result = _run_tool([executable, *arguments], timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return CheckResult(
            name,
            rov_core.STATUS_WARN if warn_only else rov_core.STATUS_FAIL,
            f"{command} could not be run: {exc}",
        )
    if result.returncode != 0:
        detail = (
            _last_line(result.stderr)
            or _last_line(result.stdout)
            or f"exit status {result.returncode}"
        )
        return CheckResult(
            name,
            rov_core.STATUS_WARN if warn_only else rov_core.STATUS_FAIL,
            f"{command} {' '.join(arguments)} failed with status "
            f"{result.returncode}: {detail}",
        )
    return CheckResult(
        name,
        rov_core.STATUS_PASS,
        _first_line(result.stdout) or _first_line(result.stderr) or "no output",
    )


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def run_doctor(project_dir: Path) -> list[CheckResult]:
    """Report the tools and board state every other command depends on.

    Missing optional tools are warnings, so a diagnosis is always runnable.
    Git is the only failure: without it no board or library command can work.
    Manufacturing export is not run here; the Docker result only says whether
    the existing KiBot workflow could run.
    """
    root = Path(project_dir)
    results: list[CheckResult] = [
        _tool_result(
            "git",
            ["--version"],
            "Install Git, then re-run 'rov doctor'.",
            warn_only=False,
        ),
        CheckResult("python", rov_core.STATUS_PASS, sys.version.splitlines()[0]),
        _tool_result(
            "github-cli",
            ["--version"],
            "Install GitHub CLI to create branches and pull requests.",
            executable_name="gh",
        ),
        _tool_result(
            KICAD_CLI,
            ["version"],
            "Install KiCad 10.x to run 'rov board validate --full'.",
        ),
        _tool_result(
            "docker",
            ["info"],
            "Install Docker to run the KiBot manufacturing export.",
        ),
    ]
    results.extend(_board_state_results(root))
    return results


def _board_state_results(project_dir: Path) -> list[CheckResult]:
    """Return the manifest and submodule results, or a warning outside a board."""
    try:
        config = rov_core.load_project_config(project_dir)
    except ValueError as exc:
        return [
            CheckResult(
                "project-config",
                rov_core.STATUS_WARN,
                f"{exc} The tool checks above do not need a board manifest.",
            ),
            CheckResult(
                "submodule",
                rov_core.STATUS_WARN,
                "Not checked because this directory has no board manifest.",
            ),
        ]
    return rov_core.validate_project_config(config) + [
        rov_core.check_submodule(project_dir, config)
    ]


# ---------------------------------------------------------------------------
# board validate
# ---------------------------------------------------------------------------


def run_board_validate(
    project_dir: Path, full: bool = False, hook: bool = False
) -> list[CheckResult]:
    """Validate a board, returning one result per checked fact.

    The fast checks are the manifest, the project file, the standard library
    tables, the submodule, merge conflict markers, and the central symbol
    linter. ``full=True`` additionally runs ``kicad-cli`` ERC and DRC. The hook
    mode always runs the fast checks only, so a commit never waits on KiCad.

    A missing ``kicad-cli`` or a missing design file is reported as BLOCKED
    instead of being skipped, because a silently skipped full validation is
    indistinguishable from a clean board.
    """
    root = Path(project_dir)
    if not root.is_dir():
        return [
            CheckResult(
                "project-dir",
                rov_core.STATUS_BLOCKED,
                f"{root} is not a directory. Pass --project-dir with the board repository root.",
            )
        ]

    results: list[CheckResult] = []
    config: dict | None = None
    try:
        config = rov_core.load_project_config(root)
    except ValueError as exc:
        results.append(CheckResult("manifest", rov_core.STATUS_FAIL, str(exc)))
    else:
        results.extend(rov_core.validate_project_config(config))

    results.append(_select_project_file(root))
    results.extend(rov_core.validate_library_tables(root))
    if config is not None:
        results.append(rov_core.check_submodule(root, config))
    results.extend(_check_conflict_markers(root, config))
    results.extend(_check_symbol_linter(root, config))
    if full and not hook:
        results.extend(_run_kicad_validation(root, rov_core.find_project_file(root)))
    return results


def _select_project_file(project_dir: Path) -> CheckResult:
    """Report the project file that validation and KiCad both use."""
    project_file = rov_core.find_project_file(project_dir)
    if project_file is None:
        return CheckResult(
            "project-file",
            rov_core.STATUS_FAIL,
            f"No *.kicad_pro file was found in {project_dir.resolve()}. Restore or "
            "create the board project file before validating.",
        )
    others = sorted(
        path.name
        for path in project_dir.glob("*.kicad_pro")
        if path.is_file() and path != project_file
    )
    if others:
        return CheckResult(
            "project-file",
            rov_core.STATUS_WARN,
            f"{project_file.name} is used; other project files were also found: "
            f"{', '.join(others)}. Keep exactly one *.kicad_pro per board.",
        )
    return CheckResult(
        "project-file",
        rov_core.STATUS_PASS,
        f"{project_file.name} is the board project file.",
    )


def _check_conflict_markers(project_dir: Path, config: dict | None) -> list[CheckResult]:
    """Scan the board's own design files, never its library submodule."""
    return rov_core.check_merge_conflict_markers(
        project_dir, skip_dirs=[rov_core.configured_library_path(config)]
    )


def _check_symbol_linter(root: Path, config: dict | None) -> list[CheckResult]:
    """Run the central linter against the library symbols the board consumes."""
    symbols = _library_symbols_dir(root, config)
    if not symbols.is_dir():
        return [
            CheckResult(
                "symbol-linter",
                rov_core.STATUS_WARN,
                f"No central symbol library was found at {symbols}. Initialize the "
                "library submodule to lint symbols.",
            )
        ]
    linter = _resolve_linter(root)
    if linter is None:
        return [
            CheckResult(
                "symbol-linter",
                rov_core.STATUS_BLOCKED,
                f"{LINTER_FILE} was not found in {root / 'scripts'} or in {SCRIPTS_DIR}.",
            )
        ]
    return [
        _script_result(
            "symbol-linter",
            linter,
            [str(symbols)],
            f"{linter.name} found no metadata problems in {symbols.name}.",
            root,
        )
    ]


def _library_symbols_dir(project_dir: Path, config: dict | None) -> Path:
    """Return the library Symbols directory a manifest selects, or the default."""
    return project_dir / Path(rov_core.configured_library_path(config)) / "Symbols"


def _resolve_linter(library_dir: Path) -> Path | None:
    """Return the library's linter when it has one, else the central linter."""
    for candidate in (Path(library_dir) / "scripts" / LINTER_FILE, SCRIPTS_DIR / LINTER_FILE):
        if candidate.is_file():
            return candidate
    return None


def _run_kicad_validation(project_dir: Path, project_file: Path | None) -> list[CheckResult]:
    """Run ``kicad-cli`` ERC and DRC, or report why neither could run."""
    executable = shutil.which(KICAD_CLI)
    if executable is None:
        return [
            CheckResult(
                KICAD_CLI,
                rov_core.STATUS_BLOCKED,
                f"{KICAD_CLI} was not found on PATH, so ERC and DRC were not run. "
                f"Install KiCad 10.x or use 'rov board validate' for the fast checks. "
                "Full validation is never reported as passing when it did not run.",
            )
        ]

    schematic = None if project_file is None else project_file.with_suffix(".kicad_sch")
    board = None if project_file is None else project_file.with_suffix(".kicad_pcb")
    checks = (
        ("erc", "sch", "erc", "erc.rpt", schematic),
        ("drc", "pcb", "drc", "drc.rpt", board),
    )
    results: list[CheckResult] = []
    with tempfile.TemporaryDirectory(prefix="rov-validate-") as report_dir:
        for name, subcommand, action, report_name, target in checks:
            if target is None or not target.is_file():
                expected = f"{project_file.stem}.kicad_{'sch' if name == 'erc' else 'pcb'}"
                results.append(
                    CheckResult(
                        name,
                        rov_core.STATUS_BLOCKED,
                        f"Full validation needs {expected} next to the project file.",
                    )
                )
                continue
            results.append(
                _kicad_check(executable, name, subcommand, action, Path(report_dir) / report_name, target)
            )
    return results


def _kicad_check(
    executable: str, name: str, subcommand: str, action: str, report: Path, target: Path
) -> CheckResult:
    """Run one kicad-cli check and map its exit code to PASS, FAIL, or BLOCKED."""
    command = [
        executable,
        subcommand,
        action,
        "--severity-all",
        "--exit-code-violations",
        "-o",
        str(report),
        str(target),
    ]
    try:
        result = _run_tool(command, cwd=target.parent, timeout=KICAD_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        return CheckResult(name, rov_core.STATUS_BLOCKED, f"{KICAD_CLI} could not be run: {exc}")
    if result.returncode == 0:
        return CheckResult(
            name,
            rov_core.STATUS_PASS,
            f"{KICAD_CLI} {subcommand} {action} reported no violations for {target.name}.",
        )
    detail = (
        _read_report_detail(report)
        or _last_line(result.stderr)
        or _last_line(result.stdout)
        or f"exit status {result.returncode}"
    )
    return CheckResult(
        name,
        rov_core.STATUS_FAIL,
        f"{KICAD_CLI} {subcommand} {action} exited with status {result.returncode} "
        f"for {target.name}: {detail}",
    )


def _read_report_detail(report: Path) -> str:
    """Return the most useful line of a kicad-cli report, if it was written."""
    try:
        content = report.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in reversed(content.splitlines()):
        if line.strip():
            return line.strip()
    return ""


# ---------------------------------------------------------------------------
# board bootstrap and sync-library
# ---------------------------------------------------------------------------


def run_board_bootstrap(project_dir: Path, project_name: str | None = None) -> list[CheckResult]:
    """Run the shared bootstrap and render its messages as one result set.

    ``project_name`` defaults to the board directory name, which is what the
    launchers pass through. The CLI never prompts: ``--non-interactive`` is
    accepted for the template wrapper and changes nothing here.
    """
    root = Path(project_dir)
    if not root.is_dir():
        return [
            CheckResult(
                "project-dir",
                rov_core.STATUS_BLOCKED,
                f"{root} is not a directory. Pass --project-dir with the board repository root.",
            )
        ]
    try:
        outcome = rov_core.bootstrap_project(root, project_name or root.name)
    except (OSError, ValueError) as exc:
        return [CheckResult("bootstrap", rov_core.STATUS_FAIL, str(exc))]
    return rov_core.bootstrap_results(outcome) + [
        CheckResult(
            "bootstrap",
            rov_core.bootstrap_status(outcome),
            f"{len(outcome.changed_files)} file(s) changed while preparing {root.name}.",
        )
    ]


def run_board_sync_library(
    project_dir: Path,
    library_path: str | None = None,
    library_branch: str = rov_core.LIBRARY_BRANCH,
    apply: bool = False,
    branch: str | None = None,
    push: bool = False,
    create_pr: bool = False,
) -> list[CheckResult]:
    """Plan a library update and apply it only when explicitly requested.

    The command is a dry run until ``--apply`` is given, so the default path
    only reports what would change. Planning and applying live in ``rov_core``
    and are reached through ``getattr`` so this command can exist, and report a
    clear BLOCKED state, before that shared implementation is in place.
    """
    root = Path(project_dir)
    try:
        config = rov_core.load_project_config(root)
    except ValueError as exc:
        return [CheckResult("manifest", rov_core.STATUS_FAIL, str(exc))]

    configured = rov_core.configured_library_path(config)
    if library_path is not None and rov_core.normalize_relative_path(library_path) != configured:
        return [
            CheckResult(
                "library-path",
                rov_core.STATUS_BLOCKED,
                f"--library-path {library_path} does not match the manifest library path "
                f"{configured}. Update {rov_core.PROJECT_CONFIG_NAME} instead.",
            )
        ]
    if create_pr and not push:
        return [
            CheckResult(
                "sync-library",
                rov_core.STATUS_BLOCKED,
                "--pr needs --push: a pull request is only opened for a branch that was "
                "pushed. The protected branch is never pushed to.",
            )
        ]
    if shutil.which("git") is None:
        return [
            CheckResult(
                "sync-library",
                rov_core.STATUS_BLOCKED,
                "Git was not found on PATH, so the library was not updated.",
            )
        ]
    if not rov_core.is_git_worktree(root):
        return [
            CheckResult(
                "sync-library",
                rov_core.STATUS_BLOCKED,
                f"{root} is not a Git work tree, so the library was not updated.",
            )
        ]
    if not rov_core.is_clean_worktree(root):
        return [
            CheckResult(
                "sync-library",
                rov_core.STATUS_BLOCKED,
                f"{root} has uncommitted changes, so it was left untouched. Commit or "
                "stash them, then run this command again.",
            )
        ]

    plan_library_update = getattr(rov_core, "plan_library_update", None)
    if plan_library_update is None:
        return [
            CheckResult(
                "sync-library",
                rov_core.STATUS_BLOCKED,
                "Library update planning is not available in this build of rov; nothing "
                "was changed. Run 'git submodule update --init --recursive' to update the "
                "library, or wait for the validated library-update workflow.",
            )
        ]

    plan = plan_library_update(root, branch=library_branch)
    results = [
        CheckResult(
            "library-current",
            rov_core.STATUS_PASS,
            f"The board uses library commit {plan.current_commit or 'unknown'}.",
        ),
        CheckResult(
            "library-target",
            rov_core.STATUS_PASS,
            f"origin/{library_branch} is {plan.target_commit or 'unresolved'} and changes "
            f"{len(plan.changed_files)} file(s): "
            f"{', '.join(str(name) for name in plan.changed_files) or 'none'}.",
        ),
    ]
    if plan.blocked_reason:
        results.append(
            CheckResult("sync-library", rov_core.STATUS_BLOCKED, plan.blocked_reason)
        )
        return results
    if not apply:
        results.append(
            CheckResult(
                "sync-library",
                rov_core.STATUS_PASS,
                "Dry run only; nothing was changed. Re-run with --apply to update the "
                "submodule and validate the result.",
            )
        )
        return results

    apply_library_update = getattr(rov_core, "apply_library_update", None)
    if apply_library_update is None:
        results.append(
            CheckResult(
                "sync-library",
                rov_core.STATUS_BLOCKED,
                "Applying a library update is not available in this build of rov; "
                "nothing was changed.",
            )
        )
        return results
    results.append(
        apply_library_update(
            root,
            plan,
            branch or DEFAULT_SYNC_BRANCH,
            DEFAULT_SYNC_COMMIT_MESSAGE,
            push=push,
            create_pr=create_pr,
        )
    )
    return results


# ---------------------------------------------------------------------------
# library
# ---------------------------------------------------------------------------


def run_library_validate(library_dir: Path) -> list[CheckResult]:
    """Run the library's own metadata linter over its symbol sources."""
    library = Path(library_dir)
    linter = _resolve_linter(library)
    if linter is None:
        return [
            CheckResult(
                "library-validate",
                rov_core.STATUS_BLOCKED,
                f"{LINTER_FILE} was not found in {library / 'scripts'} or in {SCRIPTS_DIR}.",
            )
        ]
    symbols = library / "Symbols"
    if not symbols.is_dir():
        return [
            CheckResult(
                "library-validate",
                rov_core.STATUS_BLOCKED,
                f"{symbols} does not exist, so no symbol metadata was checked.",
            )
        ]
    return [
        _script_result(
            "library-validate",
            linter,
            [str(symbols)],
            f"{linter.name} found no metadata problems in {symbols.name}.",
            library,
        )
    ]


def run_library_build(library_dir: Path) -> CheckResult:
    """Run the library's own symbol compiler."""
    library = Path(library_dir)
    script = _library_script(library, BUILD_FILE)
    if script is None:
        return CheckResult(
            "library-build",
            rov_core.STATUS_BLOCKED,
            f"{BUILD_FILE} was not found in {library / 'scripts'}. Run this command "
            "from the KiCad library repository.",
        )
    return _script_result(
        "library-build", script, [], f"{BUILD_FILE} completed successfully.", library
    )


def run_library_sync(library_dir: Path, branch: str = rov_core.LIBRARY_BRANCH) -> CheckResult:
    """Fast-forward a clean library checkout to ``origin/<branch>``.

    This command never pushes and never discards local work: a dirty work tree
    or an unreachable remote is BLOCKED, and an unreachable remote leaves the
    cached revision in place and says so.
    """
    library = Path(library_dir)
    if shutil.which("git") is None:
        return CheckResult(
            "library-sync",
            rov_core.STATUS_BLOCKED,
            "Git was not found on PATH, so the library was not updated.",
        )
    if not library.is_dir():
        return CheckResult(
            "library-sync",
            rov_core.STATUS_BLOCKED,
            f"{library} does not exist, so the library was not updated.",
        )
    if not rov_core.is_git_worktree(library):
        return CheckResult(
            "library-sync",
            rov_core.STATUS_BLOCKED,
            f"{library} is not a Git work tree, so the library was not updated.",
        )
    if not rov_core.is_clean_worktree(library):
        return CheckResult(
            "library-sync",
            rov_core.STATUS_BLOCKED,
            f"{library} has local changes, so it was left untouched. Commit or stash "
            "them, then run this command again.",
        )

    fetch = rov_core.run_git(library, "fetch", "origin", branch)
    if fetch.returncode != 0:
        detail = _last_line(fetch.stderr) or _last_line(fetch.stdout) or "unknown error"
        return CheckResult(
            "library-sync",
            rov_core.STATUS_BLOCKED,
            f"Could not reach origin/{branch} ({detail}); the cached library revision "
            "was kept and it may be stale.",
        )
    merge = rov_core.run_git(library, "merge", "--ff-only", f"origin/{branch}")
    if merge.returncode != 0:
        detail = _last_line(merge.stderr) or _last_line(merge.stdout) or "unknown error"
        return CheckResult(
            "library-sync",
            rov_core.STATUS_BLOCKED,
            f"Could not fast-forward to origin/{branch} ({detail}); the cached library "
            "revision was kept and it may be stale.",
        )
    if merge.stdout.strip() == "Already up to date.":
        return CheckResult(
            "library-sync",
            rov_core.STATUS_PASS,
            f"The library already matches origin/{branch}.",
        )
    return CheckResult(
        "library-sync",
        rov_core.STATUS_PASS,
        f"Fast-forwarded the library to origin/{branch}.",
    )


def run_library_list(library_dir: Path, query: str | None = None) -> list[CheckResult]:
    """Print one tab-separated row per symbol and return a single result.

    Parsing is delegated to the library's own ``kicad_sym_utils`` module, loaded
    by file path, so this command and the Library Manager always agree on which
    parts exist and what metadata they carry.
    """
    library = Path(library_dir)
    utils = _load_symbol_utils(library)
    if utils is None:
        return [
            CheckResult(
                "library-list",
                rov_core.STATUS_BLOCKED,
                f"{SYMBOL_UTILS_FILE} was not found in {library / 'scripts'}. Run this "
                "command from the KiCad library repository.",
            )
        ]
    symbols = library / "Symbols"
    if not symbols.is_dir():
        return [
            CheckResult(
                "library-list",
                rov_core.STATUS_BLOCKED,
                f"{symbols} does not exist, so no parts were listed.",
            )
        ]

    print("\t".join(SYMBOL_ROW_COLUMNS))
    listed = 0
    for path in _per_part_symbol_files(symbols):
        content = _read_text(path)
        if content is None:
            continue
        for entry in utils.extract_top_symbols(content):
            # The library returns (name, symbol text, start, end) per top symbol.
            name, raw_symbol = entry[0], entry[1]
            properties = utils.parse_symbol_properties(raw_symbol)[0]
            row = (
                name,
                properties.get("Category", ""),
                properties.get("MPN", ""),
                properties.get("Manufacturer", ""),
            )
            if query and not _row_matches(row, query):
                continue
            print("\t".join(row))
            listed += 1
    if query:
        return [
            CheckResult(
                "library-search",
                rov_core.STATUS_PASS,
                f"{listed} part(s) matched {query!r} in {symbols}.",
            )
        ]
    return [
        CheckResult(
            "library-list",
            rov_core.STATUS_PASS,
            f"{listed} part(s) listed from {symbols}.",
        )
    ]


def run_library_gui(library_dir: Path) -> int:
    """Launch the existing Library Manager GUI and return its exit code."""
    library = Path(library_dir)
    script = _library_script(library, GUI_FILE)
    if script is None:
        return _report_single(
            CheckResult(
                "library-gui",
                rov_core.STATUS_BLOCKED,
                f"{GUI_FILE} was not found in {library / 'scripts'}. Run this command "
                "from the KiCad library repository.",
            )
        )
    return _launch_tool([sys.executable, str(script)], library)


def run_library_import(library_dir: Path, arguments: list[str]) -> int:
    """Run the library's own import script with the remaining arguments."""
    library = Path(library_dir)
    script = _library_script(library, IMPORT_FILE)
    if script is None:
        return _report_single(
            CheckResult(
                "library-import",
                rov_core.STATUS_BLOCKED,
                f"{IMPORT_FILE} was not found in {library / 'scripts'}. Run this command "
                "from the KiCad library repository.",
            )
        )
    if not arguments:
        return _report_single(
            CheckResult(
                "library-import",
                rov_core.STATUS_BLOCKED,
                f"Pass at least --symbol PATH to {IMPORT_FILE}, for example "
                f"'rov library import --symbol part.kicad_sym --category Power'. Use "
                "'rov library gui' for the interactive flow.",
            )
        )
    return _launch_tool([sys.executable, str(script), *arguments], library)


def _library_script(library_dir: Path, name: str) -> Path | None:
    """Return a library script when it exists."""
    script = Path(library_dir) / "scripts" / name
    return script if script.is_file() else None


def _load_symbol_utils(library_dir: Path):
    """Import the library's ``kicad_sym_utils`` module by file path."""
    path = Path(library_dir) / "scripts" / SYMBOL_UTILS_FILE
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(f"rov_library_{abs(hash(str(path)))}", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    # The library module is loaded from its own directory, so its sibling
    # imports resolve the same way they do inside the repository.
    library_scripts = str(path.parent)
    added = library_scripts not in sys.path
    if added:
        sys.path.insert(0, library_scripts)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - a broken library must not crash the CLI
        raise LibraryLoadError(f"Could not load {path}: {exc}") from exc
    finally:
        if added:
            sys.path.remove(library_scripts)
    return module


def _per_part_symbol_files(symbols: Path) -> list[Path]:
    """Return the canonical per-part symbol files, excluding generated ones."""
    return [
        path
        for path in sorted(symbols.rglob("*.kicad_sym"))
        if path.name not in GENERATED_SYMBOL_FILES
        and not any(part.startswith(".") for part in path.parts)
    ]


def _row_matches(row: tuple[str, ...], query: str) -> bool:
    """Return True when any printed field contains ``query``."""
    needle = query.casefold().strip()
    return any(needle in field.casefold() for field in row if field)


def _read_text(path: Path) -> str | None:
    """Return file text, or None when it is unreadable."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Command tree
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Return the documented ``rov`` command tree."""
    parser = argparse.ArgumentParser(
        prog="rov",
        description="Purdue ROV PCB tooling: diagnose, bootstrap, validate, and update.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser("doctor", help="Report the tools and board state in use.")
    _add_project_dir(doctor)
    doctor.set_defaults(handler=_cmd_doctor)

    board = commands.add_parser("board", help="Board bootstrap, validation, and library sync.")
    board_commands = board.add_subparsers(dest="board_command", required=True)

    bootstrap = board_commands.add_parser(
        "bootstrap", help="Prepare a board directory for KiCad without discarding work."
    )
    _add_project_dir(bootstrap)
    bootstrap.add_argument("--project-name", help="Project stem (default: the directory name).")
    bootstrap.add_argument(
        "--non-interactive",
        action="store_true",
        help="Accepted for the template wrapper; the CLI never prompts.",
    )
    bootstrap.set_defaults(handler=_cmd_board_bootstrap)

    validate = board_commands.add_parser("validate", help="Run the fast or full board checks.")
    _add_project_dir(validate)
    validate.add_argument(
        "--full", action="store_true", help="Also run kicad-cli ERC and DRC."
    )
    validate.add_argument(
        "--hook",
        action="store_true",
        help="Pre-commit mode: fast checks only, and fail only on FAIL.",
    )
    validate.set_defaults(handler=_cmd_board_validate)

    sync = board_commands.add_parser(
        "sync-library", help="Plan, and optionally apply, a library submodule update."
    )
    _add_project_dir(sync)
    sync.add_argument(
        "--library-path",
        "--library-dir",
        dest="library_path",
        help="Expected library path; it must match rov.project.json.",
    )
    sync.add_argument(
        "--library-branch", default=rov_core.LIBRARY_BRANCH, help="Library branch to track."
    )
    sync.add_argument(
        "--apply", action="store_true", help="Apply the plan. Without it the command is a dry run."
    )
    sync.add_argument("--branch", help=f"Update branch to create (default: {DEFAULT_SYNC_BRANCH}).")
    sync.add_argument("--push", action="store_true", help="Push the update branch after applying.")
    sync.add_argument("--pr", action="store_true", help="Open a pull request after pushing.")
    sync.set_defaults(handler=_cmd_board_sync_library)

    library = commands.add_parser("library", help="Approved standard library commands.")
    library_commands = library.add_subparsers(dest="library_command", required=True)

    listing = library_commands.add_parser("list", help="List library parts as tab-separated rows.")
    _add_library_dir(listing)
    listing.set_defaults(handler=_cmd_library_list)

    search = library_commands.add_parser("search", help="List library parts matching a query.")
    _add_library_dir(search)
    search.add_argument("query", help="Text matched against name, category, MPN, and manufacturer.")
    search.set_defaults(handler=_cmd_library_search)

    library_sync = library_commands.add_parser(
        "sync", help="Fast-forward the library to origin/<branch> without pushing."
    )
    _add_library_dir(library_sync)
    library_sync.add_argument(
        "--branch", default=rov_core.LIBRARY_BRANCH, help="Library branch to track."
    )
    library_sync.set_defaults(handler=_cmd_library_sync)

    library_validate = library_commands.add_parser(
        "validate", help="Run the symbol metadata linter."
    )
    _add_library_dir(library_validate)
    library_validate.set_defaults(handler=_cmd_library_validate)

    library_build = library_commands.add_parser(
        "build", help="Rebuild the generated category symbol libraries."
    )
    _add_library_dir(library_build)
    library_build.set_defaults(handler=_cmd_library_build)

    library_import = library_commands.add_parser(
        "import",
        help="Run import_part.py. Pass --library-dir first, then its arguments.",
    )
    _add_library_dir(library_import)
    library_import.set_defaults(handler=_cmd_library_import)

    contribute = library_commands.add_parser(
        "contribute", help="Prepare a branch and pull request for a new part."
    )
    _add_library_dir(contribute)
    contribute.add_argument("--name", help="Manufacturer part number of the new part.")
    contribute.add_argument("--category", help="Category library for the new part.")
    contribute.add_argument("--push", action="store_true", help="Push the new branch.")
    contribute.add_argument("--pr", action="store_true", help="Open a pull request.")
    contribute.set_defaults(handler=_cmd_library_contribute)

    gui = library_commands.add_parser("gui", help="Open the Library Manager GUI.")
    _add_library_dir(gui)
    gui.set_defaults(handler=_cmd_library_gui)

    return parser


def _add_project_dir(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=None,
        help="Board repository root (default: the current directory).",
    )


def _add_library_dir(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--library-dir",
        type=Path,
        default=None,
        help="Library repository root (default: the current directory).",
    )


def _project_dir(args: argparse.Namespace) -> Path:
    value = getattr(args, "project_dir", None)
    return Path.cwd() if value is None else Path(value)


def _library_dir(args: argparse.Namespace) -> Path:
    value = getattr(args, "library_dir", None)
    return Path.cwd() if value is None else Path(value)


def _report_single(result: CheckResult) -> int:
    """Print one result and return the matching exit code."""
    print_results([result])
    return _EXIT_CODE_BY_STATUS[result.status]


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def _cmd_doctor(args: argparse.Namespace) -> int:
    results = run_doctor(_project_dir(args))
    print_results(results)
    return exit_code_for(results)


def _cmd_board_bootstrap(args: argparse.Namespace) -> int:
    results = run_board_bootstrap(_project_dir(args), args.project_name)
    print_results(results)
    return exit_code_for(results)


def _cmd_board_validate(args: argparse.Namespace) -> int:
    results = run_board_validate(_project_dir(args), full=args.full, hook=args.hook)
    print_results(results)
    return hook_exit_code_for(results) if args.hook else exit_code_for(results)


def _cmd_board_sync_library(args: argparse.Namespace) -> int:
    results = run_board_sync_library(
        _project_dir(args),
        library_path=args.library_path,
        library_branch=args.library_branch,
        apply=args.apply,
        branch=args.branch,
        push=args.push,
        create_pr=args.pr,
    )
    print_results(results)
    return exit_code_for(results)


def _cmd_library_list(args: argparse.Namespace) -> int:
    return _cmd_library_rows(args, None)


def _cmd_library_search(args: argparse.Namespace) -> int:
    return _cmd_library_rows(args, args.query)


def _cmd_library_rows(args: argparse.Namespace, query: str | None) -> int:
    try:
        results = run_library_list(_library_dir(args), query)
    except LibraryLoadError as exc:
        results = [CheckResult("library-list", rov_core.STATUS_BLOCKED, str(exc))]
    print_results(results)
    return exit_code_for(results)


def _cmd_library_sync(args: argparse.Namespace) -> int:
    return _report_single(run_library_sync(_library_dir(args), branch=args.branch))


def _cmd_library_validate(args: argparse.Namespace) -> int:
    results = run_library_validate(_library_dir(args))
    print_results(results)
    return exit_code_for(results)


def _cmd_library_build(args: argparse.Namespace) -> int:
    return _report_single(run_library_build(_library_dir(args)))


def _cmd_library_gui(args: argparse.Namespace) -> int:
    return run_library_gui(_library_dir(args))


def _cmd_library_import(args: argparse.Namespace) -> int:
    """Handle the ``library import`` subcommand when argparse reaches it.

    ``main`` normally forwards every token after ``library import`` verbatim,
    so this path only runs for a call that supplied no part arguments at all.
    """
    return run_library_import(_library_dir(args), [])


def _cmd_library_contribute(args: argparse.Namespace) -> int:
    return _report_single(
        CheckResult(
            "library-contribute",
            rov_core.STATUS_BLOCKED,
            "Library contribution preparation is not available in this build of rov. "
            "Until it lands, edit the parts under Symbols/, run 'rov library validate' "
            "and 'rov library build', then commit them on a feature branch yourself. "
            "The Library Manager GUI stays available through 'rov library gui'.",
        )
    )


def _split_library_dir_option(tokens: list[str]) -> tuple[Path, list[str]]:
    """Return the library directory and the untouched passthrough tokens.

    Only a leading ``--library-dir PATH`` is consumed so every other token
    reaches ``import_part.py`` exactly as it was typed.
    """
    if len(tokens) >= 2 and tokens[0] == "--library-dir":
        return Path(tokens[1]), tokens[2:]
    return Path.cwd(), tokens


def _run_library_import(tokens: list[str]) -> int:
    """Run ``library import`` without argparse consuming the part arguments.

    The tokens after ``library import`` belong to ``import_part.py``, so they are
    forwarded verbatim instead of being parsed here. ``--help`` is the one
    exception: a help request is always answered by this CLI.
    """
    if any(token in ("-h", "--help") for token in tokens):
        try:
            build_parser().parse_args(["library", "import", "--help"])
        except SystemExit as help_request:  # argparse printed the help text
            return int(help_request.code or 0)
        return 0
    library_dir, passthrough = _split_library_dir_option(tokens)
    return run_library_import(library_dir, passthrough)


def main(argv: list[str] | None = None) -> int:
    """Run one ``rov`` command and return its exit code."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if tuple(arguments[:2]) == LIBRARY_IMPORT_COMMAND:
        return _run_library_import(arguments[2:])
    args = build_parser().parse_args(arguments)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
