"""Contract tests for the board and template repositories.

The board repositories and the board template are separate Git repositories, so
this suite is the only place that can hold all eight of them to one contract:
`rov.project.json` describes the same library, and every update wrapper calls
the same reusable workflow with the same five inputs.

Important I5 is the reason the wrapper permissions are asserted here. A caller
can only *lower* the permissions of a called reusable workflow, so a thin
wrapper without its own `permissions:` block inherits whatever the repository
default happens to be. On a repository that defaults to read-only tokens, the
called workflow's least-privilege request is clamped and the update can neither
push the update branch nor open a pull request.

The board roots are discovered next to this repository, which is the multi
repository workspace layout (`KiCad/DevOps` beside `KiCad/Board_Template` and
`KiCad/Boards`) and also the isolated worktree layout used for a fix wave. The
tests skip, loudly, when neither is present rather than passing on nothing.
"""

import json
import os
import re
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
sys.path.insert(0, str(ROOT / "scripts"))

import rov_core  # noqa: E402

# The eight repositories that adopt the platform contract.
TEMPLATE_DIR_NAME = "Board_Template"
BOARDS_DIR_NAME = "Boards"

# The five documented inputs of the reusable update workflow, and their values.
EXPECTED_WRAPPER_INPUTS = {
    "library-path": "libs/purdue-rov-kicad-lib",
    "library-branch": "master",
    "update-branch": "chore/library-update",
    "auto-merge": False,
    "platform-ref": "master",
}

WRAPPER_RELATIVE_PATH = Path(".github") / "workflows" / "auto-update-submodule.yml"
CALLED_WORKFLOW = "purduerov/pcb-devops/.github/workflows/update-library.yml@master"


def board_roots() -> list[Path]:
    """Return every board repository that is present next to this repository."""
    roots: list[Path] = []
    template = ROOT.parent / TEMPLATE_DIR_NAME
    if (template / WRAPPER_RELATIVE_PATH).is_file():
        roots.append(template)
    boards = ROOT.parent / BOARDS_DIR_NAME
    if boards.is_dir():
        roots.extend(
            sorted(
                child
                for child in boards.iterdir()
                if (child / WRAPPER_RELATIVE_PATH).is_file()
            )
        )
    return roots


class BoardRepositoryTestCase(unittest.TestCase):
    """Shared discovery: fail loudly instead of silently testing nothing."""

    @classmethod
    def setUpClass(cls):
        cls.roots = board_roots()
        if not cls.roots:
            raise unittest.SkipTest(
                "no board repositories were found next to "
                f"{ROOT}; the cross-repository contract cannot be checked here"
            )
        if len(cls.roots) != 8:
            raise AssertionError(
                f"expected the template plus seven boards, found {len(cls.roots)}: "
                f"{[root.name for root in cls.roots]}"
            )


class TestBoardManifests(BoardRepositoryTestCase):
    """`rov.project.json` is the same usable manifest in every repository."""

    def manifest(self, root: Path) -> dict:
        path = root / rov_core.PROJECT_CONFIG_NAME
        self.assertTrue(path.is_file(), f"{path} is missing")
        return json.loads(path.read_text(encoding="utf-8"))

    def test_every_repository_has_a_usable_manifest(self):
        for root in self.roots:
            with self.subTest(repository=root.name):
                failures = [
                    result
                    for result in rov_core.validate_project_config(self.manifest(root))
                    if result.status == rov_core.STATUS_FAIL
                ]
                self.assertEqual(failures, [], [result.message for result in failures])

    def test_every_manifest_pins_the_platform_ref_the_manifests_ship_with(self):
        # `platform_ref` now selects the revision `run-kicad-ci.yml` validates
        # with, so it is a real value rather than documentation. This wave keeps
        # every board on the published platform revision.
        for root in self.roots:
            with self.subTest(repository=root.name):
                self.assertEqual(self.manifest(root)["platform_ref"], "master")

    def test_every_manifest_names_its_own_repository(self):
        for root in self.roots:
            with self.subTest(repository=root.name):
                # `board-template` and `Board_Template` are the same repository,
                # so the comparison ignores case and separators.
                def normalize(name: str) -> str:
                    return re.sub(r"[^a-z0-9]", "", name.lower())

                self.assertEqual(
                    normalize(self.manifest(root)["project_name"]), normalize(root.name)
                )

    def test_every_manifest_tracks_the_approved_library_branch(self):
        for root in self.roots:
            with self.subTest(repository=root.name):
                library = self.manifest(root)["library"]
                self.assertEqual(library["path"], "libs/purdue-rov-kicad-lib")
                self.assertEqual(library["branch"], "master")
                self.assertEqual(library["update_policy"], "pull-request")


class TestBoardUpdateWrappers(BoardRepositoryTestCase):
    """Every wrapper calls the reusable workflow the same safe way."""

    def wrapper_text(self, root: Path) -> str:
        return (root / WRAPPER_RELATIVE_PATH).read_text(encoding="utf-8")

    def wrapper(self, root: Path) -> dict:
        if yaml is None:
            self.skipTest("PyYAML is not installed in this environment")
        return yaml.safe_load(self.wrapper_text(root))

    def test_every_wrapper_calls_the_pinned_reusable_workflow(self):
        for root in self.roots:
            with self.subTest(repository=root.name):
                self.assertIn(CALLED_WORKFLOW, self.wrapper_text(root))

    def test_every_wrapper_grants_least_privilege_permissions(self):
        """Important I5: the caller has to grant what the callee asks for.

        A reusable workflow can only reduce the caller's token, never widen it,
        so a wrapper that declares nothing is silently clamped to the repository
        default. That default is read-only on many repositories, which turns
        every scheduled library update into a failure to push or to open a pull
        request while the run still looks green.
        """
        for root in self.roots:
            with self.subTest(repository=root.name):
                data = self.wrapper(root)
                job = data["jobs"]["update-library"]
                self.assertEqual(
                    {"contents": "write", "pull-requests": "write"},
                    job["permissions"],
                    f"{root.name} must grant exactly the permissions the called "
                    "workflow declares, and nothing more",
                )

    def test_every_wrapper_keeps_the_five_documented_inputs(self):
        for root in self.roots:
            with self.subTest(repository=root.name):
                with_values = self.wrapper(root)["jobs"]["update-library"]["with"]
                self.assertEqual(EXPECTED_WRAPPER_INPUTS, with_values)

    def test_no_wrapper_takes_over_the_publish_step_itself(self):
        # The wrapper is a thin caller. A push, a commit, or a force flag here
        # would be a second, unreviewed publication path.
        for root in self.roots:
            with self.subTest(repository=root.name):
                text = self.wrapper_text(root)
                for forbidden in ("run:", "git push", "gh pr", "--force", "continue-on-error"):
                    with self.subTest(forbidden=forbidden):
                        self.assertNotIn(forbidden, text)

    def test_every_wrapper_keeps_its_three_triggers(self):
        for root in self.roots:
            with self.subTest(repository=root.name):
                text = self.wrapper_text(root)
                for trigger in ("workflow_dispatch:", "repository_dispatch:", "schedule:"):
                    with self.subTest(trigger=trigger):
                        self.assertIn(trigger, text)

    def test_every_wrapper_parses_as_yaml(self):
        if yaml is None:
            self.skipTest("PyYAML is not installed in this environment")
        for root in self.roots:
            with self.subTest(repository=root.name):
                yaml.safe_load(self.wrapper_text(root))


class TestBoardWorkflowYaml(BoardRepositoryTestCase):
    """Every board workflow must parse, so CI never fails on syntax."""

    def test_every_workflow_in_every_board_parses(self):
        if yaml is None:
            self.skipTest("PyYAML is not installed in this environment")
        for root in self.roots:
            workflows = sorted((root / ".github" / "workflows").glob("*.yml"))
            self.assertTrue(workflows, f"{root.name} has no workflows to validate")
            for workflow in workflows:
                with self.subTest(repository=root.name, workflow=workflow.name):
                    yaml.safe_load(workflow.read_text(encoding="utf-8"))

    def test_every_board_ignores_the_generated_hook_directory(self):
        for root in self.roots:
            with self.subTest(repository=root.name):
                gitignore = root / ".gitignore"
                self.assertTrue(gitignore.is_file(), f"{gitignore} is missing")
                self.assertIn(
                    f"{rov_core.HOOKS_DIR_NAME}/", gitignore.read_text(encoding="utf-8")
                )


class TestBoardTemplateBootstrapScript(BoardRepositoryTestCase):
    """`bootstrap.py` must fail with an instruction, never a traceback.

    The template is used by members who have not opened KiCad yet, so a missing
    platform cache is the first thing they can hit. A traceback buries the one
    action that fixes it, and a bare `0` would report a board as bootstrapped
    when nothing happened.
    """

    TEMPLATE = Path(TEMPLATE_DIR_NAME)
    SCRIPT = "bootstrap.py"

    def copied_script(self) -> tuple[Path, Path]:
        """Copy the script into an empty directory so no candidate can resolve.

        A copy is used rather than the real template so the test never depends on
        whether a `.pcb-devops-cache` happens to exist in the working tree.
        """
        source = ROOT.parent / self.TEMPLATE / self.SCRIPT
        self.assertTrue(source.is_file(), f"{source} is missing")
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        board = Path(temp.name) / "fresh-board"
        board.mkdir()
        script = board / self.SCRIPT
        script.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return board, script

    def run_without_a_cli(self) -> subprocess.CompletedProcess[str]:
        board, script = self.copied_script()
        environment = {key: value for key, value in os.environ.items() if key != "ROV_DEVOPS_DIR"}
        return subprocess.run(
            [sys.executable, str(script), "--project-dir", str(board), "--non-interactive"],
            cwd=str(board),
            capture_output=True,
            text=True,
            timeout=120,
            env=environment,
        )

    def test_a_missing_cli_exits_two_with_an_actionable_message(self):
        result = self.run_without_a_cli()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("[BLOCKED] bootstrap:", result.stderr)
        self.assertIn("ROV_DEVOPS_DIR", result.stderr)
        self.assertIn("LAUNCH_KICAD", result.stderr)

    def test_the_missing_cli_message_names_every_candidate(self):
        # The three lookup locations are the documented resolution order, so a
        # member can see where the script looked before being told what to do.
        source = (ROOT.parent / self.TEMPLATE / self.SCRIPT).read_text(encoding="utf-8")
        for candidate in ("ROV_DEVOPS_DIR", ".pcb-devops-cache", "DevOps"):
            with self.subTest(candidate=candidate):
                self.assertIn(candidate, source)

    def test_a_present_cli_is_delegated_to(self):
        """The script still runs the real CLI and returns its exit code."""
        board, script = self.copied_script()
        cache = board / ".pcb-devops-cache" / "scripts"
        cache.mkdir(parents=True)
        (cache / "rov.py").write_text(
            "import sys\nprint('DELEGATED ' + ' '.join(sys.argv[1:]))\nsys.exit(7)\n",
            encoding="utf-8",
        )
        environment = {key: value for key, value in os.environ.items() if key != "ROV_DEVOPS_DIR"}
        result = subprocess.run(
            [sys.executable, str(script), "--project-dir", str(board), "--non-interactive"],
            cwd=str(board),
            capture_output=True,
            text=True,
            timeout=120,
            env=environment,
        )
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertIn("DELEGATED board bootstrap", result.stdout)
        self.assertIn("--non-interactive", result.stdout)


if __name__ == "__main__":
    unittest.main()
