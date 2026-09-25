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


VALID_CONFIG = {
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

GIT_AVAILABLE = shutil.which("git") is not None


class TestRovCoreContracts(unittest.TestCase):
    def test_valid_manifest_has_no_failures(self):
        failures = [r for r in rov_core.validate_project_config(VALID_CONFIG) if r.status == "FAIL"]
        self.assertEqual(failures, [])

    def test_manifest_rejects_unsupported_kicad_version(self):
        config = dict(VALID_CONFIG)
        config["kicad_version"] = "9"
        messages = [r.message for r in rov_core.validate_project_config(config) if r.status == "FAIL"]
        self.assertTrue(any("kicad_version" in message for message in messages))

    def test_missing_manifest_reports_actionable_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "rov.project.json"):
                rov_core.load_project_config(Path(tmp))

    def test_missing_standard_library_table_entry_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sym-lib-table").write_text('(sym_lib_table)\n', encoding="utf-8")
            (root / "fp-lib-table").write_text('(fp_lib_table)\n', encoding="utf-8")
            failures = [r for r in rov_core.validate_library_tables(root) if r.status == "FAIL"]
            self.assertEqual(len(failures), 2)

    def test_project_file_ignores_prl(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "demo.kicad_prl").write_text("{}", encoding="utf-8")
            (root / "demo.kicad_pro").write_text("{}", encoding="utf-8")
            self.assertEqual(rov_core.find_project_file(root), root / "demo.kicad_pro")


class TestLibraryContract(unittest.TestCase):
    def test_standard_libs_cover_six_categories(self):
        names = [lib["name"] for lib in rov_core.STANDARD_LIBS]
        self.assertEqual(
            names,
            [
                "rov_passives",
                "rov_power",
                "rov_logic",
                "rov_connectors",
                "rov_sensors",
                "rov_mech",
            ],
        )
        for lib in rov_core.STANDARD_LIBS:
            name = lib["name"]
            self.assertEqual(
                lib["sym_uri"],
                f"${{KIPRJMOD}}/libs/purdue-rov-kicad-lib/Symbols/{name}.kicad_sym",
            )
            self.assertEqual(
                lib["fp_uri"],
                f"${{KIPRJMOD}}/libs/purdue-rov-kicad-lib/Footprints/{name}.pretty",
            )

    def test_standard_libs_use_established_display_labels(self):
        expected = {
            "rov_passives": "Purdue ROV Passives",
            "rov_power": "Purdue ROV Power",
            "rov_logic": "Purdue ROV Logic",
            "rov_connectors": "Purdue ROV Connectors",
            "rov_sensors": "Purdue ROV Sensors",
            "rov_mech": "Purdue ROV Mechanical",
        }
        self.assertEqual(
            {lib["name"]: lib["sym_descr"] for lib in rov_core.STANDARD_LIBS},
            {name: f"{label} Symbols" for name, label in expected.items()},
        )
        self.assertEqual(
            {lib["name"]: lib["fp_descr"] for lib in rov_core.STANDARD_LIBS},
            {name: f"{label} Footprints" for name, label in expected.items()},
        )
        for lib in rov_core.STANDARD_LIBS:
            self.assertNotIn(lib["name"], lib["sym_descr"])
            self.assertNotIn(lib["name"], lib["fp_descr"])

    def test_sync_project_writes_established_descriptions(self):
        import sync_project_libs

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sync_project_libs.sync_project(root)
            sym_content = (root / "sym-lib-table").read_text(encoding="utf-8")
            fp_content = (root / "fp-lib-table").read_text(encoding="utf-8")
            for lib in rov_core.STANDARD_LIBS:
                self.assertIn(f'(descr "{lib["sym_descr"]}")', sym_content)
                self.assertIn(f'(descr "{lib["fp_descr"]}")', fp_content)
            self.assertIn('(descr "Purdue ROV Passives Symbols")', sym_content)
            self.assertIn('(descr "Purdue ROV Mechanical Symbols")', sym_content)
            self.assertIn('(descr "Purdue ROV Mechanical Footprints")', fp_content)

    def test_sync_project_does_not_rewrite_existing_entries(self):
        import sync_project_libs

        existing = (
            '(sym_lib_table\n'
            '  (lib (name "rov_passives")(type "KiCad")'
            '(uri "${KIPRJMOD}/libs/purdue-rov-kicad-lib/Symbols/rov_passives.kicad_sym")'
            '(options "")(descr "Local board wording"))\n)\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sym-lib-table").write_text(existing, encoding="utf-8")

            result = sync_project_libs.sync_project(root)
            self.assertTrue(result["sym_changed"])
            self.assertNotIn("rov_passives", result["sym_added"])

            content = (root / "sym-lib-table").read_text(encoding="utf-8")
            self.assertIn('(descr "Local board wording")', content)
            self.assertEqual(content.count('(name "rov_passives")'), 1)

    def test_sync_project_output_satisfies_library_table_validation(self):
        import sync_project_libs

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sync_project_libs.sync_project(root)
            failures = [r for r in rov_core.validate_library_tables(root) if r.status == "FAIL"]
            self.assertEqual(failures, [])
            self.assertEqual(
                [r for r in rov_core.validate_library_tables(root)],
                [
                    rov_core.CheckResult(
                        "sym-lib-table",
                        rov_core.STATUS_PASS,
                        "sym-lib-table contains all 6 standard library entries.",
                    ),
                    rov_core.CheckResult(
                        "fp-lib-table",
                        rov_core.STATUS_PASS,
                        "fp-lib-table contains all 6 standard library entries.",
                    ),
                ],
            )

    def test_missing_library_table_lists_every_missing_nickname(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sym-lib-table").write_text(
                '  (lib (name "rov_passives")(type "KiCad")(uri "u")(options "")(descr "d"))\n',
                encoding="utf-8",
            )
            results = {r.name: r for r in rov_core.validate_library_tables(root)}
            self.assertEqual(sorted(results), ["fp-lib-table", "sym-lib-table"])

            sym_result = results["sym-lib-table"]
            self.assertEqual(sym_result.status, rov_core.STATUS_FAIL)
            for name in ("rov_power", "rov_logic", "rov_connectors", "rov_sensors", "rov_mech"):
                self.assertIn(name, sym_result.message)
            self.assertNotIn("rov_passives", sym_result.message)

            fp_result = results["fp-lib-table"]
            self.assertEqual(fp_result.status, rov_core.STATUS_FAIL)
            self.assertIn("missing or unreadable", fp_result.message)
            for lib in rov_core.STANDARD_LIBS:
                self.assertIn(lib["name"], fp_result.message)


class TestProjectConfigValidation(unittest.TestCase):
    def _messages(self, config):
        return [r.message for r in rov_core.validate_project_config(config) if r.status == "FAIL"]

    def _mutated(self, field):
        config = json.loads(json.dumps(VALID_CONFIG))
        if field == "schema":
            config["schema"] = 2
        elif field == "project_name":
            config["project_name"] = "  "
        elif field == "kicad_version":
            config["kicad_version"] = "9"
        elif field == "platform_ref":
            config["platform_ref"] = ""
        elif field == "library":
            del config["library"]
        elif field == "library.path":
            del config["library"]["path"]
        elif field == "library.branch":
            config["library"]["branch"] = ""
        elif field == "library.update_policy":
            config["library"]["update_policy"] = "auto-merge"
        elif field == "ci_profile":
            del config["ci_profile"]
        else:  # pragma: no cover - guards test typos
            raise AssertionError(field)
        return config

    def test_every_required_field_is_reported(self):
        for field in (
            "schema",
            "project_name",
            "kicad_version",
            "platform_ref",
            "library",
            "library.path",
            "library.branch",
            "library.update_policy",
            "ci_profile",
        ):
            with self.subTest(field=field):
                messages = self._messages(self._mutated(field))
                self.assertTrue(messages, f"expected a FAIL for {field}")
                self.assertTrue(
                    any(field.split(".")[-1] in message for message in messages),
                    f"expected '{field}' to be named in a FAIL message, got {messages}",
                )

    def test_kicad_version_must_be_the_baseline_string_not_a_number(self):
        config = json.loads(json.dumps(VALID_CONFIG))
        config["kicad_version"] = 10
        self.assertTrue(any("kicad_version" in m for m in self._messages(config)))

    def test_boolean_schema_is_rejected(self):
        config = json.loads(json.dumps(VALID_CONFIG))
        config["schema"] = True
        self.assertTrue(any("schema" in m for m in self._messages(config)))

    def test_load_project_config_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "rov.project.json").write_text(json.dumps(VALID_CONFIG), encoding="utf-8")
            loaded = rov_core.load_project_config(root)
            self.assertEqual(loaded, VALID_CONFIG)
            self.assertEqual(rov_core.find_project_file(root), None)

    def test_malformed_manifest_json_raises_value_error_with_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "rov.project.json").write_text("{not json", encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                rov_core.load_project_config(root)
            self.assertIn(str((root / "rov.project.json").resolve()), str(ctx.exception))

    def test_non_object_manifest_json_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "rov.project.json").write_text("[1, 2, 3]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "JSON object"):
                rov_core.load_project_config(root)


class TestSubmoduleCheck(unittest.TestCase):
    LIB_PATH = "libs/purdue-rov-kicad-lib"

    def _write_gitmodules(self, root, path):
        (root / ".gitmodules").write_text(
            f'[submodule "{path}"]\n\tpath = {path}\n\turl = ../purdue-rov-kicad-lib.git\n'
            f"\tbranch = {rov_core.LIBRARY_BRANCH}\n",
            encoding="utf-8",
        )

    def test_missing_submodule_directory_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = rov_core.check_submodule(Path(tmp), VALID_CONFIG)
            self.assertEqual(result.status, rov_core.STATUS_BLOCKED)
            self.assertIn(self.LIB_PATH, result.message)

    def test_declared_but_uninitialized_submodule_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_gitmodules(root, self.LIB_PATH)
            (root / self.LIB_PATH).mkdir(parents=True)
            result = rov_core.check_submodule(root, VALID_CONFIG)
            self.assertEqual(result.status, rov_core.STATUS_BLOCKED)
            self.assertIn("git submodule update --init", result.message)

    def test_initialized_submodule_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_gitmodules(root, self.LIB_PATH)
            lib_dir = root / self.LIB_PATH
            lib_dir.mkdir(parents=True)
            (lib_dir / ".git").write_text(
                f"gitdir: ../.git/modules/{self.LIB_PATH}\n", encoding="utf-8"
            )
            result = rov_core.check_submodule(root, VALID_CONFIG)
            self.assertEqual(result.status, rov_core.STATUS_PASS)

    def test_undeclared_submodule_path_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_gitmodules(root, "libs/some-other-lib")
            lib_dir = root / self.LIB_PATH
            lib_dir.mkdir(parents=True)
            (lib_dir / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
            result = rov_core.check_submodule(root, VALID_CONFIG)
            self.assertEqual(result.status, rov_core.STATUS_FAIL)
            self.assertIn(self.LIB_PATH, result.message)

    def test_missing_library_config_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / self.LIB_PATH).mkdir(parents=True)
            config = json.loads(json.dumps(VALID_CONFIG))
            del config["library"]
            result = rov_core.check_submodule(root, config)
            self.assertEqual(result.status, rov_core.STATUS_FAIL)
            self.assertIn("library", result.message)


class TestGitPrimitives(unittest.TestCase):
    def test_run_git_reports_missing_git_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(rov_core.shutil, "which", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "Git is required but was not found"):
                    rov_core.run_git(Path(tmp), "status")

    def test_non_git_directory_is_not_a_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(rov_core.is_git_worktree(Path(tmp)))
            self.assertFalse(rov_core.is_clean_worktree(Path(tmp)))
            self.assertIsNone(rov_core.current_branch(Path(tmp)))

    @unittest.skipUnless(GIT_AVAILABLE, "git is not installed")
    def test_initialized_repository_reports_clean_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(rov_core.run_git(root, "init", "-q").returncode, 0)
            self.assertTrue(rov_core.is_git_worktree(root))
            self.assertTrue(rov_core.is_clean_worktree(root))
            branch = rov_core.current_branch(root)
            self.assertIsInstance(branch, str)
            self.assertTrue(branch)

            (root / "scratch.txt").write_text("dirty", encoding="utf-8")
            self.assertFalse(rov_core.is_clean_worktree(root))

    @unittest.skipUnless(GIT_AVAILABLE, "git is not installed")
    def test_run_git_check_raises_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(rov_core.run_git(root, "init", "-q").returncode, 0)
            result = rov_core.run_git(root, "rev-parse", "--verify", "refs/heads/missing")
            self.assertNotEqual(result.returncode, 0)
            with self.assertRaises(RuntimeError):
                rov_core.run_git(root, "rev-parse", "--verify", "refs/heads/missing", check=True)


class TestResultTypes(unittest.TestCase):
    def test_result_dataclasses_are_frozen_value_objects(self):
        result = rov_core.CheckResult("name", rov_core.STATUS_WARN, "message")
        with self.assertRaises(Exception):
            result.status = rov_core.STATUS_FAIL

        bootstrap = rov_core.BootstrapResult(changed_files=(), messages=("ok",))
        self.assertEqual(bootstrap.changed_files, ())
        self.assertEqual(bootstrap.messages, ("ok",))

        plan = rov_core.LibraryUpdatePlan(
            current_commit="a" * 40,
            target_commit="b" * 40,
            changed_files=("Symbols/rov_power.kicad_sym",),
            blocked_reason=None,
        )
        self.assertEqual(plan.current_commit, "a" * 40)
        self.assertIsNone(plan.blocked_reason)


if __name__ == "__main__":
    unittest.main()
