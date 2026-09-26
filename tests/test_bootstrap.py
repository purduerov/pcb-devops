import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

DEVOPS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DEVOPS_DIR / "scripts"))

import rov_core

GIT_AVAILABLE = shutil.which("git") is not None
FORBIDDEN_GIT_VERBS = frozenset(
    {"checkout", "reset", "stash", "clean", "restore", "switch", "push", "rm"}
)
CMD_AVAILABLE = shutil.which("cmd") is not None

# Resolved lazily by posix_shell(); "" means "probed and unusable".
_POSIX_SHELL: str | None = None


def posix_shell() -> str | None:
    """Return a shell that can execute a script file inside a temp tree.

    On Windows the WSL ``bash.exe`` translates the inherited working directory,
    so a temp tree is reachable once the script path is given in POSIX form.
    The probe proves the shell works on this host instead of assuming it, and
    callers skip when it does not.
    """
    global _POSIX_SHELL
    if _POSIX_SHELL is not None:
        return _POSIX_SHELL or None
    for candidate in ("sh", "bash", "dash"):
        path = shutil.which(candidate)
        if path is None:
            continue
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            probe = Path(tmp) / "probe.sh"
            write_executable(probe, "#!/usr/bin/env sh\nprintf PROBE_OK\n")
            try:
                result = run_script(path, probe, cwd=Path(tmp))
            except (OSError, subprocess.SubprocessError):
                continue
            if result.returncode == 0 and "PROBE_OK" in result.stdout:
                _POSIX_SHELL = path
                return path
    _POSIX_SHELL = ""
    return None


def bash_shell() -> str | None:
    """Return a bash that can parse and run ``LAUNCH_KICAD.sh`` in a temp tree.

    The macOS/Linux launcher is a bash script, not a POSIX ``sh`` script: it uses
    ``BASH_SOURCE`` and ``[[ ... ]]``. ``posix_shell`` may return ``sh`` or
    ``dash``, so the launcher tests need a probe of their own that insists on
    bash. On Windows the WSL ``bash.exe`` is what provides it.
    """
    for candidate in ("bash",):
        path = shutil.which(candidate)
        if path is None:
            continue
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            probe = Path(tmp) / "probe.sh"
            write_executable(
                probe,
                "#!/usr/bin/env bash\n"
                'if [ "${BASH_SOURCE[0]:-}" = "" ]; then exit 3; fi\n'
                'if [[ "a" == "a" ]]; then printf BASH_OK; else exit 4; fi\n',
            )
            try:
                result = run_script(path, probe, cwd=Path(tmp))
            except (OSError, subprocess.SubprocessError):
                continue
            if result.returncode == 0 and "BASH_OK" in result.stdout:
                return path
    return None


def posix_path(shell: str, directory: Path) -> str:
    """Return the POSIX path of ``directory`` as the shell itself sees it."""
    result = subprocess.run(
        [shell, "-c", "pwd -P"], cwd=str(directory), capture_output=True, text=True, timeout=30
    )
    return result.stdout.strip()


def run_script(
    shell: str,
    script: Path,
    *args: str,
    cwd: Path,
    wrapper: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``script`` with ``shell``, passing the script path in POSIX form.

    A Windows path argument is mangled by the WSL launcher, so the path is
    resolved by the shell itself. This executes the real file, the way Git runs
    a hook, instead of feeding the text on stdin. ``wrapper`` is a generated
    script that replaces ``PATH`` before exec, which is the only portable way to
    control what the hook can find.
    """
    target = f"{posix_path(shell, script.parent)}/{script.name}"
    command = [shell, wrapper, target, *args] if wrapper else [shell, target, *args]
    return subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, timeout=60)


def write_executable(path: Path, text: str) -> None:
    """Write a script and mark it executable, ignoring Windows chmod limits."""
    path.write_text(text, encoding="utf-8", newline="\n")
    try:
        path.chmod(0o755)
    except OSError:
        pass


class RecordingGit:
    """Delegate to the real ``run_git`` while recording every argument list."""

    def __init__(self, real_run_git):
        self._real_run_git = real_run_git
        self.calls = []

    def __call__(self, repo_dir, *args, check=False, timeout=None):
        self.calls.append(tuple(args))
        return self._real_run_git(repo_dir, *args, check=check, timeout=timeout)


class GitFixtureMixin:
    """Version-safe local Git repositories. No network is ever used."""

    GIT_IDENTITY = (
        "-c", "user.email=rov-tests@example.invalid",
        "-c", "user.name=ROV Platform Tests",
        "-c", "commit.gpgsign=false",
    )

    def git(self, repo: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=str(repo), capture_output=True, text=True, timeout=60
        )
        self.assertEqual(
            result.returncode, 0, f"git {' '.join(args)} failed: {result.stderr.strip()}"
        )
        return result.stdout.strip()

    def git_ok(self, repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=str(repo), capture_output=True, text=True, timeout=60
        )

    def init_repo(self, repo: Path, branch: str = "master") -> Path:
        repo.mkdir(parents=True, exist_ok=True)
        self.git(repo, "init", "-q")
        self.git(repo, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
        return repo

    def commit_all(self, repo: Path, message: str) -> str:
        self.git(repo, "add", "-A")
        self.git(repo, *self.GIT_IDENTITY, "commit", "-q", "-m", message)
        return self.git(repo, "rev-parse", "HEAD")

    def make_library_remote(self, base: Path) -> tuple[Path, Path]:
        """Create a bare origin and a seed checkout of its master branch."""
        remote = self.init_repo(base / "library-remote.git")
        self.git(remote, "config", "core.bare", "true")
        seed = self.init_repo(base / "seed")
        (seed / "library.kicad_sym").write_text("(kicad_sym)\n", encoding="utf-8")
        self.commit_all(seed, "library v1")
        self.git(seed, "remote", "add", "origin", str(remote))
        self.git(seed, "push", "-q", "origin", "master")
        return remote, seed


class TestBootstrap(unittest.TestCase):
    def make_template(self, root: Path) -> None:
        (root / "board-template.kicad_pro").write_text(
            json.dumps({"meta": {"version": 1}, "project": {"title": "Purdue ROV Subsystem Board"}}),
            encoding="utf-8",
        )
        (root / "board-template.kicad_sch").write_text("(kicad_sch)\n", encoding="utf-8")
        (root / "board-template.kicad_pcb").write_text("(kicad_pcb)\n", encoding="utf-8")
        (root / ".gitmodules").write_text(
            '[submodule "libs/purdue-rov-kicad-lib"]\n'
            "\tpath = libs/purdue-rov-kicad-lib\n"
            "\turl = ../purdue-rov-kicad-lib.git\n"
            "\tbranch = master\n",
            encoding="utf-8",
        )

    def test_bootstrap_renames_and_writes_manifest(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            self.make_template(root)
            result = rov_core.bootstrap_project(root, "Demo-Board")
            self.assertTrue((root / "Demo-Board.kicad_pro").exists())
            self.assertTrue((root / "Demo-Board.kicad_sch").exists())
            config = json.loads((root / "rov.project.json").read_text(encoding="utf-8"))
            self.assertEqual(config["project_name"], "Demo-Board")
            self.assertTrue((root / "sym-lib-table").exists())
            self.assertTrue((root / "fp-lib-table").exists())
            self.assertIn("Demo-Board.kicad_pro", [p.name for p in result.changed_files])

    def test_bootstrap_is_idempotent(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            self.make_template(root)
            rov_core.bootstrap_project(root, "Demo-Board")
            before = sorted(p.name for p in root.iterdir())
            rov_core.bootstrap_project(root, "Demo-Board")
            self.assertEqual(before, sorted(p.name for p in root.iterdir()))

    def test_bootstrap_does_not_overwrite_existing_project(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            self.make_template(root)
            (root / "Demo-Board.kicad_pro").write_text('{"project": {"title": "Mine"}}', encoding="utf-8")
            result = rov_core.bootstrap_project(root, "Demo-Board")
            self.assertEqual(json.loads((root / "Demo-Board.kicad_pro").read_text(encoding="utf-8"))["project"]["title"], "Mine")
            self.assertTrue(any("not overwritten" in message for message in result.messages))

    def test_bootstrap_updates_template_manifest_name(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            self.make_template(root)
            (root / "rov.project.json").write_text(
                '{"schema": 1, "project_name": "board-template", "kicad_version": "10", '
                '"platform_ref": "master", "library": {"path": "libs/purdue-rov-kicad-lib", '
                '"branch": "master", "update_policy": "pull-request"}, "ci_profile": "standard"}',
                encoding="utf-8",
            )
            rov_core.bootstrap_project(root, "Demo-Board")
            config = json.loads((root / "rov.project.json").read_text(encoding="utf-8"))
            self.assertEqual(config["project_name"], "Demo-Board")

    def test_hook_is_untracked_and_does_not_modify_githooks(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            tracked = root / ".githooks" / "pre-commit"
            tracked.parent.mkdir()
            tracked.write_text("original\n", encoding="utf-8")
            hook = rov_core.install_project_hook(root)
            self.assertEqual(hook, root / ".rov-hooks" / "pre-commit")
            self.assertEqual(tracked.read_text(encoding="utf-8"), "original\n")
            self.assertIn("board validate", hook.read_text(encoding="utf-8"))

    def test_missing_submodule_is_blocked(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            (root / ".gitmodules").write_text(
                '[submodule "libs/purdue-rov-kicad-lib"]\n'
                "\tpath = libs/purdue-rov-kicad-lib\n"
                "\turl = ../purdue-rov-kicad-lib.git\n",
                encoding="utf-8",
            )
            config = {
                "library": {"path": "libs/purdue-rov-kicad-lib", "branch": "master"}
            }
            self.assertEqual(rov_core.check_submodule(root, config).status, "BLOCKED")

    def test_dirty_submodule_is_not_fast_forwarded(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            library = root / "libs" / "purdue-rov-kicad-lib"
            library.mkdir(parents=True)
            (library / "local.txt").write_text("local change", encoding="utf-8")
            result = rov_core.bootstrap_project(root, "Demo-Board")
            self.assertEqual((library / "local.txt").read_text(encoding="utf-8"), "local change")
            self.assertTrue(any("submodule" in message.lower() for message in result.messages))

    def test_filter_scripts_do_not_select_tracked_hooks(self):
        for filename in ("setup_git_filters.sh", "setup_git_filters.ps1"):
            text = (DEVOPS_DIR / "scripts" / filename).read_text(encoding="utf-8")
            self.assertNotIn("core.hooksPath .githooks", text)

    def test_central_launchers_do_not_reset_user_worktrees(self):
        for filename in ("LAUNCH_KICAD.sh", "LAUNCH_KICAD.bat"):
            text = (DEVOPS_DIR / "scripts" / filename).read_text(encoding="utf-8")
            self.assertNotIn("reset --hard", text)
            self.assertNotIn("checkout -B", text)


class TestBootstrapSafety(GitFixtureMixin, unittest.TestCase):
    def make_template(self, root: Path) -> None:
        TestBootstrap.make_template(self, root)

    def test_bootstrap_rejects_unsafe_project_names(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            self.make_template(root)
            for bad in ("", "   ", ".", "..", "../escape", "nested/name", "nested\\name"):
                with self.subTest(name=bad):
                    with self.assertRaises(ValueError):
                        rov_core.bootstrap_project(root, bad)
            self.assertTrue((root / "board-template.kicad_pro").is_file())
            self.assertFalse((root / "rov.project.json").exists())

    def test_bootstrap_sets_project_title_when_it_renames_the_template(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            self.make_template(root)
            rov_core.bootstrap_project(root, "Demo-Board")
            project = json.loads((root / "Demo-Board.kicad_pro").read_text(encoding="utf-8"))
            self.assertEqual(project["project"]["title"], "Demo-Board")
            self.assertEqual(project["meta"]["version"], 1)

    def test_bootstrap_preserves_a_custom_manifest_name(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            self.make_template(root)
            (root / "rov.project.json").write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "project_name": "X19-Control-Board",
                        "kicad_version": "10",
                        "platform_ref": "master",
                        "library": {
                            "path": "libs/purdue-rov-kicad-lib",
                            "branch": "master",
                            "update_policy": "pull-request",
                        },
                        "ci_profile": "standard",
                    }
                ),
                encoding="utf-8",
            )
            result = rov_core.bootstrap_project(root, "Demo-Board")
            config = json.loads((root / "rov.project.json").read_text(encoding="utf-8"))
            self.assertEqual(config["project_name"], "X19-Control-Board")
            self.assertTrue(any("project_name" in message for message in result.messages))

    def test_bootstrap_preserves_custom_library_table_entries(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            self.make_template(root)
            (root / "sym-lib-table").write_text(
                '(sym_lib_table\n'
                '  (lib (name "Custom_Sensor")(type "KiCad")(uri "${KIPRJMOD}/Custom/sensor.kicad_sym")'
                '(options "")(descr ""))\n)\n',
                encoding="utf-8",
            )
            rov_core.bootstrap_project(root, "Demo-Board")
            content = (root / "sym-lib-table").read_text(encoding="utf-8")
            self.assertIn('(name "Custom_Sensor")', content)
            self.assertIn('(name "rov_mech")', content)

    def test_bootstrap_skips_hooks_config_outside_a_git_worktree(self):
        recorder = RecordingGit(rov_core.run_git)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            self.make_template(root)
            with mock.patch.object(rov_core, "run_git", recorder):
                rov_core.bootstrap_project(root, "Demo-Board")
            self.assertTrue((root / ".rov-hooks" / "pre-commit").is_file())
            self.assertNotIn(("config", "core.hooksPath", ".rov-hooks"), recorder.calls)

    @unittest.skipUnless(GIT_AVAILABLE, "git is not installed")
    def test_bootstrap_configures_hooks_path_in_a_git_worktree(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            self.make_template(root)
            tracked = root / ".githooks" / "pre-commit"
            tracked.parent.mkdir()
            tracked.write_text("original\n", encoding="utf-8")
            self.assertEqual(rov_core.run_git(root, "init", "-q").returncode, 0)

            rov_core.bootstrap_project(root, "Repo-Board")

            configured = rov_core.run_git(root, "config", "--get", "core.hooksPath")
            self.assertEqual(configured.stdout.strip(), ".rov-hooks")
            self.assertEqual(tracked.read_text(encoding="utf-8"), "original\n")
            self.assertTrue((root / ".rov-hooks" / "pre-commit").is_file())

    @unittest.skipUnless(GIT_AVAILABLE, "git is not installed")
    def test_bootstrap_never_runs_destructive_git_commands(self):
        real_run_git = rov_core.run_git
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            self.make_template(root)
            self.assertEqual(real_run_git(root, "init", "-q").returncode, 0)
            library = self.init_repo(root / "libs" / "purdue-rov-kicad-lib")

            recorder = RecordingGit(real_run_git)
            with mock.patch.object(rov_core, "run_git", recorder):
                rov_core.bootstrap_project(root, "Demo-Board")

            verbs = {call[0] for call in recorder.calls if call}
            self.assertEqual(verbs & FORBIDDEN_GIT_VERBS, set())
            self.assertIn(("fetch", "origin", "master"), recorder.calls)
            self.assertEqual(rov_core.current_branch(library), "master")

    @unittest.skipUnless(GIT_AVAILABLE, "git is not installed")
    def test_bootstrap_warns_when_the_library_remote_is_unreachable(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            self.make_template(root)
            self.init_repo(root)
            self.init_repo(root / "libs" / "purdue-rov-kicad-lib")

            result = rov_core.bootstrap_project(root, "Demo-Board")

            self.assertTrue(
                any("stale" in message.lower() for message in result.messages),
                f"expected a stale-library warning, got {result.messages}",
            )

    @unittest.skipUnless(GIT_AVAILABLE, "git is not installed")
    def test_bootstrap_blocks_a_dirty_library_submodule(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            self.make_template(root)
            self.init_repo(root)
            library = self.init_repo(root / "libs" / "purdue-rov-kicad-lib")
            (library / "local.txt").write_text("local change", encoding="utf-8")

            result = rov_core.bootstrap_project(root, "Demo-Board")

            self.assertEqual((library / "local.txt").read_text(encoding="utf-8"), "local change")
            self.assertTrue(
                any(message.startswith("[BLOCKED]") and "submodule" in message.lower() for message in result.messages),
                f"expected a BLOCKED submodule message, got {result.messages}",
            )


class TestBootstrapGitTimeouts(GitFixtureMixin, unittest.TestCase):
    """Every bootstrap Git call that can leave the machine is bounded.

    Important I8. The library ``fetch`` was the only bounded call, so a remote
    that accepted the connection and then stopped answering would hang
    ``LAUNCH_KICAD`` forever instead of warning that the cached revision was
    kept. These tests pin the bounds on the three bootstrap network commands and
    on the plan's fetch, and assert the timeout is reported like any other
    unreachable remote.
    """

    LIB_PATH = "libs/purdue-rov-kicad-lib"

    def make_template(self, root: Path) -> None:
        TestBootstrap.make_template(self, root)

    def make_board(self, base: Path) -> Path:
        remote, seed = self.make_library_remote(base)
        self.init_repo(base)
        (base / ".gitmodules").write_text(
            f'[submodule "{self.LIB_PATH}"]\n'
            f"\tpath = {self.LIB_PATH}\n"
            f"\turl = {remote.as_posix()}\n"
            "\tbranch = master\n",
            encoding="utf-8",
        )
        self.git(base, "clone", "-q", str(remote), self.LIB_PATH)
        self.make_template(base)
        return base

    @staticmethod
    def timeouts_of(real_run_git, hang_on: str):
        """Wrap ``run_git`` to record timeouts and hang on one verb."""

        def recording(repo_dir, *args, check=False, timeout=None):
            if args[:1] == (hang_on,):
                raise subprocess.TimeoutExpired(cmd="git", timeout=timeout or 0)
            return real_run_git(repo_dir, *args, check=check, timeout=timeout)

        return recording

    @unittest.skipUnless(GIT_AVAILABLE, "git is not installed")
    def test_bootstrap_bounds_every_network_git_command(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = self.make_board(Path(tmp) / "board")
            seen: dict[tuple[str, ...], object] = {}
            real_run_git = rov_core.run_git

            def recording(repo_dir, *args, check=False, timeout=None):
                seen[tuple(args)] = timeout
                return real_run_git(repo_dir, *args, check=check, timeout=timeout)

            with mock.patch.object(rov_core, "run_git", recording):
                rov_core.bootstrap_project(root, "Demo-Board")

        self.assertEqual(
            seen[("fetch", "origin", "master")], rov_core.LIBRARY_FETCH_TIMEOUT_SECONDS
        )
        self.assertEqual(
            seen[("merge", "--ff-only", "origin/master")], rov_core.GIT_MERGE_TIMEOUT_SECONDS
        )

    @unittest.skipUnless(GIT_AVAILABLE, "git is not installed")
    def test_bootstrap_warns_when_the_library_fetch_never_answers(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = self.make_board(Path(tmp) / "board")
            real_run_git = rov_core.run_git

            with mock.patch.object(
                rov_core, "run_git", self.timeouts_of(real_run_git, "fetch")
            ):
                result = rov_core.bootstrap_project(root, "Demo-Board")

        stale = [m for m in result.messages if "stale" in m.lower()]
        self.assertTrue(stale, f"expected a stale-library warning, got {result.messages}")
        self.assertTrue(
            any("did not finish within" in message for message in stale),
            f"expected the timeout to be named, got {stale}",
        )
        # A timeout keeps the cached revision, so the fast-forward is not claimed.
        self.assertFalse(
            [m for m in result.messages if m.startswith("[PASS]") and "origin/master" in m],
            f"a timed-out fetch must not report the library as synced, got {result.messages}",
        )

    @unittest.skipUnless(GIT_AVAILABLE, "git is not installed")
    def test_bootstrap_warns_when_the_fast_forward_never_answers(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = self.make_board(Path(tmp) / "board")
            real_run_git = rov_core.run_git

            with mock.patch.object(
                rov_core, "run_git", self.timeouts_of(real_run_git, "merge")
            ):
                result = rov_core.bootstrap_project(root, "Demo-Board")

        self.assertTrue(
            any("did not finish within" in m and "stale" in m.lower() for m in result.messages),
            f"expected a named timeout, got {result.messages}",
        )

    @unittest.skipUnless(GIT_AVAILABLE, "git is not installed")
    def test_submodule_initialization_is_bounded(self):
        """`submodule update --init --recursive` clones over the network too."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            self.make_template(root)
            self.make_library_remote(root)
            self.init_repo(root)
            real_run_git = rov_core.run_git
            seen: dict[tuple[str, ...], object] = {}

            def recording(repo_dir, *args, check=False, timeout=None):
                seen[tuple(args)] = timeout
                if args[:1] == ("submodule",):
                    raise subprocess.TimeoutExpired(cmd="git", timeout=timeout or 0)
                return real_run_git(repo_dir, *args, check=check, timeout=timeout)

            with mock.patch.object(rov_core, "run_git", recording):
                result = rov_core.bootstrap_project(root, "Demo-Board")

        self.assertEqual(
            seen[("submodule", "update", "--init", "--recursive")],
            rov_core.SUBMODULE_INIT_TIMEOUT_SECONDS,
        )
        self.assertTrue(
            any("did not finish within" in m for m in result.messages),
            f"expected the timeout to be named, got {result.messages}",
        )


class TestBootstrapIdempotence(GitFixtureMixin, unittest.TestCase):
    """Fix 2: a repeated bootstrap must be byte-identical and warning free."""

    LIB_PATH = "libs/purdue-rov-kicad-lib"

    def make_template(self, root: Path) -> None:
        TestBootstrap.make_template(self, root)

    def make_board(self, base: Path) -> Path:
        """A realistic already-cloned board: a Git work tree with a library."""
        remote, seed = self.make_library_remote(base)
        self.init_repo(base)
        (base / ".gitmodules").write_text(
            f'[submodule "{self.LIB_PATH}"]\n'
            f"\tpath = {self.LIB_PATH}\n"
            f"\turl = {remote.as_posix()}\n"
            "\tbranch = master\n",
            encoding="utf-8",
        )
        self.git(base, "clone", "-q", str(remote), self.LIB_PATH)
        self.make_template(base)
        return base

    @staticmethod
    def snapshot(root: Path) -> dict[str, bytes]:
        """Hash every board file. Git internals are excluded on purpose: a fetch
        rewrites ``.git/FETCH_HEAD`` without touching the working tree."""
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file() and ".git" not in path.relative_to(root).parts
        }

    @unittest.skipUnless(GIT_AVAILABLE, "git is not installed")
    def test_repeated_bootstrap_is_byte_identical_and_warning_free(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = self.make_board(Path(tmp) / "board")

            first = rov_core.bootstrap_project(root, "Demo-Board")
            self.assertTrue(first.changed_files)
            after_first = self.snapshot(root)

            second = rov_core.bootstrap_project(root, "Demo-Board")
            after_second = self.snapshot(root)

            self.assertEqual(after_first, after_second)
            self.assertEqual(second.changed_files, ())
            noisy = [message for message in second.messages if not message.startswith("[PASS]")]
            self.assertEqual(noisy, [], f"second run must not warn, got {second.messages}")
            self.assertEqual(rov_core.bootstrap_status(second), rov_core.STATUS_PASS)

    def test_bootstrap_conflict_is_reported_only_when_both_files_exist(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            self.make_template(root)
            (root / "Demo-Board.kicad_pro").write_text('{"project": {"title": "Mine"}}', encoding="utf-8")

            result = rov_core.bootstrap_project(root, "Demo-Board")

            conflicts = [m for m in result.messages if "not overwritten" in m]
            self.assertEqual(len(conflicts), 1, f"expected one conflict, got {result.messages}")
            self.assertTrue(conflicts[0].startswith("[WARN]"))
            self.assertEqual(
                json.loads((root / "Demo-Board.kicad_pro").read_text(encoding="utf-8"))["project"]["title"],
                "Mine",
            )
            # The files with a free destination are still renamed.
            self.assertTrue((root / "Demo-Board.kicad_sch").is_file())
            self.assertTrue((root / "Demo-Board.kicad_pcb").is_file())

    def test_absent_template_with_present_destination_is_pass_not_warn(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            self.make_template(root)
            rov_core.bootstrap_project(root, "Demo-Board")
            self.assertFalse((root / "board-template.kicad_sch").exists())

            result = rov_core.bootstrap_project(root, "Demo-Board")

            self.assertEqual(result.changed_files, ())
            self.assertEqual(
                [m for m in result.messages if "not overwritten" in m],
                [],
                f"an already-renamed file must not be a conflict, got {result.messages}",
            )
            self.assertTrue(
                any("already bootstrapped" in m and m.startswith("[PASS]") for m in result.messages),
                f"expected a PASS already-bootstrapped message, got {result.messages}",
            )
            self.assertTrue((root / "Demo-Board.kicad_sch").is_file())

    @unittest.skipUnless(GIT_AVAILABLE, "git is not installed")
    def test_manifest_already_named_does_not_warn(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = self.make_board(Path(tmp) / "board")
            rov_core.bootstrap_project(root, "Demo-Board")
            before = self.snapshot(root)

            result = rov_core.bootstrap_project(root, "Demo-Board")

            self.assertEqual(self.snapshot(root), before)
            self.assertEqual(
                [m for m in result.messages if "project_name" in m], [],
                f"an already-correct manifest must not report preservation, got {result.messages}",
            )
            self.assertEqual(rov_core.bootstrap_status(result), rov_core.STATUS_PASS)


class TestBootstrapStatusHelper(unittest.TestCase):
    """Fix 6: bootstrap_status derives one status from the message prefixes."""

    def result(self, *messages: str) -> rov_core.BootstrapResult:
        return rov_core.BootstrapResult(changed_files=(), messages=messages)

    def test_no_messages_is_pass(self):
        self.assertEqual(rov_core.bootstrap_status(self.result()), rov_core.STATUS_PASS)

    def test_pass_only_is_pass(self):
        self.assertEqual(
            rov_core.bootstrap_status(self.result("[PASS] Renamed a.kicad_pro to b.kicad_pro.")),
            rov_core.STATUS_PASS,
        )

    def test_warn_is_reported(self):
        self.assertEqual(
            rov_core.bootstrap_status(
                self.result("[PASS] ok", "[WARN] library may be stale")
            ),
            rov_core.STATUS_WARN,
        )

    def test_blocked_beats_warn(self):
        self.assertEqual(
            rov_core.bootstrap_status(
                self.result("[WARN] something", "[BLOCKED] submodule has local changes")
            ),
            rov_core.STATUS_BLOCKED,
        )

    def test_fail_beats_blocked(self):
        self.assertEqual(
            rov_core.bootstrap_status(
                self.result("[BLOCKED] blocked", "[FAIL] something is broken")
            ),
            rov_core.STATUS_FAIL,
        )

    def test_unprefixed_text_is_ignored(self):
        self.assertEqual(
            rov_core.bootstrap_status(self.result("plain log line", "[PASS] ok")),
            rov_core.STATUS_PASS,
        )

    def test_dataclass_stays_frozen(self):
        with self.assertRaises(Exception):
            self.result("[PASS] ok").messages = ()


class TestLibrarySubmoduleContract(GitFixtureMixin, unittest.TestCase):
    """Fix 3 and 5: fast-forward only a clean checkout of the configured branch."""

    LIB_PATH = "libs/purdue-rov-kicad-lib"

    def make_board(self, base: Path, remote: Path) -> tuple[Path, Path]:
        root = base / "board"
        self.init_repo(root)
        (root / ".gitmodules").write_text(
            f'[submodule "{self.LIB_PATH}"]\n'
            f"\tpath = {self.LIB_PATH}\n"
            f"\turl = {remote.as_posix()}\n"
            f"\tbranch = master\n",
            encoding="utf-8",
        )
        self.git(root, "clone", "-q", str(remote), self.LIB_PATH)
        return root, root / self.LIB_PATH

    @unittest.skipUnless(GIT_AVAILABLE, "git is not installed")
    def test_clean_library_submodule_fast_forwards_to_local_bare_remote(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            remote, seed = self.make_library_remote(base)
            root, library = self.make_board(base, remote)
            before = self.git(library, "rev-parse", "HEAD")

            (seed / "library.kicad_sym").write_text("(kicad_sym v2)\n", encoding="utf-8")
            expected = self.commit_all(seed, "library v2")
            self.git(seed, "push", "-q", "origin", "master")
            self.assertNotEqual(expected, before)

            result = rov_core.bootstrap_project(root, "Demo-Board")

            self.assertEqual(self.git(library, "rev-parse", "HEAD"), expected)
            self.assertTrue(
                any("Fast-forwarded" in message for message in result.messages),
                f"expected a fast-forward message, got {result.messages}",
            )
            self.assertEqual(rov_core.bootstrap_status(result), rov_core.STATUS_PASS)

    @unittest.skipUnless(GIT_AVAILABLE, "git is not installed")
    def test_diverged_library_submodule_is_not_fast_forwarded(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            remote, seed = self.make_library_remote(base)
            root, library = self.make_board(base, remote)
            (library / "local-edit.kicad_sym").write_text("(kicad_sym local)\n", encoding="utf-8")
            local_head = self.commit_all(library, "local work")

            (seed / "library.kicad_sym").write_text("(kicad_sym remote)\n", encoding="utf-8")
            remote_head = self.commit_all(seed, "library v2")
            self.git(seed, "push", "-q", "origin", "master")
            self.assertNotEqual(remote_head, local_head)

            result = rov_core.bootstrap_project(root, "Demo-Board")

            self.assertEqual(self.git(library, "rev-parse", "HEAD"), local_head)
            self.assertTrue(
                any("stale" in message.lower() for message in result.messages),
                f"expected a stale warning for a diverged library, got {result.messages}",
            )
            self.assertTrue((library / "local-edit.kicad_sym").is_file())
            self.assertEqual(rov_core.bootstrap_status(result), rov_core.STATUS_WARN)

    @unittest.skipUnless(GIT_AVAILABLE, "git is not installed")
    def test_library_on_another_branch_is_left_untouched(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            remote, seed = self.make_library_remote(base)
            root, library = self.make_board(base, remote)
            self.git(library, "checkout", "-q", "-b", "side-work")
            before = self.git(library, "rev-parse", "HEAD")

            (seed / "library.kicad_sym").write_text("(kicad_sym v2)\n", encoding="utf-8")
            self.commit_all(seed, "library v2")
            self.git(seed, "push", "-q", "origin", "master")

            result = rov_core.bootstrap_project(root, "Demo-Board")

            self.assertEqual(rov_core.current_branch(library), "side-work")
            self.assertEqual(self.git(library, "rev-parse", "HEAD"), before)
            self.assertTrue(
                any(
                    message.startswith("[WARN]") and "side-work" in message
                    for message in result.messages
                ),
                f"expected a branch mismatch warning, got {result.messages}",
            )

    @unittest.skipUnless(GIT_AVAILABLE, "git is not installed")
    def test_detached_library_head_is_left_untouched(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            remote, seed = self.make_library_remote(base)
            root, library = self.make_board(base, remote)
            before = self.git(library, "rev-parse", "HEAD")
            self.git(library, "checkout", "-q", "--detach", "HEAD")

            (seed / "library.kicad_sym").write_text("(kicad_sym v2)\n", encoding="utf-8")
            self.commit_all(seed, "library v2")
            self.git(seed, "push", "-q", "origin", "master")

            result = rov_core.bootstrap_project(root, "Demo-Board")

            self.assertIsNone(rov_core.current_branch(library))
            self.assertEqual(self.git(library, "rev-parse", "HEAD"), before)
            self.assertTrue(
                any("detached" in message for message in result.messages),
                f"expected a detached HEAD warning, got {result.messages}",
            )

    @unittest.skipUnless(GIT_AVAILABLE, "git is not installed")
    def test_dirty_library_is_not_touched_even_when_a_remote_update_exists(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            remote, seed = self.make_library_remote(base)
            root, library = self.make_board(base, remote)
            before = self.git(library, "rev-parse", "HEAD")
            origin_before = self.git(library, "rev-parse", "origin/master")
            (library / "library.kicad_sym").write_text("(kicad_sym local edit)\n", encoding="utf-8")

            (seed / "library.kicad_sym").write_text("(kicad_sym remote)\n", encoding="utf-8")
            self.commit_all(seed, "library v2")
            self.git(seed, "push", "-q", "origin", "master")

            result = rov_core.bootstrap_project(root, "Demo-Board")

            self.assertEqual(self.git(library, "rev-parse", "HEAD"), before)
            self.assertEqual(
                self.git(library, "rev-parse", "origin/master"),
                origin_before,
                "a dirty library must not even be fetched",
            )
            self.assertEqual(
                (library / "library.kicad_sym").read_text(encoding="utf-8"), "(kicad_sym local edit)\n"
            )
            self.assertTrue(
                any("local changes" in message for message in result.messages),
                f"expected a dirty-library BLOCKED message, got {result.messages}",
            )
            self.assertEqual(rov_core.bootstrap_status(result), rov_core.STATUS_BLOCKED)

    @unittest.skipUnless(GIT_AVAILABLE, "git is not installed")
    def test_undeclared_library_path_is_blocked_and_untouched(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            remote, seed = self.make_library_remote(base)
            root, library = self.make_board(base, remote)
            before = self.git(library, "rev-parse", "HEAD")
            origin_before = self.git(library, "rev-parse", "origin/master")

            (seed / "library.kicad_sym").write_text("(kicad_sym v2)\n", encoding="utf-8")
            self.commit_all(seed, "library v2")
            self.git(seed, "push", "-q", "origin", "master")
            (root / ".gitmodules").write_text(
                '[submodule "libs/some-other-lib"]\n'
                "\tpath = libs/some-other-lib\n"
                "\turl = ../some-other-lib.git\n"
                "\tbranch = master\n",
                encoding="utf-8",
            )

            result = rov_core.bootstrap_project(root, "Demo-Board")

            # The shared declaration parser drives both interfaces, so the same
            # fact is a FAIL for validation and a BLOCKED for bootstrap.
            config = rov_core.default_project_config("Demo-Board")
            self.assertEqual(rov_core.check_submodule(root, config).status, rov_core.STATUS_FAIL)
            self.assertEqual(rov_core.bootstrap_status(result), rov_core.STATUS_BLOCKED)
            self.assertTrue(
                any("not declared" in message for message in result.messages),
                f"expected a declaration BLOCKED message, got {result.messages}",
            )
            self.assertEqual(rov_core.current_branch(library), "master")
            self.assertEqual(self.git(library, "rev-parse", "HEAD"), before)
            self.assertEqual(
                self.git(library, "rev-parse", "origin/master"),
                origin_before,
                "an undeclared path must not even be fetched",
            )


class TestGeneratedHook(unittest.TestCase):
    """Fix 4: the generated hook is portable and never blocks a commit."""

    def install(self, root: Path) -> Path:
        (root / ".githooks").mkdir(parents=True, exist_ok=True)
        (root / ".githooks" / "pre-commit").write_text("tracked\n", encoding="utf-8")
        return rov_core.install_project_hook(root)

    def test_hook_resolves_python3_then_python_and_exits_zero_without_either(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            hook = self.install(Path(tmp)).read_text(encoding="utf-8")

            self.assertTrue(hook.startswith("#!/usr/bin/env sh\n"))
            self.assertIn("command -v python3", hook)
            self.assertIn("command -v python >", hook)
            self.assertLess(hook.index("command -v python3"), hook.index("command -v python >"))
            self.assertIn("else\n    exit 0\nfi", hook)
            # The CLI call is last so its status becomes the hook's status.
            self.assertIn('"$rov_python" "$cli" board validate', hook)
            self.assertTrue(hook.rstrip().endswith("--hook"))
            self.assertNotIn("python3 ", hook.split("rov_python=python3")[-1].split("\n", 1)[0])

    def test_hook_is_valid_posix_shell(self):
        shell = posix_shell()
        if shell is None:
            self.skipTest("no POSIX shell is available to parse the hook")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            hook = self.install(Path(tmp))
            result = run_script(shell, hook, "-n", cwd=hook.parent)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_hook_is_valid_posix_shell_negatively_controlled(self):
        shell = posix_shell()
        if shell is None:
            self.skipTest("no POSIX shell is available to parse the hook")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            broken = Path(tmp) / "broken.sh"
            write_executable(broken, "#!/usr/bin/env sh\nif [ 1 -eq 1 ; then\n")
            result = run_script(shell, broken, "-n", cwd=Path(tmp))
            self.assertNotEqual(result.returncode, 0, "the parser check must reject bad syntax")

    def build_hook_bin(self, shell: str, repo: Path, extra: dict[str, str]) -> str:
        """Return a POSIX wrapper that exposes only the shims the hook may use.

        The wrapper replaces ``PATH`` and then execs the hook, so the hook can
        only find the interpreters listed here. ``sh`` is shimmed as well
        because the hook's ``#!/usr/bin/env sh`` line resolves ``sh`` through
        ``PATH``. Nothing is passed through the process environment because the
        WSL launcher does not forward custom variables.
        """
        bin_dir = repo / "hookbin"
        bin_dir.mkdir(exist_ok=True)
        write_executable(bin_dir / "sh", '#!/bin/sh\nexec /bin/sh "$@"\n')
        write_executable(
            bin_dir / "git",
            "#!/bin/sh\n"
            f'if [ "$1" = "rev-parse" ]; then echo "{posix_path(shell, repo)}"; exit 0; fi\n'
            "exit 1\n",
        )
        for name, script in extra.items():
            write_executable(bin_dir / name, script)
        posix_bin = posix_path(shell, bin_dir)
        wrapper = bin_dir / "_wrap.sh"
        write_executable(
            wrapper,
            "#!/bin/sh\n"
            f'PATH="{posix_bin}"\n'
            "export PATH\n"
            "t=$1\n"
            "shift\n"
            'exec "$t" "$@"\n',
        )
        return f"{posix_bin}/_wrap.sh"

    def make_hook_repo(self, root: Path, cli_exit: int | None) -> Path:
        repo = root / "repo"
        repo.mkdir()
        cli = repo / ".pcb-devops-cache" / "scripts" / "rov.py"
        cli.parent.mkdir(parents=True)
        if cli_exit is None:
            self.assertFalse(cli.exists())
        else:
            cli.write_text(f"import sys\nsys.exit({cli_exit})\n", encoding="utf-8")
        hook = rov_core.install_project_hook(repo)
        self.assertEqual(hook, repo / ".rov-hooks" / "pre-commit")
        return hook

    def test_hook_exits_zero_when_neither_python_is_available(self):
        shell = posix_shell()
        if shell is None:
            self.skipTest("no POSIX shell is available to run the hook")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            hook = self.make_hook_repo(root, cli_exit=1)
            wrapper = self.build_hook_bin(shell, root / "repo", {})

            result = run_script(shell, hook, cwd=root / "repo", wrapper=wrapper)

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_hook_preserves_the_cli_exit_code(self):
        shell = posix_shell()
        if shell is None:
            self.skipTest("no POSIX shell is available to run the hook")
        for exit_code in (0, 1, 2):
            with self.subTest(exit_code=exit_code):
                with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                    root = Path(tmp)
                    hook = self.make_hook_repo(root, cli_exit=exit_code)
                    wrapper = self.build_hook_bin(
                        shell,
                        root / "repo",
                        {"python3": f'#!/bin/sh\nexit {exit_code}\n'},
                    )

                    result = run_script(shell, hook, cwd=root / "repo", wrapper=wrapper)

                    self.assertEqual(result.returncode, exit_code, result.stderr)

    def test_hook_prefers_python3_over_python(self):
        shell = posix_shell()
        if shell is None:
            self.skipTest("no POSIX shell is available to run the hook")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            hook = self.make_hook_repo(root, cli_exit=1)
            wrapper = self.build_hook_bin(
                shell,
                root / "repo",
                {
                    "python3": "#!/bin/sh\nexit 0\n",
                    "python": "#!/bin/sh\nexit 2\n",
                },
            )

            result = run_script(shell, hook, cwd=root / "repo", wrapper=wrapper)

            self.assertEqual(result.returncode, 0, "python3 must be preferred over python")

    def test_hook_falls_back_to_python_when_python3_is_absent(self):
        shell = posix_shell()
        if shell is None:
            self.skipTest("no POSIX shell is available to run the hook")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            hook = self.make_hook_repo(root, cli_exit=2)
            wrapper = self.build_hook_bin(
                shell, root / "repo", {"python": "#!/bin/sh\nexit 2\n"}
            )

            result = run_script(shell, hook, cwd=root / "repo", wrapper=wrapper)

            self.assertEqual(result.returncode, 2, "python must be used when python3 is absent")

    def test_hook_exits_zero_when_the_cached_cli_is_absent(self):
        shell = posix_shell()
        if shell is None:
            self.skipTest("no POSIX shell is available to run the hook")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            hook = self.make_hook_repo(root, cli_exit=None)
            wrapper = self.build_hook_bin(
                shell, root / "repo", {"python3": "#!/bin/sh\nexit 1\n"}
            )

            result = run_script(shell, hook, cwd=root / "repo", wrapper=wrapper)

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_hook_does_not_touch_the_tracked_githooks(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            tracked = root / ".githooks" / "pre-commit"
            tracked.parent.mkdir()
            tracked.write_text("original\n", encoding="utf-8")

            hook = rov_core.install_project_hook(root)

            self.assertEqual(tracked.read_text(encoding="utf-8"), "original\n")
            self.assertNotEqual(hook.read_text(encoding="utf-8"), "original\n")


class TestLaunchKicadControlFlow(unittest.TestCase):
    """Fix 1: the launcher must open KiCad and then return the bootstrap code."""

    BAT = DEVOPS_DIR / "scripts" / "LAUNCH_KICAD.bat"
    OPEN_MARKER = "REM 3. Locate and Launch KiCad Project"

    def test_launcher_never_exits_before_the_open_block(self):
        text = self.BAT.read_text(encoding="utf-8")
        self.assertIn(self.OPEN_MARKER, text)
        head, _, tail = text.partition(self.OPEN_MARKER)
        self.assertNotIn("exit /b", head, "the bootstrap step must not exit before KiCad opens")
        self.assertIn("goto :launch_kicad", head)
        self.assertIn(":launch_kicad", head)
        self.assertIn("goto :open_proj", tail)

    def test_every_goto_target_exists(self):
        import re

        text = self.BAT.read_text(encoding="utf-8")
        targets = set(re.findall(r"goto :(\w+)", text))
        labels = set(re.findall(r"^:(\w+)\s*$", text, re.MULTILINE))
        self.assertTrue(targets)
        self.assertEqual(targets - labels, set(), f"dangling goto targets: {targets - labels}")

    def test_open_block_returns_the_captured_bootstrap_code(self):
        import re

        text = self.BAT.read_text(encoding="utf-8")
        _, _, tail = text.partition(self.OPEN_MARKER)
        exits = re.findall(r"exit /b\s+(\S+)", tail)
        self.assertTrue(exits)
        for code in exits:
            self.assertIn(code, {"%BOOTSTRAP_RC%", "1"}, f"unexpected exit code {code}")
        self.assertEqual(text.count('set "BOOTSTRAP_RC=%ERRORLEVEL%"'), 2)
        self.assertIn('set "BOOTSTRAP_RC=2"', text)

    def test_launcher_keeps_the_python_and_py_fallbacks(self):
        import re

        text = self.BAT.read_text(encoding="utf-8")
        commands = [
            line.strip() for line in text.splitlines() if "board bootstrap" in line
        ]
        self.assertTrue(
            any(
                re.fullmatch(r'python "%ROV_CLI%" board bootstrap --project-dir "%TARGET_DIR%" --non-interactive', c)
                for c in commands
            ),
            f"missing a real python bootstrap invocation in {commands}",
        )
        self.assertTrue(
            any(
                re.fullmatch(r'py -3 "%ROV_CLI%" board bootstrap --project-dir "%TARGET_DIR%" --non-interactive', c)
                for c in commands
            ),
            f"missing a real py -3 bootstrap invocation in {commands}",
        )
        self.assertIn("where python >nul 2>&1", text)
        self.assertIn("where py >nul 2>&1", text)
        self.assertIn("Python is required to prepare this board", text)
        self.assertIn('set "BOOTSTRAP_RC=2"', text)

    def test_launcher_never_uses_a_destructive_git_command(self):
        text = self.BAT.read_text(encoding="utf-8")
        for forbidden in ("git pull", "git reset", "git checkout", "git clean", "git stash", "push"):
            self.assertNotIn(forbidden, text, f"launcher must not run: {forbidden}")

    @unittest.skipUnless(CMD_AVAILABLE and GIT_AVAILABLE, "cmd or git is not available")
    def test_launcher_propagates_bootstrap_codes_and_opens_kicad(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            board = Path(tmp) / "board"
            board.mkdir()
            (board / "Demo.kicad_pro").write_text('{"project": {}}', encoding="utf-8")
            shutil.copyfile(self.BAT, board / "LAUNCH_KICAD.bat")

            for code in (0, 1, 2):
                with self.subTest(code=code):
                    (board / "rov.py").write_text(
                        f"import sys\nprint('STUB_BOOTSTRAP_RC={code}')\nsys.exit({code})\n",
                        encoding="utf-8",
                    )
                    env = dict(os.environ)
                    env["ROV_LAUNCH_DRY_RUN"] = "1"
                    env["ProgramFiles"] = str(board / "no-kicad-here")

                    result = subprocess.run(
                        ["cmd", "/c", "LAUNCH_KICAD.bat"],
                        cwd=str(board), capture_output=True, text=True, env=env, timeout=120,
                    )

                    self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                    self.assertIn(f"STUB_BOOTSTRAP_RC={code}", result.stdout)
                    self.assertIn("[3/3] Launching KiCad project", result.stdout)
                    self.assertIn("Opening: Demo.kicad_pro", result.stdout)
                    self.assertIn("Dry run: not starting KiCad.", result.stdout)

    @unittest.skipUnless(CMD_AVAILABLE and GIT_AVAILABLE, "cmd or git is not available")
    def test_launcher_reports_missing_python_as_blocked_and_still_opens(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            board = Path(tmp) / "board"
            board.mkdir()
            (board / "Demo.kicad_pro").write_text('{"project": {}}', encoding="utf-8")
            shutil.copyfile(self.BAT, board / "LAUNCH_KICAD.bat")
            empty_bin = Path(tmp) / "empty-bin"
            empty_bin.mkdir()

            env = dict(os.environ)
            env["ROV_LAUNCH_DRY_RUN"] = "1"
            env["PATH"] = str(empty_bin)

            result = subprocess.run(
                ["cmd", "/c", "LAUNCH_KICAD.bat"],
                cwd=str(board), capture_output=True, text=True, env=env, timeout=120,
            )

            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("Python is required to prepare this board", result.stdout)
            self.assertIn("Opening: Demo.kicad_pro", result.stdout)

    def test_shell_launcher_still_has_reachable_kiCad_logic(self):
        text = (DEVOPS_DIR / "scripts" / "LAUNCH_KICAD.sh").read_text(encoding="utf-8")
        self.assertNotIn("reset --hard", text)
        self.assertNotIn("checkout -B", text)
        self.assertIn("board bootstrap --project-dir", text)
        self.assertIn("kicad", text)


class TestShellLauncherControlFlow(unittest.TestCase):
    """The macOS/Linux launcher must mirror the Windows launcher's control flow.

    Critical C2 regression guard. ``LAUNCH_KICAD.sh`` ran the bootstrap under an
    unguarded ``set -e``, so a BLOCKED bootstrap aborted the launcher before the
    KiCad open block and the member was left staring at a terminal instead of
    their project. ``LAUNCH_KICAD.bat`` already captured the status, continued to
    the open block, and returned the captured code; these tests assert the shell
    launcher does the same, by actually running it under bash.

    No real KiCad process is ever started: the launcher is run with
    ``ROV_LAUNCH_DRY_RUN=1`` and with a ``kicad`` shim on ``PATH`` that records
    any attempt to launch, which the tests then assert never happened.
    """

    SH = DEVOPS_DIR / "scripts" / "LAUNCH_KICAD.sh"
    OPEN_MARKER = "# 3. Locate and Launch KiCad Project"

    # A self-contained dirname. The restricted-PATH test replaces PATH with
    # only this directory, so the launcher must still be able to resolve
    # SCRIPT_DIR without any external command.
    DIRNAME_SHIM = (
        "#!/bin/sh\n"
        'case "$1" in\n'
        "  */*) printf '%s\\n' \"${1%/*}\" ;;\n"
        "  *) printf '.\\n' ;;\n"
        "esac\n"
    )

    def build_board(
        self, root: Path, bootstrap_exit: int, python_names: tuple[str, ...] = ("python3", "python")
    ) -> Path:
        """Create a board directory holding a copy of the launcher and stubs.

        The stub interpreter reports the exit code the real CLI would return and
        then exits with it, so the launcher's captured status is observable
        without starting a real Python, and the test behaves the same on WSL,
        Linux, and macOS. The ``kicad`` shim would leave a marker file if the
        launcher ever tried to start KiCad.
        """
        board = root / "board"
        board.mkdir(parents=True)
        write_executable(board / "LAUNCH_KICAD.sh", self.SH.read_text(encoding="utf-8"))
        (board / "Demo.kicad_pro").write_text('{"project": {}}', encoding="utf-8")
        bin_dir = board / "launchbin"
        bin_dir.mkdir()
        write_executable(bin_dir / "dirname", self.DIRNAME_SHIM)
        write_executable(bin_dir / "kicad", f'#!/bin/sh\ntouch "{board / "KICAD_WAS_STARTED"}"\n')
        write_executable(bin_dir / "git", "#!/bin/sh\nexit 0\n")
        for name in python_names:
            write_executable(
                bin_dir / name,
                f'#!/bin/sh\necho "STUB_BOOTSTRAP_RC={bootstrap_exit}"\nexit {bootstrap_exit}\n',
            )
        return board

    def build_wrapper(self, shell: str, board: Path, restricted: bool = False) -> str:
        """Return a wrapper that exposes the stub ``PATH`` to the launcher.

        ``restricted`` replaces ``PATH`` outright instead of prepending, so the
        launcher can find no interpreter at all. The stub directory carries a
        self-contained ``dirname`` so ``SCRIPT_DIR`` still resolves.

        ``ROV_LAUNCH_DRY_RUN`` is exported from the wrapper rather than passed in
        the process environment because the WSL ``bash.exe`` launcher drops
        custom variables, which would silently run the real open block.
        """
        posix_bin = posix_path(shell, board / "launchbin")
        wrapper = board / "launchbin" / "_wrap.sh"
        path = f'PATH="{posix_bin}"' if restricted else f'PATH="{posix_bin}:$PATH"'
        write_executable(
            wrapper,
            "#!/bin/bash\n"
            f"{path}\n"
            "export PATH\n"
            "export ROV_LAUNCH_DRY_RUN=1\n"
            "t=$1\n"
            "shift\n"
            'exec "$BASH" "$t" "$@"\n',
        )
        return f"{posix_bin}/_wrap.sh"

    def check_syntax(self, shell: str, script: Path) -> subprocess.CompletedProcess[str]:
        """Parse ``script`` without running it.

        The ``-n`` flag has to precede the script path: the launcher treats its
        first argument as a board directory, so ``bash script -n`` would run it
        with ``-n`` as the target directory.
        """
        target = f"{posix_path(shell, script.parent)}/{script.name}"
        return subprocess.run(
            [shell, "-n", target], capture_output=True, text=True, timeout=60
        )

    def assert_kicad_never_started(self, board: Path) -> None:
        """Fail if the stub ``kicad`` shim was ever executed.

        The launcher starts KiCad in the background, so the marker file can
        appear a moment after the script returns. Polling briefly is what makes
        this a real assertion instead of a race the test would usually win.
        """
        marker = board / "KICAD_WAS_STARTED"
        for _ in range(30):
            if marker.exists():
                self.fail("the dry run started a KiCad process")
            time.sleep(0.1)

    def test_shell_launcher_parses_under_bash(self):
        shell = bash_shell()
        if shell is None:
            self.skipTest("no bash is available to parse the macOS/Linux launcher")
        result = self.check_syntax(shell, self.SH)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_bad_syntax_is_rejected_so_the_parser_check_means_something(self):
        shell = bash_shell()
        if shell is None:
            self.skipTest("no bash is available to parse the macOS/Linux launcher")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            broken = Path(tmp) / "broken.sh"
            write_executable(broken, "#!/usr/bin/env bash\nif [ 1 -eq 1 ; then\n")
            result = self.check_syntax(shell, broken)
        self.assertNotEqual(result.returncode, 0, "the parser check must reject bad syntax")

    def test_shell_launcher_never_exits_before_the_open_block(self):
        """No unconditional abort may sit between the bootstrap and the open block."""
        text = self.SH.read_text(encoding="utf-8")
        self.assertIn(self.OPEN_MARKER, text)
        head, _, tail = text.partition(self.OPEN_MARKER)
        # A guarded capture is required on both interpreter branches.
        self.assertEqual(head.count("|| BOOTSTRAP_RC=$?"), 2)
        self.assertIn("BOOTSTRAP_RC=2", head)
        # A bare `exit` in the head would abort before KiCad opens.
        self.assertNotIn("exit 2\n", head, "the bootstrap must not exit before KiCad opens")
        # The captured status is what the script finally returns.
        self.assertIn('exit "$BOOTSTRAP_RC"', tail)

    def test_shell_launcher_propagates_bootstrap_codes_and_opens_kicad(self):
        """Every bootstrap exit code is returned, and KiCad is still opened.

        The stub ``kicad`` shim writes a marker file if it is ever executed, so
        ``ROV_LAUNCH_DRY_RUN`` is proven to prevent a real launch.
        """
        shell = bash_shell()
        if shell is None:
            self.skipTest("no bash is available to run the macOS/Linux launcher")
        for code in (0, 1, 2):
            with self.subTest(code=code):
                with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                    board = self.build_board(Path(tmp), code)
                    wrapper = self.build_wrapper(shell, board)
                    env = dict(os.environ, ROV_LAUNCH_DRY_RUN="1")
                    result = run_script(
                        shell,
                        board / "LAUNCH_KICAD.sh",
                        cwd=board,
                        wrapper=wrapper,
                    )
                    self.assert_kicad_never_started(board)
                    output = result.stdout + result.stderr
                    self.assertEqual(result.returncode, code, output)
                    self.assertIn(f"STUB_BOOTSTRAP_RC={code}", output)
                    self.assertIn("Launching KiCad", output)
                    self.assertIn("Opening: Demo.kicad_pro", output)
                    self.assertIn("Dry run: not starting KiCad", output)

    def test_shell_launcher_reports_missing_python_as_blocked_and_still_opens(self):
        """A missing interpreter is BLOCKED (2), but KiCad still opens."""
        shell = bash_shell()
        if shell is None:
            self.skipTest("no bash is available to run the macOS/Linux launcher")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            board = self.build_board(Path(tmp), 0, python_names=())
            wrapper = self.build_wrapper(shell, board, restricted=True)
            result = run_script(shell, board / "LAUNCH_KICAD.sh", cwd=board, wrapper=wrapper)
            self.assert_kicad_never_started(board)
            output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 2, output)
        self.assertIn("Python is required to prepare this board", output)
        self.assertIn("Opening: Demo.kicad_pro", output)

    def test_shell_launcher_returns_bootstrap_code_when_no_project_file_exists(self):
        """With no project file the captured bootstrap code is still returned."""
        shell = bash_shell()
        if shell is None:
            self.skipTest("no bash is available to run the macOS/Linux launcher")
        for code, expected in ((0, 1), (2, 2)):
            with self.subTest(code=code):
                with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                    board = self.build_board(Path(tmp), code)
                    (board / "Demo.kicad_pro").unlink()
                    wrapper = self.build_wrapper(shell, board)
                    result = run_script(
                        shell, board / "LAUNCH_KICAD.sh", cwd=board, wrapper=wrapper
                    )
                    output = result.stdout + result.stderr
                self.assertEqual(result.returncode, expected, output)
                self.assertIn("No .kicad_pro project file found", output)


class TestCollectionIntegrity(unittest.TestCase):
    """Fail loudly when a defined test method is silently never collected.

    ``unittest.TestLoader`` skips any class that is not a ``TestCase``
    subclass, so a missing base class used to shrink the reported test count
    instead of failing. This compares what the module defines with what the
    loader actually collects.
    """

    @staticmethod
    def _is_test_method(member: object) -> bool:
        return inspect.isfunction(member) or inspect.ismethod(member)

    @staticmethod
    def _flatten(suite: unittest.TestSuite) -> list[unittest.TestCase]:
        """Return every TestCase, descending into the per-class sub-suites.

        ``loadTestsFromModule`` wraps each class in its own suite, so the tests
        are one level deeper than the returned object.
        """
        cases: list[unittest.TestCase] = []
        for item in suite:
            if isinstance(item, unittest.TestSuite):
                cases.extend(TestCollectionIntegrity._flatten(item))
            else:
                cases.append(item)
        return cases

    def test_every_defined_test_method_is_collected(self):
        module = sys.modules[__name__]
        defined = {
            f"{name}.{method_name}"
            for name, obj in vars(module).items()
            if inspect.isclass(obj)
            for method_name, _ in inspect.getmembers(obj, self._is_test_method)
            if method_name.startswith("test_")
        }
        loaded = unittest.TestLoader().loadTestsFromModule(module)
        cases = self._flatten(loaded)
        collected = {f"{case.__class__.__name__}.{case._testMethodName}" for case in cases}

        self.assertEqual(
            defined - collected,
            set(),
            "these test methods are defined but never collected; check that the "
            "class inherits unittest.TestCase:",
        )
        self.assertEqual(collected - defined, set())
        self.assertEqual(
            loaded.countTestCases(),
            len(defined),
            "the loader must report one test per defined test method",
        )


if __name__ == "__main__":
    unittest.main()

