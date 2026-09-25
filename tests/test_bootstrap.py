import json
import shutil
import sys
import tempfile
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


class RecordingGit:
    """Delegate to the real ``run_git`` while recording every argument list."""

    def __init__(self, real_run_git):
        self._real_run_git = real_run_git
        self.calls = []

    def __call__(self, repo_dir, *args, check=False):
        self.calls.append(tuple(args))
        return self._real_run_git(repo_dir, *args, check=check)


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
        with tempfile.TemporaryDirectory() as tmp:
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
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_template(root)
            rov_core.bootstrap_project(root, "Demo-Board")
            before = sorted(p.name for p in root.iterdir())
            rov_core.bootstrap_project(root, "Demo-Board")
            self.assertEqual(before, sorted(p.name for p in root.iterdir()))

    def test_bootstrap_does_not_overwrite_existing_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_template(root)
            (root / "Demo-Board.kicad_pro").write_text('{"project": {"title": "Mine"}}', encoding="utf-8")
            result = rov_core.bootstrap_project(root, "Demo-Board")
            self.assertEqual(json.loads((root / "Demo-Board.kicad_pro").read_text(encoding="utf-8"))["project"]["title"], "Mine")
            self.assertTrue(any("not overwritten" in message for message in result.messages))

    def test_bootstrap_updates_template_manifest_name(self):
        with tempfile.TemporaryDirectory() as tmp:
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
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tracked = root / ".githooks" / "pre-commit"
            tracked.parent.mkdir()
            tracked.write_text("original\n", encoding="utf-8")
            hook = rov_core.install_project_hook(root)
            self.assertEqual(hook, root / ".rov-hooks" / "pre-commit")
            self.assertEqual(tracked.read_text(encoding="utf-8"), "original\n")
            self.assertIn("board validate", hook.read_text(encoding="utf-8"))

    def test_missing_submodule_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
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
        with tempfile.TemporaryDirectory() as tmp:
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


class TestBootstrapSafety(unittest.TestCase):
    def make_template(self, root: Path) -> None:
        TestBootstrap.make_template(self, root)

    def test_bootstrap_rejects_unsafe_project_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_template(root)
            for bad in ("", "   ", ".", "..", "../escape", "nested/name", "nested\\name"):
                with self.subTest(name=bad):
                    with self.assertRaises(ValueError):
                        rov_core.bootstrap_project(root, bad)
            self.assertTrue((root / "board-template.kicad_pro").is_file())
            self.assertFalse((root / "rov.project.json").exists())

    def test_bootstrap_sets_project_title_when_it_renames_the_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_template(root)
            rov_core.bootstrap_project(root, "Demo-Board")
            project = json.loads((root / "Demo-Board.kicad_pro").read_text(encoding="utf-8"))
            self.assertEqual(project["project"]["title"], "Demo-Board")
            self.assertEqual(project["meta"]["version"], 1)

    def test_bootstrap_preserves_a_custom_manifest_name(self):
        with tempfile.TemporaryDirectory() as tmp:
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
        with tempfile.TemporaryDirectory() as tmp:
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
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_template(root)
            with mock.patch.object(rov_core, "run_git", recorder):
                rov_core.bootstrap_project(root, "Demo-Board")
            self.assertTrue((root / ".rov-hooks" / "pre-commit").is_file())
            self.assertNotIn(("config", "core.hooksPath", ".rov-hooks"), recorder.calls)

    @unittest.skipUnless(GIT_AVAILABLE, "git is not installed")
    def test_bootstrap_configures_hooks_path_in_a_git_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
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
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_template(root)
            self.assertEqual(real_run_git(root, "init", "-q").returncode, 0)
            library = root / "libs" / "purdue-rov-kicad-lib"
            library.mkdir(parents=True)
            self.assertEqual(real_run_git(library, "init", "-q").returncode, 0)

            recorder = RecordingGit(real_run_git)
            with mock.patch.object(rov_core, "run_git", recorder):
                rov_core.bootstrap_project(root, "Demo-Board")

            verbs = {call[0] for call in recorder.calls if call}
            self.assertEqual(verbs & FORBIDDEN_GIT_VERBS, set())
            self.assertIn(("fetch", "origin", "master"), recorder.calls)

    @unittest.skipUnless(GIT_AVAILABLE, "git is not installed")
    def test_bootstrap_warns_when_the_library_remote_is_unreachable(self):
        real_run_git = rov_core.run_git
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_template(root)
            self.assertEqual(real_run_git(root, "init", "-q").returncode, 0)
            library = root / "libs" / "purdue-rov-kicad-lib"
            library.mkdir(parents=True)
            self.assertEqual(real_run_git(library, "init", "-q").returncode, 0)

            result = rov_core.bootstrap_project(root, "Demo-Board")

            self.assertTrue(
                any("stale" in message.lower() for message in result.messages),
                f"expected a stale-library warning, got {result.messages}",
            )

    @unittest.skipUnless(GIT_AVAILABLE, "git is not installed")
    def test_bootstrap_blocks_a_dirty_library_submodule(self):
        real_run_git = rov_core.run_git
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_template(root)
            self.assertEqual(real_run_git(root, "init", "-q").returncode, 0)
            library = root / "libs" / "purdue-rov-kicad-lib"
            library.mkdir(parents=True)
            self.assertEqual(real_run_git(library, "init", "-q").returncode, 0)
            (library / "local.txt").write_text("local change", encoding="utf-8")

            result = rov_core.bootstrap_project(root, "Demo-Board")

            self.assertEqual((library / "local.txt").read_text(encoding="utf-8"), "local change")
            self.assertTrue(
                any(message.startswith("[BLOCKED]") and "submodule" in message.lower() for message in result.messages),
                f"expected a BLOCKED submodule message, got {result.messages}",
            )


if __name__ == "__main__":
    unittest.main()
