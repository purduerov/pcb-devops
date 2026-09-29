#!/usr/bin/env python3
import sys
import re
import os
import urllib.parse
import glob

# Mandatory fields that must be present in every component symbol
MANDATORY_FIELDS = {"MPN", "Manufacturer", "Datasheet", "Temp_Range", "Category"}

ALLOWED_CATEGORIES = {"Passives", "Power", "Logic", "Connectors", "Sensors", "Mech"}

SYM_PATTERN = re.compile(r'\(symbol "([^"]+)"')
SUB_SYM_PATTERN = re.compile(r'_[0-9]+_[0-9]+$')

def _top_level_symbols(text):
    """Yield (name, block) for each top-level symbol, skipping unit sub-symbols.

    `block` is the symbol's own s-expression, from its opening paren to the
    matching close. The walk tracks depth so that a paren inside a quoted
    string is ignored and a backslash escapes the next character, which keeps
    the block boundary correct even when a symbol contains awkward text.
    """
    for match in SYM_PATTERN.finditer(text):
        name = match.group(1)
        if SUB_SYM_PATTERN.search(name):
            continue
        start = match.start()
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    yield name, text[start:index + 1]
                    break


def _direct_properties(block):
    """Return {name: value} for properties that are direct children of a symbol block.

    `block` starts at the symbol's opening paren, so a direct child sits at
    depth 1. Anything deeper was swallowed by a malformed sibling and does
    not count. A property whose name or value string is unterminated is
    unreadable, so it is reported as absent rather than raising.
    """
    props = {}
    depth = 0
    in_string = False
    escaped = False
    index = 0
    while index < len(block):
        char = block[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
        elif char == "(":
            if depth == 1 and block.startswith('(property "', index):
                try:
                    name_end = block.index('"', index + 11)
                    value_start = block.index('"', name_end + 1)
                    value_end = block.index('"', value_start + 1)
                except ValueError:
                    pass
                else:
                    props[block[index + 11:name_end]] = block[value_start + 1:value_end]
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                break
        index += 1
    return props

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

    with open(filepath, 'r', encoding='utf-8') as f:
        text = f.read()

    # Walk the file's structure, not its lines: only a property that is a
    # direct child of the symbol counts towards the contract, so a property
    # swallowed by a malformed sibling is reported as missing.
    for symbol_name, block in _top_level_symbols(text):
        present_fields = set()

        for field_name, field_value in _direct_properties(block).items():
            # Check for aliases of DigiKey (DigiKey_SKU or DigiKey)
            if field_name == "DigiKey_SKU":
                field_name = "DigiKey"
                    
            if field_name in MANDATORY_FIELDS and field_value.strip():
                present_fields.add(field_name)
                
            # Validate Category
            if field_name == "Category":
                if field_value not in ALLOWED_CATEGORIES:
                    errors.append(f"Symbol '{symbol_name}' has invalid Category '{field_value}'. Must be one of: {', '.join(sorted(ALLOWED_CATEGORIES))}")

            # Validate Datasheet URL formatting
            if field_name == "Datasheet" and field_value.strip():
                parsed_url = urllib.parse.urlparse(field_value.strip())
                if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
                    errors.append(f"Symbol '{symbol_name}' has invalid Datasheet URL format: {field_value}")
                elif not parsed_url.path.lower().endswith(".pdf") and not field_value.lower().endswith(".pdf"):
                    errors.append(f"Symbol '{symbol_name}' datasheet must be a PDF URL: {field_value}")

        for field in MANDATORY_FIELDS:
            if field not in present_fields:
                errors.append(f"Symbol '{symbol_name}' is missing mandatory field: {field}")

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
        print("\n[ERROR] Linter Verification Failed:", file=sys.stderr)
        for err in all_errors:
            print(f" - {err}", file=sys.stderr)
        sys.exit(1)
        
    print("\n[OK] Library verified. All components compliant with structural guidelines.")
    sys.exit(0)
