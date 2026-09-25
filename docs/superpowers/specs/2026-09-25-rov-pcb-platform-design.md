# ROV PCB Platform Design

**Date:** 2026-09-25  
**Status:** Draft for user review  
**Scope:** `KiCad/DevOps`, `KiCad/Board_Template`, `KiCad/Libraries`, and the board repositories that consume them

## 1. Purpose

Create a simple, maintainable PCB engineering workflow for a school club. The workflow must make it easy to:

- Start a new board from the existing GitHub board template.
- Use one approved Purdue ROV KiCad standard library.
- Open a board through the familiar `LAUNCH_KICAD` entry point.
- Import, edit, validate, and contribute library parts through either a CLI or the existing Library Manager GUI.
- Run the same essential validation locally and in GitHub Actions.
- Detect and update to the latest approved library revision through reviewable update pull requests.
- Diagnose missing tools on Windows, Linux, and macOS without silently installing software.

This design intentionally optimizes for a small club. It avoids a large framework, plugin system, dashboard, database, or custom repository-creation service.

## 2. Confirmed Decisions

- Keep `KiCad/DevOps`, `KiCad/Board_Template`, `KiCad/Libraries`, and individual boards as separate repositories.
- Use GitHub's **Use this template** action as the primary board-creation entry point.
- Add a small bootstrap command after the repository is created.
- Keep `LAUNCH_KICAD.bat` and `LAUNCH_KICAD.sh` as the normal board launchers.
- Keep the existing Library Manager GUI and its familiar import/search/edit workflow.
- Make the CLI and GUI call the same implementation.
- Support Windows, Linux, and macOS.
- Support the current club KiCad baseline only. The current template files identify KiCad 10.x; the system will not maintain a multi-major-version matrix.
- Treat the central ROV library's protected `master` branch as the approved library source. The system does not require release tags for normal board updates.
- Use safe hybrid automation: fetch and prepare automatically where safe, but require confirmation for local destructive changes and use reviewable branches/PRs for publishing.
- Environment bootstrap diagnoses and guides; it does not install software without an explicit user action.

## 3. Architecture

### 3.1 `KiCad/DevOps`: shared platform

`KiCad/DevOps` owns the common implementation and reusable workflows. Keep the existing script-oriented layout rather than introducing a large application framework.

```text
KiCad/DevOps/
├── scripts/
│   ├── rov.py                 # main cross-platform CLI
│   ├── rov_core.py            # shared CLI/GUI operations
│   ├── linter_validator.py
│   ├── existing validation and artifact scripts
│   └── existing platform launch helpers
├── tests/
├── .github/workflows/
│   ├── run-kicad-ci.yml
│   ├── update-library.yml
│   └── existing validation workflows
├── kibot_master.yaml
└── README.md
```

The existing GUI remains at `KiCad/Libraries/scripts/library_manager_gui.py`. It is a front end to the shared `rov_core.py` implementation, not a second implementation.

The initial command surface is:

```text
rov doctor
rov board bootstrap
rov board validate
rov board sync-library
rov library list
rov library search
rov library import
rov library validate
rov library build
rov library contribute
rov library gui
```

The canonical invocation is `python scripts/rov.py <command>`, so no package installation is required. Platform wrappers may be added later if they improve usability.

`rov_core.py` is the only implementation of shared operations. The CLI and GUI must not independently implement Git synchronization, validation, metadata rules, or branch preparation.

### 3.2 `KiCad/Libraries`: approved standard library

The library repository remains the source of truth for:

- Symbols.
- Footprints.
- 3D models.
- Design blocks.
- Manufacturer, distributor, lifecycle, and sourcing metadata.

Per-part source files are canonical. The six category libraries (`rov_passives`, `rov_power`, `rov_logic`, `rov_connectors`, `rov_sensors`, and `rov_mech`) are generated, validated, and committed so existing KiCad library tables continue to work.

No new library database or index is required in the first version. The existing per-part files, generated category libraries, and required metadata are the authoritative records.

Library changes enter through pull requests to the protected `master` branch. The branch must pass required validation before it is considered approved.

### 3.3 `KiCad/Board_Template`: minimal starting project

The template remains a normal GitHub template repository. It contains the starter KiCad project, design rules, standard library tables, hooks, a small project configuration, and a bootstrap entry point. It does not contain copied test suites or duplicate platform logic.

A new board uses this flow:

```text
GitHub Use this template
        -> clone
        -> python bootstrap.py
        -> configure name, links, hooks, and submodule
        -> fast validation
        -> open with LAUNCH_KICAD
```

`bootstrap.py` detects the project name, updates starter project files where needed, verifies the standard library tables, initializes the submodule, configures hooks, checks required tools, and runs fast validation. It must not overwrite modified design files without confirmation.

### 3.4 Board repositories: thin consumers

A board contains its design files, board-specific rules, a submodule pointer, a small `rov.project.json`, and a minimal reusable-workflow call.

The initial configuration shape is:

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

The Git submodule pointer is the exact library revision used by a board. No additional lock file is required initially.

## 4. User Workflows

### 4.1 New board

1. A member creates a repository with GitHub's **Use this template** action.
2. The member clones the repository and runs `python bootstrap.py`.
3. Bootstrap configures the project and verifies the standard library.
4. The member runs `LAUNCH_KICAD` to reopen the project through the shared setup path.

### 4.2 Local validation

`rov board validate` runs fast checks:

- Board configuration validity.
- Required project files.
- Standard library table entries.
- Submodule state.
- Conflict markers and unsafe generated files.
- Symbol metadata and library links.

`rov board validate full` additionally runs the current KiCad validation and manufacturing-export path. Missing Docker, KiCad, or KiBot dependencies produce actionable instructions rather than being silently treated as success.

### 4.3 Library synchronization

When the protected library `master` branch changes:

1. GitHub Actions or a scheduled/manual check detects the new approved commit.
2. The board updater checks out the board and updates the library submodule.
3. It runs the board's required validation.
4. It creates a `chore/library-update` branch and pull request.
5. The pull request is merged manually by default. A board may explicitly opt into auto-merge for library-update pull requests only after required checks pass.

`rov board sync-library` provides the local equivalent. It requires a clean working tree, shows the target commit and changed library files, asks for confirmation, updates the submodule, validates the result, and offers to prepare a branch and PR.

### 4.4 Library contribution

The Library Manager GUI and the CLI both support the same operation:

1. Import or select a part.
2. Choose its category.
3. Normalize symbol, footprint, and 3D-model links.
4. Validate required metadata and file structure.
5. Show a diff and validation report.
6. Create a feature branch.
7. Commit only relevant library files.
8. Push the branch and open a PR after explicit confirmation.

Neither interface pushes directly to the protected library branch.

### 4.5 Environment diagnosis

`rov doctor` checks:

- Git and GitHub CLI authentication.
- KiCad and `kicad-cli`.
- Python and required standard-library modules.
- Docker when the full validation path needs it.
- Submodule state.
- Project files and library tables.
- Output-directory permissions and available disk space.

It returns platform-specific guidance for Windows, Linux, and macOS. Installation is a separate explicit action and is not part of the default diagnosis path.

## 5. Safety and Failure Handling

- Never reset, stash, or overwrite a developer's board working tree automatically.
- Treat disposable tool caches differently from user repositories; caches may be refreshed, but user worktrees may not.
- Check repository state before any update, commit, push, or PR action.
- Stop on conflicts and show the exact state that needs attention.
- Never claim an offline library is current; label it as cached or stale.
- Keep protected branches protected.
- Use least-privilege GitHub Actions permissions.
- Restrict CI write access to explicitly configured update branches.
- Use concurrency controls to avoid duplicate library-update jobs.
- Do not log secrets or write API credentials to disk.
- Return clear `PASS`, `WARN`, `FAIL`, and `BLOCKED` states.

## 6. CI and Testing

### Cross-platform fast checks

Run on Windows, Linux, and macOS:

- Python compilation and unit tests.
- Configuration and schema tests.
- Library metadata and path tests.
- Mocked Git state and conflict tests.
- CLI/shared-core tests.
- Launcher and bootstrap wrapper tests.

### Linux hardware checks

Run the current KiCad 10.x baseline in Linux CI:

- ERC.
- DRC.
- Zone-fill checks.
- Symbol-library validation.
- Footprint and 3D-link validation.
- KiBot/manufacturing export smoke test.
- Template bootstrap test.
- One representative board validation.

A multi-major KiCad matrix and a full board matrix are intentionally out of scope for the first version.

## 7. Rollout

### Phase 1: Shared entry points

- Add the shared `rov.py`/`rov_core.py` implementation.
- Add `doctor`, `bootstrap`, and fast validation.
- Make `LAUNCH_KICAD` and the Library Manager call the shared implementation.
- Preserve offline fallback behavior.

### Phase 2: Validation consolidation

- Make local and CI checks use the same core validation.
- Remove copied linter/configuration files only after board repositories are verified.
- Improve actionable errors and summaries.

### Phase 3: Library contribution safety

- Move import, metadata, build, and validation behavior into shared functions.
- Add branch/PR preparation.
- Remove direct protected-branch push behavior from the GUI.
- Add integration tests around library changes.

### Phase 4: Automated board updates

- Add library-update notification and scheduled/manual fallback triggers.
- Replace direct board-branch pushes with validated update pull requests.
- Add optional, explicit auto-merge support.

### Phase 5: Documentation and cleanup

- Update the template README with one short quickstart.
- Document `rov doctor`, `rov board bootstrap`, validation, synchronization, and library contribution.
- Remove duplicate files only after checking all board repositories.

## 8. Acceptance Criteria

The design is complete when a new member can:

1. Create a board repository from the GitHub template.
2. Run one bootstrap command on Windows, Linux, or macOS.
3. Open it with `LAUNCH_KICAD`.
4. Import and validate a library part through either the CLI or Library Manager.
5. Run equivalent fast validation locally and in CI.
6. Receive the latest approved library revision through a reviewable update PR.
7. Understand what changed, what failed, and what action is required.

The implementation must not require a large framework, a database, a custom board-creation service, or silent changes to protected branches.

## 9. Non-Goals

- A monorepo migration.
- A hosted team dashboard.
- Automatic software installation.
- A multi-major KiCad compatibility matrix.
- Unreviewed direct pushes to protected library or board branches. Explicitly opted-in auto-merge of validated library-update pull requests is allowed.
- A replacement for GitHub's template mechanism.
- A replacement for the existing Library Manager or `LAUNCH_KICAD` entry points.
