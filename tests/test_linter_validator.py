import unittest
import tempfile
import os
import sys
from pathlib import Path

DEVOPS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scripts')))
from linter_validator import check_kicad_symbol_file

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
    (property "Temp_Range" "-40 to 85")
    (property "DigiKey" "123-456-ND")
    (property "Category" "Passives")
  )
)"""
        filepath = self.create_temp_file(content)
        errors = check_kicad_symbol_file(filepath)
        self.assertEqual(len(errors), 1)
        self.assertIn("missing mandatory field: Datasheet", errors[0])

    def test_digikey_sku_property_lints_clean(self):
        # A DigiKey_SKU property is still accepted alongside the other required
        # fields. The linter's DigiKey_SKU -> DigiKey alias branch is currently
        # unobservable, because the branch only ever feeds the mandatory-field
        # check and DigiKey is no longer mandatory; the branch is kept so the
        # alias stays supported if DigiKey ever becomes required again. This test
        # therefore does not cover that branch.
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

    def test_digikey_is_not_mandatory(self):
        """DigiKey is optional: MPN plus Manufacturer is the part's identity."""
        symbol = (
            '(kicad_symbol_lib (version 20211014)\n'
            '  (symbol "TESTPART" (in_bom yes) (on_board yes)\n'
            '    (property "MPN" "TESTPART-1" (id 0) (at 0 0 0))\n'
            '    (property "Manufacturer" "Acme" (id 1) (at 0 0 0))\n'
            '    (property "Datasheet" "https://example.com/t.pdf" (id 2) (at 0 0 0))\n'
            '    (property "Temp_Range" "-40C to 125C" (id 3) (at 0 0 0))\n'
            '    (property "Category" "Power" (id 4) (at 0 0 0))\n'
            '  )\n'
            ')\n'
        )
        with tempfile.NamedTemporaryFile("w", suffix=".kicad_sym", delete=False) as fh:
            fh.write(symbol)
            path = fh.name
        self.addCleanup(os.unlink, path)
        errors = check_kicad_symbol_file(Path(path))
        self.assertEqual(errors, [], f"unexpected errors: {errors}")

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
    (property "Temp_Range" "   ")
    (property "DigiKey" "123-456-ND")
    (property "Category" "Passives")
  )
)"""
        filepath = self.create_temp_file(content)
        errors = check_kicad_symbol_file(filepath)
        self.assertEqual(len(errors), 1)
        self.assertIn("missing mandatory field: Temp_Range", errors[0])

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

    def test_directory_argument_handling(self):
        # Passing a directory containing symbols should scan them without throwing PermissionError
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
        self.create_temp_file(content)
        errors = check_kicad_symbol_file(self.temp_dir.name)
        self.assertEqual(len(errors), 0)

    def test_datasheet_url_with_query_params(self):
        content = """(kicad_symbol_lib
  (symbol "TestSymbol"
    (property "MPN" "12345")
    (property "Manufacturer" "TestCorp")
    (property "Datasheet" "https://example.com/doc.pdf?download=true&ts=123")
    (property "Temp_Range" "-40 to 85")
    (property "DigiKey" "123-456-ND")
    (property "Category" "Passives")
  )
)"""
        filepath = self.create_temp_file(content)
        errors = check_kicad_symbol_file(filepath)
        self.assertEqual(len(errors), 0)

    def test_empty_datasheet_field_single_error(self):
        content = """(kicad_symbol_lib
  (symbol "TestSymbol"
    (property "MPN" "12345")
    (property "Manufacturer" "TestCorp")
    (property "Datasheet" "")
    (property "Temp_Range" "-40 to 85")
    (property "DigiKey" "123-456-ND")
    (property "Category" "Passives")
  )
)"""
        filepath = self.create_temp_file(content)
        errors = check_kicad_symbol_file(filepath)
        # Should only report missing field, not format error or not-a-pdf error
        self.assertEqual(len(errors), 1)
        self.assertIn("missing mandatory field: Datasheet", errors[0])

    def test_nested_property_does_not_satisfy_the_contract(self):
        """A property swallowed by a malformed sibling is not a symbol property.

        The Electrical board's scratch library has a Value property missing a
        closing paren, which swallows the five contract properties inside it.
        A line-based scan counts them and reports the symbol compliant.
        """
        symbol = (
            '(kicad_symbol_lib (version 20211014)\n'
            '  (symbol "NESTEDPROBE" (in_bom yes) (on_board yes)\n'
            '    (property "Reference" "U" (id 0) (at 0 0 0))\n'
            '    (property "Value" "NESTEDPROBE"\n'
            '      (at 0 0 0)\n'
            '      (effects\n'
            '        (font\n'
            '          (size 1.524 1.524)\n'
            '        )\n'
            '    (property "MPN" "NESTEDPROBE" (id 1) (at 0 0 0))\n'
            '    (property "Manufacturer" "Acme" (id 2) (at 0 0 0))\n'
            '    (property "Datasheet" "https://example.com/n.pdf" (id 3) (at 0 0 0))\n'
            '    (property "Temp_Range" "-40C to 125C" (id 4) (at 0 0 0))\n'
            '    (property "Category" "Power" (id 5) (at 0 0 0))\n'
            '      )\n'
            '    )\n'
            '  )\n'
            ')\n'
        )
        with tempfile.NamedTemporaryFile("w", suffix=".kicad_sym", delete=False) as fh:
            fh.write(symbol)
            path = fh.name
        self.addCleanup(os.unlink, path)
        errors = check_kicad_symbol_file(Path(path))
        self.assertTrue(
            any("missing mandatory field" in error for error in errors),
            f"malformed nesting was not detected: {errors}",
        )

    def test_electrical_scratch_library_is_rejected(self):
        source = (DEVOPS_DIR.parent / "Boards" / "X19-Electrical-New-Member-Board"
                  / "libs" / "temporary_New_member_lib.kicad_sym")
        if not source.is_file():
            self.skipTest("Electrical board scratch library is not present")
        errors = check_kicad_symbol_file(source)
        self.assertTrue(errors, "the known-corrupt file must not lint clean")

if __name__ == '__main__':
    unittest.main()
