"""
Shared contracts for the ROV PCB platform.

This module is the single source of truth for the values and checks that the
`rov.py` CLI, the bootstrap flow, the board template, and the Library Manager
GUI all depend on. It intentionally has no third-party dependencies and never
performs destructive Git operations.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

STATUS_PASS = "PASS"
STATUS_WARN = "WARN"
STATUS_FAIL = "FAIL"
STATUS_BLOCKED = "BLOCKED"

KICAD_BASELINE = "10"
LIBRARY_BRANCH = "master"
PROJECT_CONFIG_NAME = "rov.project.json"
LIBRARY_UPDATE_POLICY = "pull-request"
SCHEMA_VERSION = 1
LIBRARY_SUBMODULE_PATH = "libs/purdue-rov-kicad-lib"
CI_PROFILE_STANDARD = "standard"

# Library update contract. The exact strings are the published behavior of
# ``rov board sync-library``: the branch a reviewable update lands on, the
# commit it records, the pull request title it opens, and the protected branches
# that are never committed to or pushed. The CLI and this module share these
# values so a local run and a scheduled workflow cannot drift apart.
LIBRARY_UPDATE_BRANCH = "chore/library-update"
LIBRARY_UPDATE_COMMIT_MESSAGE = "chore(library): update Purdue ROV component library"
LIBRARY_UPDATE_PR_TITLE = "chore: update Purdue ROV component library"
PROTECTED_BRANCHES = frozenset({"master", "main", "develop", "development", "release"})
DEFAULT_REMOTE = "origin"
GITHUB_CLI = "gh"

# Library contribution contract. ``LIBRARY_CONTRIBUTION_PATHS`` is the closed set
# of directories a part contribution may stage: a change anywhere else is
# refused rather than swept into the commit. ``CONTRIBUTION_BRANCH_PREFIX`` is the
# reviewable branch every contribution lands on, so a protected branch is never
# committed to or pushed by this flow.
LIBRARY_CONTRIBUTION_PATHS = ("Symbols", "Footprints", "3D_Models", "Design_Blocks")
CONTRIBUTION_BRANCH_PREFIX = "add-part-"
LIBRARY_CONTRIBUTE_CHECK = "library-contribute"
CONTRIBUTION_LINTER_FILE = "linter_validator.py"

# A library fetch is the only command that can leave the machine. It is bounded
# so an unreachable or hanging remote reports a blocked plan instead of hanging a
# board; the GitHub CLI calls made while publishing a pull request are bounded
# the same way.
LIBRARY_FETCH_TIMEOUT_SECONDS = 60
GITHUB_CLI_TIMEOUT_SECONDS = 120
LINTER_TIMEOUT_SECONDS = 300

# The single wording for a rewritten remote library history. It is a contract:
# the CLI prints it verbatim and CI can match on it.
DIVERGED_HISTORY_REASON = "remote library history diverged"

# Bootstrap contract. ``TEMPLATE_STEM`` is the starter design file stem shipped by
# the board template; ``PROJECT_FILE_EXTENSIONS`` lists the files that are
# renamed to the requested project name.
TEMPLATE_STEM = "board-template"
PROJECT_FILE_EXTENSIONS = (".kicad_pro", ".kicad_sch", ".kicad_pcb")
PROJECT_TITLE_KEY = "title"

# The generated hook lives in an untracked directory so a board repository never
# commits local tooling and the template's tracked .githooks stays untouched.
HOOKS_DIR_NAME = ".rov-hooks"
HOOK_FILE_NAME = "pre-commit"
HOOKS_PATH_CONFIG = HOOKS_DIR_NAME
CACHED_CLI_RELATIVE_PATH = ".pcb-devops-cache/scripts/rov.py"

# POSIX sh so the hook runs on Linux, macOS, and Git Bash. It resolves an
# interpreter, never installs one, and exits 0 when the CLI is unavailable so a
# club member is never locked out of Git. The last command is the CLI call so
# its exit status becomes the hook's status.
PRE_COMMIT_HOOK_SCRIPT = """#!/usr/bin/env sh
repo_root=$(git rev-parse --show-toplevel) || exit 0
cli="$repo_root/.pcb-devops-cache/scripts/rov.py"
[ -f "$cli" ] || exit 0
if command -v python3 >/dev/null 2>&1; then
    rov_python=python3
elif command -v python >/dev/null 2>&1; then
    rov_python=python
else
    exit 0
fi
"$rov_python" "$cli" board validate --project-dir "$repo_root" --hook
"""

# Bootstrap messages are prefixed with a status tag. ``bootstrap_status`` folds
# them into one result using the same precedence as the CLI exit-code rule:
# FAIL beats BLOCKED, BLOCKED beats WARN, and WARN beats PASS.
_BOOTSTRAP_STATUS_PRECEDENCE = (STATUS_FAIL, STATUS_BLOCKED, STATUS_WARN, STATUS_PASS)

# Bootstrap messages carry their status as a leading ``[STATUS]`` tag. The
# pattern is the single parser for that tag, so a message can never be read as
# a pass by one interface and as a failure by another.
_STATUS_PREFIX_PATTERN = re.compile(r"^\[(PASS|WARN|FAIL|BLOCKED)\][ \t]*(.*)$", re.DOTALL)

# Unresolved Git conflict markers are matched exactly like the CI grep in
# .github/workflows/run-kicad-ci.yml: anchored to the start of a line, so
# legitimate runs of '=' inside design data are not reported. The checked file
# set is the same --include list CI uses, so a file CI scans is never skipped
# locally and vice versa.
CONFLICT_MARKER_PATTERN = re.compile(r"^(<{7}|={7}|>{7})", re.MULTILINE)
CONFLICT_CHECK_PATTERNS = ("*.kicad_*", "*-lib-table")

# Characters that are illegal in a Windows or POSIX file name. They are replaced
# rather than rejected so a friendlier name such as "X19: Control" still works.
_UNSAFE_STEM_PATTERN = re.compile(r'[<>:"|?*\x00-\x1f]')

# (nickname, display label). The label is the developer-visible wording used in
# newly created library tables and must match the established text, which is not
# always the nickname with its prefix removed (rov_mech is "Mechanical").
_STANDARD_LIB_LABELS = (
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
        "sym_uri": f"${{KIPRJMOD}}/{LIBRARY_SUBMODULE_PATH}/Symbols/{name}.kicad_sym",
        "fp_uri": f"${{KIPRJMOD}}/{LIBRARY_SUBMODULE_PATH}/Footprints/{name}.pretty",
        "sym_descr": f"Purdue ROV {label} Symbols",
        "fp_descr": f"Purdue ROV {label} Footprints",
    }
    for name, label in _STANDARD_LIB_LABELS
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


def load_project_config(project_dir: Path) -> dict[str, object]:
    """Load rov.project.json, raising ValueError with the absolute path on failure."""
    config_path = (Path(project_dir) / PROJECT_CONFIG_NAME).resolve()
    try:
        raw = config_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError(
            f"Missing {PROJECT_CONFIG_NAME} at {config_path}. "
            f"Create it at the board repository root; see KiCad/DevOps docs for the schema."
        ) from exc
    except OSError as exc:
        raise ValueError(f"Could not read {PROJECT_CONFIG_NAME} at {config_path}: {exc}") from exc

    try:
        config = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Malformed JSON in {PROJECT_CONFIG_NAME} at {config_path}: {exc}"
        ) from exc

    if not isinstance(config, dict):
        raise ValueError(
            f"{PROJECT_CONFIG_NAME} at {config_path} must contain a JSON object, "
            f"found {type(config).__name__}."
        )
    return config


def validate_project_config(config: Mapping[str, object]) -> list[CheckResult]:
    """Validate a board manifest and return one result per checked field."""
    results: list[CheckResult] = [
        _check_schema(config),
        _require_text(config, "project_name"),
        _check_expected(config, "kicad_version", KICAD_BASELINE),
        _require_text(config, "platform_ref"),
    ]

    library = config.get("library")
    if not isinstance(library, Mapping):
        results.append(
            CheckResult(
                "library",
                STATUS_FAIL,
                "library must be an object with path, branch, and update_policy.",
            )
        )
        return results

    results.append(_require_text(library, "path", "library.path"))
    results.append(_require_text(library, "branch", "library.branch"))
    results.append(
        _check_expected(library, "update_policy", LIBRARY_UPDATE_POLICY, "library.update_policy")
    )
    return results + [_require_text(config, "ci_profile")]


def _check_schema(config: Mapping[str, object]) -> CheckResult:
    """Return PASS/FAIL for the manifest schema version, rejecting booleans."""
    schema = config.get("schema")
    if isinstance(schema, bool) or schema != SCHEMA_VERSION:
        return CheckResult(
            "schema",
            STATUS_FAIL,
            f"schema must be the integer {SCHEMA_VERSION}, found {schema!r}.",
        )
    return CheckResult("schema", STATUS_PASS, f"schema is {SCHEMA_VERSION}.")


def _check_expected(
    container: Mapping[str, object], key: str, expected: object, label: str | None = None
) -> CheckResult:
    """Return PASS/FAIL requiring ``container[key]`` to equal ``expected``."""
    name = label or key
    value = container.get(key)
    if value == expected:
        return CheckResult(name, STATUS_PASS, f"{name} is {value!r}.")
    return CheckResult(name, STATUS_FAIL, f"{name} must be {expected!r}, found {value!r}.")


def _require_text(container: Mapping[str, object], key: str, label: str | None = None) -> CheckResult:
    """Return a PASS/FAIL result requiring a non-empty string at ``key``."""
    name = label or key
    value = container.get(key)
    if isinstance(value, str) and value.strip():
        return CheckResult(name, STATUS_PASS, f"{name} is set to {value!r}.")
    return CheckResult(name, STATUS_FAIL, f"{name} must be a non-empty string, found {value!r}.")


def _read_text_or_none(path: Path) -> str | None:
    """Return file text, or None when the file is missing or unreadable."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None


def validate_library_tables(project_dir: Path) -> list[CheckResult]:
    """Check sym-lib-table and fp-lib-table for every standard library nickname."""
    results: list[CheckResult] = []
    names = [lib["name"] for lib in STANDARD_LIBS]
    for table_name in ("sym-lib-table", "fp-lib-table"):
        table_path = Path(project_dir) / table_name
        content = _read_text_or_none(table_path)
        if content is None:
            results.append(
                CheckResult(
                    table_name,
                    STATUS_FAIL,
                    f"{table_name} is missing or unreadable at {table_path.resolve()}. "
                    f"Missing library entries: {', '.join(names)}.",
                )
            )
            continue

        declared = set(re.findall(r'\(lib\s+\(name\s+"([^"]+)"\)', content, re.IGNORECASE))
        missing = [name for name in names if name not in declared]
        if missing:
            results.append(
                CheckResult(
                    table_name,
                    STATUS_FAIL,
                    f"{table_name} is missing standard library entries: "
                    f"{', '.join(missing)}. Run the library table sync to add them.",
                )
            )
        else:
            results.append(
                CheckResult(
                    table_name,
                    STATUS_PASS,
                    f"{table_name} contains all {len(names)} standard library entries.",
                )
            )
    return results


def find_project_file(project_dir: Path) -> Path | None:
    """Return the lexicographically first *.kicad_pro, ignoring .kicad_prl files."""
    matches = sorted(
        path for path in Path(project_dir).glob("*.kicad_pro") if path.is_file()
    )
    return matches[0] if matches else None


def check_submodule(project_dir: Path, config: Mapping[str, object]) -> CheckResult:
    """Report the state of the configured standard library submodule."""
    project_dir = Path(project_dir)
    library = config.get("library") if isinstance(config, Mapping) else None
    if not isinstance(library, Mapping):
        return CheckResult(
            "submodule",
            STATUS_FAIL,
            "library configuration is missing or malformed, so the library "
            "submodule path cannot be resolved.",
        )

    configured_path = library.get("path")
    if not isinstance(configured_path, str) or not configured_path.strip():
        return CheckResult(
            "submodule",
            STATUS_FAIL,
            "library.path must be a non-empty string so the library submodule path "
            "can be resolved.",
        )

    relative_path = normalize_relative_path(configured_path)
    library_dir = project_dir / Path(relative_path)
    if not library_dir.is_dir():
        return CheckResult(
            "submodule",
            STATUS_BLOCKED,
            f"library submodule directory {relative_path} does not exist under "
            f"{project_dir.resolve()}. Run 'git submodule update --init --recursive'.",
        )

    declared = _declared_submodule_paths(project_dir)
    if relative_path not in declared:
        return CheckResult(
            "submodule",
            STATUS_FAIL,
            f".gitmodules does not declare the configured library path {relative_path}. "
            f"Add it with 'git submodule add <url> {relative_path}'.",
        )

    if not (library_dir / ".git").exists():
        return CheckResult(
            "submodule",
            STATUS_BLOCKED,
            f"library submodule {relative_path} is declared but not initialized. "
            f"Run 'git submodule update --init --recursive'.",
        )

    return CheckResult(
        "submodule",
        STATUS_PASS,
        f"library submodule {relative_path} is initialized.",
    )


def _declared_submodule_paths(project_dir: Path) -> set[str]:
    """Return every normalized path declared in .gitmodules."""
    gitmodules_path = project_dir / ".gitmodules"
    content = _read_text_or_none(gitmodules_path)
    if content is None:
        return set()
    return {
        match.replace("\\", "/").strip("/")
        for match in re.findall(r"^[ \t]*path[ \t]*=[ \t]*(.+?)[ \t]*$", content, re.MULTILINE)
    }


def check_merge_conflict_markers(
    project_dir: Path, skip_dirs: Iterable[str] = ()
) -> list[CheckResult]:
    """Report design files that still contain unresolved Git conflict markers.

    The scan is recursive, prunes dot-directories, and never descends into
    ``skip_dirs`` so a board does not re-scan its library submodule. It is the
    same rule CI applies, kept here so the CLI and CI can never disagree.
    """
    root = Path(project_dir)
    if not root.is_dir():
        return [
            CheckResult(
                "merge-conflict-markers",
                STATUS_BLOCKED,
                f"{root} is not a directory, so no conflict markers were checked.",
            )
        ]

    skipped = {normalize_relative_path(value) for value in skip_dirs if str(value).strip()}
    offenders: list[str] = []
    checked = 0
    for path in _design_files(root, skipped):
        content = _read_text_or_none(path)
        if content is None:
            continue
        checked += 1
        if CONFLICT_MARKER_PATTERN.search(content):
            offenders.append(path.relative_to(root).as_posix())

    if offenders:
        return [
            CheckResult(
                "merge-conflict-markers",
                STATUS_FAIL,
                f"Unresolved merge conflict markers in: {', '.join(sorted(offenders))}. "
                "Finish or abort the merge before committing.",
            )
        ]
    return [
        CheckResult(
            "merge-conflict-markers",
            STATUS_PASS,
            f"No merge conflict markers were found in {checked} design file(s).",
        )
    ]


def _design_files(root: Path, skipped: set[str]) -> Iterable[Path]:
    """Yield the conflict-marker check targets under ``root``."""
    for dirpath, dirnames, filenames in os.walk(root):
        relative_dir = Path(dirpath).relative_to(root)
        relative = "" if relative_dir == Path(".") else relative_dir.as_posix()
        if relative in skipped:
            dirnames[:] = []
            continue
        dirnames[:] = sorted(name for name in dirnames if not name.startswith("."))
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            if any(Path(name).match(pattern) for pattern in CONFLICT_CHECK_PATTERNS):
                yield Path(dirpath) / name


def run_git(
    repo_dir: Path, *args: str, check: bool = False, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a non-shell Git command in ``repo_dir``.

    ``timeout`` is passed straight to ``subprocess`` and is only set by the
    commands that can reach a network, so a remote that never answers is
    reported instead of stalling the caller.
    """
    if shutil.which("git") is None:
        raise RuntimeError("Git is required but was not found")

    result = subprocess.run(
        ["git", *args],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed"
        raise RuntimeError(detail)
    return result


def is_git_worktree(repo_dir: Path) -> bool:
    """Return True when ``repo_dir`` is inside a Git work tree."""
    return run_git(repo_dir, "rev-parse", "--is-inside-work-tree").stdout.strip() == "true"


def is_clean_worktree(repo_dir: Path) -> bool:
    """Return True only when repo_dir is a work tree with no local changes.

    `git status` prints nothing on stdout when it fails, so the return code is
    checked as well: a non-repository must never report as clean.
    """
    result = run_git(repo_dir, "status", "--porcelain")
    return result.returncode == 0 and result.stdout.strip() == ""


def current_branch(repo_dir: Path) -> str | None:
    """Return the checked-out branch name, or None when HEAD is detached."""
    return run_git(repo_dir, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip() or None


# ---------------------------------------------------------------------------
# Library update planning and update-branch preparation
# ---------------------------------------------------------------------------


def plan_library_update(
    project_dir: Path, remote: str = DEFAULT_REMOTE, branch: str = LIBRARY_BRANCH
) -> LibraryUpdatePlan:
    """Resolve the approved library revision a board should move to.

    The plan is read-only. It loads and validates the board manifest, refuses to
    continue while either worktree has local changes, syncs and fetches the
    library submodule, and reports the target commit with the library files that
    differ. Every condition that needs a developer's attention becomes a
    ``blocked_reason`` rather than an empty successful plan, so a caller can
    never mistake an unreachable or rewritten remote for an up-to-date library.

    Nothing is written to the board: the only repository state that changes is
    the submodule's fetched objects and its local ``.git/config`` URL, both of
    which Git manages for exactly this purpose.
    """
    root = Path(project_dir)

    try:
        config = load_project_config(root)
    except ValueError as exc:
        return _blocked_plan(str(exc))
    problems = [
        result
        for result in validate_project_config(config)
        if result.status != STATUS_PASS
    ]
    if problems:
        return _blocked_plan(
            f"{PROJECT_CONFIG_NAME} is not usable: "
            + "; ".join(f"{result.name}: {result.message}" for result in problems)
        )

    relative_path = configured_library_path(config)
    library_dir = root / Path(relative_path)

    if not _git_is_available():
        return _blocked_plan("Git is unavailable, so no update was planned.")
    if not is_git_worktree(root):
        return _blocked_plan(f"{root} is not a Git work tree, so no update was planned.")
    if not library_dir.is_dir() or not _is_own_git_worktree(library_dir):
        return _blocked_plan(
            f"the library submodule at {relative_path} is not initialized. Run "
            "'git submodule update --init --recursive'."
        )
    if not is_clean_worktree(library_dir):
        return _blocked_plan(
            f"the library submodule at {relative_path} has local changes, so it was left "
            "untouched. Commit or stash them, then plan the update again."
        )
    if not is_clean_worktree(root):
        return _blocked_plan(
            f"{root} has uncommitted changes, so it was left untouched. Commit or stash "
            "them, then plan the update again."
        )

    sync = run_git(root, "submodule", "sync", "--", relative_path)
    if sync.returncode != 0:
        return _blocked_plan(
            f"could not sync the library submodule URL for {relative_path}: {_git_detail(sync)}"
        )

    try:
        fetch = run_git(
            library_dir, "fetch", remote, branch, timeout=LIBRARY_FETCH_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        return _blocked_plan(
            f"fetching {remote}/{branch} for the library submodule did not finish within "
            f"{LIBRARY_FETCH_TIMEOUT_SECONDS} seconds, so the library may be stale."
        )
    if fetch.returncode != 0:
        return _blocked_plan(
            f"could not fetch {remote}/{branch} for the library submodule (git "
            f"{_git_detail(fetch)}); the cached revision was kept and the library may be stale."
        )

    current = _git_output(library_dir, "rev-parse", "HEAD")
    target = _git_output(library_dir, "rev-parse", "FETCH_HEAD")
    if not current or not target:
        return _blocked_plan(
            "could not resolve the current and target library commits, so the library "
            "may be stale."
        )
    if current == target:
        return LibraryUpdatePlan(
            current_commit=current,
            target_commit=target,
            changed_files=(),
            blocked_reason=None,
        )

    # A submodule can only fast-forward. Anything else means the approved history
    # was rewritten and a board must not be pointed at it automatically.
    if run_git(library_dir, "merge-base", "--is-ancestor", current, target).returncode != 0:
        return LibraryUpdatePlan(
            current_commit=current,
            target_commit=target,
            changed_files=(),
            blocked_reason=DIVERGED_HISTORY_REASON,
        )

    diff = run_git(library_dir, "diff", "--name-only", f"{current}..{target}")
    if diff.returncode != 0:
        return _blocked_plan(
            f"could not list the library changes between {current} and {target}: "
            f"{_git_detail(diff)}"
        )
    return LibraryUpdatePlan(
        current_commit=current,
        target_commit=target,
        changed_files=tuple(_nonempty_lines(diff.stdout)),
        blocked_reason=None,
    )


def apply_library_update(
    project_dir: Path,
    plan: LibraryUpdatePlan,
    branch_name: str,
    commit_message: str,
    push: bool = False,
    create_pr: bool = False,
) -> CheckResult:
    """Move a board to ``plan.target_commit`` on a reviewable update branch.

    The order of the steps is the safety contract:

    1. a blocked plan is refused before anything is touched, and ``create_pr``
       without ``push`` is refused rather than silently reduced to a local apply,
    2. an up-to-date board is a PASS with no branch and no commit,
    3. the base branch is never used as the update branch, so it is never
       committed to and never pushed,
    4. a plan whose target is already recorded is a PASS no-op with no branch,
       commit, push, or pull request, so a stale reapply cannot report work it
       never did,
    5. an existing branch is reused only when it was created from the same base,
    6. the submodule moves with ``git checkout --detach``; ``reset --hard``,
       ``stash``, and ``clean`` are never used,
    7. only the configured submodule path is staged and committed,
    8. ``origin/<branch>`` is pushed only when the caller passes ``push=True``,
       only for the update branch, and never when it already holds a different
       commit, and
    9. a pull request is opened only when the caller passes ``create_pr=True``,
       and an existing pull request for the branch is reused instead of
       duplicated.
    """
    root = Path(project_dir)
    name = "library-update"

    if plan.blocked_reason:
        return CheckResult(
            name, STATUS_BLOCKED, f"the library update was not applied: {plan.blocked_reason}"
        )
    if create_pr and not push:
        # Checked before any repository work: a request the caller cannot
        # satisfy is reported, never silently reduced to a plain local apply.
        return CheckResult(
            name,
            STATUS_BLOCKED,
            f"a pull request can only be opened for a branch that was pushed, so create_pr "
            f"requires push=True. The protected base branch is never pushed, so re-run with "
            "push=True on an update branch.",
        )
    if not _git_is_available():
        return CheckResult(
            name, STATUS_BLOCKED, "Git is unavailable, so nothing was changed."
        )
    if not is_git_worktree(root):
        return CheckResult(
            name, STATUS_BLOCKED, f"{root} is not a Git work tree, so nothing was changed."
        )
    if plan.current_commit == plan.target_commit:
        return CheckResult(
            name,
            STATUS_PASS,
            f"the library submodule is already at {plan.target_commit}; nothing was changed.",
        )

    update_branch = (branch_name or "").strip()
    if not update_branch:
        return CheckResult(
            name, STATUS_BLOCKED, f"an update branch name is required, such as {LIBRARY_UPDATE_BRANCH}."
        )
    base = _default_base_branch(root)
    if update_branch == base or update_branch in PROTECTED_BRANCHES:
        return CheckResult(
            name,
            STATUS_BLOCKED,
            f"refusing to use {update_branch} as the update branch: a base branch is never "
            f"committed to or pushed. Use a branch such as {LIBRARY_UPDATE_BRANCH}.",
        )

    try:
        config = load_project_config(root)
    except ValueError as exc:
        return CheckResult(name, STATUS_BLOCKED, str(exc))
    relative_path = configured_library_path(config)
    library_dir = root / Path(relative_path)

    if not library_dir.is_dir() or not _is_own_git_worktree(library_dir):
        return CheckResult(
            name,
            STATUS_BLOCKED,
            f"the library submodule at {relative_path} is not initialized. Run "
            "'git submodule update --init --recursive'.",
        )
    if not is_clean_worktree(library_dir):
        return CheckResult(
            name,
            STATUS_BLOCKED,
            f"the library submodule at {relative_path} has local changes, so it was left "
            "untouched. Commit or stash them, then run the update again.",
        )
    submodule_commit = _git_output(library_dir, "rev-parse", "HEAD")
    if submodule_commit not in {plan.current_commit, plan.target_commit}:
        return CheckResult(
            name,
            STATUS_BLOCKED,
            f"the library submodule is at {submodule_commit or 'an unknown commit'}, which is "
            f"neither the planned {plan.current_commit} nor the target {plan.target_commit}. "
            "Plan the update again.",
        )
    # The library is checked before the board so a dirty submodule is named
    # precisely instead of being reported as a dirty board, exactly as in
    # ``plan_library_update``.
    if not is_clean_worktree(root):
        return CheckResult(
            name,
            STATUS_BLOCKED,
            f"{root} has uncommitted changes, so it was left untouched. Commit or stash "
            "them, then run the update again.",
        )
    # A plan applied once is stale: the board already records the target, so
    # there is nothing to stage, commit, push, or open a pull request for. This
    # is checked before the branch is touched so a reapply leaves the checkout
    # exactly as it found it instead of reporting a commit it never made.
    if submodule_commit == plan.target_commit and _library_pointer_is_current(
        root, relative_path
    ):
        return CheckResult(
            name,
            STATUS_PASS,
            f"the library submodule is already at {plan.target_commit}, so nothing was "
            f"changed: 0 changed library file(s) and no commit on the planned "
            f"{plan.current_commit} -> {plan.target_commit} update. No branch, push, or "
            "pull request was created.",
        )

    blocked = _select_update_branch(root, update_branch, base)
    if blocked is not None:
        return blocked

    checkout = run_git(library_dir, "checkout", "--detach", plan.target_commit)
    if checkout.returncode != 0:
        return CheckResult(
            name,
            STATUS_BLOCKED,
            f"could not check out library commit {plan.target_commit}: {_git_detail(checkout)}",
        )
    if _git_output(library_dir, "rev-parse", "HEAD") != plan.target_commit:
        return CheckResult(
            name,
            STATUS_BLOCKED,
            f"the library submodule is not at {plan.target_commit} after checkout, so "
            "nothing was committed.",
        )

    added = run_git(root, "add", "--", relative_path)
    if added.returncode != 0:
        return CheckResult(
            name,
            STATUS_BLOCKED,
            f"could not stage {relative_path}: {_git_detail(added)}",
        )
    staged = tuple(_nonempty_lines(run_git(root, "diff", "--cached", "--name-only").stdout))
    if staged and staged != (relative_path,):
        return CheckResult(
            name,
            STATUS_BLOCKED,
            "refusing to commit staged changes outside the library submodule: "
            f"{', '.join(staged)}.",
        )

    if staged:
        message = (commit_message or "").strip()
        if not message:
            return CheckResult(
                name, STATUS_BLOCKED, "a commit message is required to record the update."
            )
        committed = run_git(root, "commit", "-m", message)
        if committed.returncode != 0:
            return CheckResult(
                name,
                STATUS_BLOCKED,
                f"could not commit the library update on {update_branch}: "
                f"{_git_detail(committed)}",
            )
    commit = _git_output(root, "rev-parse", "HEAD")
    if not commit:
        return CheckResult(
            name, STATUS_BLOCKED, f"could not resolve the commit on {update_branch}."
        )

    pull_request_url = ""
    if push:
        published = _publish_update_branch(
            root, update_branch, commit, create_pr, base, plan, relative_path
        )
        if isinstance(published, CheckResult):
            return published
        pull_request_url = published

    summary = (
        f"Updated {relative_path} from {plan.current_commit[:12]} to "
        f"{plan.target_commit[:12]} on branch {update_branch}: "
        f"{len(plan.changed_files)} changed library file(s), commit {commit[:12]}"
    )
    if pull_request_url:
        summary = f"{summary}; pull request {pull_request_url}"
    return CheckResult(name, STATUS_PASS, summary)


def _blocked_plan(reason: str) -> LibraryUpdatePlan:
    """Return a plan that reports only why the update cannot proceed."""
    return LibraryUpdatePlan(
        current_commit="", target_commit="", changed_files=(), blocked_reason=reason
    )


def _nonempty_lines(text: str) -> list[str]:
    """Return the stripped, non-empty lines of command output."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def _git_output(repo_dir: Path, *args: str) -> str:
    """Return a Git command's trimmed stdout, or an empty string on failure."""
    result = run_git(repo_dir, *args)
    return result.stdout.strip() if result.returncode == 0 else ""


def _local_branch_commit(root: Path, branch: str) -> str | None:
    """Return the commit a local branch points at, or None when it is absent."""
    return _git_output(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}") or None


def _library_pointer_is_current(root: Path, relative_path: str) -> bool:
    """Return True when the recorded library pointer already matches the worktree.

    A clean board already implies this, so the staged and unstaged views of the
    submodule path are both confirmed instead of assuming it. The answer decides
    whether an update still has a diff to commit.
    """
    staged = run_git(root, "diff", "--cached", "--name-only", "--", relative_path)
    unstaged = run_git(root, "status", "--porcelain", "--", relative_path)
    return not _nonempty_lines(staged.stdout) and not _nonempty_lines(unstaged.stdout)


def _default_base_branch(root: Path, remote: str = DEFAULT_REMOTE) -> str:
    """Return the branch an update branch should be based on.

    ``origin/HEAD`` is the remote's own record of the default branch. The local
    ``master``/``main`` fallbacks keep a board without a remote usable, and the
    checked-out branch is the last resort so a detached HEAD is never reported as
    a base.
    """
    symbolic = run_git(
        root, "symbolic-ref", "--quiet", "--short", f"refs/remotes/{remote}/HEAD"
    ).stdout.strip()
    prefix = f"{remote}/"
    if symbolic.startswith(prefix):
        return symbolic[len(prefix) :]
    for candidate in (LIBRARY_BRANCH, "main"):
        if _local_branch_commit(root, candidate):
            return candidate
    return current_branch(root) or LIBRARY_BRANCH


def _select_update_branch(root: Path, update_branch: str, base: str) -> CheckResult | None:
    """Create the update branch from ``base`` or switch to a matching one.

    An existing branch is reused only when it was created from the same base
    commit, so a branch that was made from an older base is reported instead of
    being silently extended. Returns a BLOCKED result, or None when the update
    branch is checked out and ready for the commit.
    """
    existing = _local_branch_commit(root, update_branch)
    if existing is None:
        created = run_git(root, "switch", "-c", update_branch, base)
        if created.returncode != 0:
            return CheckResult(
                "library-update",
                STATUS_BLOCKED,
                f"could not create the update branch {update_branch} from {base}: "
                f"{_git_detail(created)}",
            )
        return None

    fork_point = _git_output(root, "merge-base", update_branch, base)
    if fork_point != _git_output(root, "rev-parse", base):
        return CheckResult(
            "library-update",
            STATUS_BLOCKED,
            f"the existing branch {update_branch} was not created from {base}, so it was "
            "not reused. Update or delete that branch, then run the update again.",
        )
    switched = run_git(root, "switch", update_branch)
    if switched.returncode != 0:
        return CheckResult(
            "library-update",
            STATUS_BLOCKED,
            f"could not switch to the update branch {update_branch}: {_git_detail(switched)}",
        )
    return None


def _publish_update_branch(
    root: Path,
    update_branch: str,
    commit: str,
    create_pr: bool,
    base: str,
    plan: LibraryUpdatePlan,
    relative_path: str,
) -> CheckResult | str:
    """Push the update branch and optionally open one pull request.

    Returns the pull request URL, or a BLOCKED result. A pull request is only
    looked up when one is requested, so pushing a branch never depends on the
    GitHub CLI being installed.
    """
    if create_pr and not _gh_is_available():
        return CheckResult(
            "library-update",
            STATUS_BLOCKED,
            f"the local branch {update_branch} was prepared, but {GITHUB_CLI} was not found "
            "on PATH, so nothing was pushed. Install and authenticate the GitHub CLI, or "
            "push the branch yourself and open the pull request in the browser.",
        )

    remote_commit = _remote_branch_commit(root, DEFAULT_REMOTE, update_branch)
    if remote_commit and remote_commit != commit:
        return CheckResult(
            "library-update",
            STATUS_BLOCKED,
            f"{DEFAULT_REMOTE}/{update_branch} already points at {remote_commit}, which is "
            f"not the prepared commit {commit}, so nothing was pushed. Resolve that branch "
            "before running the update again.",
        )
    if not remote_commit:
        pushed = run_git(
            root, "push", DEFAULT_REMOTE, f"refs/heads/{update_branch}:refs/heads/{update_branch}"
        )
        if pushed.returncode != 0:
            return CheckResult(
                "library-update",
                STATUS_BLOCKED,
                f"could not push {update_branch} to {DEFAULT_REMOTE}: {_git_detail(pushed)}. "
                f"The commit is kept on {update_branch} locally.",
            )

    if not create_pr:
        return ""
    existing_url = _existing_pull_request_url(root, update_branch)
    if existing_url:
        return existing_url

    created = _run_gh(
        [
            "pr",
            "create",
            "--base",
            base,
            "--head",
            update_branch,
            "--title",
            LIBRARY_UPDATE_PR_TITLE,
            "--body",
            _library_update_body(plan, relative_path),
        ],
        cwd=root,
        timeout=GITHUB_CLI_TIMEOUT_SECONDS,
    )
    if created.returncode != 0:
        return CheckResult(
            "library-update",
            STATUS_BLOCKED,
            f"{update_branch} was pushed, but {GITHUB_CLI} could not open the pull request: "
            f"{_output_detail(created, f'{GITHUB_CLI} pr create failed')}. The branch is "
            "published; open the pull request from GitHub.",
        )
    return _pull_request_url(created.stdout)


def _remote_branch_commit(root: Path, remote: str, branch: str) -> str | None:
    """Return the commit a remote branch points at, or None when it is absent."""
    result = run_git(root, "ls-remote", "--heads", remote, branch)
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        commit, _, ref = line.partition("\t")
        if ref.strip() == f"refs/heads/{branch}":
            return commit.strip() or None
    return None


def _gh_is_available() -> bool:
    """Return True when the GitHub CLI is on PATH."""
    return shutil.which(GITHUB_CLI) is not None


def _run_gh(
    args: list[str], cwd: Path | None = None, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    """Run one GitHub CLI command without a shell.

    A missing, unusable, or hung CLI is reported as a failed command instead of
    an exception, so every caller can turn it into a BLOCKED result. The caller
    still checks ``_gh_is_available`` first to decide whether a pull request can
    be opened at all.
    """
    if shutil.which(GITHUB_CLI) is None:
        return _failed_gh(args, f"{GITHUB_CLI} was required but was not found on PATH")
    try:
        return subprocess.run(
            [GITHUB_CLI, *args],
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return _failed_gh(
            args, f"{GITHUB_CLI} did not finish within {timeout or 0} seconds"
        )
    except OSError as exc:
        return _failed_gh(args, f"{GITHUB_CLI} could not be started: {exc}")


def _failed_gh(args: list[str], detail: str) -> subprocess.CompletedProcess[str]:
    """Return a failed GitHub CLI result carrying ``detail`` as its error."""
    return subprocess.CompletedProcess(
        args=[GITHUB_CLI, *args], returncode=1, stdout="", stderr=f"{detail}\n"
    )


def _existing_pull_request_url(root: Path, branch: str) -> str | None:
    """Return the URL of an open pull request for ``branch``, if one exists."""
    if not _gh_is_available():
        return None
    result = _run_gh(
        ["pr", "view", branch, "--json", "url"],
        cwd=root,
        timeout=GITHUB_CLI_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None
    url = payload.get("url") if isinstance(payload, Mapping) else None
    return url.strip() if isinstance(url, str) and url.strip() else None


def _library_update_body(plan: LibraryUpdatePlan, relative_path: str) -> str:
    """Return the pull request body describing the library revision change."""
    changed = "\n".join(f"- `{name}`" for name in plan.changed_files) or "- none"
    return (
        f"Updates {relative_path} from {plan.current_commit[:12]} to "
        f"{plan.target_commit[:12]}.\n\n"
        f"Library files changed: {len(plan.changed_files)}\n\n{changed}\n"
    )


def _pull_request_url(stdout: str) -> str:
    """Return the pull request URL from ``gh pr create`` output."""
    for line in reversed(_nonempty_lines(stdout)):
        if line.startswith("http"):
            return line
    return ""


# ---------------------------------------------------------------------------
# Library contribution preparation
# ---------------------------------------------------------------------------


def prepare_library_contribution(
    library_dir: Path,
    component_name: str,
    category: str,
    push: bool = False,
    create_pr: bool = False,
) -> CheckResult:
    """Turn local library changes into a reviewable branch and pull request.

    The order of the steps is the safety contract:

    1. an unusable request is refused before the repository is read, and
       ``create_pr`` without ``push`` is refused rather than silently reduced to
       a local commit, because a pull request needs a published branch,
    2. the library's own metadata linter runs first, so a non-compliant part is a
       FAIL that leaves no branch behind,
    3. ``git status --porcelain`` decides what is contributed, and a change
       outside ``LIBRARY_CONTRIBUTION_PATHS`` is BLOCKED instead of being
       committed, so a contribution can never sweep up unrelated work,
    4. the new ``add-part-<slug>-<timestamp>`` branch is created from the current
       branch and stays checked out; a protected branch is never committed to,
       never pushed, and never re-checked out afterwards,
    5. only the four allowed directories are staged, and the staged set is
       re-read and re-checked before the commit, and
    6. the branch is pushed and the pull request opened only when the caller
       passes ``push=True`` and ``create_pr=True``.

    Nothing here resets, stashes, or discards a developer's work, and no command
    in this flow names a protected branch as a push or commit target.
    """
    library = Path(library_dir)
    name = LIBRARY_CONTRIBUTE_CHECK

    component = (component_name or "").strip()
    part_category = (category or "").strip()
    if not component or not part_category:
        return CheckResult(
            name,
            STATUS_BLOCKED,
            "a part name and a category are both required, for example 'rov library "
            "contribute --name TPS54302 --category Power'.",
        )
    if create_pr and not push:
        return CheckResult(
            name,
            STATUS_BLOCKED,
            "a pull request can only be opened for a branch that was pushed, so "
            "create_pr requires push=True. The protected base branch is never "
            "pushed, so re-run with push=True to open a reviewable pull request.",
        )
    if not _git_is_available():
        return CheckResult(name, STATUS_BLOCKED, "Git is unavailable, so nothing was changed.")
    if not library.is_dir():
        return CheckResult(
            name, STATUS_BLOCKED, f"{library} does not exist, so nothing was changed."
        )
    if not is_git_worktree(library):
        return CheckResult(
            name, STATUS_BLOCKED, f"{library} is not a Git work tree, so nothing was changed."
        )

    linted = _run_contribution_linter(library)
    if linted is not None:
        return linted

    status = run_git(library, "status", "--porcelain")
    if status.returncode != 0:
        return CheckResult(
            name, STATUS_BLOCKED, f"could not read the library status: {_git_detail(status)}"
        )
    changed = _porcelain_paths(status.stdout)
    if not changed:
        return CheckResult(
            name,
            STATUS_BLOCKED,
            f"there are no local changes in {library} to contribute. Import or edit a "
            "part first, then run this command again.",
        )
    outside = [path for path in changed if not _is_contribution_path(path)]
    if outside:
        return CheckResult(
            name,
            STATUS_BLOCKED,
            f"these changes are outside the library directories and were not staged: "
            f"{', '.join(outside)}. A contribution may only stage "
            f"{', '.join(LIBRARY_CONTRIBUTION_PATHS)}. Commit or stash the other "
            "changes yourself, then run this command again.",
        )

    base = current_branch(library)
    if not base:
        return CheckResult(
            name,
            STATUS_BLOCKED,
            "HEAD is detached, so no contribution branch could be created from it. "
            f"Check out {LIBRARY_BRANCH} and run this command again.",
        )
    branch = f"{CONTRIBUTION_BRANCH_PREFIX}{_contribution_slug(component)}-{int(time.time()) % 100000}"
    if branch in PROTECTED_BRANCHES:  # pragma: no cover - the prefix makes this unreachable
        return CheckResult(
            name, STATUS_BLOCKED, f"refusing to use the protected branch {branch}."
        )
    created = run_git(library, "switch", "-c", branch, base)
    if created.returncode != 0:
        return CheckResult(
            name,
            STATUS_BLOCKED,
            f"could not create the contribution branch {branch} from {base}: "
            f"{_git_detail(created)}",
        )

    staged_result = _stage_contribution_paths(library)
    if isinstance(staged_result, CheckResult):
        return staged_result
    staged = staged_result
    if not staged:
        return CheckResult(
            name,
            STATUS_BLOCKED,
            f"no changes under {', '.join(LIBRARY_CONTRIBUTION_PATHS)} were staged on "
            f"{branch}, so nothing was committed.",
        )

    message = f"feat(parts): add {component} to {part_category}"
    committed = run_git(library, "commit", "-m", message)
    if committed.returncode != 0:
        return CheckResult(
            name,
            STATUS_BLOCKED,
            f"could not commit the contribution on {branch}: {_git_detail(committed)}",
        )
    commit = _git_output(library, "rev-parse", "HEAD")
    if not commit:
        return CheckResult(
            name, STATUS_BLOCKED, f"could not resolve the commit on {branch}."
        )

    pull_request_url = ""
    if push:
        published = _publish_contribution_branch(library, branch, commit, message, create_pr)
        if isinstance(published, CheckResult):
            return published
        pull_request_url = published

    summary = (
        f"prepared {branch} from {base} with {len(staged)} library file(s) in commit "
        f"{commit[:12]}: {message}. The protected {base} branch was not changed and "
        f"the local checkout stays on {branch}."
    )
    if pull_request_url:
        summary = f"{summary} Pull request: {pull_request_url}"
    return CheckResult(name, STATUS_PASS, summary)


def _run_contribution_linter(library: Path) -> CheckResult | None:
    """Validate the library's symbol metadata; return a result or None on success.

    The library owns its own linter, so the same script the GUI and
    ``rov library validate`` run is used here. A non-zero exit is a FAIL rather
    than a BLOCKED one: the request is valid, the content is not.
    """
    linter = library / "scripts" / CONTRIBUTION_LINTER_FILE
    if not linter.is_file():
        return CheckResult(
            LIBRARY_CONTRIBUTE_CHECK,
            STATUS_BLOCKED,
            f"{CONTRIBUTION_LINTER_FILE} was not found in {library / 'scripts'}, so "
            "the part metadata was not checked.",
        )
    symbols = library / "Symbols"
    if not symbols.is_dir():
        return CheckResult(
            LIBRARY_CONTRIBUTE_CHECK,
            STATUS_BLOCKED,
            f"{symbols} does not exist, so the part metadata was not checked.",
        )
    try:
        result = subprocess.run(
            [sys.executable, str(linter), str(symbols)],
            cwd=str(library),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=LINTER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            LIBRARY_CONTRIBUTE_CHECK,
            STATUS_BLOCKED,
            f"{CONTRIBUTION_LINTER_FILE} did not finish within {LINTER_TIMEOUT_SECONDS} "
            f"seconds, so {symbols} was not validated and nothing was committed.",
        )
    except OSError as exc:
        return CheckResult(
            LIBRARY_CONTRIBUTE_CHECK,
            STATUS_BLOCKED,
            f"{CONTRIBUTION_LINTER_FILE} could not be run: {exc}",
        )
    if result.returncode == 0:
        return None
    detail = _output_detail(result, f"exit status {result.returncode}")
    return CheckResult(
        LIBRARY_CONTRIBUTE_CHECK,
        STATUS_FAIL,
        f"{CONTRIBUTION_LINTER_FILE} found metadata problems in {symbols} and exited "
        f"with status {result.returncode}: {detail}. Nothing was committed.",
    )


def _porcelain_paths(porcelain: str) -> list[str]:
    """Return the changed paths in ``git status --porcelain`` output.

    Each record is a two-character status code, a space, and a path. A rename
    carries ``old -> new``, and the new path is the one that has to be allowed,
    and a quoted path is unquoted so a space in a part name is compared whole.
    """
    paths: list[str] = []
    for line in porcelain.splitlines():
        if len(line) < 4:
            continue
        entry = line[3:].strip()
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1].strip()
        normalized = _normalize_reported_path(entry)
        if normalized:
            paths.append(normalized)
    return paths


def _name_only_paths(porcelain: str) -> tuple[str, ...]:
    """Return the paths in ``git diff --name-only`` output.

    That output is a bare path per line with no status code, so it is parsed
    separately from ``git status --porcelain`` instead of reusing its offset.
    """
    return tuple(
        normalized
        for line in porcelain.splitlines()
        if (normalized := _normalize_reported_path(line.strip()))
    )


def _normalize_reported_path(entry: str) -> str:
    """Return one Git-reported path as a trimmed, forward-slash relative path."""
    if len(entry) >= 2 and entry.startswith('"') and entry.endswith('"'):
        entry = entry[1:-1]
    entry = entry.replace("\\", "/").strip()
    return entry if entry and entry != "." else ""


def _is_contribution_path(path: str) -> bool:
    """Return True when ``path`` is inside an allowed contribution directory."""
    return path.split("/", 1)[0] in LIBRARY_CONTRIBUTION_PATHS


def _contribution_slug(component_name: str) -> str:
    """Return a branch-safe, lowercase slug for a manufacturer part number."""
    slug = re.sub(r"[^a-z0-9]+", "-", component_name.lower()).strip("-")
    return slug or "part"


def _stage_contribution_paths(library: Path) -> tuple[str, ...] | CheckResult:
    """Stage only the allowed library directories and return the staged paths.

    Directories that do not exist are skipped rather than passed to ``git add``,
    which would fail on an unmatched pathspec, and the staged set is re-read and
    re-checked so a pre-staged file outside the allowed directories can never be
    swept into the commit.
    """
    present = [name for name in LIBRARY_CONTRIBUTION_PATHS if (library / name).is_dir()]
    if not present:
        return CheckResult(
            LIBRARY_CONTRIBUTE_CHECK,
            STATUS_BLOCKED,
            f"none of {', '.join(LIBRARY_CONTRIBUTION_PATHS)} exist in {library}, so "
            "nothing could be staged.",
        )
    staged_result = run_git(library, "add", "--", *present)
    if staged_result.returncode != 0:
        return CheckResult(
            LIBRARY_CONTRIBUTE_CHECK,
            STATUS_BLOCKED,
            f"could not stage {', '.join(present)}: {_git_detail(staged_result)}",
        )
    staged = _name_only_paths(run_git(library, "diff", "--cached", "--name-only").stdout)
    outside = [path for path in staged if not _is_contribution_path(path)]
    if outside:
        return CheckResult(
            LIBRARY_CONTRIBUTE_CHECK,
            STATUS_BLOCKED,
            "refusing to commit staged changes outside the library directories: "
            f"{', '.join(outside)}.",
        )
    return staged


def _publish_contribution_branch(
    library: Path, branch: str, commit: str, message: str, create_pr: bool
) -> CheckResult | str:
    """Push the contribution branch and optionally open one pull request.

    Returns the pull request URL, or a BLOCKED result. The push names the new
    branch explicitly, so a protected branch can never be a target, and a remote
    branch that already holds a different commit is reported instead of being
    overwritten. A pull request is only looked up when one is requested, so
    pushing a branch never depends on the GitHub CLI being installed.
    """
    if create_pr and not _gh_is_available():
        return CheckResult(
            LIBRARY_CONTRIBUTE_CHECK,
            STATUS_BLOCKED,
            f"the local branch {branch} was prepared, but {GITHUB_CLI} was not found on "
            "PATH, so nothing was pushed. Install and authenticate the GitHub CLI, or "
            f"push {branch} yourself and open the pull request in the browser.",
        )

    remote_commit = _remote_branch_commit(library, DEFAULT_REMOTE, branch)
    if remote_commit and remote_commit != commit:
        return CheckResult(
            LIBRARY_CONTRIBUTE_CHECK,
            STATUS_BLOCKED,
            f"{DEFAULT_REMOTE}/{branch} already points at {remote_commit}, which is not "
            f"the prepared commit {commit}, so nothing was pushed. Resolve that branch "
            "before running the contribution again.",
        )
    if not remote_commit:
        pushed = run_git(library, "push", "-u", DEFAULT_REMOTE, branch)
        if pushed.returncode != 0:
            return CheckResult(
                LIBRARY_CONTRIBUTE_CHECK,
                STATUS_BLOCKED,
                f"could not push {branch} to {DEFAULT_REMOTE}: {_git_detail(pushed)}. "
                f"The commit is kept on {branch} locally.",
            )

    if not create_pr:
        return ""
    existing_url = _existing_pull_request_url(library, branch)
    if existing_url:
        return existing_url

    created = _run_gh(
        [
            "pr",
            "create",
            "--base",
            LIBRARY_BRANCH,
            "--head",
            branch,
            "--title",
            message,
            "--body",
            _contribution_body(message),
        ],
        cwd=library,
        timeout=GITHUB_CLI_TIMEOUT_SECONDS,
    )
    if created.returncode != 0:
        return CheckResult(
            LIBRARY_CONTRIBUTE_CHECK,
            STATUS_BLOCKED,
            f"{branch} was pushed, but {GITHUB_CLI} could not open the pull request: "
            f"{_output_detail(created, f'{GITHUB_CLI} pr create failed')}. The branch is "
            "published; open the pull request from GitHub.",
        )
    return _pull_request_url(created.stdout)


def _contribution_body(message: str) -> str:
    """Return the pull request body describing a part contribution."""
    return (
        f"{message}\n\n"
        "Prepared by `rov library contribute`, which validated the symbol metadata and "
        f"staged only {', '.join(LIBRARY_CONTRIBUTION_PATHS)}.\n"
    )


# ---------------------------------------------------------------------------
# Board bootstrap
# ---------------------------------------------------------------------------


def bootstrap_project(project_dir: Path, project_name: str) -> BootstrapResult:
    """Prepare a board directory for KiCad without destroying local work.

    Every step is idempotent and non-destructive:

    * the template design files are renamed only when the destination is free,
    * an existing ``.kicad_pro`` and a customized ``project_name`` are kept,
    * standard library table entries are added without removing custom ones,
    * the library submodule is only fast-forwarded when it is clean, and
    * the pre-commit hook is written to the untracked ``.rov-hooks`` directory.

    ``git checkout``, ``git reset``, ``git stash``, and ``git clean`` are never
    issued. Progress is reported through ``BootstrapResult.messages``, each one
    prefixed with a ``[PASS]``, ``[WARN]``, or ``[BLOCKED]`` status tag.
    """
    root = Path(project_dir)
    name = _sanitize_project_name(project_name)
    changed: list[Path] = []
    messages: list[str] = []

    _rename_template_design_files(root, name, changed, messages)
    config = _ensure_manifest(root, name, changed, messages)
    _sync_library_tables(root, changed, messages)
    _prepare_library_submodule(root, config, messages)
    _install_hook_step(root, changed, messages)

    return BootstrapResult(changed_files=tuple(changed), messages=tuple(messages))


def install_project_hook(project_dir: Path) -> Path:
    """Write the untracked ``.rov-hooks/pre-commit`` hook and select it in Git.

    The hook file is created even when the project is not a Git work tree; only
    the ``core.hooksPath`` configuration is skipped in that case. A failing
    ``git config`` call is not fatal because the hook file is still installed.
    """
    root = Path(project_dir)
    hooks_dir = root / HOOKS_DIR_NAME
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / HOOK_FILE_NAME
    hook_path.write_text(PRE_COMMIT_HOOK_SCRIPT, encoding="utf-8", newline="\n")
    try:
        hook_path.chmod(0o755)
    except OSError:
        pass
    if _git_is_available() and is_git_worktree(root):
        run_git(root, "config", "core.hooksPath", HOOKS_PATH_CONFIG)
    return hook_path


def bootstrap_status(result: BootstrapResult) -> str:
    """Return the worst status tag found in ``result.messages``.

    ``BootstrapResult`` is a frozen Task 1 contract with no status field, so the
    documented ``[PASS]``, ``[WARN]``, ``[FAIL]``, and ``[BLOCKED]`` message
    prefixes are the single source of truth. Precedence matches the CLI exit
    code rule: FAIL, then BLOCKED, then WARN. An empty message tuple is PASS.
    """
    for status in _BOOTSTRAP_STATUS_PRECEDENCE:
        prefix = f"[{status}]"
        if any(message.startswith(prefix) for message in result.messages):
            return status
    return STATUS_PASS


def worst_status(results: Iterable[CheckResult]) -> str:
    """Return the most severe status in ``results`` using the shared precedence.

    FAIL beats BLOCKED, BLOCKED beats WARN, and WARN beats PASS. An empty
    sequence is PASS. Every interface maps a status set to one decision through
    this helper so the CLI, the GUI, and CI cannot disagree.
    """
    statuses = {result.status for result in results}
    for status in _BOOTSTRAP_STATUS_PRECEDENCE:
        if status in statuses:
            return status
    return STATUS_PASS


def bootstrap_results(result: BootstrapResult) -> list[CheckResult]:
    """Convert bootstrap messages into results, keeping each message status.

    The message prefixes are the contract, so a ``[BLOCKED]`` step can never be
    reported to a caller as a pass. The prefix is removed from the message text
    because the status is rendered separately.
    """
    return [
        CheckResult("bootstrap", *_split_status_prefix(message)) for message in result.messages
    ]


def _split_status_prefix(message: str) -> tuple[str, str]:
    """Split a ``[STATUS] text`` bootstrap message into its status and text."""
    match = _STATUS_PREFIX_PATTERN.match(message)
    if match is None:
        return STATUS_PASS, message
    return match.group(1), match.group(2)


def default_project_config(project_name: str) -> dict[str, object]:
    """Return the starter manifest content written for a new board."""
    return {
        "schema": SCHEMA_VERSION,
        "project_name": project_name,
        "kicad_version": KICAD_BASELINE,
        "platform_ref": LIBRARY_BRANCH,
        "library": {
            "path": LIBRARY_SUBMODULE_PATH,
            "branch": LIBRARY_BRANCH,
            "update_policy": LIBRARY_UPDATE_POLICY,
        },
        "ci_profile": CI_PROFILE_STANDARD,
    }


def _sanitize_project_name(project_name: str) -> str:
    """Return a filename-safe project stem, rejecting empty and pathed names."""
    raw = (project_name or "").strip()
    if not raw:
        raise ValueError("project_name must not be empty")
    if "/" in raw or "\\" in raw:
        raise ValueError(f"project_name must not contain path separators: {project_name!r}")
    if raw in {".", ".."}:
        raise ValueError(f"project_name must not be a relative path segment: {project_name!r}")
    stem = _UNSAFE_STEM_PATTERN.sub("-", raw).strip().rstrip(". ")
    if not stem:
        raise ValueError(f"project_name has no filename-safe characters: {project_name!r}")
    return stem


def _note(status: str, text: str) -> str:
    """Return one status-prefixed bootstrap message."""
    return f"[{status}] {text}"


def _write_json(path: Path, data: object) -> None:
    """Write ``data`` as UTF-8 JSON with the repository's two-space style."""
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _output_detail(result: subprocess.CompletedProcess[str], fallback: str) -> str:
    """Return the last non-empty line of a failed command's output."""
    for stream in (result.stderr, result.stdout):
        lines = [line.strip() for line in stream.splitlines() if line.strip()]
        if lines:
            return lines[-1]
    return fallback


def _git_detail(result: subprocess.CompletedProcess[str]) -> str:
    """Return a single-line summary of a failed Git command."""
    return _output_detail(result, f"git exited with status {result.returncode}")


def _git_is_available() -> bool:
    """Return True when a Git executable is on PATH."""
    return shutil.which("git") is not None


def _is_own_git_worktree(repo_dir: Path) -> bool:
    """Return True only when ``repo_dir`` is the top level of its own work tree.

    ``git rev-parse --is-inside-work-tree`` also succeeds for a plain
    subdirectory of a parent repository, so an uninitialized submodule directory
    would otherwise look like a submodule checkout. The reported top level is
    compared instead.
    """
    result = run_git(repo_dir, "rev-parse", "--show-toplevel")
    top_level = result.stdout.strip()
    if result.returncode != 0 or not top_level:
        return False
    return Path(top_level).resolve() == Path(repo_dir).resolve()


def _rename_template_design_files(
    root: Path, name: str, changed: list[Path], messages: list[str]
) -> None:
    """Rename the template design files, never replacing an existing file.

    A destination whose source is already gone is a board that was bootstrapped
    before, so it is reported as PASS rather than as a conflict. Only a genuine
    source-and-destination pair, which means the name was reused by hand, is a
    WARN.
    """
    for extension in PROJECT_FILE_EXTENSIONS:
        source = root / f"{TEMPLATE_STEM}{extension}"
        destination = root / f"{name}{extension}"
        source_exists = source.is_file()
        destination_exists = destination.exists()
        if source_exists and destination_exists:
            messages.append(
                _note(STATUS_WARN, f"{destination.name} already exists; not overwritten.")
            )
            continue
        if not source_exists:
            if destination_exists:
                messages.append(
                    _note(STATUS_PASS, f"{destination.name} is already bootstrapped.")
                )
            continue
        try:
            source.replace(destination)
        except OSError as exc:
            messages.append(
                _note(STATUS_BLOCKED, f"Could not rename {source.name} to {destination.name}: {exc}")
            )
            continue
        changed.append(destination)
        messages.append(_note(STATUS_PASS, f"Renamed {source.name} to {destination.name}."))
        if extension == ".kicad_pro":
            _set_project_title(destination, name, changed, messages)


def _set_project_title(
    project_path: Path, name: str, changed: list[Path], messages: list[str]
) -> None:
    """Update only the ``project.title`` value of a freshly renamed project file.

    The file is parsed as JSON so unrelated keys survive; an unreadable or
    non-JSON project file is reported and left alone.
    """
    raw = _read_text_or_none(project_path)
    if raw is None:
        messages.append(
            _note(STATUS_WARN, f"Could not read {project_path.name}; its title was left unchanged.")
        )
        return
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        messages.append(
            _note(STATUS_WARN, f"{project_path.name} is not JSON; its title was left unchanged. {exc}")
        )
        return
    project = data.get("project") if isinstance(data, dict) else None
    if not isinstance(project, dict) or PROJECT_TITLE_KEY not in project:
        return
    if project[PROJECT_TITLE_KEY] == name:
        return
    project[PROJECT_TITLE_KEY] = name
    try:
        _write_json(project_path, data)
    except OSError as exc:
        messages.append(
            _note(STATUS_BLOCKED, f"Could not update the title in {project_path.name}: {exc}")
        )
        return
    if project_path not in changed:
        changed.append(project_path)
    messages.append(_note(STATUS_PASS, f"Set the {project_path.name} project title to {name!r}."))


def _ensure_manifest(
    root: Path, name: str, changed: list[Path], messages: list[str]
) -> dict[str, object]:
    """Create the board manifest, or adopt the starter ``board-template`` name."""
    config_path = root / PROJECT_CONFIG_NAME
    raw = _read_text_or_none(config_path)
    if raw is None:
        config = default_project_config(name)
        _write_json(config_path, config)
        changed.append(config_path)
        messages.append(_note(STATUS_PASS, f"Created {PROJECT_CONFIG_NAME} for project {name!r}."))
        return config

    try:
        config = json.loads(raw)
    except json.JSONDecodeError as exc:
        messages.append(
            _note(STATUS_BLOCKED, f"{PROJECT_CONFIG_NAME} is not valid JSON and was left unchanged. {exc}")
        )
        return default_project_config(name)
    if not isinstance(config, dict):
        messages.append(
            _note(STATUS_BLOCKED, f"{PROJECT_CONFIG_NAME} is not a JSON object and was left unchanged.")
        )
        return default_project_config(name)

    current = config.get("project_name")
    if current == name:
        return config
    if current != TEMPLATE_STEM:
        messages.append(
            _note(
                STATUS_WARN,
                f"{PROJECT_CONFIG_NAME} already sets project_name {current!r}; it was preserved.",
            )
        )
        return config

    config["project_name"] = name
    try:
        _write_json(config_path, config)
    except OSError as exc:
        messages.append(
            _note(STATUS_BLOCKED, f"Could not update {PROJECT_CONFIG_NAME}: {exc}")
        )
        return config
    changed.append(config_path)
    messages.append(_note(STATUS_PASS, f"Updated {PROJECT_CONFIG_NAME} project_name to {name!r}."))
    return config


def _sync_library_tables(root: Path, changed: list[Path], messages: list[str]) -> None:
    """Add missing standard library table entries through ``sync_project_libs``."""
    try:
        import sync_project_libs
    except ImportError as exc:
        messages.append(
            _note(STATUS_WARN, f"Library table sync is unavailable: {exc}")
        )
        return

    try:
        result = sync_project_libs.sync_project(root)
    except OSError as exc:
        messages.append(_note(STATUS_BLOCKED, f"Could not update the KiCad library tables: {exc}"))
        return

    for table_name, changed_key, added_key in (
        ("sym-lib-table", "sym_changed", "sym_added"),
        ("fp-lib-table", "fp_changed", "fp_added"),
    ):
        if not result.get(changed_key):
            continue
        changed.append(root / table_name)
        added = ", ".join(result.get(added_key) or []) or "none"
        messages.append(
            _note(STATUS_PASS, f"Added standard library entries to {table_name}: {added}.")
        )
    if result.get("gitmodules_changed"):
        changed.append(root / ".gitmodules")
        messages.append(
            _note(
                STATUS_PASS,
                f"Recorded the {LIBRARY_BRANCH} branch for the library submodule in .gitmodules.",
            )
        )


def _prepare_library_submodule(
    root: Path, config: Mapping[str, object], messages: list[str]
) -> None:
    """Initialize or fast-forward the library submodule without discarding work.

    The declaration of the submodule path is read through the same
    ``_declared_submodule_paths`` helper that ``check_submodule`` uses, so
    validation and bootstrap can never disagree about what ``.gitmodules``
    declares. The statuses differ only because of the role: ``check_submodule``
    reports a FAIL for the caller of a validate command, while bootstrap must
    leave an undeclared directory completely untouched and says BLOCKED.
    """
    relative_path, branch = _library_target(config)

    if not _git_is_available():
        messages.append(
            _note(
                STATUS_WARN,
                f"Git is unavailable, so the library submodule at {relative_path} was left untouched.",
            )
        )
        return

    if relative_path not in _declared_submodule_paths(root):
        messages.append(
            _note(
                STATUS_BLOCKED,
                f"The library submodule path {relative_path} is not declared in .gitmodules, so it "
                f"was left untouched. Add it with 'git submodule add <url> {relative_path}'.",
            )
        )
        return

    library_dir = root / Path(relative_path)
    if not library_dir.is_dir():
        if not _is_own_git_worktree(root):
            messages.append(
                _note(
                    STATUS_WARN,
                    f"The library submodule at {relative_path} is missing and {root} is not a "
                    "Git work tree, so nothing was initialized.",
                )
            )
            return
        result = run_git(root, "submodule", "update", "--init", "--recursive")
        if result.returncode == 0:
            messages.append(_note(STATUS_PASS, f"Initialized the library submodule at {relative_path}."))
        else:
            messages.append(
                _note(
                    STATUS_BLOCKED,
                    f"Could not initialize the library submodule at {relative_path}: "
                    f"{_git_detail(result)}",
                )
            )
        return

    if not _is_own_git_worktree(library_dir):
        messages.append(
            _note(
                STATUS_WARN,
                f"The library submodule at {relative_path} is not an initialized Git work "
                "tree, so it was left untouched. Run 'git submodule update --init --recursive'.",
            )
        )
        return

    checked_out = current_branch(library_dir)
    if checked_out != branch:
        where = f"branch {checked_out!r}" if checked_out else "a detached HEAD"
        messages.append(
            _note(
                STATUS_WARN,
                f"The library submodule at {relative_path} is on {where} instead of {branch!r}, "
                "so it was left untouched. Check out the library branch and run bootstrap again.",
            )
        )
        return

    if not is_clean_worktree(library_dir):
        messages.append(
            _note(
                STATUS_BLOCKED,
                f"The library submodule at {relative_path} has local changes and was left "
                "untouched. Commit or stash them, then run bootstrap again.",
            )
        )
        return

    fetch = run_git(library_dir, "fetch", "origin", branch)
    if fetch.returncode != 0:
        messages.append(
            _note(
                STATUS_WARN,
                f"Could not reach origin/{branch} for the library submodule at {relative_path} "
                f"({_git_detail(fetch)}); the cached revision was kept and the library may be stale.",
            )
        )
        return

    merge = run_git(library_dir, "merge", "--ff-only", f"origin/{branch}")
    if merge.returncode != 0:
        messages.append(
            _note(
                STATUS_WARN,
                f"The library submodule at {relative_path} could not be fast-forwarded to "
                f"origin/{branch} ({_git_detail(merge)}); the cached revision was kept and the "
                "library may be stale.",
            )
        )
        return

    if merge.stdout.strip() == "Already up to date.":
        messages.append(
            _note(STATUS_PASS, f"The library submodule at {relative_path} already matches origin/{branch}.")
        )
    else:
        messages.append(
            _note(STATUS_PASS, f"Fast-forwarded the library submodule at {relative_path} to origin/{branch}.")
        )


def _library_target(config: Mapping[str, object]) -> tuple[str, str]:
    """Return the configured library submodule path and branch, with defaults."""
    relative_path = LIBRARY_SUBMODULE_PATH
    branch = LIBRARY_BRANCH
    library = config.get("library") if isinstance(config, Mapping) else None
    if isinstance(library, Mapping):
        configured_path = library.get("path")
        if isinstance(configured_path, str) and configured_path.strip():
            relative_path = normalize_relative_path(configured_path)
        configured_branch = library.get("branch")
        if isinstance(configured_branch, str) and configured_branch.strip():
            branch = configured_branch.strip()
    return relative_path, branch


def configured_library_path(config: Mapping[str, object] | None) -> str:
    """Return the library submodule path a manifest selects, or the default.

    Validation, bootstrap, and the CLI all read the path through this helper so
    they can never resolve a different directory for the same manifest.
    """
    return _library_target(config)[0]


def normalize_relative_path(value: str) -> str:
    """Return a trimmed, forward-slash relative path for a configured path.

    Public because the CLI compares a caller-supplied library path against the
    manifest value, and both sides must normalize identically on every platform.
    """
    return value.strip().replace("\\", "/")


def _install_hook_step(root: Path, changed: list[Path], messages: list[str]) -> None:
    """Install the untracked pre-commit hook and report the resulting state."""
    hook_path = root / HOOKS_DIR_NAME / HOOK_FILE_NAME
    previous = _read_text_or_none(hook_path)
    installed = install_project_hook(root)
    if _read_text_or_none(installed) != previous:
        changed.append(installed)
        messages.append(_note(STATUS_PASS, f"Installed the untracked {HOOKS_DIR_NAME} hook."))
    if not _git_is_available() or not is_git_worktree(root):
        return
    configured = run_git(root, "config", "--get", "core.hooksPath").stdout.strip()
    if configured != HOOKS_PATH_CONFIG:
        shown = configured or "unset"
        messages.append(
            _note(
                STATUS_BLOCKED,
                f"core.hooksPath is {shown} instead of {HOOKS_PATH_CONFIG}. "
                f"Run 'git config core.hooksPath {HOOKS_PATH_CONFIG}'.",
            )
        )
