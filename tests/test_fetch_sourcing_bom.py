import os
import sys
import pytest

# Add the 'scripts' directory to the python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scripts')))
from fetch_sourcing_bom import parse_kicad_xml_bom

def test_missing_file_handling():
    # Should return an empty dictionary and not crash when file is missing
    parts = parse_kicad_xml_bom("non_existent_file.xml")
    assert parts == {}

def test_standard_extraction(tmp_path):
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
    xml_file = tmp_path / "bom.xml"
    xml_file.write_text(xml_content)

    parts = parse_kicad_xml_bom(str(xml_file))

    assert len(parts) == 2
    assert ("ERJ-3EKF1002V", "P10.0KHTR-ND") in parts
    assert parts[("ERJ-3EKF1002V", "P10.0KHTR-ND")] == ["R1", "R2"]

    assert ("CL10B104KB8NNNC", None) in parts
    assert parts[("CL10B104KB8NNNC", None)] == ["C1"]

def test_whitespace_handling(tmp_path):
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
    xml_file = tmp_path / "bom_whitespace.xml"
    xml_file.write_text(xml_content)

    parts = parse_kicad_xml_bom(str(xml_file))

    assert len(parts) == 1
    # Check if trailing/leading spaces are removed
    assert ("RES-10K", "DK-10K-RES") in parts
    assert parts[("RES-10K", "DK-10K-RES")] == ["R3"]
