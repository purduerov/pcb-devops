"""
Shared contracts for the ROV PCB platform.

This module is the single source of truth for the values and checks that the
`rov.py` CLI, the bootstrap flow, the board template, and the Library Manager
GUI all depend on. It intentionally has no third-party dependencies and never
performs destructive Git operations.
"""

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
LIBRARY_UPDATE_POLICY = "pull-request"
SCHEMA_VERSION = 1
LIBRARY_SUBMODULE_PATH = "libs/purdue-rov-kicad-lib"

STANDARD_LIBS = [
    {
        "name": name,
        "sym_uri": f"${{KIPRJMOD}}/{LIBRARY_SUBMODULE_PATH}/Symbols/{name}.kicad_sym",
        "fp_uri": f"${{KIPRJMOD}}/{LIBRARY_SUBMODULE_PATH}/Footprints/{name}.pretty",
        "sym_descr": f"Purdue ROV {name} Symbols",
        "fp_descr": f"Purdue ROV {name} Footprints",
    }
    for name in (
        "rov_passives",
        "rov_power",
        "rov_logic",
        "rov_connectors",
        "rov_sensors",
        "rov_mech",
    )
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

    relative_path = configured_path.strip().replace("\\", "/")
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
