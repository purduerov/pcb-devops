import unittest
import tempfile
import os
from scripts.linter_validator import check_kicad_symbol_file

class TestLinterValidator(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_temp_file(self, content):
        fd, filepath = tempfile.mkstemp(dir=self.temp_dir.name, suffix='.kicad_sym')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
        return filepath

    def test_file_not_found(self):
        errors = check_kicad_symbol_file("nonexistent_file.kicad_sym")
        self.assertEqual(len(errors), 1)
        self.assertIn("File not found", errors[0])

    def test_valid_symbol(self):
        content = """(kicad_symbol_lib
  (symbol "TestSymbol"
    (property "MPN" "12345")
    (property "Manufacturer" "TestCorp")
    (property "Datasheet" "https://example.com/data.pdf")
    (property "Temp_Range" "-40 to 85")
    (property "DigiKey" "123-456-ND")
    (property "Category" "Passives")
  )
)"""
        filepath = self.create_temp_file(content)
        errors = check_kicad_symbol_file(filepath)
        self.assertEqual(len(errors), 0)

    def test_missing_mandatory_field(self):
        content = """(kicad_symbol_lib
  (symbol "TestSymbol"
    (property "MPN" "12345")
    (property "Manufacturer" "TestCorp")
    (property "Datasheet" "https://example.com/data.pdf")
    (property "Temp_Range" "-40 to 85")
    (property "Category" "Passives")
  )
)"""
        filepath = self.create_temp_file(content)
        errors = check_kicad_symbol_file(filepath)
        self.assertEqual(len(errors), 1)
        self.assertIn("missing mandatory field: DigiKey", errors[0])

    def test_digikey_sku_alias(self):
        content = """(kicad_symbol_lib
  (symbol "TestSymbol"
    (property "MPN" "12345")
    (property "Manufacturer" "TestCorp")
    (property "Datasheet" "https://example.com/data.pdf")
    (property "Temp_Range" "-40 to 85")
    (property "DigiKey_SKU" "123-456-ND")
    (property "Category" "Passives")
  )
)"""
        filepath = self.create_temp_file(content)
        errors = check_kicad_symbol_file(filepath)
        self.assertEqual(len(errors), 0)

    def test_invalid_datasheet_url_format(self):
        content = """(kicad_symbol_lib
  (symbol "TestSymbol"
    (property "MPN" "12345")
    (property "Manufacturer" "TestCorp")
    (property "Datasheet" "ftp://example.com/data.pdf")
    (property "Temp_Range" "-40 to 85")
    (property "DigiKey" "123-456-ND")
    (property "Category" "Passives")
  )
)"""
        filepath = self.create_temp_file(content)
        errors = check_kicad_symbol_file(filepath)
        self.assertEqual(len(errors), 1)
        self.assertIn("invalid Datasheet URL format", errors[0])

    def test_datasheet_not_pdf(self):
        content = """(kicad_symbol_lib
  (symbol "TestSymbol"
    (property "MPN" "12345")
    (property "Manufacturer" "TestCorp")
    (property "Datasheet" "https://example.com/data.html")
    (property "Temp_Range" "-40 to 85")
    (property "DigiKey" "123-456-ND")
    (property "Category" "Passives")
  )
)"""
        filepath = self.create_temp_file(content)
        errors = check_kicad_symbol_file(filepath)
        self.assertEqual(len(errors), 1)
        self.assertIn("datasheet must be a PDF URL", errors[0])

    def test_ignore_sub_symbols(self):
        content = """(kicad_symbol_lib
  (symbol "TestSymbol_0_1"
    (property "MPN" "12345")
  )
)"""
        filepath = self.create_temp_file(content)
        errors = check_kicad_symbol_file(filepath)
        self.assertEqual(len(errors), 0)

    def test_empty_mandatory_field(self):
        content = """(kicad_symbol_lib
  (symbol "TestSymbol"
    (property "MPN" "12345")
    (property "Manufacturer" "TestCorp")
    (property "Datasheet" "https://example.com/data.pdf")
    (property "Temp_Range" "-40 to 85")
    (property "DigiKey" "   ")
    (property "Category" "Passives")
  )
)"""
        filepath = self.create_temp_file(content)
        errors = check_kicad_symbol_file(filepath)
        self.assertEqual(len(errors), 1)
        self.assertIn("missing mandatory field: DigiKey", errors[0])

    def test_invalid_category(self):
        content = """(kicad_symbol_lib
  (symbol "TestSymbol"
    (property "MPN" "12345")
    (property "Manufacturer" "TestCorp")
    (property "Datasheet" "https://example.com/data.pdf")
    (property "Temp_Range" "-40 to 85")
    (property "DigiKey" "123-456-ND")
    (property "Category" "InvalidCategory")
  )
)"""
        filepath = self.create_temp_file(content)
        errors = check_kicad_symbol_file(filepath)
        self.assertEqual(len(errors), 1)
        self.assertIn("invalid Category 'InvalidCategory'", errors[0])

if __name__ == '__main__':
    unittest.main()

