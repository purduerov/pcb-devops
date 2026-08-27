import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scripts')))
from fetch_sourcing_bom import parse_kicad_xml_bom

class TestFetchSourcingBom(unittest.TestCase):
    def test_missing_file_handling(self):
        # Should return an empty dictionary and not crash when file is missing
        parts = parse_kicad_xml_bom("non_existent_file.xml")
        self.assertEqual(parts, {})

    def test_standard_extraction(self):
        xml_content = """<?xml version="1.0" encoding="utf-8"?>
<export version="D">
  <components>
    <comp ref="R1">
      <fields>
        <field name="MPN">ERJ-3EKF1002V</field>
        <field name="DigiKey">P10.0KHTR-ND</field>
      </fields>
    </comp>
    <comp ref="R2">
      <fields>
        <field name="MPN">ERJ-3EKF1002V</field>
        <field name="DigiKey">P10.0KHTR-ND</field>
      </fields>
    </comp>
    <comp ref="C1">
      <fields>
        <field name="MPN">CL10B104KB8NNNC</field>
      </fields>
    </comp>
  </components>
</export>
"""
        with tempfile.NamedTemporaryFile('w', suffix='.xml', delete=False, encoding='utf-8') as f:
            f.write(xml_content)
            temp_path = f.name

        try:
            parts = parse_kicad_xml_bom(temp_path)
            self.assertEqual(len(parts), 2)
            self.assertIn(("ERJ-3EKF1002V", "P10.0KHTR-ND"), parts)
            self.assertEqual(parts[("ERJ-3EKF1002V", "P10.0KHTR-ND")], ["R1", "R2"])
            self.assertIn(("CL10B104KB8NNNC", None), parts)
            self.assertEqual(parts[("CL10B104KB8NNNC", None)], ["C1"])
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_whitespace_handling(self):
        xml_content = """<?xml version="1.0" encoding="utf-8"?>
<export version="D">
  <components>
    <comp ref="R3">
      <fields>
        <field name="MPN">  RES-10K  </field>
        <field name="DigiKey"> DK-10K-RES </field>
      </fields>
    </comp>
  </components>
</export>
"""
        with tempfile.NamedTemporaryFile('w', suffix='.xml', delete=False, encoding='utf-8') as f:
            f.write(xml_content)
            temp_path = f.name

        try:
            parts = parse_kicad_xml_bom(temp_path)
            self.assertEqual(len(parts), 1)
            self.assertIn(("RES-10K", "DK-10K-RES"), parts)
            self.assertEqual(parts[("RES-10K", "DK-10K-RES")], ["R3"])
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

if __name__ == '__main__':
    unittest.main()
