"""Contract tests for the shared GitHub Actions workflows.

The workflows are the automation surface of the platform, so their safety
properties are asserted here instead of being trusted to review. The tests
read the workflow text because that is what GitHub Actions executes: a missing
token, a force push, or a lost permission would otherwise only be discovered on
a real board.
"""

import re
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

# A push to a protected base branch, with or without a refspec. The check is
# line based so an unrelated word in a comment cannot hide a real command.
BASE_BRANCH_PUSH = re.compile(r"push\b[^\n]*\b(master|main)\b")
FORCE_FLAG = re.compile(r"(--force|-f\b|force-with-lease)")


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
        self.assertIn("pcb-devops-tools", self.text)
        self.assertIn("ref: ${{ inputs.platform-ref }}", self.text)
        self.assertIn("purduerov/pcb-devops", self.text)

    def test_update_workflow_configures_a_bot_identity(self):
        self.assertIn("user.name", self.text)
        self.assertIn("user.email", self.text)

    def test_update_workflow_passes_a_local_github_token(self):
        self.assertIn("GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}", self.text)
        # The token is scoped to the steps that talk to GitHub instead of being
        # exported into the whole job.
        self.assertNotIn("GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n    env:", self.text)

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
