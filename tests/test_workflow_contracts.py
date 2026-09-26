"""Contract tests for the shared GitHub Actions workflows.

The workflows are the automation surface of the platform, so their safety
properties are asserted here instead of being trusted to review. The tests
read the workflow text because that is what GitHub Actions executes: a missing
token, a force push, or a lost permission would otherwise only be discovered on
a real board.
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:  # PyYAML is a CI dependency, not a CLI requirement.
    yaml = None

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIR = ROOT / ".github/workflows"
UPDATE_WORKFLOW = WORKFLOW_DIR / "update-library.yml"
DEVOPS_WORKFLOW = WORKFLOW_DIR / "devops-ci.yml"
RUN_KICAD_CI_WORKFLOW = WORKFLOW_DIR / "run-kicad-ci.yml"

# A push to a protected base branch, with or without a refspec. The check is
# line based so an unrelated word in a comment cannot hide a real command.
BASE_BRANCH_PUSH = re.compile(r"push\b[^\n]*\b(master|main)\b")
FORCE_FLAG = re.compile(r"(--force|-f\b|force-with-lease)")


def combined_output(result: subprocess.CompletedProcess[str]) -> str:
    """Return both captured streams as one string, for assertion messages.

    A bare ``result.stdout + result.stderr`` raises ``TypeError`` when the
    platform hands back ``None`` for a stream instead of text. Treating an
    absent stream as empty keeps a failure naming the behavior under test.
    """
    return (result.stdout or "") + (result.stderr or "")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def nested_block(text: str, header: str) -> str:
    """Return the lines nested under the first ``header:`` key in ``text``.

    The helpers are indentation based so a reformatted but equivalent workflow
    is not reported as a broken contract.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != header:
            continue
        indent = len(line) - len(line.lstrip())
        block = []
        for child in lines[index + 1:]:
            if not child.strip():
                block.append(child)
                continue
            if len(child) - len(child.lstrip()) <= indent:
                break
            block.append(child)
        return "\n".join(block)
    raise AssertionError(f"{header}: was not found")


def key_values(block: str) -> dict[str, str]:
    """Map each direct child key of ``block`` to its value or nested block text.

    A key written on one line maps to its inline value; a key with children maps
    to those children with the parent indentation removed, so both spellings of
    the same workflow are read the same way.
    """
    lines = block.splitlines()
    values: dict[str, str] = {}
    index = 0
    while index < len(lines):
        match = re.match(r"^(\s+)([A-Za-z0-9_.-]+):\s*(.*)$", lines[index])
        if not match:
            index += 1
            continue
        indent, name, inline = match.group(1), match.group(2), match.group(3)
        child: list[str] = []
        index += 1
        while index < len(lines):
            following = re.match(r"^(\s+)\S", lines[index])
            if not following or len(following.group(1)) <= len(indent):
                break
            child.append(lines[index])
            index += 1
        if name in values:
            raise AssertionError(f"{name} is declared more than once")
        if child:
            body = [f"{name}: {inline}".rstrip()]
            body += [line[len(indent) + 2:] for line in child]
            values[name] = "\n".join(body)
        else:
            values[name] = inline
    return values


def posix_dir(shell: str, directory: Path) -> str:
    """Return ``directory`` in the form the shell itself reports it.

    A Windows path is not the same string a POSIX shell sees, so the shell is
    asked for its own working directory instead of guessing a translation.
    """
    result = subprocess.run(
        [shell, "-c", "pwd -P"], cwd=str(directory), capture_output=True, text=True, timeout=30
    )
    return result.stdout.strip()


def run_blocks(workflow: Path) -> dict[str, str]:
    """Return ``{step name: run script}`` for every step that has one.

    The scripts are read from the parsed YAML rather than from the text, so a
    step cannot be checked by accident against a commented-out copy. Parsing is
    skipped, not silently replaced, when PyYAML is missing.
    """
    if yaml is None:
        raise unittest.SkipTest("PyYAML is not installed in this environment")
    data = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    blocks: dict[str, str] = {}
    for job in (data.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            script = step.get("run")
            if script:
                blocks[step.get("name") or script[:30]] = script
    return blocks



class TestUpdateLibraryWorkflow(unittest.TestCase):
    """The reusable library-update workflow must stay safe to call from a board."""

    def setUp(self):
        self.text = read(UPDATE_WORKFLOW)

    def test_update_workflow_is_reusable_and_creates_pr(self):
        self.assertIn("workflow_call:", self.text)
        self.assertIn("gh pr create", self.text)
        self.assertNotIn("git push origin master", self.text)
        self.assertNotIn("git push origin main", self.text)

    def test_update_workflow_uses_concurrency(self):
        self.assertIn("concurrency:", self.text)
        self.assertIn("cancel-in-progress: false", self.text)

    def test_concurrency_is_keyed_on_the_update_branch(self):
        # Two updates to the same branch must queue behind each other, and an
        # unrelated ref must not cancel or block an update already running.
        self.assertIn(
            "group: library-update-${{ github.repository }}-${{ inputs.update-branch }}",
            self.text,
        )
        self.assertNotIn("group: library-update-${{ github.repository }}-${{ github.ref }}", self.text)

    def test_update_workflow_publishes_url_and_state_outputs(self):
        outputs = key_values(nested_block(self.text, "outputs:"))
        self.assertEqual(
            {
                "url": "${{ steps.update.outputs.url }}",
                "state": "${{ steps.update.outputs.state }}",
            },
            outputs,
            "a calling board can only read the update result through these outputs",
        )

    def test_update_workflow_consumes_only_the_cli_markers(self):
        # PR_URL= and NO_CHANGE are the CLI contract. Scraping the human report
        # would make a reworded message silently change the workflow's result.
        self.assertIn("s|^PR_URL=||p", self.text)
        self.assertIn("NO_CHANGE", self.text)
        self.assertNotIn("/pull/[0-9]", self.text)
        self.assertNotIn("pull request \\(", self.text)
        self.assertNotIn(".*pull request", self.text)
        # Neither marker present on a successful run is a failure, never a
        # silent "up to date".
        self.assertIn("::error::The CLI reported neither PR_URL= nor NO_CHANGE", self.text)

    def test_update_workflow_declares_every_documented_input(self):
        inputs = key_values(nested_block(self.text, "inputs:"))
        self.assertEqual(
            {"library-path", "library-branch", "update-branch", "auto-merge", "platform-ref"},
            set(inputs),
            "the reusable interface must not grow or lose inputs without a decision",
        )
        for name, block in inputs.items():
            with self.subTest(input=name):
                self.assertIn("required: false", block, f"{name} must stay optional")

    def test_update_workflow_input_defaults_match_the_library_contract(self):
        inputs = key_values(nested_block(self.text, "inputs:"))
        expected = {
            "library-path": "libs/purdue-rov-kicad-lib",
            "library-branch": "master",
            "update-branch": "chore/library-update",
            "platform-ref": "master",
        }
        for name, default in expected.items():
            with self.subTest(input=name):
                block = inputs[name]
                self.assertIn("type: string", block)
                self.assertIn(f"default: {default}", block)
        auto_merge = inputs["auto-merge"]
        self.assertIn("type: boolean", auto_merge)
        self.assertIn("default: false", auto_merge)

    def test_library_path_is_documented_as_a_fail_closed_cross_check(self):
        # The manifest is the source of truth. The input is a cross-check that
        # must fail closed, not a way to point the update somewhere else.
        description = key_values(nested_block(self.text, "inputs:"))["library-path"]
        self.assertIn("cross-check", description)
        self.assertIn("blocked", description)

    def test_update_workflow_uses_least_privilege_permissions(self):
        permissions = key_values(nested_block(self.text, "permissions:"))
        self.assertEqual({"contents": "write", "pull-requests": "write"}, permissions)

    def test_update_workflow_checks_out_the_board_with_submodules(self):
        self.assertRegex(self.text, r"submodules:\s*recursive")
        self.assertRegex(self.text, r"fetch-depth:\s*0")

    def test_update_workflow_checks_out_the_platform_tools_at_the_requested_ref(self):
        self.assertIn("path: pcb-devops-tools", self.text)
        self.assertIn("ref: ${{ inputs.platform-ref }}", self.text)
        self.assertIn("purduerov/pcb-devops", self.text)

    def test_the_platform_tools_never_make_the_board_dirty(self):
        """The tools checkout must not look like an uncommitted board change.

        actions/checkout only accepts a path under the workspace, so the tools
        are cloned inside the board. Left untracked, that directory is a change
        in the board repository, and the CLI refuses to update a board with
        uncommitted changes, so the update workflow blocked its own update with
        "uncommitted changes" as the only clue. Each workflow therefore adds the
        directory to `.git/info/exclude`, which is local to the runner and so
        never becomes a CI-only entry in a board's committed .gitignore.
        """
        for workflow in (UPDATE_WORKFLOW, RUN_KICAD_CI_WORKFLOW):
            with self.subTest(workflow=workflow.name):
                text = workflow.read_text(encoding="utf-8")
                self.assertIn("path: pcb-devops-tools", text)
                self.assertIn('echo "/pcb-devops-tools/" >> .git/info/exclude', text)
                # The exclusion has to land before anything reads the board's
                # status or the CLI is asked to update it.
                exclude = text.index(".git/info/exclude")
                first_use = min(
                    text.index(needle)
                    for needle in ("board sync-library", "board validate")
                    if needle in text
                )
                self.assertLess(exclude, first_use)
                self.assertNotIn("runner.temp", text)

    def test_the_update_pull_request_needs_no_token_secret(self):
        """The update workflow runs on the built-in token, with no secret to manage.

        A pull request opened with the built-in GITHUB_TOKEN is authored by the
        github-actions app, so GitHub holds that pull request's workflow run
        until a person approves it. Supporting a token to avoid the click was
        tried and removed: it would have meant creating and rotating a secret in
        every board repository to save one click on a weekly review, which is a
        poor trade for a club. The workflow therefore states the approval step
        in the run summary instead of hiding it.
        """
        self.assertNotIn("ORG_UPDATE_TOKEN", self.text)
        self.assertNotIn("PAT_TOKEN", self.text)
        self.assertNotIn("secrets.", self.text.split("jobs:")[0].split("on:")[0])
        for line in self.text.splitlines():
            if line.strip().startswith("GH_TOKEN:"):
                with self.subTest(line=line.strip()):
                    self.assertEqual("GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}", line.strip())
        # The approval step is part of reviewing the pull request, so the run
        # summary says so rather than leaving a silent yellow banner.
        self.assertIn("approves the run", self.text)
        self.assertIn("GITHUB_STEP_SUMMARY", self.text)

    def test_update_workflow_configures_a_bot_identity(self):
        self.assertIn("user.name", self.text)
        self.assertIn("user.email", self.text)

    def test_update_workflow_passes_a_local_github_token(self):
        # The token is scoped to the steps that talk to GitHub instead of being
        # exported into the whole job, and it is the built-in one so no secret
        # has to be created for a board.
        self.assertNotIn("GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n    env:", self.text)
        token_lines = [
            line.strip()
            for line in self.text.splitlines()
            if line.strip().startswith("GH_TOKEN:")
        ]
        self.assertTrue(token_lines, "the workflow must pass a token to gh")
        for line in token_lines:
            with self.subTest(line=line):
                self.assertEqual("GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}", line)

    def test_update_workflow_never_pushes_a_base_branch(self):
        offenders = [line for line in self.text.splitlines() if BASE_BRANCH_PUSH.search(line)]
        self.assertEqual([], offenders, "a protected base branch must never be pushed")

    def test_update_workflow_never_forces_a_push(self):
        offenders = [line for line in self.text.splitlines() if FORCE_FLAG.search(line)]
        self.assertEqual([], offenders, "no force flag may appear in the update workflow")

    def test_update_workflow_creates_the_update_pr_through_the_cli(self):
        self.assertIn("board sync-library", self.text)
        self.assertIn("--apply --push --pr", self.text)
        self.assertIn(
            'commit-message "chore(library): update Purdue ROV component library"', self.text
        )
        self.assertIn('echo "url=$PR_URL" >> "$GITHUB_OUTPUT"', self.text)

    def test_update_workflow_reports_an_unchanged_library_without_failing(self):
        self.assertIn("NO_CHANGE", self.text)
        self.assertIn("up-to-date", self.text)

    def test_update_workflow_enables_auto_merge_only_for_a_created_pr(self):
        self.assertIn(
            "if: ${{ inputs.auto-merge && steps.update.outputs.url != '' }}", self.text
        )
        self.assertIn("gh pr merge --auto --merge", self.text)
        # Auto-merge must not be reachable from more than one step.
        self.assertEqual(1, self.text.count("gh pr merge"))

    def test_scheduled_branch_reuse_is_reported_before_the_update_runs(self):
        """Important I4: the reuse state is made explicit, and reported only.

        The update branch is shared between runs, so the CLI correctly refuses to
        push over a remote branch that holds a different commit. Without a
        report, that refusal reads as an unexplained weekly failure. The
        preflight publishes the state to the job summary, which is safe because
        it changes nothing.
        """
        blocks = run_blocks(UPDATE_WORKFLOW)
        self.assertIn("Preflight Update Branch and Pull Request", blocks)
        preflight = blocks["Preflight Update Branch and Pull Request"]
        self.assertIn("git ls-remote", preflight)
        self.assertIn("gh pr list", preflight)
        self.assertIn("GITHUB_STEP_SUMMARY", preflight)
        # The preflight must come before the update step so a blocked run still
        # shows why, and it must never be the step that fails.
        self.assertLess(
            self.text.index("Preflight Update Branch and Pull Request"),
            self.text.index("Prepare Library Update Pull Request"),
        )
        self.assertNotIn("exit 1", preflight, "a report must never fail the run")
        self.assertNotIn("set -e", preflight, "a report must survive a missing gh or remote")

    def test_preflight_documents_the_required_head_branch_cleanup_setting(self):
        preflight = run_blocks(UPDATE_WORKFLOW)["Preflight Update Branch and Pull Request"]
        self.assertIn("delete head branches automatically after merge", preflight)
        self.assertIn("README.md", preflight)
        self.assertIn("rather than pushing over it", preflight)

    def test_update_workflow_never_destroys_a_branch_or_a_pull_request(self):
        """Important I4: the safe answer is to report, never to clean up.

        Force pushing the shared update branch could discard a reviewer's work,
        and closing an unrelated pull request or deleting a branch a member is
        using is never this workflow's call. Only the reusable workflow's own
        update branch and its own pull request are ever touched, and only
        through the CLI.
        """
        for forbidden in (
            "gh pr close",
            "gh pr merge --squash",
            "--force",
            "force-with-lease",
            "git push origin --delete",
            "git push --delete",
            "git branch -D",
            "git branch -d",
            "git push origin :",
            "git clean",
            "gh pr delete",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.text)

    def test_auto_merge_is_the_only_gh_verb_that_changes_a_pull_request(self):
        # A closed or reopened pull request would be a second, unreviewed way
        # for the update workflow to end a review.
        offenders = [
            line.strip()
            for line in self.text.splitlines()
            if re.search(r"\bgh\s+pr\s+(close|reopen|delete|edit|ready|lock)\b", line)
        ]
        self.assertEqual([], offenders)

    def test_update_workflow_pins_the_platform_tools_before_use(self):
        blocks = run_blocks(UPDATE_WORKFLOW)
        self.assertLess(
            self.text.index("Checkout Platform Tools"),
            self.text.index("Prepare Library Update Pull Request"),
        )
        self.assertIn("ref: ${{ inputs.platform-ref }}", self.text)



class TestDevOpsWorkflowContracts(unittest.TestCase):
    """The DevOps validation job must run the same checks on every platform."""

    def setUp(self):
        self.text = read(DEVOPS_WORKFLOW)

    def test_validation_runs_on_every_supported_operating_system(self):
        self.assertIn("runs-on: ${{ matrix.os }}", self.text)
        self.assertIn("fail-fast: false", self.text)
        for runner in ("ubuntu-latest", "windows-latest", "macos-latest"):
            with self.subTest(runner=runner):
                self.assertIn(runner, self.text)

    def test_validation_keeps_the_existing_dependencies_and_tests(self):
        self.assertIn("python-version: '3.10'", self.text)
        self.assertIn("pip install pyyaml defusedxml", self.text)
        self.assertIn("kibot_master.yaml", self.text)
        self.assertIn("-m unittest discover -s tests -v", self.text)
        for script in (
            "scripts/rov.py",
            "scripts/rov_core.py",
            "scripts/fetch_sourcing_bom.py",
            "scripts/linter_validator.py",
            "scripts/generate_portal_page.py",
            "scripts/sync_project_libs.py",
        ):
            with self.subTest(script=script):
                self.assertIn(script, self.text)

    def test_validation_uses_one_interpreter_name_on_every_platform(self):
        # setup-python provides `python` on every runner; `python3` is not a
        # guaranteed alias on Windows or macOS hosted images.
        self.assertNotIn("python3 ", self.text)

    def test_validation_checks_every_workflow_in_the_repository(self):
        self.assertIn("'.github/workflows'", self.text)

    def test_validation_requests_read_only_permissions(self):
        permissions = key_values(nested_block(self.text, "permissions:"))
        self.assertEqual({"contents": "read"}, permissions)


class TestRunKicadCiWorkflow(unittest.TestCase):
    """The central board CI must run the same fast validation a member runs.

    The shared ``rov board validate`` call is what makes a local check and a CI
    check the same check, so its presence, its position, and the surrounding
    sourcing/artifact behavior are all asserted instead of assumed.
    """

    def setUp(self):
        self.text = read(RUN_KICAD_CI_WORKFLOW)

    def test_run_kicad_ci_calls_shared_validation(self):
        self.assertIn(
            "run: python3 pcb-devops-tools/scripts/rov.py board validate --project-dir .",
            self.text,
        )

    def test_shared_validation_runs_after_checkout_and_before_kibot(self):
        # Fast validation only reads checked-out files, so it must come after the
        # platform tools are present, and a manifest failure must be reported
        # before any manufacturing export work starts.
        validation = self.text.index("Run Shared ROV Board Validation")
        checkout = self.text.index("path: pcb-devops-tools")
        kibot = self.text.index("Configure KiBot Preflight Rules")
        self.assertLess(checkout, validation)
        self.assertLess(validation, kibot)

    def test_shared_validation_validates_the_calling_board(self):
        self.assertIn(
            'run: python3 pcb-devops-tools/scripts/rov.py board validate --project-dir .',
            self.text,
        )

    def test_sourcing_secrets_are_still_scoped_to_the_sourcing_step(self):
        for secret in (
            "MOUSER_API_KEY: ${{ secrets.MOUSER_API_KEY }}",
            "DIGIKEY_CLIENT_ID: ${{ secrets.DIGIKEY_CLIENT_ID }}",
            "DIGIKEY_CLIENT_SECRET: ${{ secrets.DIGIKEY_CLIENT_SECRET }}",
            "DIGIKEY_REFRESH_TOKEN: ${{ secrets.DIGIKEY_REFRESH_TOKEN }}",
        ):
            with self.subTest(secret=secret):
                self.assertIn(secret, self.text)
        self.assertEqual(1, self.text.count("fetch_sourcing_bom.py"))

    def test_generated_artifact_upload_is_unchanged(self):
        self.assertIn("uses: actions/upload-artifact@v4", self.text)
        self.assertIn("name: design-outputs", self.text)
        self.assertIn("path: Generated_Outputs/", self.text)

    def test_platform_ref_is_resolved_from_the_board_manifest(self):
        """Important I2/I7: `platform_ref` selects the validating revision.

        The manifest documented the platform revision but nothing read it, so
        every board was validated by whatever `purduerov/pcb-devops` default
        branch happened to be. The checkout must consume the resolved output.
        """
        blocks = run_blocks(RUN_KICAD_CI_WORKFLOW)
        self.assertIn("Resolve Platform Ref", blocks)
        resolve = blocks["Resolve Platform Ref"]
        self.assertIn("rov.project.json", resolve)
        self.assertIn("platform_ref", resolve)
        self.assertIn('echo "ref=$REF" >> "$GITHUB_OUTPUT"', resolve)
        self.assertIn("ref: ${{ steps.platform_ref.outputs.ref }}", self.text)
        # The step must run before the checkout it feeds, and the checkout must
        # no longer fall back to the platform default branch.
        self.assertLess(
            self.text.index("Resolve Platform Ref"),
            self.text.index("path: pcb-devops-tools"),
        )

    def test_platform_ref_resolution_fails_closed_with_an_actionable_message(self):
        resolve = run_blocks(RUN_KICAD_CI_WORKFLOW)["Resolve Platform Ref"]
        # A missing manifest, an empty ref, a traversal-shaped ref, and a ref with
        # a disallowed character each stop the run.
        self.assertEqual(4, resolve.count("exit 1"), resolve)
        for message in (
            "is missing, so the platform ref for purduerov/pcb-devops is unknown",
            "platform_ref is missing or empty",
            "may not start or end with '/'",
            "is not a valid ref",
        ):
            with self.subTest(message=message):
                self.assertIn(message, resolve)
        # Every stop is announced as a GitHub error annotation, not a bare exit.
        self.assertEqual(resolve.count("echo \"::error::"), 4, resolve)
        # A strict mode is required so a broken manifest cannot slip past.
        self.assertIn("set -euo pipefail", resolve)

    def test_platform_ref_resolution_never_reads_a_null_as_a_ref(self):
        """A JSON `null` must fail closed, not become the ref "None".

        `dict.get(key, "")` returns the stored `None`, which would print as the
        literal string `None` and pass the character check, so the workflow
        would check out purduerov/pcb-devops at a ref named "None".
        """
        resolve = run_blocks(RUN_KICAD_CI_WORKFLOW)["Resolve Platform Ref"]
        self.assertIn(".get('platform_ref') or ''", resolve)
        self.assertNotIn(".get('platform_ref','')", resolve.replace(" ", ""))


    def test_platform_ref_resolution_runs_after_python_is_available(self):
        # The manifest is read with `python`, so the interpreter has to exist
        # before the step runs.
        self.assertLess(
            self.text.index("Setup Python"), self.text.index("Resolve Platform Ref")
        )

    def test_library_symbol_lint_has_exactly_one_owner(self):
        """Important I9: the library lint must not run twice in board CI.

        `rov board validate` already runs the linter over the library's Symbols
        directory, so the old `find ... -exec linter_validator.py` step linted
        the same files a second time and could report the same failure twice.
        The shared validation is the single owner.
        """
        self.assertNotIn("linter_validator.py", self.text)
        self.assertIn("Run Shared ROV Board Validation", self.text)
        blocks = run_blocks(RUN_KICAD_CI_WORKFLOW)
        self.assertNotIn("Run Central Symbol Library Linter", blocks)
        self.assertIn(
            'python3 pcb-devops-tools/scripts/rov.py board validate --project-dir .',
            self.text,
        )
        # The owner must still be present: the submodule check stays, and the
        # shared validation is not merely moved.
        self.assertIn("Verify Central Library Submodule", blocks)

    def test_manifest_policy_matches_the_shared_manifest_validator(self):
        """The workflow reads the same `platform_ref` key the validator checks.

        `validate_project_config` reports an empty `platform_ref` as a FAIL, and
        the workflow has to fail closed on the same value, or a manifest could
        pass CI and block a member locally.
        """
        rov_core_source = (ROOT / "scripts" / "rov_core.py").read_text(encoding="utf-8")
        self.assertIn('_require_text(config, "platform_ref"', rov_core_source)


class TestPlatformRefResolutionScript(unittest.TestCase):
    """Run the workflow's platform-ref resolution script for real.

    Important I2/I7. Reading the manifest is the whole point of `platform_ref`,
    and a script that quietly accepted an empty, missing, or malformed ref would
    validate a board against an arbitrary platform revision. The script is
    extracted from the parsed workflow and executed verbatim against real
    manifests, so the cases below are the cases CI will actually see.

    Only the interpreter is supplied: a shim named ``python`` on ``PATH`` that
    execs the interpreter running the tests. Everything that is the contract -
    the manifest read, the validation, the ``::error::`` text, the exit codes,
    and the ``GITHUB_OUTPUT`` line - is the workflow's own text.
    """

    def setUp(self):
        self.bash = self.find_bash()
        if self.bash is None:
            self.skipTest("no bash is available to run the platform-ref script")
        self.script = run_blocks(RUN_KICAD_CI_WORKFLOW)["Resolve Platform Ref"]

    @staticmethod
    def find_bash() -> str | None:
        for candidate in ("bash",):
            path = shutil.which(candidate)
            if path is None:
                continue
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                probe = Path(tmp) / "probe.sh"
                probe.write_text('printf OK\n', encoding="utf-8", newline="\n")
                posix_probe = f"{posix_dir(path, Path(tmp))}/probe.sh"
                result = subprocess.run(
                    [path, posix_probe], capture_output=True, text=True, timeout=60
                )
                if result.returncode == 0 and "OK" in result.stdout:
                    return path
        return None

    def run_script(self, manifest: str | None) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            board = Path(tmp) / "board"
            board.mkdir()
            if manifest is not None:
                (board / "rov.project.json").write_text(manifest, encoding="utf-8")
            script = board / "resolve.sh"
            script.write_text(self.script, encoding="utf-8", newline="\n")
            outputs = board / "github-output.txt"
            outputs.write_text("", encoding="utf-8")
            self.write_interpreter_shim(board)
            posix_board = posix_dir(self.bash, board)
            wrapper = board / "wrapper.sh"
            wrapper.write_text(
                "#!/bin/bash\n"
                f'PATH="{posix_board}/tools:$PATH"\n'
                "export PATH\n"
                f'export GITHUB_OUTPUT="{posix_board}/github-output.txt"\n'
                'exec "$BASH" "$1" "$2"\n',
                encoding="utf-8",
                newline="\n",
            )
            self.chmod(wrapper)
            result = subprocess.run(
                [self.bash, f"{posix_board}/wrapper.sh", f"{posix_board}/resolve.sh"],
                cwd=str(board),
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.outputs = outputs.read_text(encoding="utf-8")
        return result

    def write_interpreter_shim(self, board: Path) -> None:
        """Create a ``python`` shim that runs the interpreter running the tests."""
        tools = board / "tools"
        tools.mkdir()
        interpreter = Path(sys.executable)
        posix = f"{posix_dir(self.bash, interpreter.parent)}/{interpreter.name}"
        shim = tools / "python"
        shim.write_text(f'#!/bin/sh\nexec "{posix}" "$@"\n', encoding="utf-8", newline="\n")
        self.chmod(shim)

    @staticmethod
    def chmod(path: Path) -> None:
        try:
            path.chmod(0o755)
        except OSError:
            pass

    def test_a_master_manifest_resolves_to_that_ref(self):
        result = self.run_script('{"schema": 1, "platform_ref": "master"}')
        self.assertEqual(result.returncode, 0, combined_output(result))
        self.assertEqual(self.outputs.strip(), "ref=master")
        self.assertIn("at master", result.stdout)

    def test_a_tag_or_commit_ref_is_accepted(self):
        for ref in ("v1.2.3", "0123abc", "release/2026-09"):
            with self.subTest(ref=ref):
                result = self.run_script(json.dumps({"platform_ref": ref}))
                self.assertEqual(result.returncode, 0, combined_output(result))
                self.assertEqual(self.outputs.strip(), f"ref={ref}")

    def test_a_missing_manifest_stops_the_run(self):
        result = self.run_script(None)
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error::", result.stdout)
        self.assertIn("rov.project.json is missing", result.stdout)
        self.assertEqual(self.outputs.strip(), "")

    def test_an_empty_or_absent_platform_ref_stops_the_run(self):
        for manifest in ('{"schema": 1}', '{"platform_ref": ""}', '{"platform_ref": null}'):
            with self.subTest(manifest=manifest):
                result = self.run_script(manifest)
                self.assertEqual(result.returncode, 1, combined_output(result))
                self.assertIn("platform_ref is missing or empty", result.stdout)
                self.assertEqual(self.outputs.strip(), "")

    def test_a_malformed_platform_ref_stops_the_run(self):
        for ref in (
            "../evil",
            "master branch",
            "feature/../master",
            "/master",
            "master/",
            "refs/heads/master;rm -rf /",
            "master\nrefs/heads/evil",
        ):
            with self.subTest(ref=ref):
                result = self.run_script(json.dumps({"platform_ref": ref}))
                self.assertEqual(result.returncode, 1, combined_output(result))
                self.assertIn("is not a valid ref", result.stdout)
                self.assertEqual(self.outputs.strip(), "")

    def test_a_ref_with_a_newline_cannot_smuggle_a_second_output(self):
        """A multi-line value must not add a second `GITHUB_OUTPUT` line.

        The ref is written straight into the step output, so a value carrying a
        newline could otherwise inject an output of its own. The character
        whitelist is what stops it.
        """
        result = self.run_script(json.dumps({"platform_ref": "master\nref=evil"}))
        self.assertEqual(result.returncode, 1, combined_output(result))
        self.assertEqual(self.outputs.strip(), "")





class TestWorkflowShellBlocksParse(unittest.TestCase):
    """Every `run:` block must be valid shell.

    The workflows are the automation surface, and a syntax error in one of them
    only shows up as a failed CI run on a real board. Parsing the scripts is
    cheap and catches the mistake before it is pushed.
    """

    def bash(self) -> str | None:
        for candidate in ("bash",):
            path = shutil.which(candidate)
            if path is None:
                continue
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                probe = Path(tmp) / "probe.sh"
                probe.write_text("if true; then :; fi\n", encoding="utf-8", newline="\n")
                result = subprocess.run(
                    [path, "-n", f"{posix_dir(path, Path(tmp))}/probe.sh"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.returncode == 0:
                    return path
        return None

    def test_every_run_block_parses(self):
        bash = self.bash()
        if bash is None:
            self.skipTest("no bash is available to parse the workflow scripts")
        for workflow in sorted(WORKFLOW_DIR.glob("*.yml")):
            for name, script in run_blocks(workflow).items():
                if "${" in script:
                    continue  # GitHub expressions are not shell; they are asserted as text
                with self.subTest(workflow=workflow.name, step=name):
                    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                        path = Path(tmp) / "step.sh"
                        path.write_text(script, encoding="utf-8", newline="\n")
                        result = subprocess.run(
                            [bash, "-n", f"{posix_dir(bash, Path(tmp))}/step.sh"],
                            capture_output=True,
                            text=True,
                            timeout=60,
                        )
                    self.assertEqual(result.returncode, 0, result.stderr)


class TestReadmeMatchesTheAutomation(unittest.TestCase):
    """The README is the documentation, so its claims are asserted too.

    A workflow that changed while the prose still describes the old behavior
    sends a member looking for a step that no longer exists, and that is exactly
    what a follow-up item in the README is supposed to prevent.
    """

    def setUp(self):
        self.text = (ROOT / "README.md").read_text(encoding="utf-8")

    def test_the_documented_step_order_names_every_current_step(self):
        # The README describes each step by its purpose, in the order the workflow
        # runs it. Every current step has to appear, and the order has to match, so
        # a step that is added, removed, or moved cannot be left undocumented.
        documented = [
            "check out the board",
            "set up Python",
            "resolve `platform_ref` from the board",
            "check out the platform tools at that ref and hide that\n  checkout from Git, install dependencies, verify no merge conflict markers,\n  verify the",
            "install dependencies",
            "verify no merge conflict markers",
            "verify the\n  central library submodule",
            "run the shared `rov board validate`",
            "configure\n  the KiBot preflight rules",
            "resolve\n  the design file names",
            "run KiBot ERC/DRC and\n  the manufacturing exports",
            "run the sourcing check",
            "generate the portal page and\n  job summary",
            "upload the artifacts",
            "deploy the interactive BOM to GitHub\n  Pages",
        ]
        prose = self.text.split("- `run-kicad-ci.yml`:", 1)[1]
        prose = prose.split("- `update-library.yml`:", 1)[0]
        flat = " ".join(prose.split())
        phrases = [" ".join(phrase.split()) for phrase in documented]
        for phrase in phrases:
            with self.subTest(step=phrase):
                self.assertIn(phrase, flat)
        positions = [flat.index(phrase) for phrase in phrases]
        self.assertEqual(sorted(positions), positions, "the documented order is wrong")
        # Nothing may be documented that the workflow no longer runs.
        self.assertNotIn("run the central symbol linter", flat)

    def test_the_readme_does_not_still_describe_the_removed_linter_step(self):
        self.assertNotIn("run the central symbol linter, **run the shared", self.text)
        self.assertIn("single owner", self.text)

    def test_the_readme_states_the_severity_policy_of_full_validation(self):
        self.assertIn("--severity-error", self.text)
        self.assertIn("severity: error", self.text)

    def test_the_readme_documents_the_head_branch_cleanup_setting(self):
        # The workflow's preflight points a human at this, so the README is where
        # the requirement has to be written down.
        self.assertIn("Automatically delete head branches", self.text)
        self.assertIn("The scheduled update branch", self.text)

    def test_the_readme_documents_the_pre_bootstrap_hook_exposure(self):
        self.assertIn("pre-bootstrap exposure", self.text)
        self.assertIn("core.hooksPath", self.text)

    def test_the_readme_shows_the_caller_permissions_the_wrapper_needs(self):
        self.assertRegex(
            self.text,
            r"permissions:\n\s+contents: write\n\s+pull-requests: write\n\s+uses: purduerov/pcb-devops",
        )

    def test_the_readme_no_longer_queues_the_wrapper_permissions_as_a_follow_up(self):
        follow_ups = self.text.split("The remaining follow-ups:", 1)[1]
        self.assertNotIn("Board update wrappers rely on repository default permissions", follow_ups)

    def test_the_readme_documents_that_row_listing_summaries_go_to_stderr(self):
        self.assertIn("standard error", self.text)


class TestWorkflowFilesAreValidYaml(unittest.TestCase):
    """Every checked-in workflow must parse, so CI never fails on syntax."""

    def test_workflow_directory_parses(self):
        if yaml is None:
            self.skipTest("PyYAML is not installed in this environment")
        workflows = sorted(WORKFLOW_DIR.glob("*.yml"))
        self.assertTrue(workflows, "no workflows were found to validate")
        for workflow in workflows:
            with self.subTest(workflow=workflow.name):
                yaml.safe_load(workflow.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
