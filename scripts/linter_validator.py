#!/usr/bin/env python3
import sys
import re
import os
import urllib.parse
import glob

# Mandatory fields that must be present in every component symbol
MANDATORY_FIELDS = {"MPN", "Manufacturer", "Datasheet", "Temp_Range", "DigiKey", "Category"}

ALLOWED_CATEGORIES = {"Passives", "Power", "Logic", "Connectors", "Sensors", "Mech"}

SYM_PATTERN = re.compile(r'\(symbol "([^"]+)"')
SUB_SYM_PATTERN = re.compile(r'_[0-9]+_[0-9]+$')
PROP_PATTERN = re.compile(r'\(property "([^"]+)" "([^"]*)"')

def check_kicad_symbol_file(filepath):
    errors = []
    
    if not os.path.exists(filepath):
        return [f"File not found: {filepath}"]

    # If a directory is passed, recursively scan all .kicad_sym files inside it
    if os.path.isdir(filepath):
        dir_errors = []
        for root, _, files in os.walk(filepath):
            for file in sorted(files):
                if file.endswith(".kicad_sym"):
                    subpath = os.path.join(root, file)
                    dir_errors.extend(check_kicad_symbol_file(subpath))
        return dir_errors

    current_symbol = None
    present_fields = set()

    with open(filepath, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            # Parse symbol declaration (matches: (symbol "SymbolName" ...)
            sym_match = SYM_PATTERN.search(line)
            if sym_match:
                symbol_name = sym_match.group(1)
                # Skip sub-symbols (units/graphic parts) which end in _[0-9]+_[0-9]+
                if SUB_SYM_PATTERN.search(symbol_name):
                    continue
                if current_symbol:
                    # Validate previous symbol fields before starting the new one
                    for field in MANDATORY_FIELDS:
                        if field not in present_fields:
                            errors.append(f"Symbol '{current_symbol}' is missing mandatory field: {field}")
                current_symbol = symbol_name
                present_fields = set()
                
            # Parse properties (matches: (property "PropertyName" "PropertyValue" ...)
            prop_match = PROP_PATTERN.search(line)
            if prop_match and current_symbol:
                field_name = prop_match.group(1)
                field_value = prop_match.group(2)
                
                # Check for aliases of DigiKey (DigiKey_SKU or DigiKey)
                if field_name == "DigiKey_SKU":
                    field_name = "DigiKey"
                    
                if field_name in MANDATORY_FIELDS and field_value.strip():
                    present_fields.add(field_name)
                    
                # Validate Category
                if field_name == "Category":
                    if field_value not in ALLOWED_CATEGORIES:
                        errors.append(f"Symbol '{current_symbol}' has invalid Category '{field_value}'. Must be one of: {', '.join(sorted(ALLOWED_CATEGORIES))}")

                # Validate Datasheet URL formatting
                if field_name == "Datasheet" and field_value.strip():
                    parsed_url = urllib.parse.urlparse(field_value.strip())
                    if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
                        errors.append(f"Symbol '{current_symbol}' has invalid Datasheet URL format: {field_value}")
                    elif not parsed_url.path.lower().endswith(".pdf") and not field_value.lower().endswith(".pdf"):
                        errors.append(f"Symbol '{current_symbol}' datasheet must be a PDF URL: {field_value}")

        # Check final symbol at the end of the file
        if current_symbol:
            for field in MANDATORY_FIELDS:
                if field not in present_fields:
                    errors.append(f"Symbol '{current_symbol}' is missing mandatory field: {field}")
                    
    return errors

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: ./linter_validator.py <symbols.kicad_sym | directory> [...]")
        sys.exit(1)
        
    symbol_files = []
    for arg in sys.argv[1:]:
        matched = glob.glob(arg)
        targets = matched if matched else [arg]
        for target in targets:
            if os.path.isdir(target):
                for root, _, files in os.walk(target):
                    for file in sorted(files):
                        if file.endswith(".kicad_sym"):
                            symbol_files.append(os.path.join(root, file))
            else:
                symbol_files.append(target)
            
    all_errors = []
    for symbols_file in symbol_files:
        print(f"Linting KiCad symbol file: {symbols_file}")
        all_errors.extend(check_kicad_symbol_file(symbols_file))
        
    if all_errors:
        print("\n❌ Linter Verification Failed:", file=sys.stderr)
        for err in all_errors:
            print(f" - {err}", file=sys.stderr)
        sys.exit(1)
        
    print("\n✅ Library verified. All components compliant with structural guidelines.")
    sys.exit(0)
