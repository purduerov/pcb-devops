"""Contract tests for safe library update planning and update-branch preparation.

Every fixture is a real local Git setup: a bare library remote, a library source
repository, a bare board remote, and a board repository that consumes the library
through a real submodule. Nothing here contacts the network, so the safety rules
under test (clean-tree checks, detached submodule checkout, no base-branch push,
no duplicate pull request) are enforced by Git itself instead of by a mock.

The GitHub CLI is the only external program. It is reached through the
``rov_core._gh_is_available`` and ``rov_core._run_gh`` seams, and the tests that
do not expect it patch ``_run_gh`` to raise, so no test can reach GitHub.
"""

import contextlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

DEVOPS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DEVOPS_DIR / "scripts"))

import rov  # noqa: E402
import rov_core  # noqa: E402

GIT_AVAILABLE = shutil.which("git") is not None

LIBRARY_PATH = "libs/purdue-rov-kicad-lib"
UPDATE_BRANCH = "chore/library-update"
COMMIT_MESSAGE = "chore(library): update Purdue ROV component library"
PR_URL = "https://github.com/example/Demo-Board/pull/7"
BASE_BRANCH = "master"

MANIFEST = {
    "schema": 1,
    "project_name": "Demo-Board",
    "kicad_version": "10",
    "platform_ref": "master",
    "library": {
        "path": LIBRARY_PATH,
        "branch": "master",
        "update_policy": "pull-request",
    },
    "ci_profile": "standard",
}


def run_git(cwd: Path, *args: str) -> str:
    """Run a Git command in ``cwd`` and return its trimmed standard output."""
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def make_repo(path: Path) -> None:
    """Create an isolated ``master`` repository with a fixed test identity."""
    path.mkdir(parents=True)
    run_git(path, "init", "-b", "master")
    run_git(path, "config", "user.name", "PCB Test")
    run_git(path, "config", "user.email", "pcb-test@example.invalid")
    # A unit test must never wait on a signing prompt or write a signature.
    run_git(path, "config", "commit.gpgsign", "false")


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


def forbid_git_verb(real_run_git, *forbidden: str):
    """Return a ``run_git`` wrapper that raises before running the given verbs.

    The real function still runs, so the repository state stays real, but any
    command in ``forbidden`` fails the test instead of starting a subprocess. It
    is how a test proves that a destructive or publishing step was never even
    attempted.
    """
    blocked = frozenset(forbidden)

    def guarded(repo_dir, *args, **kwargs):
        if args and args[0] in blocked:
            raise AssertionError(f"must never run 'git {args[0]}': {args}")
        return real_run_git(repo_dir, *args, **kwargs)

    return guarded


@contextlib.contextmanager
def recording_git_verb(real_run_git, verb: str, sink: list):
    """Record every ``git <verb>`` call while still running it for real."""

    def recording(repo_dir, *args, **kwargs):
        if args and args[0] == verb:
            sink.append(tuple(args))
        return real_run_git(repo_dir, *args, **kwargs)

    with mock.patch.object(rov_core, "run_git", recording):
        yield sink


@contextlib.contextmanager
def forbid_git_verbs(*verbs: str):
    """Refuse the given Git verbs for the duration of the block."""

    with mock.patch.object(rov_core, "run_git", forbid_git_verb(rov_core.run_git, *verbs)):
        yield


@contextlib.contextmanager
def offline_github():
    """Refuse the GitHub CLI outright so no test can contact GitHub."""

    def forbidden(*args, **kwargs):
        raise AssertionError("tests must not run gh")

    # ``create=True`` keeps the helper usable while the seams do not exist yet,
    # so a test fails on the behaviour it is actually about. The tests that
    # expect GitHub patch the same names without ``create`` and therefore fail
    # if the seams are missing.
    with mock.patch.object(
        rov_core, "_gh_is_available", return_value=False, create=True
    ), mock.patch.object(rov_core, "_run_gh", side_effect=forbidden, create=True):
        yield


class RecordingGitHub:
    """Stand-in for the GitHub CLI that records its argument lists."""

    def __init__(self, existing_url: str | None = None, create_error: str = "") -> None:
        self.existing_url = existing_url
        self.create_error = create_error
        self.calls: list[list[str]] = []
        self.created: list[list[str]] = []

    def available(self) -> bool:
        return True

    def run(
        self, args: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess:
        self.calls.append(list(args))
        if args[:2] == ["pr", "create"]:
            self.created.append(list(args))
            if self.create_error:
                return completed(1, "", f"{self.create_error}\n")
            return completed(0, f"{PR_URL}\n")
        if args[:2] == ["pr", "list"]:
            # The real lookup is scoped to open pull requests on the branch, so
            # a closed pull request answers with no URL at all.
            if self.existing_url is None:
                return completed(0, "\n")
            return completed(0, f"{self.existing_url}\n")
        raise AssertionError(f"unexpected gh invocation: {args}")


class LibraryFixture:
    """A local library remote, library source, and board with a real submodule.

    The board consumes the library through ``git submodule add`` and the
    submodule's ``origin`` is then pointed at the bare remote, so
    ``git fetch origin master`` inside the submodule is a real remote fetch over
    a local path. Every path is inside the caller's temporary directory, so no
    test can reach the network.
    """

    def __init__(self, base: Path) -> None:
        self.base = base
        self.library_remote = base / "library.git"
        self.board_remote = base / "board.git"
        self.library = base / "purdue-rov-kicad-lib"
        self.board = base / "Demo-Board"
        self.submodule = self.board / Path(LIBRARY_PATH)

        run_git(base, "init", "--bare", "-b", "master", str(self.library_remote))
        run_git(base, "init", "--bare", "-b", "master", str(self.board_remote))

        make_repo(self.library)
        (self.library / "Symbols").mkdir(parents=True)
        (self.library / "Symbols" / "part.txt").write_text("initial", encoding="utf-8")
        run_git(self.library, "add", "Symbols/part.txt")
        run_git(self.library, "commit", "-m", "initial library")
        run_git(self.library, "remote", "add", "origin", self.library_remote.as_posix())
        run_git(self.library, "push", "-u", "origin", "master")
        self.initial_library_commit = self.rev(self.library, "HEAD")

        make_repo(self.board)
        (self.board / "rov.project.json").write_text(
            json.dumps(MANIFEST, indent=2) + "\n", encoding="utf-8"
        )
        (self.board / "notes.txt").write_text("board\n", encoding="utf-8")
        run_git(self.board, "add", "rov.project.json", "notes.txt")
        run_git(self.board, "remote", "add", "origin", self.board_remote.as_posix())
        run_git(
            self.board,
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            self.library.as_posix(),
            LIBRARY_PATH,
        )
        run_git(self.board, "commit", "-am", "add library")
        self.track_approved_library_remote()
        run_git(self.board, "commit", "-am", "track the approved library remote")
        run_git(self.board, "push", "-u", "origin", "master")
        run_git(self.board, "remote", "set-head", "origin", "master")
        self.board_base_commit = self.rev(self.board, "HEAD")

    def track_approved_library_remote(self) -> None:
        """Point ``.gitmodules`` and the submodule at the bare library remote.

        ``git submodule sync`` rewrites the submodule's ``origin`` from
        ``.gitmodules`` on every plan, so the manifest has to name the bare
        remote. Otherwise the board would keep fetching the source checkout and a
        rewritten remote history would never be seen.
        """
        gitmodules = self.board / ".gitmodules"
        content = gitmodules.read_text(encoding="utf-8")
        gitmodules.write_text(
            re.sub(
                r"(?m)^([ \t]*url[ \t]*=[ \t]*).*$",
                rf"\g<1>{self.library_remote.as_posix()}",
                content,
            ),
            encoding="utf-8",
            newline="\n",
        )
        run_git(self.submodule, "remote", "set-url", "origin", self.library_remote.as_posix())

    # -- fixture operations -------------------------------------------------

    def publish(self, name: str = "part.txt", text: str = "updated") -> str:
        """Commit a change in the library and publish it to the remote."""
        (self.library / "Symbols" / name).write_text(text, encoding="utf-8")
        run_git(self.library, "add", f"Symbols/{name}")
        run_git(self.library, "commit", "-m", f"update {name}")
        run_git(self.library, "push", "origin", "master")
        return self.rev(self.library, "HEAD")

    def publish_divergent(self) -> str:
        """Replace remote ``master`` with unrelated history and return it."""
        run_git(self.library, "checkout", "--orphan", "divergent")
        (self.library / "Symbols" / "part.txt").unlink()
        (self.library / "Symbols" / "other.txt").write_text("divergent", encoding="utf-8")
        run_git(self.library, "add", "-A", "--", "Symbols")
        run_git(self.library, "commit", "-m", "divergent library root")
        run_git(self.library, "push", "--force", "origin", "divergent:master")
        return self.rev(self.library, "HEAD")

    def commit_board(self, text: str) -> str:
        """Add a board commit on the checked-out branch and return it."""
        (self.board / "notes.txt").write_text(text, encoding="utf-8")
        run_git(self.board, "commit", "-am", f"board change: {text.strip()}")
        return self.rev(self.board, "HEAD")

    def seed_remote_update_branch(self, text: str) -> str:
        """Publish unrelated work to the remote's update branch and return it."""
        run_git(self.board, "switch", "-c", "seed")
        commit = self.commit_board(text)
        run_git(self.board, "push", "origin", f"seed:{UPDATE_BRANCH}")
        run_git(self.board, "switch", BASE_BRANCH)
        run_git(self.board, "branch", "-D", "seed")
        return commit

    # -- observation --------------------------------------------------------

    def rev(self, repo: Path, ref: str) -> str:
        return run_git(repo, "rev-parse", ref)

    def board_head(self) -> str:
        return self.rev(self.board, "HEAD")

    def submodule_head(self) -> str:
        return self.rev(self.submodule, "HEAD")

    def board_branch(self) -> str | None:
        return rov_core.current_branch(self.board)

    def head_subject(self) -> str:
        return run_git(self.board, "log", "-1", "--format=%s")

    def submodule_is_detached(self) -> bool:
        return rov_core.current_branch(self.submodule) is None

    def board_clean(self) -> bool:
        return rov_core.is_clean_worktree(self.board)

    def remote_heads(self) -> dict[str, str]:
        """Return the board remote's branches as ``{ref: commit}``."""
        heads = {}
        for line in run_git(self.board, "ls-remote", "--heads", "origin").splitlines():
            commit, _, ref = line.partition("\t")
            if ref.strip():
                heads[ref.strip()] = commit.strip()
        return heads

    def changed_between(self, base: str, head: str) -> list[str]:
        output = run_git(self.board, "diff", "--name-only", f"{base}..{head}")
        return [line for line in output.splitlines() if line]


class LibraryUpdateTestCase(unittest.TestCase):
    """Base fixture wiring: one local Git world per test."""

    def setUp(self) -> None:
        if not GIT_AVAILABLE:
            self.skipTest("git is not installed")
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        self.fixture = LibraryFixture(Path(temp.name))


class TestPlanLibraryUpdate(LibraryUpdateTestCase):
    def test_dirty_board_blocks_update(self):
        """An uncommitted board change blocks planning and changes nothing."""
        fixture = self.fixture
        fixture.publish()
        (fixture.board / "notes.txt").write_text("work in progress\n", encoding="utf-8")

        with offline_github():
            plan = rov_core.plan_library_update(fixture.board)

        self.assertIsNotNone(plan.blocked_reason)
        self.assertIn("uncommitted changes", plan.blocked_reason)
        self.assertEqual(plan.changed_files, ())
        self.assertEqual(fixture.board_head(), fixture.board_base_commit)
        self.assertEqual(fixture.board_branch(), BASE_BRANCH)
        self.assertEqual(fixture.submodule_head(), fixture.initial_library_commit)

    def test_dirty_submodule_blocks_update(self):
        """A local library edit is reported and preserved, never discarded."""
        fixture = self.fixture
        fixture.publish()
        edited = fixture.submodule / "Symbols" / "part.txt"
        edited.write_text("local library edit\n", encoding="utf-8")

        with offline_github():
            plan = rov_core.plan_library_update(fixture.board)

        self.assertIsNotNone(plan.blocked_reason)
        self.assertIn("submodule", plan.blocked_reason)
        self.assertIn("local changes", plan.blocked_reason)
        self.assertEqual(plan.changed_files, ())
        self.assertEqual(edited.read_text(encoding="utf-8"), "local library edit\n")
        self.assertEqual(fixture.submodule_head(), fixture.initial_library_commit)
        self.assertEqual(fixture.board_head(), fixture.board_base_commit)
        self.assertEqual(fixture.board_branch(), BASE_BRANCH)

    def test_plan_reports_no_change_when_remote_is_current(self):
        """A board that already matches the remote needs no update at all."""
        fixture = self.fixture

        with offline_github():
            plan = rov_core.plan_library_update(fixture.board)

        self.assertIsNone(plan.blocked_reason)
        self.assertEqual(plan.current_commit, fixture.initial_library_commit)
        self.assertEqual(plan.target_commit, fixture.initial_library_commit)
        self.assertEqual(plan.current_commit, plan.target_commit)
        self.assertEqual(plan.changed_files, ())
        self.assertEqual(fixture.board_branch(), BASE_BRANCH)
        self.assertEqual(fixture.board_head(), fixture.board_base_commit)
        self.assertEqual(fixture.submodule_head(), fixture.initial_library_commit)
        self.assertTrue(fixture.board_clean())

    def test_plan_reports_changed_files_without_touching_the_tree(self):
        """Planning reports the target commit and files but moves nothing."""
        fixture = self.fixture
        target = fixture.publish()

        with offline_github():
            plan = rov_core.plan_library_update(fixture.board)

        self.assertIsNone(plan.blocked_reason)
        self.assertEqual(plan.current_commit, fixture.initial_library_commit)
        self.assertEqual(plan.target_commit, target)
        self.assertEqual(plan.changed_files, ("Symbols/part.txt",))
        self.assertEqual(fixture.board_branch(), BASE_BRANCH)
        self.assertEqual(fixture.board_head(), fixture.board_base_commit)
        self.assertEqual(fixture.submodule_head(), fixture.initial_library_commit)
        self.assertTrue(fixture.board_clean())

    def test_conflicting_remote_commit_is_blocked(self):
        """Unrelated remote history is blocked, never force-updated."""
        fixture = self.fixture
        divergent = fixture.publish_divergent()

        with offline_github():
            plan = rov_core.plan_library_update(fixture.board)

        self.assertEqual(plan.blocked_reason, "remote library history diverged")
        self.assertEqual(plan.current_commit, fixture.initial_library_commit)
        self.assertEqual(plan.target_commit, divergent)
        self.assertEqual(plan.changed_files, ())
        self.assertEqual(fixture.submodule_head(), fixture.initial_library_commit)
        self.assertEqual(fixture.board_head(), fixture.board_base_commit)
        self.assertTrue(fixture.board_clean())

    def test_unreachable_remote_is_blocked_with_the_git_error(self):
        """A fetch failure blocks with the Git message, not a false success."""
        fixture = self.fixture
        remove_git_dir(fixture.library_remote)

        with offline_github():
            plan = rov_core.plan_library_update(fixture.board)

        self.assertIsNotNone(plan.blocked_reason)
        self.assertNotEqual(plan.blocked_reason, "remote library history diverged")
        self.assertIn("git", plan.blocked_reason.lower())
        self.assertEqual(plan.changed_files, ())
        self.assertEqual(fixture.submodule_head(), fixture.initial_library_commit)

    def test_missing_manifest_is_blocked(self):
        """A board without rov.project.json is blocked before any Git work."""
        fixture = self.fixture
        (fixture.board / "rov.project.json").unlink()

        with offline_github():
            plan = rov_core.plan_library_update(fixture.board)

        self.assertIsNotNone(plan.blocked_reason)
        self.assertIn("rov.project.json", plan.blocked_reason)
        self.assertEqual(plan.changed_files, ())
        self.assertEqual(fixture.board_head(), fixture.board_base_commit)

    def test_invalid_manifest_is_blocked(self):
        """A manifest that fails validation is blocked, never ignored."""
        fixture = self.fixture
        (fixture.board / "rov.project.json").write_text(
            json.dumps({**MANIFEST, "schema": 99}), encoding="utf-8"
        )

        with offline_github():
            plan = rov_core.plan_library_update(fixture.board)

        self.assertIsNotNone(plan.blocked_reason)
        self.assertIn("schema", plan.blocked_reason)
        self.assertEqual(plan.changed_files, ())

    def test_plan_never_uses_destructive_git_commands(self):
        """Planning never resets, stashes, cleans, restores, or commits."""
        fixture = self.fixture
        target = fixture.publish()
        guarded = forbid_git_verb(
            rov_core.run_git, "reset", "stash", "clean", "restore", "commit", "add"
        )

        with offline_github(), mock.patch.object(rov_core, "run_git", guarded):
            plan = rov_core.plan_library_update(fixture.board)

        self.assertIsNone(plan.blocked_reason)
        self.assertEqual(plan.target_commit, target)
        self.assertEqual(fixture.board_branch(), BASE_BRANCH)
        self.assertEqual(fixture.submodule_head(), fixture.initial_library_commit)


class TestApplyLibraryUpdate(LibraryUpdateTestCase):
    def test_apply_updates_submodule_and_creates_branch_commit(self):
        """Applying moves the submodule, commits it, and leaves master alone."""
        fixture = self.fixture
        target = fixture.publish()
        with offline_github():
            plan = rov_core.plan_library_update(fixture.board)
        guarded = forbid_git_verb(rov_core.run_git, "reset", "stash", "clean", "restore", "push")

        with offline_github(), mock.patch.object(rov_core, "run_git", guarded):
            result = rov_core.apply_library_update(
                fixture.board, plan, UPDATE_BRANCH, COMMIT_MESSAGE
            )

        self.assertEqual(result.status, rov_core.STATUS_PASS, result.message)
        self.assertIn(UPDATE_BRANCH, result.message)
        self.assertIn("1 changed library file", result.message)
        self.assertEqual(fixture.board_branch(), UPDATE_BRANCH)
        self.assertEqual(fixture.submodule_head(), target)
        self.assertTrue(fixture.submodule_is_detached())
        self.assertEqual(fixture.head_subject(), COMMIT_MESSAGE)
        self.assertTrue(fixture.board_clean())
        # The base branch and the board remote are untouched.
        self.assertEqual(fixture.rev(fixture.board, BASE_BRANCH), fixture.board_base_commit)
        self.assertEqual(fixture.remote_heads(), {f"refs/heads/{BASE_BRANCH}": fixture.board_base_commit})
        self.assertEqual(fixture.changed_between(BASE_BRANCH, UPDATE_BRANCH), [LIBRARY_PATH])

    def test_apply_reports_pass_when_there_is_nothing_to_do(self):
        """An up-to-date board is a PASS that creates no branch."""
        fixture = self.fixture
        with offline_github():
            plan = rov_core.plan_library_update(fixture.board)
            result = rov_core.apply_library_update(
                fixture.board, plan, UPDATE_BRANCH, COMMIT_MESSAGE
            )

        self.assertEqual(result.status, rov_core.STATUS_PASS, result.message)
        self.assertEqual(fixture.board_branch(), BASE_BRANCH)
        self.assertEqual(fixture.board_head(), fixture.board_base_commit)

    def test_apply_of_a_blocked_plan_changes_nothing(self):
        """A blocked plan is never applied, whatever the caller requests."""
        fixture = self.fixture
        with offline_github():
            plan = rov_core.plan_library_update(fixture.board)
        blocked = rov_core.LibraryUpdatePlan(
            current_commit=plan.current_commit,
            target_commit="deadbeef",
            changed_files=("Symbols/part.txt",),
            blocked_reason="remote library history diverged",
        )
        guarded = forbid_git_verb(
            rov_core.run_git, "switch", "checkout", "commit", "add", "push"
        )

        with offline_github(), mock.patch.object(rov_core, "run_git", guarded):
            result = rov_core.apply_library_update(
                fixture.board, blocked, UPDATE_BRANCH, COMMIT_MESSAGE, push=True, create_pr=True
            )

        self.assertEqual(result.status, rov_core.STATUS_BLOCKED, result.message)
        self.assertIn("remote library history diverged", result.message)
        self.assertEqual(fixture.board_branch(), BASE_BRANCH)
        self.assertEqual(fixture.board_head(), fixture.board_base_commit)
        self.assertEqual(fixture.remote_heads(), {f"refs/heads/{BASE_BRANCH}": fixture.board_base_commit})

    def test_apply_never_pushes_base_branch(self):
        """A base-branch request is blocked before any push, commit, or switch."""
        fixture = self.fixture
        fixture.publish()
        with offline_github():
            plan = rov_core.plan_library_update(fixture.board)
        second = fixture.commit_board("second board change\n")
        guarded = forbid_git_verb(
            rov_core.run_git, "push", "switch", "checkout", "commit", "add"
        )

        with offline_github(), mock.patch.object(rov_core, "run_git", guarded):
            result = rov_core.apply_library_update(
                fixture.board, plan, BASE_BRANCH, COMMIT_MESSAGE, push=True, create_pr=True
            )

        self.assertEqual(result.status, rov_core.STATUS_BLOCKED, result.message)
        self.assertIn(BASE_BRANCH, result.message)
        # Local master keeps its own commit and is still the checked-out branch.
        self.assertEqual(fixture.board_head(), second)
        self.assertEqual(fixture.rev(fixture.board, BASE_BRANCH), second)
        self.assertEqual(fixture.board_branch(), BASE_BRANCH)
        # The remote still only has the commit the fixture pushed.
        self.assertEqual(fixture.remote_heads(), {f"refs/heads/{BASE_BRANCH}": fixture.board_base_commit})
        self.assertEqual(fixture.submodule_head(), fixture.initial_library_commit)
        self.assertTrue(fixture.board_clean())

    def test_apply_pushes_only_the_update_branch(self):
        """--push publishes the update branch and nothing else."""
        fixture = self.fixture
        fixture.publish()
        with offline_github():
            plan = rov_core.plan_library_update(fixture.board)

        with recording_git_verb(rov_core.run_git, "push", []) as pushed, offline_github():
            result = rov_core.apply_library_update(
                fixture.board, plan, UPDATE_BRANCH, COMMIT_MESSAGE, push=True
            )

        self.assertEqual(result.status, rov_core.STATUS_PASS, result.message)
        self.assertTrue(pushed, "the update branch must be pushed")
        for args in pushed:
            self.assertIn(f"refs/heads/{UPDATE_BRANCH}", " ".join(args))
            self.assertNotIn(f"refs/heads/{BASE_BRANCH}", " ".join(args))
        heads = fixture.remote_heads()
        self.assertEqual(
            set(heads), {f"refs/heads/{BASE_BRANCH}", f"refs/heads/{UPDATE_BRANCH}"}
        )
        self.assertEqual(heads[f"refs/heads/{BASE_BRANCH}"], fixture.board_base_commit)
        self.assertEqual(heads[f"refs/heads/{UPDATE_BRANCH}"], fixture.board_head())
        self.assertTrue(fixture.board_clean())

    def test_existing_update_pr_is_reused_without_duplicate_push(self):
        """Reapplying a spent plan leaves the branch and its pull request alone.

        Once the update branch is published, replaying the same plan finds the
        board already at the target. The result must not report a commit it did
        not make, must not push the branch again, and must not ask GitHub for or
        open a second pull request.
        """
        fixture = self.fixture
        fixture.publish()
        with offline_github():
            plan = rov_core.plan_library_update(fixture.board)
            first = rov_core.apply_library_update(
                fixture.board, plan, UPDATE_BRANCH, COMMIT_MESSAGE, push=True
            )
        self.assertEqual(first.status, rov_core.STATUS_PASS, first.message)
        local_commit = fixture.board_head()
        remote_before = fixture.remote_heads()

        github = RecordingGitHub(existing_url=PR_URL)
        with recording_git_verb(rov_core.run_git, "push", []) as pushed, mock.patch.object(
            rov_core, "_gh_is_available", github.available
        ), mock.patch.object(rov_core, "_run_gh", github.run):
            result = rov_core.apply_library_update(
                fixture.board, plan, UPDATE_BRANCH, COMMIT_MESSAGE, push=True, create_pr=True
            )

        self.assertEqual(result.status, rov_core.STATUS_PASS, result.message)
        self.assertEqual(github.created, [], "an existing pull request must not be recreated")
        self.assertEqual(github.calls, [], "an up-to-date board must not contact GitHub")
        self.assertEqual(pushed, [], "an identical branch must not be pushed again")
        self.assertEqual(fixture.remote_heads(), remote_before)
        self.assertEqual(fixture.board_head(), local_commit)
        self.assertEqual(fixture.board_branch(), UPDATE_BRANCH)
        self.assertTrue(fixture.board_clean())

    def test_publishing_reuses_an_open_pull_request_without_pushing_again(self):
        """The publish step reuses an open pull request and skips an identical push.

        ``apply_library_update`` now returns a PASS no-op before publishing when
        the board already records the target, so this covers the publish step
        itself: an ``origin`` branch that already holds the prepared commit is
        not pushed again, and an open pull request for it is reported instead of
        a second one being opened.
        """
        fixture = self.fixture
        fixture.publish()
        with offline_github():
            plan = rov_core.plan_library_update(fixture.board)
        prepared = rov_core.apply_library_update(
            fixture.board, plan, UPDATE_BRANCH, COMMIT_MESSAGE
        )
        self.assertEqual(prepared.status, rov_core.STATUS_PASS, prepared.message)
        commit = fixture.board_head()
        run_git(fixture.board, "push", "origin", f"{UPDATE_BRANCH}:{UPDATE_BRANCH}")
        remote_before = fixture.remote_heads()

        github = RecordingGitHub(existing_url=PR_URL)
        with recording_git_verb(rov_core.run_git, "push", []) as pushed, mock.patch.object(
            rov_core, "_gh_is_available", github.available
        ), mock.patch.object(rov_core, "_run_gh", github.run):
            published = rov_core._publish_update_branch(
                fixture.board, UPDATE_BRANCH, commit, True, BASE_BRANCH, plan, LIBRARY_PATH
            )

        self.assertEqual(published, PR_URL)
        self.assertEqual(github.created, [], "an existing pull request must not be recreated")
        self.assertEqual(
            github.calls,
            [
                [
                    "pr",
                    "list",
                    "--head",
                    UPDATE_BRANCH,
                    "--state",
                    "open",
                    "--json",
                    "url",
                    "--jq",
                    ".[0].url // empty",
                ]
            ],
            github.calls,
        )
        self.assertEqual(pushed, [], "an identical branch must not be pushed again")
        self.assertEqual(fixture.remote_heads(), remote_before)

    def test_a_closed_pull_request_is_not_reported_as_the_update(self):
        """A closed pull request must not be reused as a successful result.

        The branch lookup used to resolve the branch with no state filter, which
        also matched a pull request that was closed while its branch survived.
        The run then reported that dead pull request as the outcome, so the board
        silently never received the update while the workflow reported success.
        """
        fixture = self.fixture
        fixture.publish()
        with offline_github():
            plan = rov_core.plan_library_update(fixture.board)
        prepared = rov_core.apply_library_update(
            fixture.board, plan, UPDATE_BRANCH, COMMIT_MESSAGE
        )
        self.assertEqual(prepared.status, rov_core.STATUS_PASS, prepared.message)
        commit = fixture.board_head()
        run_git(fixture.board, "push", "origin", f"{UPDATE_BRANCH}:{UPDATE_BRANCH}")

        # No open pull request for the branch, which is what a closed one looks
        # like to a state-scoped lookup.
        github = RecordingGitHub(existing_url=None)
        with mock.patch.object(
            rov_core, "_gh_is_available", github.available
        ), mock.patch.object(rov_core, "_run_gh", github.run):
            published = rov_core._publish_update_branch(
                fixture.board, UPDATE_BRANCH, commit, True, BASE_BRANCH, plan, LIBRARY_PATH
            )

        self.assertEqual(published, PR_URL, "a new pull request must be opened")
        self.assertEqual(len(github.created), 1, github.created)
        lookup = github.calls[0]
        self.assertEqual(lookup[:2], ["pr", "list"])
        self.assertIn("--state", lookup)
        self.assertEqual(lookup[lookup.index("--state") + 1], "open")

    def test_pull_request_without_push_is_blocked_before_any_change(self):
        """create_pr without push is refused, not silently dropped."""
        fixture = self.fixture
        fixture.publish()
        with offline_github():
            plan = rov_core.plan_library_update(fixture.board)
        guarded = forbid_git_verb(
            rov_core.run_git, "switch", "checkout", "add", "commit", "push"
        )

        with mock.patch.object(rov_core, "run_git", guarded):
            result = rov_core.apply_library_update(
                fixture.board, plan, UPDATE_BRANCH, COMMIT_MESSAGE, push=False, create_pr=True
            )

        self.assertEqual(result.status, rov_core.STATUS_BLOCKED, result.message)
        self.assertIn("push=True", result.message)
        self.assertIn("pull request", result.message)
        self.assertEqual(fixture.board_branch(), BASE_BRANCH)
        self.assertEqual(fixture.board_head(), fixture.board_base_commit)
        self.assertEqual(fixture.submodule_head(), fixture.initial_library_commit)
        self.assertEqual(fixture.remote_heads(), {f"refs/heads/{BASE_BRANCH}": fixture.board_base_commit})
        self.assertTrue(fixture.board_clean())

    def test_reapplying_a_spent_plan_is_a_no_op(self):
        """A stale plan is a PASS no-op: no commit, no push, no pull request."""
        fixture = self.fixture
        fixture.publish()
        with offline_github():
            plan = rov_core.plan_library_update(fixture.board)
            first = rov_core.apply_library_update(
                fixture.board, plan, UPDATE_BRANCH, COMMIT_MESSAGE
            )
        self.assertEqual(first.status, rov_core.STATUS_PASS, first.message)
        target = fixture.submodule_head()
        local_commit = fixture.board_head()
        remote_before = fixture.remote_heads()
        github = RecordingGitHub(existing_url=PR_URL)

        with recording_git_verb(rov_core.run_git, "push", []) as pushed, mock.patch.object(
            rov_core, "_gh_is_available", github.available
        ), mock.patch.object(rov_core, "_run_gh", github.run):
            result = rov_core.apply_library_update(
                fixture.board, plan, UPDATE_BRANCH, COMMIT_MESSAGE, push=True, create_pr=True
            )

        self.assertEqual(result.status, rov_core.STATUS_PASS, result.message)
        self.assertIn(plan.current_commit, result.message)
        self.assertIn(plan.target_commit, result.message)
        self.assertIn("0 changed library file(s)", result.message)
        self.assertNotIn(local_commit, result.message)
        self.assertEqual(pushed, [])
        self.assertEqual(github.calls, [])
        self.assertEqual(fixture.board_head(), local_commit)
        self.assertEqual(fixture.submodule_head(), target)
        self.assertEqual(fixture.remote_heads(), remote_before)
        self.assertEqual(fixture.board_branch(), UPDATE_BRANCH)
        self.assertTrue(fixture.board_clean())

    def test_apply_opens_one_pull_request_when_asked(self):
        """--push --pr publishes the branch and opens exactly one PR."""
        fixture = self.fixture
        fixture.publish()
        with offline_github():
            plan = rov_core.plan_library_update(fixture.board)
        github = RecordingGitHub()
        guarded = forbid_git_verb(rov_core.run_git, "reset", "stash", "clean", "restore")

        with mock.patch.object(rov_core, "_gh_is_available", github.available), mock.patch.object(
            rov_core, "_run_gh", github.run
        ), mock.patch.object(rov_core, "run_git", guarded):
            result = rov_core.apply_library_update(
                fixture.board, plan, UPDATE_BRANCH, COMMIT_MESSAGE, push=True, create_pr=True
            )

        self.assertEqual(result.status, rov_core.STATUS_PASS, result.message)
        self.assertIn(PR_URL, result.message)
        self.assertEqual(len(github.created), 1)
        create = github.created[0]
        self.assertEqual(create[:2], ["pr", "create"])
        self.assertEqual(create[create.index("--base") + 1], BASE_BRANCH)
        self.assertEqual(create[create.index("--head") + 1], UPDATE_BRANCH)
        self.assertEqual(create[create.index("--title") + 1], rov_core.LIBRARY_UPDATE_PR_TITLE)
        self.assertIn(fixture.board_head(), fixture.remote_heads()[f"refs/heads/{UPDATE_BRANCH}"])

    def test_pull_request_failure_is_reported_after_the_branch_was_pushed(self):
        """A gh failure is a BLOCKED result that keeps the pushed branch."""
        fixture = self.fixture
        fixture.publish()
        with offline_github():
            plan = rov_core.plan_library_update(fixture.board)
        github = RecordingGitHub(create_error="gh: authentication required")

        with mock.patch.object(rov_core, "_gh_is_available", github.available), mock.patch.object(
            rov_core, "_run_gh", github.run
        ):
            result = rov_core.apply_library_update(
                fixture.board, plan, UPDATE_BRANCH, COMMIT_MESSAGE, push=True, create_pr=True
            )

        self.assertEqual(result.status, rov_core.STATUS_BLOCKED, result.message)
        self.assertIn("authentication required", result.message)
        self.assertEqual(len(github.created), 1)
        # The branch is already published, so the commit is not lost.
        self.assertEqual(
            fixture.remote_heads()[f"refs/heads/{UPDATE_BRANCH}"], fixture.board_head()
        )
        self.assertEqual(fixture.head_subject(), COMMIT_MESSAGE)
        self.assertTrue(fixture.board_clean())

    def test_missing_github_cli_is_blocked_before_publishing(self):
        """Without gh on PATH the local branch is kept and nothing is pushed."""
        fixture = self.fixture
        fixture.publish()
        with offline_github():
            plan = rov_core.plan_library_update(fixture.board)
        guarded = forbid_git_verb(rov_core.run_git, "push")

        with mock.patch.object(rov_core, "_gh_is_available", return_value=False), mock.patch.object(
            rov_core, "run_git", guarded
        ):
            result = rov_core.apply_library_update(
                fixture.board, plan, UPDATE_BRANCH, COMMIT_MESSAGE, push=True, create_pr=True
            )

        self.assertEqual(result.status, rov_core.STATUS_BLOCKED, result.message)
        self.assertIn("gh", result.message)
        self.assertEqual(fixture.remote_heads(), {f"refs/heads/{BASE_BRANCH}": fixture.board_base_commit})
        self.assertEqual(fixture.board_branch(), UPDATE_BRANCH)
        self.assertEqual(fixture.head_subject(), COMMIT_MESSAGE)
        self.assertTrue(fixture.board_clean())

    def test_push_over_a_different_remote_branch_is_blocked(self):
        """An update branch that already holds other work is never overwritten."""
        fixture = self.fixture
        fixture.publish()
        with offline_github():
            plan = rov_core.plan_library_update(fixture.board)
        seeded = fixture.seed_remote_update_branch("unrelated work\n")
        guarded = forbid_git_verb(rov_core.run_git, "push")

        with offline_github(), mock.patch.object(rov_core, "run_git", guarded):
            result = rov_core.apply_library_update(
                fixture.board, plan, UPDATE_BRANCH, COMMIT_MESSAGE, push=True
            )

        self.assertEqual(result.status, rov_core.STATUS_BLOCKED, result.message)
        self.assertIn(UPDATE_BRANCH, result.message)
        self.assertEqual(fixture.remote_heads()[f"refs/heads/{UPDATE_BRANCH}"], seeded)

    def test_dirty_board_blocks_apply(self):
        """An uncommitted board change is refused before any branch work."""
        fixture = self.fixture
        fixture.publish()
        with offline_github():
            plan = rov_core.plan_library_update(fixture.board)
        (fixture.board / "notes.txt").write_text("work in progress\n", encoding="utf-8")
        guarded = forbid_git_verb(rov_core.run_git, "switch", "checkout", "commit", "add", "push")

        with offline_github(), mock.patch.object(rov_core, "run_git", guarded):
            result = rov_core.apply_library_update(
                fixture.board, plan, UPDATE_BRANCH, COMMIT_MESSAGE, push=True
            )

        self.assertEqual(result.status, rov_core.STATUS_BLOCKED, result.message)
        self.assertIn("uncommitted changes", result.message)
        self.assertEqual(fixture.board_branch(), BASE_BRANCH)
        self.assertEqual(fixture.submodule_head(), fixture.initial_library_commit)
        self.assertEqual(fixture.head_subject(), "track the approved library remote")

    def test_dirty_submodule_blocks_apply(self):
        """A local library edit is refused and preserved."""
        fixture = self.fixture
        fixture.publish()
        with offline_github():
            plan = rov_core.plan_library_update(fixture.board)
        edited = fixture.submodule / "Symbols" / "part.txt"
        edited.write_text("local library edit\n", encoding="utf-8")
        guarded = forbid_git_verb(rov_core.run_git, "switch", "checkout", "commit", "add", "push")

        with offline_github(), mock.patch.object(rov_core, "run_git", guarded):
            result = rov_core.apply_library_update(
                fixture.board, plan, UPDATE_BRANCH, COMMIT_MESSAGE, push=True
            )

        self.assertEqual(result.status, rov_core.STATUS_BLOCKED, result.message)
        self.assertIn("submodule", result.message)
        self.assertIn("local changes", result.message)
        self.assertEqual(edited.read_text(encoding="utf-8"), "local library edit\n")
        self.assertEqual(fixture.board_branch(), BASE_BRANCH)
        self.assertEqual(fixture.board_head(), fixture.board_base_commit)

    def test_branch_from_a_different_base_is_blocked(self):
        """An existing branch that did not come from the base is not reused."""
        fixture = self.fixture
        fixture.publish()
        with offline_github():
            plan = rov_core.plan_library_update(fixture.board)
        older = fixture.rev(fixture.board, BASE_BRANCH)
        moved = fixture.commit_board("board work\n")
        run_git(fixture.board, "switch", "-c", UPDATE_BRANCH, older)
        run_git(fixture.board, "switch", BASE_BRANCH)
        guarded = forbid_git_verb(
            rov_core.run_git, "switch", "checkout", "commit", "add", "push"
        )

        with offline_github(), mock.patch.object(rov_core, "run_git", guarded):
            result = rov_core.apply_library_update(
                fixture.board, plan, UPDATE_BRANCH, COMMIT_MESSAGE, push=True
            )

        self.assertEqual(result.status, rov_core.STATUS_BLOCKED, result.message)
        self.assertIn(UPDATE_BRANCH, result.message)
        self.assertEqual(fixture.rev(fixture.board, UPDATE_BRANCH), older)
        self.assertEqual(fixture.board_head(), moved)
        self.assertEqual(fixture.submodule_head(), fixture.initial_library_commit)
        self.assertTrue(fixture.board_clean())

    def test_post_switch_failure_names_the_branch_and_how_to_return(self):
        """Important I3: a failure after the branch switch must be actionable.

        ``apply_library_update`` checks the update branch out before it moves the
        submodule, so a failure after that point leaves the developer on a
        shared, bot-published branch they did not choose. Reporting only the
        failure would leave them guessing where they are and what to do.
        """
        fixture = self.fixture
        fixture.publish()
        with offline_github():
            plan = rov_core.plan_library_update(fixture.board)
        self.assertIsNone(plan.blocked_reason)

        real_run_git = rov_core.run_git

        def fail_add(repo_dir, *args, **kwargs):
            if args[:1] == ("add",):
                return completed(128, stderr="index file corrupt")
            return real_run_git(repo_dir, *args, **kwargs)

        with offline_github(), mock.patch.object(rov_core, "run_git", fail_add):
            result = rov_core.apply_library_update(
                fixture.board, plan, UPDATE_BRANCH, COMMIT_MESSAGE
            )

        self.assertEqual(result.status, rov_core.STATUS_BLOCKED, result.message)
        self.assertIn("index file corrupt", result.message)
        # The board really is on the update branch, so the message has to say so.
        self.assertEqual(fixture.board_branch(), UPDATE_BRANCH)
        self.assertIn(UPDATE_BRANCH, result.message)
        self.assertIn(f"from {BASE_BRANCH}", result.message)
        self.assertIn(f"git switch {BASE_BRANCH}", result.message)
        self.assertIn("Nothing was pushed", result.message)
        self.assertIn("no pull request was opened", result.message)

    def test_post_switch_failure_does_not_leave_a_stale_staging_area(self):
        """The recovery path must not imply work was published."""
        fixture = self.fixture
        fixture.publish()
        with offline_github():
            plan = rov_core.plan_library_update(fixture.board)

        real_run_git = rov_core.run_git

        def fail_checkout(repo_dir, *args, **kwargs):
            if args[:1] == ("checkout",):
                return completed(1, stderr="pathspec did not match")
            return real_run_git(repo_dir, *args, **kwargs)

        with offline_github(), mock.patch.object(rov_core, "run_git", fail_checkout):
            result = rov_core.apply_library_update(
                fixture.board, plan, UPDATE_BRANCH, COMMIT_MESSAGE, push=True
            )

        self.assertEqual(result.status, rov_core.STATUS_BLOCKED, result.message)
        self.assertIn(UPDATE_BRANCH, result.message)
        # Nothing was pushed, and the protected base branch is untouched.
        self.assertNotIn(f"refs/heads/{UPDATE_BRANCH}", fixture.remote_heads())
        self.assertEqual(fixture.submodule_head(), fixture.initial_library_commit)
        self.assertEqual(fixture.head_subject(), "track the approved library remote")

    def test_every_post_switch_blocked_result_carries_the_recovery_sentence(self):
        """One fixed sentence, so CI and the CLI read the same contract."""
        recovery = rov_core._update_branch_recovery(UPDATE_BRANCH, BASE_BRANCH)
        self.assertIn(UPDATE_BRANCH, recovery)
        self.assertIn(BASE_BRANCH, recovery)
        self.assertIn(f"git switch {BASE_BRANCH}", recovery)

    def test_apply_stages_only_the_configured_library_path(self):
        """Only the configured submodule path is staged and committed."""
        fixture = self.fixture
        fixture.publish()
        with offline_github():
            plan = rov_core.plan_library_update(fixture.board)

        with recording_git_verb(rov_core.run_git, "add", []) as staged, offline_github():
            result = rov_core.apply_library_update(
                fixture.board, plan, UPDATE_BRANCH, COMMIT_MESSAGE
            )

        self.assertEqual(result.status, rov_core.STATUS_PASS, result.message)
        self.assertEqual(staged, [("add", "--", LIBRARY_PATH)])
        self.assertEqual(
            run_git(fixture.board, "show", "--name-only", "--format=", "HEAD").split(),
            [LIBRARY_PATH],
        )


class TestBoardSyncLibraryCommand(LibraryUpdateTestCase):
    """``rov board sync-library`` end to end against the local Git fixture.

    The other CLI tests mock Git away; these run the real command so the flag
    contract itself - dry run by default, no branch without ``--apply``, no
    push without ``--push``, no pull request without ``--push`` and ``--pr`` -
    is proven against real repositories.
    """

    def run_cli(self, *extra: str) -> tuple[int, str]:
        """Run the command and return its exit code and printed output."""
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = rov.main(
                ["board", "sync-library", "--project-dir", str(self.fixture.board), *extra]
            )
        return code, buffer.getvalue()

    def test_dry_run_reports_the_plan_and_changes_nothing(self):
        """Without --apply the command only reports the planned change."""
        fixture = self.fixture
        target = fixture.publish()

        with offline_github():
            code, output = self.run_cli()

        self.assertEqual(code, 0, output)
        self.assertIn(fixture.initial_library_commit, output)
        self.assertIn(target, output)
        self.assertIn("Symbols/part.txt", output)
        self.assertIn("--apply", output)
        self.assertEqual(fixture.board_branch(), BASE_BRANCH)
        self.assertEqual(fixture.board_head(), fixture.board_base_commit)
        self.assertEqual(fixture.submodule_head(), fixture.initial_library_commit)
        self.assertEqual(fixture.remote_heads(), {f"refs/heads/{BASE_BRANCH}": fixture.board_base_commit})

    def test_apply_uses_the_default_branch_and_commit_message(self):
        """--apply prepares the documented default branch and message."""
        fixture = self.fixture
        fixture.publish()

        with offline_github():
            code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        self.assertEqual(fixture.board_branch(), rov_core.LIBRARY_UPDATE_BRANCH)
        self.assertEqual(fixture.head_subject(), rov_core.LIBRARY_UPDATE_COMMIT_MESSAGE)
        self.assertEqual(fixture.rev(fixture.board, BASE_BRANCH), fixture.board_base_commit)
        self.assertEqual(fixture.remote_heads(), {f"refs/heads/{BASE_BRANCH}": fixture.board_base_commit})

    def test_branch_and_commit_message_flags_are_honored(self):
        """--branch and --commit-message override the defaults."""
        fixture = self.fixture
        fixture.publish()

        with offline_github():
            code, output = self.run_cli(
                "--apply", "--branch", "chore/custom-library", "--commit-message", "custom message"
            )

        self.assertEqual(code, 0, output)
        self.assertEqual(fixture.board_branch(), "chore/custom-library")
        self.assertEqual(fixture.head_subject(), "custom message")
        self.assertEqual(fixture.rev(fixture.board, BASE_BRANCH), fixture.board_base_commit)

    def test_push_publishes_only_the_update_branch(self):
        """--push publishes the update branch and leaves the base branch alone."""
        fixture = self.fixture
        fixture.publish()

        with offline_github():
            code, output = self.run_cli("--apply", "--push")

        self.assertEqual(code, 0, output)
        heads = fixture.remote_heads()
        self.assertEqual(
            set(heads), {f"refs/heads/{BASE_BRANCH}", f"refs/heads/{UPDATE_BRANCH}"}
        )
        self.assertEqual(heads[f"refs/heads/{BASE_BRANCH}"], fixture.board_base_commit)
        self.assertEqual(heads[f"refs/heads/{UPDATE_BRANCH}"], fixture.board_head())

    def test_pr_without_github_is_blocked_before_any_push(self):
        """--push --pr without gh on PATH reports BLOCKED and publishes nothing."""
        fixture = self.fixture
        fixture.publish()
        guarded = forbid_git_verbs("push")

        with offline_github(), guarded:
            code, output = self.run_cli("--apply", "--push", "--pr")

        self.assertEqual(code, 2, output)
        self.assertIn("BLOCKED", output)
        self.assertIn("gh", output)
        self.assertEqual(fixture.remote_heads(), {f"refs/heads/{BASE_BRANCH}": fixture.board_base_commit})
        self.assertEqual(fixture.board_branch(), UPDATE_BRANCH)

    def test_push_with_pr_opens_one_pull_request(self):
        """--push --pr publishes the branch and opens exactly one pull request."""
        fixture = self.fixture
        fixture.publish()
        github = RecordingGitHub()

        with mock.patch.object(rov_core, "_gh_is_available", github.available), mock.patch.object(
            rov_core, "_run_gh", github.run
        ):
            code, output = self.run_cli("--apply", "--push", "--pr")

        self.assertEqual(code, 0, output)
        self.assertIn(PR_URL, output)
        self.assertEqual(len(github.created), 1)
        self.assertEqual(github.calls[0][:2], ["pr", "list"])
        # The lookup is scoped to open pull requests on the branch.
        self.assertIn("--state", github.calls[0])
        self.assertEqual(
            github.calls[0][github.calls[0].index("--state") + 1], "open"
        )
        self.assertEqual(
            fixture.remote_heads()[f"refs/heads/{UPDATE_BRANCH}"], fixture.board_head()
        )

    def test_repeat_run_opens_no_duplicate_pull_request(self):
        """A second run of the same command never opens a second pull request."""
        fixture = self.fixture
        fixture.publish()
        first = RecordingGitHub()
        with mock.patch.object(rov_core, "_gh_is_available", first.available), mock.patch.object(
            rov_core, "_run_gh", first.run
        ):
            self.assertEqual(self.run_cli("--apply", "--push", "--pr")[0], 0)
        self.assertEqual(len(first.created), 1)
        local_commit = fixture.board_head()
        remote_before = fixture.remote_heads()

        second = RecordingGitHub(existing_url=PR_URL)
        with mock.patch.object(rov_core, "_gh_is_available", second.available), mock.patch.object(
            rov_core, "_run_gh", second.run
        ):
            code, output = self.run_cli("--apply", "--push", "--pr")

        self.assertEqual(code, 0, output)
        self.assertIn("already at", output)
        self.assertEqual(second.created, [], "a second pull request must not be created")
        self.assertEqual(second.calls, [], "an up-to-date board must not touch GitHub")
        self.assertEqual(fixture.board_head(), local_commit)
        self.assertEqual(fixture.remote_heads(), remote_before)

    def test_blocked_plan_is_reported_by_the_command(self):
        """A diverged remote is reported with its reason and nothing changes."""
        fixture = self.fixture
        fixture.publish_divergent()

        with offline_github():
            code, output = self.run_cli("--apply", "--push")

        self.assertEqual(code, 2, output)
        self.assertIn(rov_core.DIVERGED_HISTORY_REASON, output)
        self.assertEqual(fixture.board_branch(), BASE_BRANCH)
        self.assertEqual(fixture.board_head(), fixture.board_base_commit)
        self.assertEqual(fixture.submodule_head(), fixture.initial_library_commit)
        self.assertEqual(fixture.remote_heads(), {f"refs/heads/{BASE_BRANCH}": fixture.board_base_commit})


class TestSyncLibraryMachineMarkers(LibraryUpdateTestCase):
    """``rov board sync-library`` ends with exactly one marker line.

    The reusable update workflow reads nothing but these markers, so they are
    the published machine interface: ``PR_URL=<url>`` when an update pull
    request is open for the update branch, ``NO_CHANGE`` when the board already
    records the approved library revision, and no marker at all for every other
    outcome. A marker never replaces the human report, and it never changes an
    exit code.
    """

    def run_cli(self, *extra: str) -> tuple[int, str]:
        """Run the command and return its exit code and printed output."""
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = rov.main(
                ["board", "sync-library", "--project-dir", str(self.fixture.board), *extra]
            )
        return code, buffer.getvalue()

    def marker_lines(self, output: str) -> list[str]:
        """Return every marker line in the order the CLI printed them."""
        return [
            line
            for line in output.splitlines()
            if line.startswith("PR_URL=") or line == "NO_CHANGE"
        ]

    def test_created_pull_request_ends_with_a_pr_url_marker(self):
        """An opened pull request is reported as the final ``PR_URL=`` line."""
        fixture = self.fixture
        fixture.publish()
        github = RecordingGitHub()

        with mock.patch.object(rov_core, "_gh_is_available", github.available), mock.patch.object(
            rov_core, "_run_gh", github.run
        ):
            code, output = self.run_cli("--apply", "--push", "--pr")

        self.assertEqual(code, 0, output)
        self.assertEqual([f"PR_URL={PR_URL}"], self.marker_lines(output))
        self.assertEqual(
            f"PR_URL={PR_URL}",
            output.splitlines()[-1],
            "the marker must be last so a caller can read the final line",
        )
        self.assertIn("[PASS] library-update:", output, "the human report is kept")
        self.assertNotIn("NO_CHANGE", output)

    def test_reused_pull_request_reports_the_existing_url(self):
        """A pull request that already exists is reported, never duplicated."""
        fixture = self.fixture
        fixture.publish()
        github = RecordingGitHub(existing_url=PR_URL)

        with mock.patch.object(rov_core, "_gh_is_available", github.available), mock.patch.object(
            rov_core, "_run_gh", github.run
        ):
            code, output = self.run_cli("--apply", "--push", "--pr")

        self.assertEqual(code, 0, output)
        self.assertEqual([f"PR_URL={PR_URL}"], self.marker_lines(output))
        self.assertEqual([], github.created, "the existing pull request is reused")

    def test_unchanged_library_ends_with_a_no_change_marker(self):
        """A board that already records the approved revision reports NO_CHANGE."""
        with offline_github():
            code, output = self.run_cli("--apply", "--push", "--pr")

        self.assertEqual(code, 0, output)
        self.assertEqual(["NO_CHANGE"], self.marker_lines(output))
        self.assertEqual("NO_CHANGE", output.splitlines()[-1])
        self.assertIn("[PASS] library-update:", output, "the human report is kept")

    def test_a_spent_update_plan_reports_no_change(self):
        """A stale plan that is already applied reports NO_CHANGE, not work."""
        fixture = self.fixture
        fixture.publish()
        first = RecordingGitHub()
        with mock.patch.object(rov_core, "_gh_is_available", first.available), mock.patch.object(
            rov_core, "_run_gh", first.run
        ):
            self.assertEqual(self.run_cli("--apply", "--push", "--pr")[0], 0)
        local_commit = fixture.board_head()
        remote_before = fixture.remote_heads()

        second = RecordingGitHub(existing_url=PR_URL)
        with mock.patch.object(rov_core, "_gh_is_available", second.available), mock.patch.object(
            rov_core, "_run_gh", second.run
        ):
            code, output = self.run_cli("--apply", "--push", "--pr")

        self.assertEqual(code, 0, output)
        self.assertEqual(["NO_CHANGE"], self.marker_lines(output))
        self.assertEqual([], second.calls, "an unchanged board must not touch GitHub")
        self.assertEqual(fixture.board_head(), local_commit)
        self.assertEqual(fixture.remote_heads(), remote_before)

    def test_a_dry_run_reports_neither_marker(self):
        """A dry run plans an update without claiming one was opened or needed."""
        fixture = self.fixture
        target = fixture.publish()

        with offline_github():
            code, output = self.run_cli()

        self.assertEqual(code, 0, output)
        self.assertIn(target, output)
        self.assertEqual([], self.marker_lines(output))

    def test_a_local_update_reports_neither_marker(self):
        """A committed but unpublished update is neither a PR nor a no-op."""
        fixture = self.fixture
        fixture.publish()

        with offline_github():
            code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        self.assertIn("[PASS] library-update:", output)
        self.assertEqual(fixture.board_branch(), UPDATE_BRANCH)
        self.assertEqual([], self.marker_lines(output))

    def test_a_blocked_run_reports_no_marker(self):
        """A blocked update never claims a pull request or a no-op."""
        fixture = self.fixture
        fixture.publish()
        (fixture.board / "notes.txt").write_text("work in progress\n", encoding="utf-8")

        with offline_github():
            code, output = self.run_cli("--apply", "--push", "--pr")

        self.assertEqual(code, 2, output)
        self.assertIn("BLOCKED", output)
        self.assertEqual([], self.marker_lines(output))

    def test_markers_are_rendered_by_a_shared_helper(self):
        """The marker rules live in one helper, not in the command handler."""
        opened = rov_core.CheckResult(
            "library-update", rov_core.STATUS_PASS, "opened", pull_request_url=PR_URL
        )
        current = rov_core.CheckResult(
            "library-update", rov_core.STATUS_PASS, "current", no_change=True
        )
        plain = rov_core.CheckResult("library-update", rov_core.STATUS_PASS, "applied")

        for results, expected in (
            ([opened], f"PR_URL={PR_URL}"),
            ([current], "NO_CHANGE"),
            ([plain, opened], f"PR_URL={PR_URL}"),
            ([opened, current], f"PR_URL={PR_URL}"),
            ([plain], ""),
            ([], ""),
        ):
            with self.subTest(expected=expected):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    rov.print_library_update_markers(results)
                printed = buffer.getvalue().splitlines()
                self.assertEqual([expected] if expected else [], printed)


if __name__ == "__main__":
    unittest.main()