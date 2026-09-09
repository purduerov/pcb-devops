import sys
import unittest
import tempfile
from pathlib import Path

DEVOPS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DEVOPS_DIR / "scripts"))

import sync_project_libs

class TestSyncProjectLibs(unittest.TestCase):
    def test_sync_fresh_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            res = sync_project_libs.sync_project(p)
            self.assertTrue(res["sym_changed"])
            self.assertTrue(res["fp_changed"])
            self.assertEqual(len(res["sym_added"]), 6)
            self.assertEqual(len(res["fp_added"]), 6)

            sym_content = (p / "sym-lib-table").read_text(encoding="utf-8")
            self.assertIn('(name "rov_passives")', sym_content)
            self.assertIn('(name "rov_power")', sym_content)
            self.assertIn('(name "rov_logic")', sym_content)
            self.assertIn('(name "rov_connectors")', sym_content)
            self.assertIn('(name "rov_sensors")', sym_content)
            self.assertIn('(name "rov_mech")', sym_content)

            fp_content = (p / "fp-lib-table").read_text(encoding="utf-8")
            self.assertIn('(name "rov_passives")', fp_content)

            # Second run should make 0 changes
            res2 = sync_project_libs.sync_project(p)
            self.assertFalse(res2["sym_changed"])
            self.assertFalse(res2["fp_changed"])
            self.assertEqual(len(res2["sym_added"]), 0)

    def test_preserve_custom_libs(self):
        existing_sym = """(sym_lib_table
  (lib (name "Custom_Sensor")(type "KiCad")(uri "${KIPRJMOD}/Custom/sensor.kicad_sym")(options "")(descr ""))
)"""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            (p / "sym-lib-table").write_text(existing_sym, encoding="utf-8")
            
            res = sync_project_libs.sync_project(p)
            self.assertTrue(res["sym_changed"])
            self.assertEqual(len(res["sym_added"]), 6)

            sym_content = (p / "sym-lib-table").read_text(encoding="utf-8")
            self.assertIn('(name "Custom_Sensor")', sym_content)
            self.assertIn('(name "rov_passives")', sym_content)
            self.assertIn('(name "rov_power")', sym_content)

    def test_sync_gitmodules(self):
        gitmod_content = """[submodule "libs/purdue-rov-kicad-lib"]
\tpath = libs/purdue-rov-kicad-lib
\turl = ../purdue-rov-kicad-lib.git
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            gm_path = p / ".gitmodules"
            gm_path.write_text(gitmod_content, encoding="utf-8")

            changed = sync_project_libs.sync_gitmodules(gm_path)
            self.assertTrue(changed)

            updated = gm_path.read_text(encoding="utf-8")
            self.assertIn("branch = master", updated)

            # Second run
            changed2 = sync_project_libs.sync_gitmodules(gm_path)
            self.assertFalse(changed2)

if __name__ == "__main__":
    unittest.main()
