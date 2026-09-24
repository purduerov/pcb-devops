import io
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, mock_open

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scripts')))
import fetch_sourcing_bom
from fetch_sourcing_bom import parse_kicad_xml_bom

class TestFetchSourcingBom(unittest.TestCase):
    def test_missing_file_handling(self):
        # Should return an empty dictionary and not crash when file is missing
        parts = parse_kicad_xml_bom("non_existent_file.xml")
        self.assertEqual(parts, {})

    def test_invalid_xml_handling(self):
        # Should return an empty dictionary when XML is malformed
        xml_content = "<?xml version='1.0'?><export><components><comp ref='R1'>"  # unclosed tag
        with tempfile.NamedTemporaryFile('w', suffix='.xml', delete=False, encoding='utf-8') as f:
            f.write(xml_content)
            temp_path = f.name

        try:
            parts = parse_kicad_xml_bom(temp_path)
            self.assertEqual(parts, {})
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

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

    @patch('fetch_sourcing_bom.DIGIKEY_CLIENT_ID', 'dummy_client_id')
    @patch('fetch_sourcing_bom.DIGIKEY_CLIENT_SECRET', 'dummy_client_secret')
    @patch('fetch_sourcing_bom.DIGIKEY_TOKEN_PATH', 'dummy_token.json')
    @patch('os.path.exists')
    def test_get_digikey_access_token_json_error(self, mock_exists):
        mock_exists.return_value = True
        invalid_json_data = "{invalid_json: true"

        captured_stderr = io.StringIO()
        with patch('builtins.open', mock_open(read_data=invalid_json_data)):
            with patch('sys.stderr', captured_stderr):
                with patch('os.getenv', return_value=None):
                    result = fetch_sourcing_bom.get_digikey_access_token()

        self.assertIsNone(result)
        self.assertIn("Warning: Failed to load DigiKey token file", captured_stderr.getvalue())

    @patch('urllib.request.urlopen')
    @patch('fetch_sourcing_bom.get_digikey_access_token', return_value='test_token')
    @patch('fetch_sourcing_bom.DIGIKEY_CLIENT_ID', 'dummy_client')
    def test_digikey_request_timeout(self, mock_get_token, mock_urlopen):
        mock_response = unittest.mock.MagicMock()
        mock_response.read.return_value = b'{"Products": []}'
        mock_urlopen.return_value.__enter__.return_value = mock_response

        fetch_sourcing_bom.query_digikey_part_data("RES-10K")
        self.assertTrue(mock_urlopen.called)
        _, kwargs = mock_urlopen.call_args
        self.assertIn('timeout', kwargs)
        self.assertEqual(kwargs['timeout'], 10)

    @patch('urllib.request.urlopen')
    @patch('fetch_sourcing_bom.MOUSER_API_KEY', 'dummy_key')
    def test_mouser_request_timeout(self, mock_urlopen):
        mock_response = unittest.mock.MagicMock()
        mock_response.read.return_value = b'{"SearchResults": {"Parts": []}}'
        mock_urlopen.return_value.__enter__.return_value = mock_response

        fetch_sourcing_bom.query_mouser_part_data("RES-10K")
        self.assertTrue(mock_urlopen.called)
        _, kwargs = mock_urlopen.call_args
        self.assertIn('timeout', kwargs)

    def test_case_insensitive_and_value_fallback(self):
        xml_content = """<?xml version="1.0" encoding="utf-8"?>
<export version="D">
  <components>
    <comp ref="U1">
      <fields>
        <field name="mpn">TPS62130RGTR</field>
        <field name="Digi-Key">296-30230-1-ND</field>
      </fields>
    </comp>
    <comp ref="U2">
      <value>STM32F405RGT6</value>
    </comp>
    <comp ref="R1">
      <value>10k</value>
    </comp>
  </components>
</export>
"""
        with tempfile.NamedTemporaryFile('w', suffix='.xml', delete=False, encoding='utf-8') as f:
            f.write(xml_content)
            temp_path = f.name

        try:
            parts = parse_kicad_xml_bom(temp_path)
            self.assertEqual(len(parts), 3)
            self.assertIn(("TPS62130RGTR", "296-30230-1-ND"), parts)
            self.assertEqual(parts[("TPS62130RGTR", "296-30230-1-ND")], ["U1"])
            self.assertIn(("STM32F405RGT6", None), parts)
            self.assertEqual(parts[("STM32F405RGT6", None)], ["U2"])
            self.assertIn(("10k", None), parts)
            self.assertEqual(parts[("10k", None)], ["R1"])
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_digikey_sku_field_alias(self):
        xml_content = """<?xml version="1.0" encoding="utf-8"?>
<export version="D">
  <components>
    <comp ref="U1">
      <fields>
        <field name="MPN">ATMEGA328P-PU</field>
        <field name="DigiKey_SKU">ATMEGA328P-PU-ND</field>
      </fields>
    </comp>
    <comp ref="U2">
      <fields>
        <field name="DigiKey_SKU">DK-ONLY-SKU</field>
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
            self.assertIn(("ATMEGA328P-PU", "ATMEGA328P-PU-ND"), parts)
            self.assertEqual(parts[("ATMEGA328P-PU", "ATMEGA328P-PU-ND")], ["U1"])
            self.assertIn((None, "DK-ONLY-SKU"), parts)
            self.assertEqual(parts[(None, "DK-ONLY-SKU")], ["U2"])
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_no_mpn_or_digikey_fields(self):
        xml_content = """<?xml version="1.0" encoding="utf-8"?>
<export version="D">
  <components>
    <comp ref="FID1">
      <fields>
        <field name="Value">Fiducial</field>
      </fields>
    </comp>
    <comp ref="TP1">
    </comp>
    <comp ref="R4">
      <fields>
        <field name="MPN">   </field>
        <field name="DigiKey"></field>
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
            self.assertEqual(parts, {})
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

if __name__ == '__main__':
    unittest.main()
