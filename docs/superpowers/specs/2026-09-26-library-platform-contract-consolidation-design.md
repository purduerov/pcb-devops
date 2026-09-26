# ROV PCB Platform Contract Consolidation Design

Date: 2026-09-26
Status: Approved design, not implemented

## Outcome

Consolidate the ROV PCB platform so behavior lives in one place per concern, while keeping every existing CLI, GUI, workflow, exit code, and machine-readable marker unchanged.

This design covers `KiCad/DevOps`, `KiCad/Libraries`, `KiCad/Board_Template`, and board consumers. It does not change embedded firmware, Surface, or Core.

## Architecture and components

- `KiCad/DevOps/scripts/rov.py` and `scripts/rov_core.py` remain the sole invocation and policy layer for:
  - board bootstrap and validation;
  - library validation, build, sync, contribution, and GUI launch delegation;
  - Git safety rules, exit codes, and `PR_URL`/`NO_CHANGE` markers.
- `KiCad/Libraries` keeps user-facing behavior:
  - `scripts/library_manager_gui.py` keeps dialogs, search, editing, import UX, threading, and reporting.
  - `scripts/rov_bridge.py` remains the only seam to the shared CLI.
  - `scripts/import_part.py` keeps parsing, prompting, and contribution-hint behavior.
  - Direct parser and file operations in `kicad_sym_utils.py` and per-part library handling stay local.
- Validation, build, sync, and contribution actions invoked from the GUI or importer go through `rov_bridge` to the shared CLI.
- Dead one-shot migration scripts and local-only artifacts are quarantined or removed; live tests and contracts stay green.

## Data flow

1. A GUI or importer action calls `rov_bridge`.
2. The bridge resolves the DevOps checkout and runs `rov` with a bounded timeout.
3. The CLI returns `PASS`, `WARN`, `FAIL`, or `BLOCKED`, plus `PR_URL` or `NO_CHANGE` where applicable.
4. The GUI marshals results to the main thread for dialogs and browser actions.
5. Local parser and file edits remain in `KiCad/Libraries`; validation of those edits goes through the shared CLI.

## Error handling

- Preserve exit codes: `0` for pass or warning-only success, `1` for failure, `2` for blocked.
- Use `BLOCKED` for dirty worktrees, missing tools or checkouts, unreachable remotes, timeouts, and unsafe operations.
- Never report false success for offline, missing, or unvalidated states.
- GUI work runs off the main thread where remote or long operations are possible; user feedback returns on the main thread.
- No direct commits or pushes to protected branches.
- Fail closed with actionable recovery messages.

## Testing

- Keep existing DevOps and Libraries suites passing.
- Retain or extend coverage for:
  - CLI delegation from GUI and importer paths;
  - bridge checkout resolution order;
  - worker-thread behavior and bounded timeouts;
  - exit codes and machine-readable markers;
  - workflow contracts and README automation parity.
- Tests must not use real network access, pushes, submodule initialization, or destructive repository operations.
- Verify CLI and GUI behavior remain equivalent after consolidation.

## Keep, remove, and refactor inventory

Keep:
- `DevOps/scripts/rov.py`, `rov_core.py`, `linter_validator.py`, `sync_project_libs.py`, launchers, workflows, `kibot_master.yaml`, and tests.
- `Libraries/scripts/library_manager_gui.py`, `rov_bridge.py`, `import_part.py`, `kicad_sym_utils.py`, `build_symbol_libs.py` category generation, `linter_validator.py` as the lint implementation invoked through the CLI and retained for offline use, `test_e2e_flow.py`, `dependency_check.py`, tests, launchers, and library-table examples.
- Board manifests, reusable workflow wrappers, launchers, hooks, library tables, and board-local design content.

Remove or quarantine, subject to owner approval where teammate-authored or shared history is involved:
- Obsolete one-shot library migration scripts.
- Local-only benchmark and disposable artifacts.
- Untracked `__pycache__` outputs.
- Stale template-sync tooling that predates the manifest and platform-ref contract.

Refactor without behavior change:
- Route GUI and importer validation through `rov_bridge`.
- Unify duplicate linter resolver behavior.
- Use one source for category naming.
- Reduce shell-script duplication behind `rov.py`.
- Synchronize README and CONTRIBUTING command paths and naming.
- Propagate template bootstrap and launcher consistency to boards through normal template-sync pull requests.

## Contracts that must not break

- CLI command names, flags, exit codes, and `PR_URL`/`NO_CHANGE` output.
- Reusable workflow names, inputs, outputs, permissions, and pins.
- `rov.project.json` schema and key names.
- Board cache, hook, branch, commit-message, library-table, and submodule-path contracts.
- Existing protected-branch and reviewable-PR safety behavior.
