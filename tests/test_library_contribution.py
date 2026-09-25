"""Contract tests for the safe ``rov library contribute`` flow.

Every fixture is a real local Git repository containing one compliant symbol file
and a committed baseline, so the branch, staging, and commit rules under test are
enforced by Git itself instead of by a mock. Nothing here contacts the network:
the push and the pull-request creation are the only commands that would leave the
machine, and both are intercepted before a subprocess starts.
"""

import contextlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

DEVOPS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DEVOPS_DIR / "scripts"))

import rov_core  # noqa: E402

GIT_AVAILABLE = shutil.which("git") is not None

PR_URL = "https://github.com/purduerov/purdue-rov-kicad-lib/pull/1"

COMPLIANT_SYMBOL = """(kicad_symbol_lib
  (version 20211014)
  (generator "kicad_symbol_editor")
  (symbol "NEW-PART"
    (property "Reference" "U" (id 0) (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Value" "NEW-PART" (id 1) (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Footprint" "rov_power:NEW-PART" (id 2) (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
    (property "Datasheet" "https://example.com/new-part.pdf" (id 3) (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
    (property "Category" "Power" (id 4) (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
    (property "MPN" "NEW-PART" (id 5) (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
    (property "Manufacturer" "Example Semiconductor" (id 6) (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
    (property "DigiKey" "000-00000-ND" (id 7) (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
    (property "Temp_Range" "-40C to 125C" (id 8) (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
  )
)
"""


def run_git(cwd: Path, *args: str) -> str:
    """Run a Git command in ``cwd`` and return its trimmed standard output."""
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def remove_git_dir(path: Path) -> None:
    """Delete a Git directory whose object files are marked read-only."""

    def clear_readonly_and_retry(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=clear_readonly_and_retry)
    else:  # pragma: no cover - Python 3.11 and older
        shutil.rmtree(path, onerror=clear_readonly_and_retry)


def completed(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess:
    """Return a finished process without starting anything."""
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


class LibraryFixture:
    """A local library remote and a library checkout with a committed baseline."""

    def __init__(self, base: Path) -> None:
        self.remote = base / "library.git"
        self.library = base / "Libraries"

        run_git(base, "init", "--bare", "-b", "master", str(self.remote))

        self.library.mkdir(parents=True)
        run_git(self.library, "init", "-b", "master")
        run_git(self.library, "config", "user.name", "PCB Test")
        run_git(self.library, "config", "user.email", "pcb-test@example.invalid")
        # A unit test must never wait on a signing prompt or write a signature.
        run_git(self.library, "config", "commit.gpgsign", "false")

        (self.library / "scripts").mkdir()
        (self.library / "scripts" / "linter_validator.py").write_text(
            "raise SystemExit('this copy must not run: the tests mock it')\n",
            encoding="utf-8",
            newline="\n",
        )
        (self.library / "Symbols" / "parts" / "power").mkdir(parents=True)
        (self.library / "Symbols" / "parts" / "power" / "baseline.kicad_sym").write_text(
            COMPLIANT_SYMBOL, encoding="utf-8", newline="\n"
        )
        (self.library / "notes.txt").write_text("library notes\n", encoding="utf-8")
        run_git(self.library, "add", ".")
        run_git(self.library, "commit", "-m", "baseline library")
        run_git(self.library, "remote", "add", "origin", self.remote.as_posix())
        run_git(self.library, "push", "-u", "origin", "master")
        self.baseline_commit = self.rev("master")

    # -- fixture operations -------------------------------------------------

    def add_part(self, relative: str = "Symbols/parts/power/new-part.kicad_sym") -> Path:
        """Write a new part file and return its path."""
        path = self.library / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(COMPLIANT_SYMBOL, encoding="utf-8", newline="\n")
        return path

    def write_notes(self, text: str = "unrelated\n") -> Path:
        """Write a file outside the allowed contribution paths."""
        path = self.library / "notes.txt"
        path.write_text(text, encoding="utf-8")
        return path

    # -- observation --------------------------------------------------------

    def rev(self, ref: str) -> str:
        return run_git(self.library, "rev-parse", ref)

    def branch(self) -> str | None:
        return rov_core.current_branch(self.library)

    def subject(self) -> str:
        return run_git(self.library, "log", "-1", "--format=%s")

    def local_branches(self) -> list[str]:
        return [
            line.strip()
            for line in run_git(self.library, "for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines()
            if line.strip()
        ]


@contextlib.contextmanager
def offline_linter(returncode: int = 0):
    """Run the real library fixture while the linter subprocess is mocked out."""
    real_run = subprocess.run
    calls: list[list[str]] = []

    def guarded(command, *args, **kwargs):
        if "linter_validator.py" in " ".join(str(part) for part in command[:3]):
            calls.append([str(part) for part in command])
            return completed(returncode, "Library verified.\n")
        return real_run(command, *args, **kwargs)

    with mock.patch.object(rov_core.subprocess, "run", guarded):
        yield calls


@contextlib.contextmanager
def recorded_push():
    """Record and fake every ``git push`` so no test can reach a remote."""
    real_run_git = rov_core.run_git
    pushes: list[tuple[str, ...]] = []

    def recording(repo_dir, *args, **kwargs):
        if args and args[0] == "push":
            pushes.append(tuple(args))
            return completed(0, "pushed\n")
        return real_run_git(repo_dir, *args, **kwargs)

    with mock.patch.object(rov_core, "run_git", recording):
        yield pushes


@contextlib.contextmanager
def recorded_github():
    """Stand in for the GitHub CLI and record its argument lists."""
    calls: list[list[str]] = []

    def available() -> bool:
        return True

    def run(args, cwd=None, timeout=None):
        calls.append(list(args))
        if args[:2] == ["pr", "create"]:
            return completed(0, f"{PR_URL}\n")
        return completed(1, "", "no pull requests found for branch\n")

    with mock.patch.object(rov_core, "_gh_is_available", available), mock.patch.object(
        rov_core, "_run_gh", run
    ):
        yield calls


@unittest.skipUnless(GIT_AVAILABLE, "Git is required")
class TestPrepareLibraryContribution(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.fixture = LibraryFixture(self.base)
        self.library = self.fixture.library

    def tearDown(self):
        remove_git_dir(self.base)

    def contribute(self, **kwargs):
        """Run the contribution flow with every outbound command intercepted."""
        options = {"component_name": "TPS54302", "category": "Power"}
        options.update(kwargs)
        with offline_linter() as linter_calls, recorded_push() as pushes, recorded_github() as gh:
            result = rov_core.prepare_library_contribution(self.library, **options)
        return result, linter_calls, pushes, gh

    def test_allowed_symbol_change_passes(self):
        """An allowed part file is validated, committed, and reported as PASS."""
        self.fixture.add_part()

        result, linter_calls, pushes, gh = self.contribute()

        self.assertEqual(result.status, rov_core.STATUS_PASS, result.message)
        self.assertIn("add-part-tps54302-", result.message)
        self.assertFalse(pushes, "nothing may be pushed without push=True")
        self.assertFalse(gh, "no pull request may be opened without create_pr=True")

        self.assertEqual(len(linter_calls), 1)
        self.assertEqual(linter_calls[0][0], sys.executable)
        self.assertEqual(len(linter_calls[0]), 3)
        self.assertTrue(
            linter_calls[0][1].replace("\\", "/").endswith("scripts/linter_validator.py"),
            linter_calls[0][1],
        )
        self.assertEqual(linter_calls[0][2], str(self.library / "Symbols"))

        self.assertTrue(
            self.fixture.branch().startswith("add-part-tps54302-"),
            f"a contribution branch must stay checked out: {self.fixture.branch()}",
        )
        self.assertEqual(
            self.fixture.subject(), "feat(parts): add TPS54302 to Power", self.fixture.subject()
        )
        staged = run_git(self.library, "show", "--name-only", "--format=", "HEAD").split()
        self.assertIn("Symbols/parts/power/new-part.kicad_sym", staged)
        self.assertNotIn("notes.txt", staged)

    def test_master_is_never_committed_to_or_left_checked_out(self):
        """The protected branch keeps its baseline commit and is not checked out."""
        self.fixture.add_part()

        self.contribute()

        self.assertEqual(self.fixture.rev("master"), self.fixture.baseline_commit)
        self.assertNotEqual(self.fixture.rev("HEAD"), self.fixture.baseline_commit)

    def test_unrelated_change_is_blocked(self):
        """A change outside the four library directories stops the flow."""
        self.fixture.add_part()
        self.fixture.write_notes()

        result, _linter_calls, pushes, gh = self.contribute()

        self.assertEqual(result.status, rov_core.STATUS_BLOCKED, result.message)
        self.assertIn("notes.txt", result.message)
        self.assertEqual(self.fixture.local_branches(), ["master"])
        self.assertEqual(self.fixture.rev("master"), self.fixture.baseline_commit)
        self.assertFalse(pushes)
        self.assertFalse(gh)

    def test_push_targets_only_the_new_branch(self):
        """A published contribution pushes its branch and never the protected one."""
        self.fixture.add_part()

        result, _linter_calls, pushes, gh = self.contribute(push=True)

        self.assertEqual(result.status, rov_core.STATUS_PASS, result.message)
        self.assertEqual(len(pushes), 1)
        command = " ".join(pushes[0])
        self.assertTrue(
            command.startswith("push -u origin add-part-"),
            f"the push must name the new branch: {command}",
        )
        self.assertNotIn("master", command)
        self.assertFalse(gh, "a push alone must not open a pull request")

    def test_pull_request_uses_the_same_branch_as_the_push(self):
        """The pull request targets the branch that was actually pushed."""
        self.fixture.add_part()

        result, _linter_calls, pushes, gh = self.contribute(push=True, create_pr=True)

        self.assertEqual(result.status, rov_core.STATUS_PASS, result.message)
        self.assertIn(PR_URL, result.message)
        self.assertEqual(len(pushes), 1)
        branch = pushes[0][3]
        creates = [call for call in gh if call[:2] == ["pr", "create"]]
        self.assertEqual(len(creates), 1, gh)
        self.assertEqual(creates[0][2:6], ["--base", "master", "--head", branch])

    def test_linter_failure_reports_fail_and_commits_nothing(self):
        """Validation runs first, so a failing linter never creates a branch."""
        self.fixture.add_part()

        with offline_linter(returncode=1), recorded_push() as pushes, recorded_github() as gh:
            result = rov_core.prepare_library_contribution(
                self.library, "TPS54302", "Power", push=True, create_pr=True
            )

        self.assertEqual(result.status, rov_core.STATUS_FAIL, result.message)
        self.assertEqual(self.fixture.local_branches(), ["master"])
        self.assertEqual(self.fixture.rev("master"), self.fixture.baseline_commit)
        self.assertFalse(pushes)
        self.assertFalse(gh)

    def test_contribution_requires_a_name_and_a_category(self):
        """An unusable request is reported before the repository is touched."""
        self.fixture.add_part()

        for options in ({"component_name": "  "}, {"category": ""}):
            with self.subTest(options=options):
                result, _linter, pushes, gh = self.contribute(**options)
                self.assertEqual(result.status, rov_core.STATUS_BLOCKED, result.message)
                self.assertEqual(self.fixture.local_branches(), ["master"])
                self.assertFalse(pushes)
                self.assertFalse(gh)

    def test_pull_request_without_a_push_is_blocked(self):
        """A pull request needs a published branch, so the request is refused."""
        self.fixture.add_part()

        result, _linter_calls, pushes, gh = self.contribute(create_pr=True)

        self.assertEqual(result.status, rov_core.STATUS_BLOCKED, result.message)
        self.assertIn("push", result.message)
        self.assertFalse(pushes)
        self.assertFalse(gh)

    def test_non_work_tree_is_blocked(self):
        """A directory that is not a repository is refused, not committed to."""
        plain = self.base / "not-a-repo"
        plain.mkdir()

        result = rov_core.prepare_library_contribution(plain, "TPS54302", "Power")

        self.assertEqual(result.status, rov_core.STATUS_BLOCKED, result.message)


class TestContributionContract(unittest.TestCase):
    def test_allowed_paths_are_the_four_library_directories(self):
        self.assertEqual(
            tuple(rov_core.LIBRARY_CONTRIBUTION_PATHS),
            ("Symbols", "Footprints", "3D_Models", "Design_Blocks"),
        )

    def test_contribution_paths_are_a_stable_tuple(self):
        self.assertIsInstance(rov_core.LIBRARY_CONTRIBUTION_PATHS, tuple)

    def test_pull_request_base_is_the_protected_library_branch(self):
        """The published base branch comes from the shared contract, not a literal."""
        self.assertEqual(rov_core.LIBRARY_BRANCH, "master")
        self.assertIn(rov_core.LIBRARY_BRANCH, rov_core.PROTECTED_BRANCHES)


if __name__ == "__main__":
    unittest.main()
