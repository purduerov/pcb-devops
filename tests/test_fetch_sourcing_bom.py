import os
import pytest
from scripts.fetch_sourcing_bom import parse_kicad_xml_bom

def test_missing_file():
    """Test behavior when the BOM XML file does not exist."""
    result = parse_kicad_xml_bom("nonexistent_file.xml")
    assert result == {}

def test_happy_path(tmp_path):
    """Test successful extraction of MPN and DigiKey part numbers."""
    xml_content = """<?xml version="1.0" encoding="utf-8"?>
    <export version="E">
      <components>
        <comp ref="C1">
          <fields>
            <field name="MPN">CAP-001</field>
            <field name="DigiKey">DK-001</field>
          </fields>
        </comp>
        <comp ref="C2">
          <fields>
            <field name="MPN">CAP-001</field>
            <field name="DigiKey">DK-001</field>
          </fields>
        </comp>
        <comp ref="R1">
          <fields>
            <field name="MPN">RES-001</field>
            <field name="DigiKey">DK-002</field>
          </fields>
        </comp>
      </components>
    </export>
    """
    bom_file = tmp_path / "bom.xml"
    bom_file.write_text(xml_content)

    result = parse_kicad_xml_bom(str(bom_file))

    assert len(result) == 2
    assert ("CAP-001", "DK-001") in result
    assert result[("CAP-001", "DK-001")] == ["C1", "C2"]

    assert ("RES-001", "DK-002") in result
    assert result[("RES-001", "DK-002")] == ["R1"]

def test_missing_fields_and_names(tmp_path):
    """Test behavior when components are missing 'fields', 'MPN', or 'DigiKey'."""
    xml_content = """<?xml version="1.0" encoding="utf-8"?>
    <export version="E">
      <components>
        <!-- Missing fields entirely -->
        <comp ref="C1">
        </comp>
        <!-- Missing MPN -->
        <comp ref="C2">
          <fields>
            <field name="DigiKey">DK-001</field>
          </fields>
        </comp>
        <!-- Missing DigiKey -->
        <comp ref="C3">
          <fields>
            <field name="MPN">CAP-002</field>
          </fields>
        </comp>
        <!-- Empty fields, missing name attrs entirely-->
        <comp ref="C4">
          <fields>
            <field name="NotMPNorDigiKey">SomeValue</field>
          </fields>
        </comp>
      </components>
    </export>
    """
    bom_file = tmp_path / "bom.xml"
    bom_file.write_text(xml_content)

    result = parse_kicad_xml_bom(str(bom_file))

    assert len(result) == 2
    assert (None, "DK-001") in result
    assert result[(None, "DK-001")] == ["C2"]

    assert ("CAP-002", None) in result
    assert result[("CAP-002", None)] == ["C3"]

def test_edge_cases_whitespace(tmp_path):
    """Test whitespace-padded part numbers are properly stripped."""
    xml_content = """<?xml version="1.0" encoding="utf-8"?>
    <export version="E">
      <components>
        <comp ref="C1">
          <fields>
            <field name="MPN">  CAP-001  </field>
            <field name="DigiKey">DK-001	</field>
          </fields>
        </comp>
        <comp ref="C2">
          <fields>
            <field name="MPN">CAP-001</field>
            <field name="DigiKey"> DK-001 </field>
          </fields>
        </comp>
      </components>
    </export>
    """
    bom_file = tmp_path / "bom.xml"
    bom_file.write_text(xml_content)

    result = parse_kicad_xml_bom(str(bom_file))

    # Should group both components under the same stripped keys
    assert len(result) == 1
    assert ("CAP-001", "DK-001") in result
    assert result[("CAP-001", "DK-001")] == ["C1", "C2"]
