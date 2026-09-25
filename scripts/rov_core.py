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

# Unresolved Git conflict markers are matched exactly like the CI grep: anchored
# to the start of a line, so legitimate runs of '=' inside design data are not
# reported. The checked file set is the same one CI scans.
CONFLICT_MARKER_PATTERN = re.compile(r"^(<{7}|={7}|>{7})", re.MULTILINE)
CONFLICT_CHECK_PATTERNS = ("*.kicad_sch", "*.kicad_pcb", "*.kicad_pro", "*-lib-table")

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
    repo_dir: Path, *args: str, check: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run a non-shell Git command in ``repo_dir``."""
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


def _git_detail(result: subprocess.CompletedProcess[str]) -> str:
    """Return a single-line summary of a failed Git command."""
    for stream in (result.stderr, result.stdout):
        lines = [line.strip() for line in stream.splitlines() if line.strip()]
        if lines:
            return lines[-1]
    return f"git exited with status {result.returncode}"


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
