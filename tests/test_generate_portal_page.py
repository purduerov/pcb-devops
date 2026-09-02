import unittest
import tempfile
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scripts')))
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

if __name__ == '__main__':
    unittest.main()
