"""Contract tests for the cross-platform ``rov`` CLI.

Every test is hermetic: no KiCad process is started, no network is used, and no
real board repository is required. External commands are exercised through the
``rov._run_tool`` seam and Git is exercised through ``rov_core.run_git``, so the
tests assert the CLI contract rather than the host's installed tools.
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

DEVOPS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DEVOPS_DIR / "scripts"))

import rov  # noqa: E402
import rov_core  # noqa: E402

VALID_CONFIG = {
    "schema": 1,
    "project_name": "Demo-Board",
    "kicad_version": "10",
    "platform_ref": "master",
    "library": {
        "path": "libs/purdue-rov-kicad-lib",
        "branch": "master",
        "update_policy": "pull-request",
    },
    "ci_profile": "standard",
}

# A compliant symbol: every mandatory metadata field the linter requires.
SYMBOL_TEXT = """(kicad_symbol_lib
  (symbol "R_0603"
    (property "Category" "Passives")
    (property "MPN" "RES-0603-10K")
    (property "Manufacturer" "Yageo")
    (property "Datasheet" "https://example.invalid/rc0603.pdf")
    (property "Temp_Range" "-55 to 155")
    (property "DigiKey" "311-10.0KLRCT-ND")
  )
)
"""

# The library repository owns the symbol parser. The CLI only loads it by file
# path, so a stub with the same two entry points is enough to prove the wiring
# and the printed column order without duplicating the real parser here.
SYMBOL_UTILS_STUB = '''"""Minimal stand-in for the library's kicad_sym_utils module."""

import re

SYMBOL_PATTERN = re.compile(r'\\(symbol "([^"]+)"')
PROPERTY_PATTERN = re.compile(r'\\(property "([^"]+)" "([^"]*)"')


def extract_top_symbols(content):
    return [(name, content, 0, len(content)) for name in SYMBOL_PATTERN.findall(content)]


def parse_symbol_properties(sym_text):
    return dict(PROPERTY_PATTERN.findall(sym_text)), {}
'''


def completed(returncode=0, stdout="", stderr=""):
    """Return a completed process without touching the host."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def write_library_tables(root: Path) -> None:
    """Write the standard symbol and footprint library tables."""
    for table, tag, uri_key in (
        ("sym-lib-table", "sym_lib_table", "sym_uri"),
        ("fp-lib-table", "fp_lib_table", "fp_uri"),
    ):
        lines = [f"({tag}"]
        for lib in rov_core.STANDARD_LIBS:
            lines.append(
                f'  (lib (name "{lib["name"]}")(type "KiCad")(uri "{lib[uri_key]}")'
                '(options "")(descr ""))'
            )
        lines.append(")")
        (root / table).write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_board(base: Path, name: str = "Demo-Board") -> Path:
    """Create a complete, already-bootstrapped board directory."""
    root = base / name
    root.mkdir(parents=True)
    (root / "rov.project.json").write_text(json.dumps(VALID_CONFIG, indent=2), encoding="utf-8")
    (root / f"{name}.kicad_pro").write_text('{"project": {"title": "Demo"}}', encoding="utf-8")
    (root / f"{name}.kicad_sch").write_text("(kicad_sch)\n", encoding="utf-8")
    (root / f"{name}.kicad_pcb").write_text("(kicad_pcb)\n", encoding="utf-8")
    write_library_tables(root)
    library = root / "libs" / "purdue-rov-kicad-lib"
    (library / "Symbols" / "parts" / "passives").mkdir(parents=True)
    (library / "Symbols" / "parts" / "passives" / "R_0603.kicad_sym").write_text(
        SYMBOL_TEXT, encoding="utf-8"
    )
    (library / ".git").write_text("gitdir: ../../.git/modules/library\n", encoding="utf-8")
    (root / ".gitmodules").write_text(
        '[submodule "libs/purdue-rov-kicad-lib"]\n'
        "\tpath = libs/purdue-rov-kicad-lib\n"
        "\turl = ../purdue-rov-kicad-lib.git\n"
        "\tbranch = master\n",
        encoding="utf-8",
    )
    return root


def make_library(base: Path) -> Path:
    """Create a library directory with the scripts the CLI delegates to."""
    library = base / "purdue-rov-kicad-lib"
    (library / "scripts").mkdir(parents=True)
    (library / "scripts" / "kicad_sym_utils.py").write_text(SYMBOL_UTILS_STUB, encoding="utf-8")
    (library / "scripts" / "linter_validator.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    (library / "scripts" / "build_symbol_libs.py").write_text("print('built')\n", encoding="utf-8")
    (library / "scripts" / "import_part.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    (library / "scripts" / "library_manager_gui.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    (library / "Symbols" / "parts" / "passives").mkdir(parents=True)
    (library / "Symbols" / "parts" / "passives" / "R_0603.kicad_sym").write_text(
        SYMBOL_TEXT, encoding="utf-8"
    )
    return library


def recording_run_tool(returncode=0, stdout=""):
    """Return ``(calls, fake)`` so a test can assert the exact commands."""
    calls: list[list[str]] = []

    def fake(command, cwd=None, timeout=None, env=None):
        calls.append(list(command))
        return completed(returncode, stdout)

    return calls, fake


def recording_launch(returncode=0):
    """Return ``(calls, fake)`` for a foreground script launch."""
    calls: list[list[str]] = []

    def fake(command, cwd=None):
        calls.append(list(command))
        return returncode

    return calls, fake


# Git is never allowed to run for real: a real ``git submodule update`` or
# ``git fetch`` would reach the network, and a real ``git reset`` would touch a
# developer's work tree. Every test that can reach Git patches
# ``rov_core.run_git``, the single seam all Git access goes through.
FORBIDDEN_GIT_VERBS = frozenset(
    {
        "fetch",
        "merge",
        "push",
        "submodule",
        "checkout",
        "switch",
        "reset",
        "restore",
        "stash",
        "clean",
        "rm",
        "commit",
    }
)


def read_only_git(stdout_for_rev_parse="false"):
    """Return ``(calls, fake)``: Git reads are answered, any write raises.

    ``rev-parse`` reports ``stdout_for_rev_parse``, so a temporary directory
    behaves like a non-repository without Git ever running. The write verbs are
    refused so a test fails loudly instead of touching a remote or a work tree.
    """
    calls: list[tuple] = []

    def fake(repo_dir, *args, check=False, timeout=None):
        calls.append(tuple(args))
        if args and args[0] in FORBIDDEN_GIT_VERBS:
            raise AssertionError(f"tests must not run 'git {args[0]}': {args}")
        if args[:1] == ("rev-parse",):
            return completed(0, stdout_for_rev_parse)
        return completed(0)

    return calls, fake


def no_git():
    """Refuse every Git command outright."""
    return patch.object(
        rov_core, "run_git", side_effect=AssertionError("tests must not run git")
    )


def no_kicad_cli():
    """Let the fast checks run their tools but refuse to start kicad-cli.

    KiCad is never launched in a unit test. The symbol linter still runs
    because it is a plain Python script, which keeps the assertion specific:
    only the kicad-cli ERC and DRC invocations are refused.
    """

    def fake(command, cwd=None, timeout=None, env=None):
        if any("kicad-cli" in str(part) for part in command):
            raise AssertionError(f"tests must not start kicad-cli: {command}")
        return completed(0)

    return patch.object(rov, "_run_tool", fake)


class TestChildProcessEncoding(unittest.TestCase):
    """A captured child must not die on a Windows console code page.

    One member could not commit at all: the symbol linter printed a check mark,
    their console was cp1252, the child raised UnicodeEncodeError and exited
    non-zero, and the CLI reported that crash as a lint failure. Reproducing it
    here needs no Windows machine, because the child is told what encoding to use
    and the failure only appears when it is *not* told.
    """

    def _script_that_prints_a_check_mark(self, directory: Path) -> Path:
        script = directory / "prints_non_ascii.py"
        script.write_text(
            "print('\\u2705 linted')\n", encoding="utf-8", newline="\n"
        )
        return script

    def test_a_child_may_print_non_ascii_on_a_legacy_code_page(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            script = self._script_that_prints_a_check_mark(Path(tmp))
            # A cp1252 parent console is the situation that broke the commit.
            with patch.dict(os.environ, {"PYTHONIOENCODING": "cp1252"}, clear=False):
                os.environ.pop("PYTHONUTF8", None)
                result = rov._run_tool([sys.executable, str(script)], cwd=Path(tmp))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("\u2705", result.stdout)

    def test_the_child_is_told_to_encode_its_output_as_utf8(self):
        env = rov._child_environment()
        self.assertEqual(env["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(env["PYTHONUTF8"], "1")

    def test_the_existing_environment_is_preserved(self):
        with patch.dict(os.environ, {"SOME_MEMBER_VARIABLE": "kept"}):
            env = rov._child_environment()
        self.assertEqual(env["SOME_MEMBER_VARIABLE"], "kept")


class TestDoctor(unittest.TestCase):
    def test_doctor_missing_kicad_is_warning(self):
        with patch.object(rov.shutil, "which", side_effect=lambda name: "/usr/bin/git" if name == "git" else None):
            results = rov.run_doctor(Path.cwd())
        kicad = next(r for r in results if r.name == "kicad-cli")
        self.assertEqual(kicad.status, "WARN")

    def test_missing_git_is_the_only_doctor_failure(self):
        with patch.object(rov.shutil, "which", return_value=None):
            results = rov.run_doctor(Path.cwd())
        statuses = {result.name: result.status for result in results}
        self.assertEqual(statuses["git"], "FAIL")
        self.assertEqual(statuses["python"], "PASS")
        for optional in ("github-cli", "kicad-cli", "docker", "project-config", "submodule"):
            self.assertIn(optional, statuses)
            self.assertIn(statuses[optional], ("PASS", "WARN"), optional)
        self.assertEqual(rov.exit_code_for(results), 1)

    def test_doctor_reports_present_tools(self):
        calls, fake = recording_run_tool()
        with patch.object(rov.shutil, "which", return_value="/usr/bin/tool"), patch.object(rov, "_run_tool", fake):
            results = rov.run_doctor(Path.cwd())
        statuses = {result.name: result.status for result in results}
        self.assertEqual(statuses["git"], "PASS")
        self.assertEqual(statuses["github-cli"], "PASS")
        self.assertEqual(statuses["kicad-cli"], "PASS")
        self.assertEqual(statuses["docker"], "PASS")
        self.assertIn(["/usr/bin/tool", "info"], calls)

    def test_doctor_warns_when_docker_daemon_is_unreachable(self):
        def fake(command, cwd=None, timeout=None, env=None):
            if command[1:] == ["info"]:
                return completed(1, stderr="cannot connect to the Docker daemon")
            return completed(0)

        with patch.object(rov.shutil, "which", return_value="/usr/bin/tool"), patch.object(rov, "_run_tool", fake):
            results = rov.run_doctor(Path.cwd())
        docker = next(r for r in results if r.name == "docker")
        self.assertEqual(docker.status, "WARN")
        self.assertEqual(rov.exit_code_for(results), 0)

    def test_doctor_uses_the_board_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_board(Path(tmp))
            with patch.object(rov.shutil, "which", return_value="/usr/bin/tool"), patch.object(
                rov, "_run_tool", lambda *a, **k: completed(0)
            ):
                results = rov.run_doctor(root)
        statuses = {result.name: result.status for result in results}
        self.assertEqual(statuses["submodule"], "PASS")
        self.assertEqual(statuses["schema"], "PASS")
        self.assertEqual(rov.exit_code_for(results), 0)


class TestBoardValidate(unittest.TestCase):
    def test_full_validation_without_kicad_is_blocked(self):
        with patch.object(rov.shutil, "which", return_value=None):
            results = rov.run_board_validate(Path.cwd(), full=True)
        self.assertTrue(any(r.status == "BLOCKED" for r in results))

    def test_fast_validation_passes_for_a_complete_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_board(Path(tmp))
            calls, fake = recording_run_tool()
            with patch.object(rov, "_run_tool", fake):
                results = rov.run_board_validate(root)
        self.assertEqual(rov.exit_code_for(results), 0, results)
        self.assertEqual([r.name for r in results if r.status != "PASS"], [])
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][1].endswith("linter_validator.py"))
        self.assertEqual(Path(calls[0][2]), root / "libs" / "purdue-rov-kicad-lib" / "Symbols")

    def test_fast_validation_reports_a_missing_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "empty"
            root.mkdir()
            results = rov.run_board_validate(root)
        manifest = next(r for r in results if r.name == "manifest")
        self.assertEqual(manifest.status, "FAIL")
        self.assertEqual(rov.exit_code_for(results), 1)

    def test_fast_validation_flags_merge_conflict_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_board(Path(tmp))
            (root / "Demo-Board.kicad_sch").write_text(
                "(kicad_sch)\n<<<<<<< HEAD\n(a)\n=======\n(b)\n>>>>>>> feature\n",
                encoding="utf-8",
            )
            with patch.object(rov, "_run_tool", lambda *a, **k: completed(0)):
                results = rov.run_board_validate(root)
        markers = next(r for r in results if r.name == "merge-conflict-markers")
        self.assertEqual(markers.status, "FAIL")
        self.assertIn("Demo-Board.kicad_sch", markers.message)
        self.assertEqual(rov.exit_code_for(results), 1)

    def test_conflict_markers_are_found_in_every_kicad_design_file(self):
        """The checked file set is the CI include set, so .kicad_dru counts too."""
        for filename in ("Demo-Board.kicad_dru", "Demo-Board.kicad_mod", "sym-lib-table"):
            with self.subTest(filename=filename):
                with tempfile.TemporaryDirectory() as tmp:
                    root = make_board(Path(tmp))
                    target = root / filename
                    if filename == "sym-lib-table":
                        target.write_text(target.read_text(encoding="utf-8") + "<<<<<<< HEAD\n", encoding="utf-8")
                    else:
                        target.write_text("(design)\n=======\n", encoding="utf-8")
                    with patch.object(rov, "_run_tool", lambda *a, **k: completed(0)):
                        results = rov.run_board_validate(root)
                markers = next(r for r in results if r.name == "merge-conflict-markers")
                self.assertEqual(markers.status, "FAIL", markers.message)
                self.assertIn(filename, markers.message)
                self.assertEqual(rov.exit_code_for(results), 1)

    def test_conflict_marker_rule_matches_the_ci_workflow(self):
        """The shared rule must stay the same rule run-kicad-ci.yml greps for."""
        workflow = (DEVOPS_DIR / ".github" / "workflows" / "run-kicad-ci.yml").read_text(
            encoding="utf-8"
        )
        for pattern in rov_core.CONFLICT_CHECK_PATTERNS:
            self.assertIn(f"--include='{pattern}'", workflow)
        self.assertIn("^(<{7}|={7}|>{7})", workflow)
        self.assertEqual(
            rov_core.CONFLICT_MARKER_PATTERN.pattern.replace("(?m)", ""),
            "^(<{7}|={7}|>{7})",
        )

    def test_multiple_project_files_are_a_warning_not_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_board(Path(tmp))
            (root / "Other.kicad_pro").write_text('{"project": {}}', encoding="utf-8")
            (root / "Other.kicad_sch").write_text("(kicad_sch)\n", encoding="utf-8")
            with patch.object(rov, "_run_tool", lambda *a, **k: completed(0)):
                results = rov.run_board_validate(root)
        project = next(r for r in results if r.name == "project-file")
        self.assertEqual(project.status, "WARN")
        self.assertIn("Other.kicad_pro", project.message)
        self.assertEqual(rov.exit_code_for(results), 0)

    def test_missing_project_file_is_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_board(Path(tmp))
            (root / "Demo-Board.kicad_pro").unlink()
            with patch.object(rov, "_run_tool", lambda *a, **k: completed(0)):
                results = rov.run_board_validate(root)
        project = next(r for r in results if r.name == "project-file")
        self.assertEqual(project.status, "FAIL")

    def test_full_validation_runs_the_documented_kicad_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_board(Path(tmp))
            calls, fake = recording_run_tool()
            with patch.object(rov.shutil, "which", return_value="/usr/bin/kicad-cli"), patch.object(
                rov, "_run_tool", fake
            ):
                results = rov.run_board_validate(root, full=True)
            erc = next(call for call in calls if "erc" in call)
            drc = next(call for call in calls if "drc" in call)
        self.assertEqual(rov.exit_code_for(results), 0, results)
        self.assertEqual(erc[:5], ["/usr/bin/kicad-cli", "sch", "erc", "--severity-error", "--exit-code-violations"])
        self.assertEqual(erc[5], "-o")
        self.assertTrue(erc[6].endswith("erc.rpt"))
        self.assertEqual(Path(erc[7]), root / "Demo-Board.kicad_sch")
        self.assertEqual(drc[:5], ["/usr/bin/kicad-cli", "pcb", "drc", "--severity-error", "--exit-code-violations"])
        self.assertEqual(drc[5], "-o")
        self.assertTrue(drc[6].endswith("drc.rpt"))
        self.assertEqual(Path(drc[7]), root / "Demo-Board.kicad_pcb")

    def test_full_validation_uses_the_kibot_severity_policy(self):
        """`--full` must not promote warnings to failures.

        `kibot_master.yaml` runs its preflight at `severity: error`, so a local
        `--full` that used `--severity-all` would report boards as broken that
        CI accepts, and a local pass/fail would stop meaning the same thing as
        the shared gate. The flag and the shared policy are asserted together
        here so the two cannot drift apart.
        """
        self.assertEqual(rov.KICAD_SEVERITY_FLAG, "--severity-error")
        kibot = (DEVOPS_DIR / "kibot_master.yaml").read_text(encoding="utf-8")
        self.assertIn("severity: error", kibot)
        # The relaxed non-strict CI path is the documented exception.
        workflow = (DEVOPS_DIR / ".github/workflows/run-kicad-ci.yml").read_text(encoding="utf-8")
        self.assertIn("sed -i 's/severity: error/severity: warning/g' local_kibot.yaml", workflow)
        with tempfile.TemporaryDirectory() as tmp:
            root = make_board(Path(tmp))
            calls, fake = recording_run_tool()
            with patch.object(rov.shutil, "which", return_value="/usr/bin/kicad-cli"), patch.object(
                rov, "_run_tool", fake
            ):
                rov.run_board_validate(root, full=True)
        for call in calls:
            if "kicad-cli" not in call[0]:
                continue  # the symbol linter is not a kicad-cli invocation
            with self.subTest(call=call[1:4]):
                self.assertIn(rov.KICAD_SEVERITY_FLAG, call)
                self.assertNotIn("--severity-all", call)
        self.assertEqual(
            sum("kicad-cli" in call[0] for call in calls), 2, "both ERC and DRC must run"
        )

    def test_full_validation_fails_when_erc_reports_violations(self):
        def fake(command, cwd=None, timeout=None, env=None):
            if "erc" in command:
                return completed(5, stderr="violations found")
            return completed(0)

        with tempfile.TemporaryDirectory() as tmp:
            root = make_board(Path(tmp))
            with patch.object(rov.shutil, "which", return_value="/usr/bin/kicad-cli"), patch.object(
                rov, "_run_tool", fake
            ):
                results = rov.run_board_validate(root, full=True)
        erc = next(r for r in results if r.name == "erc")
        self.assertEqual(erc.status, "FAIL")
        self.assertEqual(rov.exit_code_for(results), 1)

    def test_full_validation_is_blocked_without_the_schematic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_board(Path(tmp))
            (root / "Demo-Board.kicad_sch").unlink()
            with patch.object(rov.shutil, "which", return_value="/usr/bin/kicad-cli"), patch.object(
                rov, "_run_tool", lambda *a, **k: completed(0)
            ):
                results = rov.run_board_validate(root, full=True)
        schematic = next(r for r in results if r.name == "erc")
        self.assertEqual(schematic.status, "BLOCKED")
        self.assertIn("Demo-Board.kicad_sch", schematic.message)
        self.assertEqual(rov.exit_code_for(results), 2)

    def test_full_validation_is_blocked_without_any_project_file(self):
        """Regression: a board with no *.kicad_pro must never dereference None.

        kicad-cli is present, so the full path runs and previously crashed with
        ``AttributeError`` while building the missing-file message.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = make_board(Path(tmp))
            (root / "Demo-Board.kicad_pro").unlink()
            with patch.object(rov.shutil, "which", return_value="/usr/bin/kicad-cli"), no_kicad_cli():
                results = rov.run_board_validate(root, full=True)
        kicad_results = [r for r in results if r.name in ("erc", "drc")]
        self.assertEqual([r.name for r in kicad_results], ["erc", "drc"])
        for result in kicad_results:
            self.assertEqual(result.status, "BLOCKED", result.message)
            self.assertIn("*.kicad_pro", result.message)
        # The kicad-cli checks are BLOCKED, which is exit code 2. The missing
        # required project file is itself a FAIL, and FAIL outranks BLOCKED, so
        # the command as a whole reports 1 and says why.
        self.assertEqual(rov.exit_code_for(kicad_results), 2)
        self.assertEqual(rov.exit_code_for(results), 1)
        project = next(r for r in results if r.name == "project-file")
        self.assertEqual(project.status, "FAIL")

    def test_full_validation_blocked_missing_project_file_through_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_board(Path(tmp))
            for name in ("Demo-Board.kicad_pro", "Demo-Board.kicad_sch", "Demo-Board.kicad_pcb"):
                (root / name).unlink()
            with patch.object(rov.shutil, "which", return_value="/usr/bin/kicad-cli"), no_kicad_cli(), redirect_stdout(
                io.StringIO()
            ) as buffer:
                code = rov.main(["board", "validate", "--full", "--project-dir", str(root)])
        self.assertEqual(code, 1)
        self.assertEqual(buffer.getvalue().count("[BLOCKED] erc"), 1)
        self.assertEqual(buffer.getvalue().count("[BLOCKED] drc"), 1)
        # The hook never starts KiCad, so a board with no design file at all is
        # still reported as one FAIL rather than two BLOCKED results.
        with tempfile.TemporaryDirectory() as tmp:
            root = make_board(Path(tmp))
            for name in ("Demo-Board.kicad_pro", "Demo-Board.kicad_sch", "Demo-Board.kicad_pcb"):
                (root / name).unlink()
            with patch.object(rov.shutil, "which", return_value="/usr/bin/kicad-cli"), no_kicad_cli(), redirect_stdout(
                io.StringIO()
            ) as hook_buffer:
                hook_code = rov.main(["board", "validate", "--hook", "--project-dir", str(root)])
        self.assertEqual(hook_code, 1)
        self.assertNotIn("[BLOCKED] erc", hook_buffer.getvalue())

    def test_hook_mode_runs_fast_validation_only(self):
        with patch.object(
            rov, "run_board_validate", return_value=[rov_core.CheckResult("x", "WARN", "m")]
        ) as mock_validate:
            with redirect_stdout(io.StringIO()):
                code = rov.main(["board", "validate", "--hook", "--project-dir", "."])
        self.assertEqual(code, 0)
        self.assertEqual(mock_validate.call_args.kwargs, {"full": False, "hook": True})

    def test_hook_mode_fails_only_on_fail(self):
        blocked = [rov_core.CheckResult("submodule", "BLOCKED", "not initialized")]
        warned = [rov_core.CheckResult("submodule", "WARN", "may be stale")]
        failed = [rov_core.CheckResult("manifest", "FAIL", "bad manifest")]
        self.assertEqual(rov.hook_exit_code_for(blocked), 0)
        self.assertEqual(rov.hook_exit_code_for(warned), 0)
        self.assertEqual(rov.hook_exit_code_for(failed), 1)
        self.assertEqual(rov.exit_code_for(failed), 1)
        self.assertEqual(rov.exit_code_for(blocked), 2)


class TestLibraryCommands(unittest.TestCase):
    def test_library_sync_blocks_a_dirty_worktree(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            rov.shutil, "which", return_value="/usr/bin/git"
        ), patch.object(rov_core, "is_git_worktree", return_value=True), patch.object(
            rov_core, "is_clean_worktree", return_value=False
        ), patch.object(rov_core, "run_git", side_effect=AssertionError("git must not run")):
            result = rov.run_library_sync(Path(tmp))
        self.assertEqual(result.status, "BLOCKED")
        self.assertIn("local changes", result.message)

    def test_library_sync_fast_forwards_and_never_pushes(self):
        calls: list[list[str]] = []
        timeouts: list = []

        def fake_run_git(repo_dir, *args, check=False, timeout=None):
            calls.append(list(args))
            timeouts.append(timeout)
            stdout = "Already up to date.\n" if args[0] == "merge" else ""
            return completed(0, stdout)

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            rov.shutil, "which", return_value="/usr/bin/git"
        ), patch.object(rov_core, "is_git_worktree", return_value=True), patch.object(
            rov_core, "is_clean_worktree", return_value=True
        ), patch.object(rov_core, "run_git", fake_run_git):
            result = rov.run_library_sync(Path(tmp), branch="main")

        self.assertEqual(result.status, "PASS")
        self.assertEqual(calls, [["fetch", "origin", "main"], ["merge", "--ff-only", "origin/main"]])
        self.assertTrue(
            {"push", "commit", "reset", "checkout", "clean", "stash"}.isdisjoint(
                {call[0] for call in calls}
            )
        )
        # Both network-facing Git commands must carry a hard time limit.
        self.assertEqual(
            timeouts,
            [rov_core.LIBRARY_FETCH_TIMEOUT_SECONDS, rov_core.GIT_MERGE_TIMEOUT_SECONDS],
        )

    def test_library_sync_is_bounded_when_the_remote_never_answers(self):
        """A hung remote is reported, never waited on.

        `library sync` fetches and fast-forwards, so an unresponsive remote
        would otherwise hang the Library Manager's own worker thread. The
        timeout is turned into the same BLOCKED result an unreachable remote
        produces, and the merge is never attempted.
        """
        calls: list[list[str]] = []

        def fake_run_git(repo_dir, *args, check=False, timeout=None):
            calls.append(list(args))
            raise subprocess.TimeoutExpired(cmd="git", timeout=timeout or 0)

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            rov.shutil, "which", return_value="/usr/bin/git"
        ), patch.object(rov_core, "is_git_worktree", return_value=True), patch.object(
            rov_core, "is_clean_worktree", return_value=True
        ), patch.object(rov_core, "run_git", fake_run_git):
            result = rov.run_library_sync(Path(tmp))

        self.assertEqual(result.status, "BLOCKED")
        self.assertIn(str(rov_core.LIBRARY_FETCH_TIMEOUT_SECONDS), result.message)
        self.assertIn("stale", result.message)
        self.assertEqual([call[0] for call in calls], ["fetch"])

    def test_library_sync_reports_a_timeout_on_the_fast_forward(self):
        """The fast-forward is bounded too, not only the fetch."""

        def fake_run_git(repo_dir, *args, check=False, timeout=None):
            if args[0] == "merge":
                raise subprocess.TimeoutExpired(cmd="git", timeout=timeout or 0)
            return completed(0)

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            rov.shutil, "which", return_value="/usr/bin/git"
        ), patch.object(rov_core, "is_git_worktree", return_value=True), patch.object(
            rov_core, "is_clean_worktree", return_value=True
        ), patch.object(rov_core, "run_git", fake_run_git):
            result = rov.run_library_sync(Path(tmp))

        self.assertEqual(result.status, "BLOCKED")
        self.assertIn(str(rov_core.GIT_MERGE_TIMEOUT_SECONDS), result.message)

    def test_library_sync_blocks_an_unreachable_remote(self):
        calls: list[list[str]] = []

        def fake_run_git(repo_dir, *args, check=False, timeout=None):
            calls.append(list(args))
            return completed(128, stderr="could not read from remote repository")

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            rov.shutil, "which", return_value="/usr/bin/git"
        ), patch.object(rov_core, "is_git_worktree", return_value=True), patch.object(
            rov_core, "is_clean_worktree", return_value=True
        ), patch.object(rov_core, "run_git", fake_run_git):
            result = rov.run_library_sync(Path(tmp))

        self.assertEqual(result.status, "BLOCKED")
        self.assertIn("stale", result.message)
        self.assertEqual([call[0] for call in calls], ["fetch"])

    def test_library_sync_is_blocked_outside_a_git_worktree(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            rov.shutil, "which", return_value="/usr/bin/git"
        ), no_git(), patch.object(rov_core, "is_git_worktree", return_value=False):
            result = rov.run_library_sync(Path(tmp))
        self.assertEqual(result.status, "BLOCKED")

    def test_library_validate_uses_the_library_linter(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = make_library(Path(tmp))
            calls, fake = recording_run_tool()
            with patch.object(rov, "_run_tool", fake):
                results = rov.run_library_validate(library)
        self.assertEqual([r.status for r in results], ["PASS"])
        self.assertEqual(calls, [[sys.executable, str(library / "scripts" / "linter_validator.py"), str(library / "Symbols")]])

    def test_library_validate_fails_on_a_non_zero_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = make_library(Path(tmp))
            with patch.object(rov, "_run_tool", lambda *a, **k: completed(1, stderr="missing field")):
                results = rov.run_library_validate(library)
        self.assertEqual([r.status for r in results], ["FAIL"])
        self.assertIn("missing field", results[0].message)

    def test_library_build_runs_the_library_builder(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = make_library(Path(tmp))
            calls, fake = recording_run_tool()
            with patch.object(rov, "_run_tool", fake):
                self.assertEqual(rov.run_library_build(library).status, "PASS")
            with patch.object(rov, "_run_tool", lambda *a, **k: completed(2, stderr="boom")):
                self.assertEqual(rov.run_library_build(library).status, "FAIL")

    def test_missing_library_script_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / "purdue-rov-kicad-lib"
            library.mkdir()
            with patch.object(rov, "_run_tool", side_effect=AssertionError("must not run")):
                self.assertEqual(rov.run_library_build(library).status, "BLOCKED")
                self.assertEqual(rov.run_library_validate(library)[0].status, "BLOCKED")

    def test_library_list_prints_tab_separated_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = make_library(Path(tmp))
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = rov.main(["library", "list", "--library-dir", str(library)])
        self.assertEqual(code, 0)
        lines = [line for line in out.getvalue().splitlines() if line.strip()]
        self.assertEqual(lines, ["name\tcategory\tMPN\tmanufacturer", "R_0603\tPassives\tRES-0603-10K\tYageo"])
        self.assertIn("[PASS] library-list:", err.getvalue())

    def test_library_rows_are_machine_readable_on_stdout_alone(self):
        """The status summary must not be interleaved with the rows.

        `rov library list` and `rov library search` are the two commands a script
        pipes into another tool, so a `[PASS] library-list:` line on standard
        output is a row a consumer has to filter out. The summary belongs on
        standard error, where it is still visible to a human.
        """
        for command in (["library", "list"], ["library", "search", "yageo"]):
            with self.subTest(command=command):
                with tempfile.TemporaryDirectory() as tmp:
                    library = make_library(Path(tmp))
                    out, err = io.StringIO(), io.StringIO()
                    with redirect_stdout(out), redirect_stderr(err):
                        code = rov.main([*command, "--library-dir", str(library)])
                self.assertEqual(code, 0)
                rows = [line for line in out.getvalue().splitlines() if line.strip()]
                self.assertTrue(rows)
                for row in rows:
                    with self.subTest(row=row):
                        self.assertNotIn("[PASS]", row)
                        self.assertNotIn("[BLOCKED]", row)
                        self.assertEqual(len(row.split("\t")), len(rov.SYMBOL_ROW_COLUMNS))
                self.assertIn("[PASS] library-", err.getvalue())

    def test_a_blocked_row_listing_still_explains_itself_on_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / "purdue-rov-kicad-lib"
            (library / "Symbols").mkdir(parents=True)
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = rov.main(["library", "list", "--library-dir", str(library)])
        self.assertEqual(code, 2)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("BLOCKED", err.getvalue())

    def test_library_search_filters_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = make_library(Path(tmp))
            buffer = io.StringIO()
            with redirect_stdout(buffer), redirect_stderr(io.StringIO()):
                code = rov.main(["library", "search", "yageo", "--library-dir", str(library)])
            self.assertEqual(code, 0)
            self.assertIn("R_0603\tPassives", buffer.getvalue())
            empty = io.StringIO()
            with redirect_stdout(empty), redirect_stderr(io.StringIO()):
                self.assertEqual(rov.main(["library", "search", "nonexistent", "--library-dir", str(library)]), 0)
            self.assertNotIn("R_0603", empty.getvalue())

    def test_library_list_is_blocked_without_the_symbol_utils_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / "purdue-rov-kicad-lib"
            (library / "Symbols").mkdir(parents=True)
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                code = rov.main(["library", "list", "--library-dir", str(library)])
        self.assertEqual(code, 2)
        self.assertIn("BLOCKED", err.getvalue())

    def test_library_import_forwards_remaining_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = make_library(Path(tmp))
            calls, fake = recording_launch()
            with patch.object(rov, "_launch_tool", fake):
                code = rov.main(
                    [
                        "library",
                        "import",
                        "--library-dir",
                        str(library),
                        "--symbol",
                        "part.kicad_sym",
                        "--category",
                        "Power",
                    ]
                )
        self.assertEqual(code, 0)
        self.assertEqual(
            calls,
            [
                [
                    sys.executable,
                    str(library / "scripts" / "import_part.py"),
                    "--symbol",
                    "part.kicad_sym",
                    "--category",
                    "Power",
                ]
            ],
        )

    def test_library_import_without_arguments_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = make_library(Path(tmp))
            buffer = io.StringIO()
            with redirect_stdout(buffer), patch.object(
                rov, "_launch_tool", side_effect=AssertionError("must not run")
            ):
                code = rov.main(["library", "import", "--library-dir", str(library)])
        self.assertEqual(code, 2)
        self.assertIn("BLOCKED", buffer.getvalue())

    def test_library_import_help_is_answered_by_the_cli(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer), patch.object(
            rov, "_launch_tool", side_effect=AssertionError("must not run")
        ):
            code = rov.main(["library", "import", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("rov library import", buffer.getvalue())
        self.assertIn("--library-dir", buffer.getvalue())

    def test_library_gui_launches_the_existing_manager(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = make_library(Path(tmp))
            calls, fake = recording_launch()
            with patch.object(rov, "_launch_tool", fake):
                code = rov.main(["library", "gui", "--library-dir", str(library)])
        self.assertEqual(code, 0)
        self.assertEqual(calls, [[sys.executable, str(library / "scripts" / "library_manager_gui.py")]])

    def test_library_gui_returns_the_manager_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = make_library(Path(tmp))
            with patch.object(rov, "_launch_tool", return_value=3):
                self.assertEqual(rov.main(["library", "gui", "--library-dir", str(library)]), 3)

    def test_library_contribute_delegates_to_the_shared_core(self):
        """The CLI only parses arguments; the core prepares the contribution."""
        prepared = rov_core.CheckResult("library-contribute", "PASS", "prepared add-part-tps54302-1")
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp)
            with patch.object(
                rov, "run_library_contribute", return_value=prepared
            ) as mock_contribute, redirect_stdout(io.StringIO()) as buffer:
                code = rov.main([
                    "library", "contribute",
                    "--library-dir", str(library),
                    "--name", "TPS54302",
                    "--category", "Power",
                    "--push",
                    "--pr",
                ])
        self.assertEqual(code, 0)
        self.assertIn("PASS", buffer.getvalue())
        self.assertEqual(mock_contribute.call_args.args[1:], ("TPS54302", "Power"))
        self.assertTrue(mock_contribute.call_args.kwargs["push"])
        self.assertTrue(mock_contribute.call_args.kwargs["create_pr"])

    def test_library_contribute_defaults_to_no_push_and_no_pr(self):
        """Publishing is opt-in, so a bare invocation never leaves the machine."""
        prepared = rov_core.CheckResult("library-contribute", "PASS", "prepared add-part-x-1")
        with patch.object(rov, "run_library_contribute", return_value=prepared) as mock_contribute:
            with redirect_stdout(io.StringIO()):
                rov.main(["library", "contribute", "--name", "TPS54302", "--category", "Power"])
        self.assertFalse(mock_contribute.call_args.kwargs["push"])
        self.assertFalse(mock_contribute.call_args.kwargs["create_pr"])

    def test_library_contribute_reports_a_blocked_core_result(self):
        """A refused contribution exits 2 and prints the shared reason."""
        blocked = rov_core.CheckResult(
            "library-contribute", rov_core.STATUS_BLOCKED, "changes are outside the library directories"
        )
        with patch.object(rov, "run_library_contribute", return_value=blocked):
            with redirect_stdout(io.StringIO()) as buffer:
                code = rov.main(["library", "contribute", "--name", "TPS54302", "--category", "Power"])
        self.assertEqual(code, 2)
        self.assertIn("outside the library directories", buffer.getvalue())


class TestBoardSyncLibrary(unittest.TestCase):
    def fake_plan(self, calls):
        def planner(project_dir, remote="origin", branch="master"):
            calls.append((Path(project_dir), branch))
            return rov_core.LibraryUpdatePlan(
                current_commit="1111111",
                target_commit="2222222",
                changed_files=("Symbols/parts/power/new.kicad_sym",),
                blocked_reason=None,
            )

        return planner

    def test_sync_library_is_blocked_before_the_planner_exists(self):
        """A missing planner is BLOCKED, not an AttributeError.

        The planner attribute is patched to ``None`` instead of asserting it is
        absent, so this test keeps its meaning once Task 4 adds the real
        function to ``rov_core``.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = make_board(Path(tmp))
            with patch.object(rov.shutil, "which", return_value="/usr/bin/git"), no_git(), patch.object(
                rov_core, "is_git_worktree", return_value=True
            ), patch.object(rov_core, "is_clean_worktree", return_value=True), patch.object(
                rov_core, "plan_library_update", None, create=True
            ), redirect_stdout(io.StringIO()) as buffer:
                code = rov.main(["board", "sync-library", "--project-dir", str(root)])
        self.assertEqual(code, 2)
        self.assertIn("not available in this build", buffer.getvalue())

    def test_sync_library_dry_run_changes_nothing(self):
        calls: list[tuple] = []
        with tempfile.TemporaryDirectory() as tmp:
            root = make_board(Path(tmp))
            buffer = io.StringIO()
            with patch.object(rov.shutil, "which", return_value="/usr/bin/git"), no_git(), patch.object(
                rov_core, "is_git_worktree", return_value=True
            ), patch.object(rov_core, "is_clean_worktree", return_value=True), patch.object(
                rov_core, "plan_library_update", self.fake_plan(calls), create=True
            ), patch.object(
                rov_core,
                "apply_library_update",
                side_effect=AssertionError("a dry run must not apply"),
                create=True,
            ), redirect_stdout(buffer):
                code = rov.main(["board", "sync-library", "--project-dir", str(root)])
        self.assertEqual(code, 0)
        self.assertEqual(calls, [(root, "master")])
        self.assertIn("1111111", buffer.getvalue())
        self.assertIn("2222222", buffer.getvalue())
        self.assertIn("new.kicad_sym", buffer.getvalue())
        self.assertIn("--apply", buffer.getvalue())

    def test_sync_library_rejects_a_library_path_the_manifest_does_not_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_board(Path(tmp))
            buffer = io.StringIO()
            with no_git(), redirect_stdout(buffer):
                code = rov.main(
                    [
                        "board",
                        "sync-library",
                        "--project-dir",
                        str(root),
                        "--library-path",
                        "libs/somewhere-else",
                    ]
                )
        self.assertEqual(code, 2)
        self.assertIn("BLOCKED", buffer.getvalue())

    def test_sync_library_rejects_a_pull_request_without_a_push(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_board(Path(tmp))
            buffer = io.StringIO()
            with no_git(), redirect_stdout(buffer):
                code = rov.main(
                    ["board", "sync-library", "--project-dir", str(root), "--apply", "--pr"]
                )
        self.assertEqual(code, 2)
        self.assertIn("--pr needs --push", buffer.getvalue())

    def test_sync_library_blocks_a_dirty_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_board(Path(tmp))
            with patch.object(rov.shutil, "which", return_value="/usr/bin/git"), no_git(), patch.object(
                rov_core, "is_git_worktree", return_value=True
            ), patch.object(rov_core, "is_clean_worktree", return_value=False), redirect_stdout(
                io.StringIO()
            ):
                code = rov.main(["board", "sync-library", "--project-dir", str(root)])
        self.assertEqual(code, 2)


class TestBoardBootstrap(unittest.TestCase):
    def make_template(self, root: Path) -> None:
        (root / "board-template.kicad_pro").write_text(
            json.dumps({"project": {"title": "Purdue ROV Subsystem Board"}}), encoding="utf-8"
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

    def test_bootstrap_uses_the_directory_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Demo-Board"
            root.mkdir()
            self.make_template(root)
            buffer = io.StringIO()
            with patch.object(rov_core, "run_git", read_only_git()[1]), redirect_stdout(buffer):
                code = rov.main(["board", "bootstrap", "--project-dir", str(root), "--non-interactive"])
            self.assertEqual(code, 0, buffer.getvalue())
            self.assertTrue((root / "Demo-Board.kicad_pro").is_file())
            config = json.loads((root / "rov.project.json").read_text(encoding="utf-8"))
            self.assertEqual(config["project_name"], "Demo-Board")

    def test_bootstrap_never_uses_an_unrecorded_git_command(self):
        """The Git seam is the only way bootstrap reaches Git, and it stays read-only."""
        calls, fake = read_only_git()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Demo-Board"
            root.mkdir()
            self.make_template(root)
            with patch.object(rov_core, "run_git", fake), redirect_stdout(io.StringIO()):
                code = rov.main(["board", "bootstrap", "--project-dir", str(root)])
        self.assertEqual(code, 0)
        self.assertTrue(calls, "the Git seam must be exercised, not bypassed")
        verbs = {call[0] for call in calls if call}
        self.assertEqual(verbs & FORBIDDEN_GIT_VERBS, set())
        self.assertEqual(verbs, {"rev-parse"})

    def test_bootstrap_rejects_an_unusable_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Demo-Board"
            root.mkdir()
            self.make_template(root)
            buffer = io.StringIO()
            with no_git(), redirect_stdout(buffer):
                code = rov.main(
                    ["board", "bootstrap", "--project-dir", str(root), "--project-name", "../escape"]
                )
        self.assertEqual(code, 1)
        self.assertIn("FAIL", buffer.getvalue())

    def test_bootstrap_keeps_each_step_status(self):
        outcome = rov_core.BootstrapResult(
            changed_files=(),
            messages=(
                "[PASS] Renamed board-template.kicad_pro to Demo-Board.kicad_pro.",
                "[BLOCKED] core.hooksPath is unset instead of .rov-hooks. Run 'git config core.hooksPath .rov-hooks'.",
                "unprefixed line",
            ),
        )
        results = rov_core.bootstrap_results(outcome)
        self.assertEqual(
            [(r.status, r.message) for r in results],
            [
                (rov_core.STATUS_PASS, "Renamed board-template.kicad_pro to Demo-Board.kicad_pro."),
                (
                    rov_core.STATUS_BLOCKED,
                    "core.hooksPath is unset instead of .rov-hooks. "
                    "Run 'git config core.hooksPath .rov-hooks'.",
                ),
                (rov_core.STATUS_PASS, "unprefixed line"),
            ],
        )
        self.assertEqual(rov.exit_code_for(results), 2)


class TestCliContract(unittest.TestCase):
    def test_validate_returns_one_for_fail(self):
        with patch.object(rov, "run_board_validate") as mock_validate:
            mock_validate.return_value = [rov_core.CheckResult("manifest", "FAIL", "bad manifest")]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(rov.main(["board", "validate"]), 1)

    def test_doctor_missing_tool_returns_zero(self):
        with patch.object(rov, "run_doctor") as mock_doctor:
            mock_doctor.return_value = [rov_core.CheckResult("docker", "WARN", "not installed")]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(rov.main(["doctor"]), 0)

    def test_exit_code_precedence(self):
        self.assertEqual(rov.exit_code_for([]), 0)
        self.assertEqual(rov.exit_code_for([rov_core.CheckResult("a", "PASS", "")]), 0)
        self.assertEqual(rov.exit_code_for([rov_core.CheckResult("a", "WARN", "")]), 0)
        self.assertEqual(rov.exit_code_for([rov_core.CheckResult("a", "BLOCKED", "")]), 2)
        self.assertEqual(
            rov.exit_code_for(
                [rov_core.CheckResult("a", "BLOCKED", ""), rov_core.CheckResult("b", "FAIL", "")]
            ),
            1,
        )

    def test_results_are_rendered_as_text(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            rov.print_results([rov_core.CheckResult("submodule", "BLOCKED", "not initialized")])
        self.assertEqual(buffer.getvalue(), "[BLOCKED] submodule: not initialized\n")
        self.assertTrue(buffer.getvalue().isascii(), "status output must not use emoji or color markers")

    def test_missing_project_directory_is_blocked(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = rov.main(["board", "validate", "--project-dir", "does-not-exist-anywhere"])
        self.assertEqual(code, 2)
        self.assertIn("BLOCKED", buffer.getvalue())

    def test_command_tree_matches_the_documented_surface(self):
        parser = rov.build_parser()
        for argv in (
            ["doctor"],
            ["board", "bootstrap"],
            ["board", "validate"],
            ["board", "sync-library"],
            ["library", "list"],
            ["library", "search", "q"],
            ["library", "sync"],
            ["library", "validate"],
            ["library", "build"],
            ["library", "import"],
            ["library", "contribute"],
            ["library", "gui"],
        ):
            with self.subTest(argv=argv):
                namespace = parser.parse_args(argv)
                self.assertTrue(callable(namespace.handler), f"{argv} has no handler")

    def test_board_validate_defaults_to_the_current_directory(self):
        namespace = rov.build_parser().parse_args(["board", "validate"])
        self.assertIsNone(namespace.project_dir)
        self.assertFalse(namespace.full)
        self.assertFalse(namespace.hook)


if __name__ == "__main__":
    unittest.main()
