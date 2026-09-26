# Purdue ROV PCB DevOps

Shared tooling and CI/CD for Purdue ROV hardware. This repository owns the `rov`
command-line tool, the KiBot export profile, the symbol linter, distributor
sourcing checks, and the reusable GitHub Actions workflows that every board
repository calls.

There is nothing to install. The canonical invocation is:

```text
python scripts/rov.py <command>
```

Requires Python 3.10 or newer. Windows may need `py -3` in place of `python`.

## Quickstart

Board commands run **from a board repository root**:

```text
python <devops>/scripts/rov.py doctor
python <devops>/scripts/rov.py board validate
python <devops>/scripts/rov.py board validate --full
python <devops>/scripts/rov.py board sync-library
```

Library commands run **from the `Libraries` repository root**, or from anywhere
with an explicit `--library-dir`:

```text
python <devops>/scripts/rov.py library validate
python <devops>/scripts/rov.py library build
python <devops>/scripts/rov.py library list
python <devops>/scripts/rov.py library search <query>
python <devops>/scripts/rov.py library sync
python <devops>/scripts/rov.py library gui
```

`--library-dir` defaults to the current directory, so the directory you run
from matters:

- **`library sync` must never be run from a board repository.** It fetches and
  fast-forwards whatever directory it is given, so from a board root it would
  try to fast-forward the board's own branch. Run it from the library checkout,
  or pass `--library-dir libs/purdue-rov-kicad-lib` explicitly.
- The other `library` commands are read-only or GUI launches, but they still
  resolve the library from the current directory, so run them from the library
  or pass `--library-dir`.
- A board that vendored the platform tools can drop the `<devops>/scripts/`
  prefix and call `python .pcb-devops-cache/scripts/rov.py <command>`.

Start with `doctor`. It reports the tools and board state actually in use and
tells you what to install yourself; it never installs anything for you.

## Commands

### `rov doctor`

Reports Git, Python, GitHub CLI, `kicad-cli`, Docker, the board manifest, and
submodule state. A missing optional tool is a `WARN`, not a failure; a missing Git
is the only `FAIL`. Run outside a board, the two board checks report `WARN`
because there is no `rov.project.json` to read.

### `rov board bootstrap`

Prepares a board directory for KiCad: renames the starter `board-template.kicad_*`
files, writes `rov.project.json`, fills in the standard library tables, installs
the untracked `.rov-hooks/` pre-commit hook, and fast-forwards the library
submodule when it is safe to do so. It never runs `git reset`, `git stash`,
`git checkout -B`, or `git clean`, and it will not overwrite a design file you
have already modified.

`LAUNCH_KICAD` calls this for you, so most members never run it directly.

### `rov board validate`

Fast checks: the `rov.project.json` manifest, required project files, the six
standard library table entries, submodule initialization, merge-conflict
markers, and symbol metadata links. `--full` additionally runs `kicad-cli` ERC
and DRC; without `kicad-cli` installed, `--full` reports `BLOCKED` rather than
quietly passing. `--hook` is the pre-commit mode: fast checks only, and it fails
the commit only on a real `FAIL` so missing tools never lock you out of Git.

This fast check is the one `run-kicad-ci.yml` runs, so a local pass means the
shared CI gate will pass. ERC, DRC, and the manufacturing exports are separate
KiBot steps, not part of this check. The central symbol lint is the one thing
that *is* part of this check: the shared validation step is the single owner of
the library lint in board CI, so the same files are never linted twice.

`--full` is **not** the whole CI path. It adds `kicad-cli` ERC and DRC only. It
does not run the KiBot manufacturing export, so a local `--full` pass does not
mean the export step would succeed. See the known gaps at the end of this file.

`--full` uses the same severity policy as CI. `kibot_master.yaml` runs its
preflight at `severity: error`, so `--full` passes `--severity-error` to
`kicad-cli`: an ERC or DRC *warning* does not fail a local run any more than it
fails CI. A local failure means CI would fail too.

### `rov board sync-library`

Plans a library submodule update. **It is a dry run by default** and prints the
current commit, the target commit, the changed library files, and any blocked
reason without changing anything. To act on the plan, from the board root:

```text
python <devops>/scripts/rov.py board sync-library --apply
python <devops>/scripts/rov.py board sync-library --apply --branch chore/library-update --push --pr
```

This is the board-side command. It updates the board's library submodule pointer
through a reviewable pull request. It is not the same as `rov library sync`,
which updates the library repository itself and must be run from the library
checkout.

It requires a clean board worktree and a clean submodule. A dirty worktree, a
diverged remote, or an existing update branch with different content is
`BLOCKED`, not something to force through. If a `BLOCKED` happens after the
update branch has been checked out, the message names that branch, the base it
was created from, and the `git switch` that returns you, so you are never left
on a branch the next scheduled run overwrites.

### `rov library` commands

Run these from the `Libraries` repository root, or pass `--library-dir`:

```text
rov library validate     # run the symbol metadata linter
rov library build        # rebuild the generated category symbol libraries
rov library list         # tab-separated name/category/MPN/manufacturer
rov library search QUERY # same rows, filtered
rov library sync         # fetch and fast-forward a clean library checkout
rov library import ...   # runs scripts/import_part.py
rov library contribute --name MPN --category Power --push --pr
rov library gui          # open the Library Manager GUI
```

`library sync` never pushes, but it does fetch and fast-forward, so point it at
the library and never at a board. Both of its Git commands are bounded, so a
remote that stops answering is reported as `BLOCKED` with the cached revision
kept instead of hanging the caller. `library contribute` validates first, stages
only `Symbols/`, `Footprints/`, `3D_Models/`, and `Design_Blocks/`, and pushes
only the new `add-part-*` branch.

`library list` and `library search` are the machine-readable pair: the rows go to
standard output and the `[PASS]`/`[BLOCKED]` summary goes to standard error, so
`rov library list | ...` never has to filter a status line out of a part table.

## Exit codes

| Code | Meaning |
| :--- | :--- |
| `0` | No `FAIL` and no `BLOCKED`. Warnings may still be printed. |
| `1` | At least one check reported `FAIL`. |
| `2` | No `FAIL`, but at least one check was `BLOCKED`. |

`BLOCKED` means a prerequisite is missing or unreachable (no `kicad-cli`, an
uninitialized submodule, an offline remote, a dirty worktree). It is distinct
from `FAIL` on purpose: a missing prerequisite is an action for a human, not a
broken design.

## Safety rules

- **No command pushes a protected branch.** `master`, `main`, `develop`,
  `development`, and `release` are never committed to or pushed by the CLI, the
  launchers, or the GUI. Everything is published through a reviewable branch
  and pull request.
- **No command resets, stashes, or overwrites your worktree.**
- **Nothing is installed for you.** `doctor` reports and guides.
- **Publishing is always opt-in.** `sync-library` and `contribute` only push
  when you pass `--push`, and only open a pull request when you also pass
  `--pr`.
- **Optional auto-merge is explicit.** The reusable update workflow only enables
  `gh pr merge --auto` when a pull request was actually created and the board
  passes `auto-merge: true`.
- **Every Git command that can leave the machine is time-bounded.** A library
  fetch, submodule init, fast-forward, or `gh` call that stops answering is
  reported, never waited on, so a launcher or the Library Manager cannot hang on
  a sleeping remote.

## The scheduled update branch

`update-library.yml` publishes the library update on one long-lived branch,
`chore/library-update`, and reuses the open pull request for it. That reuse is
deliberate and it is the reason a run can stop with "already points at a
different commit": the CLI refuses to push over a branch it did not create in
this run rather than force-pushing a reviewer's work.

The workflow therefore **reports** the reuse state in the job summary before it
does anything, and it never cleans up: nothing is force pushed, no branch is
deleted, and no pull request is closed or reopened.

**Repository setting required for the weekly run to keep working.** Enable
*Settings -> General -> Pull Requests -> Automatically delete head branches*
in each board repository. That deletes `chore/library-update` once its pull
request merges, so the next scheduled run recreates it from the current base.
Without the setting the merged branch lingers, the next run stops with a
`BLOCKED` naming the stale commit, and a human has to delete the branch by
hand. Enabling the setting is a one-time owner action; the workflow cannot do it
for you, and it will not delete a branch on your behalf.

## The `.pcb-devops-cache` contract

A board does not vendor this repository. `LAUNCH_KICAD` makes a shallow clone of
`purduerov/pcb-devops` into `.pcb-devops-cache/` and runs the CLI from there;
`bootstrap.py` and the generated `.rov-hooks/pre-commit` resolve the CLI the same
way. `rov_bridge.py` in the library repository uses the same cache, and looks for
it both inside a library checkout and at the root of a board that owns the
library as a submodule.

The cache is a **whole-repository** copy, not a single script. `rov.py` imports
`rov_core.py` and `sync_project_libs.py`, so `scripts/` must stay intact. Do not
hand-assemble a cache containing only `scripts/rov.py`; refresh the clone
instead. `.pcb-devops-cache/` is disposable and is never committed.

If the clone cannot be reached, the launcher falls back to opening the project
directly and `doctor` reports the CLI as unavailable. Nothing is silently
treated as up to date.

## Repository contents

### `scripts/`

Shared platform:

- `rov.py`: the cross-platform CLI, exit codes, and status rendering.
- `rov_core.py`: the only implementation of shared operations. The CLI, the
  launchers, and the Library Manager all call it; none of them re-implement Git
  synchronization, validation, or metadata rules.

Validation and artifacts:

- `linter_validator.py`: checks `.kicad_sym` files for the six required fields
  (`Category`, `MPN`, `Manufacturer`, `Datasheet`, `Temp_Range`, `DigiKey`).
- `sync_project_libs.py`: writes the standard library table entries into a
  board's `sym-lib-table`, `fp-lib-table`, and `.gitmodules`, preserving custom
  entries. Takes an optional board directory argument.
- `fetch_sourcing_bom.py`: queries DigiKey and Mouser for lifecycle status and
  stock.
- `generate_portal_page.py`: builds the GitHub Pages artifact portal.

Board launchers and local setup:

- `LAUNCH_KICAD.bat` / `LAUNCH_KICAD.sh`: bootstrap the board, then open KiCad.
- `setup_git_filters.ps1` / `setup_git_filters.sh`: register the KiCad
  clean/smudge filters and recursive submodule config in local Git. They no
  longer set `core.hooksPath`; bootstrap owns the untracked `.rov-hooks` path.
- `run_local_validation.ps1` / `run_local_validation.sh`: the pre-existing local
  Docker path that lints symbols and generates a fabrication package. Separate
  from `rov board validate --full`; see the known gaps below.

Maintenance and comparison:

- `visual_diff.ps1` / `visual_diff.sh`: render and compare PCB copper layers
  between two Git refs without switching the working tree.
- `sync_template_to_all.ps1` / `sync_template_to_all.sh`: propagate shared
  infrastructure from the board template to the board repositories.

### `.github/workflows/`

- `run-kicad-ci.yml`: reusable board workflow. Its steps run in this order:
  check out the board, set up Python, **resolve `platform_ref` from the board's
  `rov.project.json`**, check out the platform tools at that ref and hide that
  checkout from Git, install dependencies, verify no merge conflict markers,
  verify the
  central library submodule, **run the shared `rov board validate`**, configure
  the KiBot preflight rules, resolve the design file names, run KiBot ERC/DRC and
  the manufacturing exports, run the sourcing check, generate the portal page and
  job summary, upload the artifacts, and deploy the interactive BOM to GitHub
  Pages on `master`/`main`.
  The platform-ref step is what makes `platform_ref` in the manifest real: it
  fails closed when the key is missing, empty, or malformed, and the checkout then
  pins the validating revision instead of the platform's default branch. The
  shared validation deliberately sits after the conflict-marker and submodule
  checks so those cheap failures are reported first, and before KiBot so a
  manifest failure is reported before any export work. It is also the single owner
  of the central symbol lint in board CI, so no separate linter step duplicates
  the same run over the same files.
- `update-library.yml`: reusable validated library-update workflow. Reports the
  update-branch and pull-request reuse state in the job summary, then opens a
  `chore/library-update` pull request; never pushes the base branch, never force
  pushes, and never deletes a branch or closes a pull request. See "The
  scheduled update branch" above for the repository setting it depends on.
- `devops-ci.yml`: runs the Python compile and test suite on Windows, Linux, and
  macOS.

### Other

- `kibot_master.yaml`: shared preflight (ERC, zone fill) and manufacturing
  exports.
- `tests/`: the Python test suite for everything above.

## Using CI in a board repository

`.github/workflows/ci.yml`:

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
```

Library updates use a second reusable call. A reusable workflow can only narrow
the caller's token, never widen it, so the caller has to grant what the called
workflow asks for; without its own `permissions:` block the call is clamped to
the repository default, which is read-only on several boards:

```yaml
jobs:
  update-library:
    permissions:
      contents: write
      pull-requests: write
    uses: purduerov/pcb-devops/.github/workflows/update-library.yml@master
    with:
      library-path: libs/purdue-rov-kicad-lib
      library-branch: master
      update-branch: chore/library-update
      auto-merge: false
      platform-ref: master
    secrets: inherit
```

## Secrets configuration

For component stock and lifecycle checks in GitHub Actions:

- **DigiKey:** add `DIGIKEY_CLIENT_ID`, `DIGIKEY_CLIENT_SECRET`, and
  `DIGIKEY_REFRESH_TOKEN` to repository secrets.
- **Mouser:** add `MOUSER_API_KEY` to repository secrets.

The sourcing step is scoped to those steps only; the shared validation step
runs before them and needs no credentials.

## Known follow-ups

These are recorded, not fixed. They need owner approval before anyone changes
them.

**Blocking, must be ruled on before this branch is published.**
`X19-Control-Board` fails `rov board validate` with exit `1`, so the shared
validation step in `run-kicad-ci.yml` will stop that board's CI and produce no
artifacts. The cause is one file: its `sym-lib-table` still carries a single
legacy entry pointing at `Symbols/rov_parts.kicad_sym`, a file that no longer
exists in the library revision the board pins, and none of the six standard
`rov_*` category entries. Its `fp-lib-table` is already correct.

The owner must choose one of two remedies:

- **Approve the targeted one-file table sync** in the board repository:

  ```text
  python KiCad/DevOps/scripts/sync_project_libs.py KiCad/Boards/X19-Control-Board
  ```

  That script only appends the six missing standard entries, preserves the
  board's custom `ROV_Symbols` and `New_Library` entries, and does not touch
  any design file. It can also add `branch = master` to `.gitmodules` if that
  key is absent, so review both files in the resulting diff.
- **Or make the gate non-blocking**, for example by running the shared
  validation with `continue-on-error: true` until the board is migrated, at
  the cost of the gate no longer protecting every board.

`rov board bootstrap` is deliberately **not** the remedy here: it performs the
full board preparation, including the submodule fetch, the hook install, and
the project-file rename, which is far more than this one-file fix and is
outside the scope of this decision. The one exception is a brand-new board
straight from the template, where bootstrap is the intended path.

`X19-Control-Board` also has two project files, `X19-Control-Board.kicad_pro`
and `X19_Board_Control.kicad_pro`, which the validator reports as a `WARN`
while still using the first. A human should decide which one is canonical.

The remaining follow-ups:

1. **The legacy tracked `.githooks/pre-commit` is a pre-bootstrap exposure.**
   The exposure is not the hook's behaviour alone but *when* it can run: it is
   active from the first `git commit` in a board, which is before `LAUNCH_KICAD`
   or `python bootstrap.py` has installed the untracked `.rov-hooks/` hook and set
   `core.hooksPath`. Until bootstrap runs, an ordinary commit on a board created
   before this platform shipped, or on the board template itself, can execute a
   hook that reaches the network (`git fetch`/`git pull` against the library
   submodule) and stages a submodule change as a side effect. New boards do not
   have this window: the template installs the safe hook during the first
   bootstrap, and that hook only validates.
   The tracked `.githooks/` files were left in place because they are
   teammate-authored; removing or rewriting them is a separate, owner-approved
   change. The exposure is documented rather than silently closed, and the
   mitigation a member has today is `--no-verify` plus a `git status` check before
   committing.
2. **`prepare_library_contribution` runs the linter and then publishes.** The
   contribution flow validates before committing, but the pull request it opens
   is not guaranteed to already have its board/library checks green. The
   protected branch plus required checks is what protects the library today.
3. **`rov board validate --full` does not run the manufacturing export.** The
   platform spec says full validation "additionally runs the current KiCad
   validation and manufacturing-export path". What is implemented runs
   `kicad-cli sch erc` and `kicad-cli pcb drc` and nothing else. The KiBot
   preflight and export path still lives only in `run-kicad-ci.yml` and in the
   separate `run_local_validation` scripts, so a local `--full` pass does not
   prove the export step would succeed. Closing this means either wiring KiBot
   into the CLI or documenting `--full` as ERC/DRC only; it needs a decision.
   What *is* aligned is the severity policy: `--full` fails a board only where
   `kibot_master.yaml` at `severity: error` would.
4. **The Linux hardware-check list from the spec is not implemented.** The spec
   calls for a Linux job covering zone-fill checks, symbol-library validation,
   footprint and 3D-link validation, a KiBot manufacturing-export smoke test, a
   template bootstrap test, and one representative board validation. None of
   those exist as a dedicated hardware-check job. The KiBot export step in
   `run-kicad-ci.yml` covers part of the intent incidentally, and the template
   bootstrap path is covered by unit tests, but there is no job that asserts the
   list. Recorded so the gap is not mistaken for a finished rollout phase.
5. **The board repositories need one owner-enabled repository setting.** Enable
   *Automatically delete head branches* in each board repository, or a merged
   `chore/library-update` branch lingers and the next scheduled run stops with a
   `BLOCKED` until a human deletes it. See "The scheduled update branch"; the
   update workflow deliberately never deletes a branch itself.
