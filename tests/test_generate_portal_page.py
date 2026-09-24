import io
import os
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scripts')))
import generate_portal_page
from generate_portal_page import generate_portal

class TestGeneratePortalPage(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()
    def test_html_escaping(self):
        repo_name = "<script>alert('xss')</script>"
        branch = "feature/<test>&\"branch\""
        sha = "1234567890"

        generate_portal(self.temp_dir.name, repo_name=repo_name, commit_sha=sha, branch=branch)
        index_file = os.path.join(self.temp_dir.name, "index.html")
        self.assertTrue(os.path.exists(index_file))

        with open(index_file, 'r', encoding='utf-8') as f:
            content = f.read()

        self.assertNotIn("<script>alert('xss')</script>", content)
        self.assertIn("&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;", content)
        self.assertIn("&lt;test&gt;&amp;&quot;branch&quot;", content)

    def test_portal_with_artifacts(self):
        # Create mock artifacts
        for filename in ["Test-iBOM.html", "Test-Schematic.pdf", "Test-Layout.pdf", "Test-BOM.csv"]:
            with open(os.path.join(self.temp_dir.name, filename), 'w') as f:
                f.write("mock")

        generate_portal(self.temp_dir.name, repo_name="Purdue ROV Board", commit_sha="abcdef12345", branch="main")
        index_file = os.path.join(self.temp_dir.name, "index.html")

        with open(index_file, 'r', encoding='utf-8') as f:
            content = f.read()

        self.assertIn('Test-iBOM.html', content)
        self.assertIn('Test-Schematic.pdf', content)
        self.assertIn('Test-Layout.pdf', content)
        self.assertIn('Test-BOM.csv', content)

    def test_portal_empty_directory(self):
        # No files in output directory
        generate_portal(self.temp_dir.name, repo_name="Empty Board")
        index_file = os.path.join(self.temp_dir.name, "index.html")
        self.assertTrue(os.path.exists(index_file))

        with open(index_file, 'r', encoding='utf-8') as f:
            content = f.read()

        self.assertIn("Empty Board", content)
        self.assertIn("No design artifacts", content)

    def test_nonexistent_directory(self):
        non_existent_path = os.path.join(self.temp_dir.name, "does_not_exist")
        captured_stderr = io.StringIO()
        with patch('sys.stderr', captured_stderr):
            generate_portal_page.generate_portal(non_existent_path)
        self.assertIn("Error: Output directory not found", captured_stderr.getvalue())

    def test_empty_directory(self):
        out_dir = self.temp_dir.name
        generate_portal_page.generate_portal(
            out_dir,
            repo_name="Test Repo",
            commit_sha="1234567890abcdef",
            branch="feature-branch"
        )
        index_path = os.path.join(out_dir, "index.html")
        self.assertTrue(os.path.exists(index_path))

        with open(index_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("Test Repo", content)
        self.assertIn("feature-branch", content)
        self.assertIn("1234567", content)  # First 7 chars of commit_sha
        self.assertNotIn("Interactive BOM", content)
        self.assertNotIn("Schematic PDF", content)
        self.assertNotIn("PCB Layout PDF", content)
        self.assertNotIn("BOM Spreadsheet", content)

    def test_with_hyphenated_artifacts(self):
        out_dir = self.temp_dir.name
        files_to_create = [
            "board-iBOM.html",
            "board-Schematic.pdf",
            "board-Layout.pdf",
            "board-BOM.csv"
        ]
        for fname in files_to_create:
            with open(os.path.join(out_dir, fname), "w") as f:
                f.write("dummy content")

        generate_portal_page.generate_portal(
            out_dir,
            repo_name="Hyphen Repo",
            commit_sha="abcdef1234567890",
            branch="main"
        )
        index_path = os.path.join(out_dir, "index.html")
        self.assertTrue(os.path.exists(index_path))

        with open(index_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("Hyphen Repo", content)
        self.assertIn('href="board-iBOM.html"', content)
        self.assertIn('href="board-Schematic.pdf"', content)
        self.assertIn('href="board-Layout.pdf"', content)
        self.assertIn('href="board-BOM.csv"', content)
        self.assertIn("Interactive BOM", content)
        self.assertIn("Schematic PDF", content)
        self.assertIn("PCB Layout PDF", content)
        self.assertIn("BOM Spreadsheet", content)

    def test_with_underscored_artifacts(self):
        out_dir = self.temp_dir.name
        files_to_create = [
            "board_ibom.html",
            "board_schematic.pdf",
            "board_layout.pdf"
        ]
        for fname in files_to_create:
            with open(os.path.join(out_dir, fname), "w") as f:
                f.write("dummy content")

        generate_portal_page.generate_portal(out_dir)
        index_path = os.path.join(out_dir, "index.html")
        self.assertTrue(os.path.exists(index_path))

        with open(index_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn('href="board_ibom.html"', content)
        self.assertIn('href="board_schematic.pdf"', content)
        self.assertIn('href="board_layout.pdf"', content)

    def test_no_commit_sha(self):
        out_dir = self.temp_dir.name
        generate_portal_page.generate_portal(out_dir, commit_sha="")
        index_path = os.path.join(out_dir, "index.html")
        with open(index_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("Commit:", content)

    def test_main_cli_entry_point(self):
        out_dir = self.temp_dir.name
        test_args = ["generate_portal_page.py", out_dir, "CLI Board", "9876543210", "dev"]
        script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scripts', 'generate_portal_page.py'))
        with patch.object(sys, 'argv', test_args):
            runpy.run_path(script_path, run_name="__main__")

        index_path = os.path.join(out_dir, "index.html")
        self.assertTrue(os.path.exists(index_path))
        with open(index_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("CLI Board", content)
        self.assertIn("9876543", content)
        self.assertIn("dev", content)

    def test_main_cli_entry_point_usage_exit(self):
        test_args = ["generate_portal_page.py"]
        script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scripts', 'generate_portal_page.py'))
        captured_stdout = io.StringIO()
        with patch.object(sys, 'argv', test_args):
            with patch('sys.stdout', captured_stdout):
                with self.assertRaises(SystemExit) as cm:
                    runpy.run_path(script_path, run_name="__main__")
                self.assertEqual(cm.exception.code, 1)
        self.assertIn("Usage: generate_portal_page.py", captured_stdout.getvalue())

if __name__ == '__main__':
    unittest.main()
