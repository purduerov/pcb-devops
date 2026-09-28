# Library Part Promotion and Reference Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote 12 board-local parts and their footprints into the shared library, repoint 32 dangling symbol references across four boards onto the shared `rov_*` libraries, and retire the board-local libraries that made those references unresolvable.

**Architecture:** The shared library stays the single source of truth and keeps its existing 6-category layout. Part promotion goes through the existing importer, extended so it also writes the per-part source file the build step compiles from, so a rebuild can never silently discard an import. A new standalone script in DevOps performs the schematic reference rewrite, driven by an explicit mapping table rather than heuristics, and reports what it cannot map instead of guessing.

**Tech Stack:** Python 3.10+, `unittest`, KiCad 6+ S-expression files, the existing `rov` CLI and `import_part.py` / `build_symbol_libs.py` scripts.

**Spec:** `docs/superpowers/specs/2026-09-28-library-part-promotion-and-reference-consolidation-design.md`

## Global Constraints

- Required metadata fields are exactly `MPN`, `Manufacturer`, `Datasheet`, `Temp_Range`, `Category`. `DigiKey` is no longer required but is never deleted from an existing symbol.
- Allowed categories are exactly `["Passives", "Power", "Logic", "Connectors", "Sensors", "Mech"]`.
- Never merge two distinct manufacturer part numbers. `TCAN1044VDRQ1` and `TCAN1044AVDRQ1` are separate parts, as are `SMCJ58A` and `SMBJ58A-TR`.
- Never author a symbol from a datasheet. The five parts with no source file stay unresolved and are reported.
- Do not read or copy anything from `Archive_and_Legacy/`.
- The rewrite touches no reference designators, no net connectivity, and no instance counts. It changes library binding only.
- Every promoted symbol must survive `build_symbol_libs.py`. A symbol that exists only in an aggregate file is a defect, not a result.
- No emojis in any code, comment, commit message, or generated file.
- Each board is committed separately so one failure does not take the others with it.
- Changes are pushed directly to `master` with no pull request, per the owner's instruction. Every commit message lists what changed and what a reviewer should check.

## Review Focus

These are the inputs most likely to cause silent, wrong results. Each has a test pinning it in the task that owns the relevant code.

1. **A cached `lib_symbols` body was hand-edited in a board.** Replacing it with the library's body discards that local edit without trace. The transform must report every body it replaces, with old and new keys, so the diff is reviewable.
2. **A `lib_id` names a different part than its `Value` property.** Three instances on `X19-Pi-Shield-Board` do this: `ECS-120-18-33-JGN-TR` carries `Value` `ECS-400-18-33-JGN-TR`, and `MCP2518FDT-E_QBB` carries `Value` `MCP2518FDT-E/QBB`. Repointing resolves these to something that loads, but not necessarily to what the board owner intended. They must be reported and skipped, not silently repointed.
3. **The same symbol is cached under two different nicknames in one file.** A cache replace keyed on one nickname would miss the other and leave a stale body behind. The transform must key on both old and new keys and verify no old key survives.
4. **The schematic has CRLF line endings or a byte-order mark.** Naive read-rewrite normalizes or corrupts the file, producing a whole-file diff that hides the real change.
5. **The transform runs twice.** The second run must be a no-op. A non-idempotent transform would rewrite already-correct references to themselves or double-apply a body replacement.

## File Structure

Created:

- `KiCad/DevOps/scripts/consolidate_symbol_refs.py` — the reference rewrite. Self-contained: it does not import `kicad_sym_utils`, so it has no dependency on a library checkout for its own logic. Reads the shared library only to source replacement symbol bodies.
- `KiCad/DevOps/tests/test_consolidate_symbol_refs.py` — unit tests for the above.

Modified:

- `KiCad/Libraries/scripts/import_part.py` — write the per-part source file before appending; drop `DigiKey` from `MANDATORY_FIELDS`.
- `KiCad/DevOps/scripts/linter_validator.py` — drop `DigiKey` from `MANDATORY_FIELDS`; remove emoji from output.
- `KiCad/Libraries/scripts/linter_validator.py` — drop `DigiKey` from `MANDATORY_FIELDS`.
- `KiCad/Libraries/scripts/kicad_sym_utils.py` — drop `DigiKey` from `mandatory`.
- `KiCad/Libraries/scripts/library_manager_gui.py` — drop `DigiKey` from `MANDATORY_FIELDS`; unmark the field optional in the form.
- `KiCad/Libraries/CONTRIBUTING.md`, `KiCad/Libraries/README.md` — document the new required set.
- `KiCad/DevOps/tests/test_linter_validator.py` — two assertions expect the `DigiKey` error.
- `KiCad/Libraries/tests/test_kicad_sym_utils.py` — one assertion expects the `DigiKey` error.
- The four affected boards' `*.kicad_sch`, `sym-lib-table`, and `fp-lib-table` files.

---

### Task 1: Drop DigiKey from the required metadata contract

The required field set is defined in five places, and two of them are near-duplicate linter scripts that both execute. `rov.py:_resolve_linter` prefers the library's copy and falls back to DevOps', so a change to only one produces two different contracts depending on which repo is validated.

**Files:**
- Modify: `KiCad/Libraries/scripts/import_part.py:56`
- Modify: `KiCad/Libraries/scripts/kicad_sym_utils.py:614`
- Modify: `KiCad/Libraries/scripts/linter_validator.py:15`
- Modify: `KiCad/Libraries/scripts/library_manager_gui.py:197`
- Modify: `KiCad/DevOps/scripts/linter_validator.py:9`
- Modify: `KiCad/DevOps/tests/test_linter_validator.py:55,128`
- Modify: `KiCad/Libraries/tests/test_kicad_sym_utils.py:289`
- Modify: `KiCad/Libraries/CONTRIBUTING.md`, `KiCad/Libraries/README.md`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: the single required-field set `{"MPN", "Manufacturer", "Datasheet", "Temp_Range", "Category"}`, enforced identically by every linter copy. Later tasks rely on a part importing successfully without a `--digikey` argument.

- [ ] **Step 1: Write the failing test**

Add to `KiCad/DevOps/tests/test_linter_validator.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_linter_validator.LinterValidatorTest.test_digikey_is_not_mandatory -v`
Expected: FAIL with `missing mandatory field: DigiKey` in the error list.

- [ ] **Step 3: Drop DigiKey from all five definitions**

In `KiCad/DevOps/scripts/linter_validator.py` line 9 and `KiCad/Libraries/scripts/linter_validator.py` line 15, change:

```python
MANDATORY_FIELDS = {"MPN", "Manufacturer", "Datasheet", "Temp_Range", "DigiKey", "Category"}
```

to:

```python
MANDATORY_FIELDS = {"MPN", "Manufacturer", "Datasheet", "Temp_Range", "Category"}
```

In `KiCad/Libraries/scripts/kicad_sym_utils.py` line 614, change:

```python
    mandatory = ["Category", "MPN", "Manufacturer", "DigiKey", "Datasheet", "Temp_Range"]
```

to:

```python
    mandatory = ["Category", "MPN", "Manufacturer", "Datasheet", "Temp_Range"]
```

In `KiCad/Libraries/scripts/import_part.py` line 56, change:

```python
MANDATORY_FIELDS = ["MPN", "Manufacturer", "Datasheet", "Temp_Range", "DigiKey", "Category"]
```

to:

```python
MANDATORY_FIELDS = ["MPN", "Manufacturer", "Datasheet", "Temp_Range", "Category"]
```

In `KiCad/Libraries/scripts/library_manager_gui.py` line 197, change:

```python
    MANDATORY_FIELDS = ["Category", "MPN", "Manufacturer", "DigiKey", "Datasheet", "Temp_Range"]
```

to:

```python
    MANDATORY_FIELDS = ["Category", "MPN", "Manufacturer", "Datasheet", "Temp_Range"]
```

Leave the `DigiKey` alias handling in both linters, the `DigiKey` property in `kicad_sym_utils.autofill_component_data`, and the `--digikey` CLI flag in place. The field is still detected, still autofilled, and still importable; it is simply no longer required.

- [ ] **Step 4: Run the new test to verify it passes**

Run: `python -m unittest tests.test_linter_validator -v`
Expected: the new test PASSes. Three pre-existing tests will now FAIL; that is expected and is fixed in Step 5.

- [ ] **Step 5: Update the tests that assert the DigiKey error**

In `KiCad/DevOps/tests/test_linter_validator.py`, the tests at lines 55 and 128 assert on the old behavior. Read each test, then change the symbol under test so it is missing a still-required field instead of `DigiKey`, and update the assertion to match. Concretely, remove the `(property "DigiKey" ...)` line from the symbol fixture and change:

```python
        self.assertIn("missing mandatory field: DigiKey", errors[0])
```

to:

```python
        self.assertIn("missing mandatory field: Datasheet", errors[0])
```

adding a `(property "Datasheet" ...)` line back only where the test needs `DigiKey` to be the absent field. Where a test exists purely to prove the `DigiKey_SKU` alias is accepted (`test_digikey_sku_alias`), leave the fixture alone and change only the assertion to check that no `DigiKey` error appears.

In `KiCad/Libraries/tests/test_kicad_sym_utils.py` line 289, change:

```python
        self.assertTrue(any("Missing mandatory field: 'DigiKey'" in e for e in errors))
```

to:

```python
        self.assertTrue(any("Missing mandatory field: 'Temp_Range'" in e for e in errors))
```

adjusting the fixture so `Temp_Range` is the field actually absent.

- [ ] **Step 6: Mark the field optional in the GUI form**

In `KiCad/Libraries/scripts/library_manager_gui.py`, line 391 and line 1117, change the label:

```python
    ("DigiKey", "DigiKey SKU / Link *"),
```

to:

```python
    ("DigiKey", "DigiKey SKU / Link"),
```

The asterisk marks required fields. Do not remove the field or the `copy_digikey` handler.

- [ ] **Step 7: Update the documentation**

In `KiCad/Libraries/CONTRIBUTING.md` and `KiCad/Libraries/README.md`, find every listing of the required symbol fields and change it from the six-field set to:

```
MPN, Manufacturer, Datasheet, Temp_Range, Category
```

Where the text explains why a field is required, keep the explanation for the five and add one line stating that `DigiKey` is optional because it is one distributor's SKU and a part is not required to be stocked there.

- [ ] **Step 8: Run both full suites**

Run: `python -m unittest discover -s tests -v` from `KiCad/DevOps`, then from `KiCad/Libraries`.
Expected: both report `OK`.

- [ ] **Step 9: Commit**

```bash
git -C KiCad/DevOps add scripts/linter_validator.py tests/test_linter_validator.py
git -C KiCad/DevOps commit -m "feat(contract): stop requiring a DigiKey SKU on every part

A DigiKey part number is one distributor's internal SKU and is not
derivable from an MPN without their product API, so requiring it made
parts permanently unimportable. MPN plus Manufacturer is the identity
of a part. The field is still detected, autofilled, and imported when
present; it is just no longer mandatory.

Also replaces the emoji markers in the DevOps linter output with plain
text, which AGENTS.md forbids in code. That copy is a live fallback
for repos without their own linter, so it is a real output path, not a
dead file.

Both linter copies are changed together on purpose: rov.py prefers the
library's copy and falls back to this one, so changing only one would
produce two different contracts depending on which repo is validated."
```

Commit the `KiCad/Libraries` half separately:

```bash
git -C KiCad/Libraries add scripts/import_part.py scripts/kicad_sym_utils.py scripts/linter_validator.py scripts/library_manager_gui.py tests/test_kicad_sym_utils.py CONTRIBUTING.md README.md
git -C KiCad/Libraries commit -m "feat(contract): stop requiring a DigiKey SKU on every part

DigiKey is one distributor's SKU and is not derivable from an MPN
without their product API. MPN plus Manufacturer identifies a part, so
DigiKey becomes optional. Existing symbols keep their DigiKey values;
nothing is removed, only the requirement."
```

---

### Task 2: Make the CLI import path rebuild-safe

`import_part.py` appends to the aggregate `Symbols/rov_<category>.kicad_sym` only. `build_symbol_libs.py` compiles `Symbols/parts/<category>/*.kicad_sym` and **overwrites** that aggregate. A part imported through the CLI is therefore destroyed by the next build. The GUI already writes both files; the CLI does not. This task closes that gap, because every later promotion task depends on it.

**Files:**
- Modify: `KiCad/Libraries/scripts/import_part.py` — add a per-part writer and call it.
- Test: `KiCad/Libraries/tests/test_library_manager.py`

**Interfaces:**
- Consumes: `CATEGORIES` from `kicad_sym_utils` (already imported), the resolved `category` and `updated_sym` in `main()`.
- Produces: `write_part_source_file(category: str, sym_block: str) -> Path` in `import_part.py`, mirroring the GUI's write at `library_manager_gui.py:282-295`. Tasks 3, 4, and 5 depend on the per-part file existing after any import.

- [ ] **Step 1: Write the failing test**

Add to `KiCad/Libraries/tests/test_library_manager.py`:

```python
class TestPerPartSourceFile(unittest.TestCase):
    """A CLI import must survive a rebuild of the aggregate category file."""

    def test_imported_symbol_survives_rebuild(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            lib = Path(tmpdir)
            scripts = lib / "scripts"
            scripts.mkdir()
            (lib / "Symbols" / "parts").mkdir(parents=True)
            for name in ("import_part", "kicad_sym_utils", "build_symbol_libs"):
                shutil.copy2(BASE_DIR / "scripts" / f"{name}.py", scripts / f"{name}.py")
            (lib / "Symbols" / "rov_power.kicad_sym").write_text(
                '(kicad_symbol_lib (version 20211014)\n  (generator "kicad_symbol_editor")\n)\n',
                encoding="utf-8",
            )
            source = Path(tmpdir) / "part.kicad_sym"
            source.write_text(
                '(kicad_symbol_lib (version 20211014)\n'
                '  (symbol "REBUILDPROBE" (in_bom yes) (on_board yes)\n'
                '    (property "Reference" "U" (id 0) (at 0 0 0))\n'
                '  )\n'
                ')\n',
                encoding="utf-8",
            )
            env = dict(os.environ, PYTHONPATH=str(scripts))
            result = subprocess.run(
                [sys.executable, str(scripts / "import_part.py"),
                 "--symbol", str(source), "--category", "Power",
                 "--mpn", "REBUILDPROBE", "--mfr", "Acme",
                 "--datasheet", "https://example.com/p.pdf",
                 "--temp", "-40C to 125C"],
                capture_output=True, text=True, env=env, cwd=tmpdir,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            per_part = lib / "Symbols" / "parts" / "power" / "REBUILDPROBE.kicad_sym"
            self.assertTrue(per_part.is_file(), "per-part source file was not written")

            rebuild = subprocess.run(
                [sys.executable, str(scripts / "build_symbol_libs.py")],
                capture_output=True, text=True, env=env, cwd=tmpdir,
            )
            self.assertEqual(rebuild.returncode, 0, rebuild.stderr)
            aggregate = (lib / "Symbols" / "rov_power.kicad_sym").read_text(encoding="utf-8")
            self.assertIn('(symbol "REBUILDPROBE"', aggregate,
                          "rebuild discarded the imported symbol")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_library_manager.TestPerPartSourceFile -v`
Expected: FAIL, `per-part source file was not written` is `False`.

- [ ] **Step 3: Add the per-part writer**

In `KiCad/Libraries/scripts/import_part.py`, immediately after `copy_footprint_to_category` (which ends at line 155), add:

```python
PARTS_DIR = SYMBOLS_DIR / "parts"

def write_part_source_file(cat, sym_block):
    """Write the per-part source file that build_symbol_libs.py compiles from.

    The aggregate category file is derived output. Appending to it alone
    loses the part on the next build, so every import also writes the
    per-part file under Symbols/parts/<category>/.
    """
    match = re.search(r'\(\s*symbol\s+"([^"]+)"', sym_block)
    sym_name = match.group(1) if match else "NEW_PART"
    safe_name = re.sub(r'[^a-zA-Z0-9_\-\.\+]', '_', sym_name)
    cat_folder = PARTS_DIR / cat.lower()
    cat_folder.mkdir(parents=True, exist_ok=True)
    part_file = cat_folder / f"{safe_name}.kicad_sym"
    part_content = (
        '(kicad_symbol_lib (version 20211014) (generator "kicad_symbol_editor")\n'
        f'  {sym_block.strip()}\n'
        ')\n'
    )
    part_file.write_text(part_content, encoding="utf-8")
    print(f"[OK] Wrote per-part source to: {part_file}")
    return part_file
```

- [ ] **Step 4: Call it from main()**

In `main()`, replace:

```python
    updated_sym = inject_or_update_properties(sym_block, field_updates)
    append_symbol_to_category(category, updated_sym)
```

with:

```python
    updated_sym = inject_or_update_properties(sym_block, field_updates)
    write_part_source_file(category, updated_sym)
    append_symbol_to_category(category, updated_sym)
```

The rename to the MPN happens earlier at the `rename_symbol` call, so the per-part file is named after the MPN, matching the convention the existing 20 parts already follow.

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m unittest tests.test_library_manager.TestPerPartSourceFile -v`
Expected: PASS.

- [ ] **Step 6: Run the full Libraries suite**

Run: `python -m unittest discover -s tests -v`
Expected: `OK`. If any existing test counted symbols in `Symbols/parts/`, update its expected count.

- [ ] **Step 7: Commit**

```bash
git -C KiCad/Libraries add scripts/import_part.py tests/test_library_manager.py
git -C KiCad/Libraries commit -m "fix(import): write the per-part source file on every CLI import

build_symbol_libs.py compiles Symbols/parts/<category>/ into the
aggregate rov_<category>.kicad_sym and overwrites it, so a part added
only to the aggregate was silently destroyed by the next build. The GUI
already wrote both files; the CLI wrote only the aggregate. This makes
the CLI path match the GUI path and adds a test that imports a part and
then rebuilds to prove the part survives."
```

---

### Task 3: Promote the power parts

**Files:**
- Source (read-only): `KiCad/Boards/X19-Float-Board/CustomComponents/buck_tps561208.kicad_sym`, `X19-Float-Board/CustomComponents/footprints.pretty/DDC0006A_N.kicad_mod`
- Source (read-only): `KiCad/Boards/X19-Electrical-New-Member-Board/libs/temporary_New_member_lib.kicad_sym`
- Source (read-only): `KiCad/Boards/X19-Power-Slab-Board/DigikeyParts/E48SC12030NRFH/E48SC12030NRFH/KiCad/E48SC12030NRFH.kicad_sym` and the `.kicad_mod` beside it
- Create: `KiCad/Libraries/Symbols/parts/power/TPS561208DDCR.kicad_sym`, `.../E48SC12030NRFH.kicad_sym`, `.../AZ1117IH-3.3TRG1.kicad_sym`, `.../IRS-5_10-Q12P-C.kicad_sym`
- Create: `KiCad/Libraries/Footprints/rov_power.pretty/DDC0006A_N.kicad_mod`, `.../E48SC12030NRFH.kicad_mod`

**Interfaces:**
- Consumes: `write_part_source_file` and `copy_footprint_to_category` from Task 2, by way of the `import_part.py` CLI.
- Produces: four symbols resolvable as `rov_power:<MPN>`, which Task 7 and Task 9 depend on.

- [ ] **Step 1: Record the metadata for each part before importing**

Verified metadata for the parts in this task. Every datasheet value below was confirmed to resolve; do not substitute a guessed URL.

| Part | Manufacturer | Datasheet | Temp_Range |
| --- | --- | --- | --- |
| `TPS561208DDCR` | Texas Instruments | `https://www.ti.com/lit/ds/symlink/tps561208.pdf` | `-40°C to 125°C` |
| `E48SC12030NRFH` | Delta Electronics | `https://resources.ampheo.com/static/datasheets/delta-electronics/e48sc12030nrfh.pdf` | `-40°C to 85°C` |
| `AZ1117IH-3.3TRG1` | already in the symbol | already in the symbol | already in the symbol |
| `IRS-5_10-Q12P-C` | already in the symbol | already in the symbol | already in the symbol |

The `E48SC12030NRFH` datasheet is Delta's own `E48SC12030 360W 1/8 Brick DC/DC Power Modules` document, reachable only through a distributor mirror; Delta's product page did not resolve. Name the document in the commit message so a reviewer can find it.

`AZ1117IH-3.3TRG1` and `IRS-5_10-Q12P-C` already carry `MPN`, `Manufacturer`, `Datasheet`, `Temp_Range`, and `Category` in the source symbol, so no lookup is needed and no value should be typed by hand.

- [ ] **Step 2: Import `TPS561208DDCR` with its footprint**

Run from `KiCad/Libraries`:

```bash
python scripts/import_part.py \
  --symbol ../Boards/X19-Float-Board/CustomComponents/buck_tps561208.kicad_sym \
  --footprint ../Boards/X19-Float-Board/CustomComponents/footprints.pretty/DDC0006A_N.kicad_mod \
  --category Power --mpn "TPS561208DDCR" --mfr "Texas Instruments" \
  --datasheet "https://www.ti.com/lit/ds/symlink/tps561208.pdf" --temp "-40°C to 125°C"
```

Expected output includes `[OK] Wrote per-part source to:` and `[OK] Part imported successfully and verified compliant!`.

- [ ] **Step 3: Import `E48SC12030NRFH` with its footprint**

```bash
python scripts/import_part.py \
  --symbol "../Boards/X19-Power-Slab-Board/DigikeyParts/E48SC12030NRFH/E48SC12030NRFH/KiCad/E48SC12030NRFH.kicad_sym" \
  --footprint "../Boards/X19-Power-Slab-Board/DigikeyParts/E48SC12030NRFH/E48SC12030NRFH/KiCad/E48SC12030NRFH.kicad_mod" \
  --category Power --mpn "E48SC12030NRFH" --mfr "Delta Electronics" \
  --datasheet "https://resources.ampheo.com/static/datasheets/delta-electronics/e48sc12030nrfh.pdf" \
  --temp "-40°C to 85°C"
```

- [ ] **Step 4: Import `AZ1117IH-3.3TRG1` and `IRS-5_10-Q12P-C` with no footprint**

`temporary_New_member_lib.kicad_sym` already carries the full contract set for both, so pass the category and let the alias resolver fill the rest. Omit `--footprint`; no file exists for either.

```bash
python scripts/import_part.py \
  --symbol ../Boards/X19-Electrical-New-Member-Board/libs/temporary_New_member_lib.kicad_sym \
  --category Power --mpn "AZ1117IH-3.3TRG1"
python scripts/import_part.py \
  --symbol ../Boards/X19-Electrical-New-Member-Board/libs/temporary_New_member_lib.kicad_sym \
  --category Power --mpn "IRS-5_10-Q12P-C"
```

Note: `import_part.py` imports only the first top-level symbol in a file (`symbols[0]` in `main()`). Both Electrical parts live in one file, so split them out first.

Write this throwaway helper to the approved temp directory, not to any repository:

```python
# $TMP/split_symbol.py  -- usage: split_symbol.py SOURCE NAME TARGET
import sys
from pathlib import Path

source, name, target = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
text = source.read_text(encoding="utf-8")
start = text.index('(symbol "%s"' % name) - 2
depth = 0
in_string = False
escaped = False
end = start
for index in range(start, len(text)):
    char = text[index]
    if in_string:
        if escaped:
            escaped = False
        elif char == chr(92):
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
            end = index + 1
            break
target.write_text(
    '(kicad_symbol_lib (version 20211014) (generator "kicad_symbol_editor")\n  '
    + text[start:end].strip() + "\n)\n",
    encoding="utf-8",
)
print("wrote", target)
```

Then split and import:

```bash
python $TMP/split_symbol.py ../Boards/X19-Electrical-New-Member-Board/libs/temporary_New_member_lib.kicad_sym AZ1117IH-3.3TRG1 $TMP/AZ1117IH-3.3TRG1.kicad_sym
python $TMP/split_symbol.py ../Boards/X19-Electrical-New-Member-Board/libs/temporary_New_member_lib.kicad_sym IRS-5_10-Q12P-C $TMP/IRS-5_10-Q12P-C.kicad_sym
python scripts/import_part.py --symbol $TMP/AZ1117IH-3.3TRG1.kicad_sym --category Power
python scripts/import_part.py --symbol $TMP/IRS-5_10-Q12P-C.kicad_sym --category Power
```

Omit `--mpn` for these two. `rename_symbol` only fires when `--mpn` is supplied, so the imported symbol keeps its original name, which is what the `REPOINT_MAP` in Task 6 expects.

- [ ] **Step 5: Verify all four survived a rebuild**

Run:

```bash
python scripts/build_symbol_libs.py
python -c "import re,pathlib; t=pathlib.Path('Symbols/rov_power.kicad_sym').read_text(encoding='utf-8'); print(sorted(re.findall(r'^  \(symbol \"([^\"]+)\"', t, re.M)))"
```

Expected: the four new MPNs present alongside the three existing power symbols.

Run: `python -m unittest discover -s tests -v`
Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git -C KiCad/Libraries add Symbols/parts/power Symbols/rov_power.kicad_sym Footprints/rov_power.pretty
git -C KiCad/Libraries commit -m "feat(power): promote four board-local power parts

TPS561208DDCR, E48SC12030NRFH, AZ1117IH-3.3TRG1, IRS-5_10-Q12P-C.

Source: X19-Float-Board CustomComponents, X19-Power-Slab-Board
DigikeyParts, X19-Electrical-New-Member-Board temporary_New_member_lib.

Check per part: the manufacturer, the datasheet URL host, and the temp
range. AZ1117IH-3.3TRG1 and IRS-5_10-Q12P-C carry no footprint file, so
their Footprint field is empty by design, not by omission.

E48SC12030NRFH is a different part from PKU5511ESI and is kept separate."
```

---

### Task 4: Promote the connector parts

Same shape as Task 3, category `Connectors`.

**Files:**
- Source: `KiCad/Boards/X19-Float-Board/CustomComponents/USB4220-03-1040-C_REVA.kicad_sym`, `.../footprints.pretty/GCT_USB4220-03-1040-C_REVA.kicad_mod`
- Source: `KiCad/Boards/X19-Float-Board/X19-Float-Board-eagle-import.kicad_sym`
- Source: `KiCad/Boards/X19-Electrical-New-Member-Board/libs/temporary_New_member_lib.kicad_sym`
- Source: `KiCad/Boards/X19-Control-Board/part_files/board_control_manual_lib.kicad_sym`
- Create: `KiCad/Libraries/Symbols/parts/connectors/USB4220-03-1040-C.kicad_sym`, `.../rfm95adafruitmodule.kicad_sym`, `.../ESD2CAN24DBZRQ1.kicad_sym`, `.../UJ20-C-H-G-SMT-1-P16-TR.kicad_sym`, `.../TYPE-C-31-M-12.kicad_sym`
- Create: `KiCad/Libraries/Footprints/rov_connectors.pretty/GCT_USB4220-03-1040-C_REVA.kicad_mod`, `.../rf_module.kicad_mod`

**Interfaces:**
- Consumes: Task 2's rebuild-safe import.
- Produces: `rov_connectors:USB4220-03-1040-C_REVA`, `rov_connectors:UJ20-C-H-G-SMT-1-P16-TR`, `rov_connectors:ESD2CAN24DBZRQ1`, `rov_connectors:TYPE-C-31-M-12`, and `rov_connectors:rfm95adafruitmodule`. Tasks 7, 8, 9 depend on these.

- [ ] **Step 1: Rename the `rf module` footprint before importing**

`X19-Float-Board/CustomComponents/footprints.pretty/rf module.kicad_mod` has a space in its name, which is a persistent quoting hazard. Copy it to the approved temp directory under a safe name first, then import from there. Do not rename the file in the board repo; Task 11 deletes it.

```powershell
$TMP = "C:\Users\Aman Katyal\AppData\Local\Temp\opencode\rov-promote"
New-Item -ItemType Directory -Force -Path $TMP | Out-Null
Copy-Item "..\Boards\X19-Float-Board\CustomComponents\footprints.pretty\rf module.kicad_mod" "$TMP\rf_module.kicad_mod"
```

- [ ] **Step 2: Import the USB-C receptacle with its footprint**

The GCT manufacturer part number is `USB4220-03-1040-C`. The `REVA` in the board's symbol name is a mechanical revision of the board, not part of the orderable MPN, so the part is imported under its true MPN and the symbol is renamed. Task 6's `REPOINT_MAP` points the old name at the new one.

```bash
python scripts/import_part.py \
  --symbol ../Boards/X19-Float-Board/CustomComponents/USB4220-03-1040-C_REVA.kicad_sym \
  --footprint ../Boards/X19-Float-Board/CustomComponents/footprints.pretty/GCT_USB4220-03-1040-C_REVA.kicad_mod \
  --category Connectors --mpn "USB4220-03-1040-C" --mfr "GCT" \
  --datasheet "https://www.tme.eu/Document/98c00771187b331cf9d9de30723f32c6/USB4220+-+Product+Specification.pdf" \
  --temp "-25°C to 85°C"
```

Confirm the symbol landed as `USB4220-03-1040-C`, not as `USB4220-03-1040-C_REVA`:

```bash
python -c "import re,pathlib; print(re.findall(r'^[ ]*[(]symbol .([^\"]+).', pathlib.Path('Symbols/rov_connectors.kicad_sym').read_text(encoding='utf-8'), re.M))"
```

- [ ] **Step 3: Import `rfm95adafruitmodule` without renaming it**

This symbol is a module, not a purchasable MPN, so it has no manufacturer part number. Omitting `--mpn` stops `rename_symbol` from replacing its name with a guess.

```bash
python $TMP/split_symbol.py ../Boards/X19-Float-Board/X19-Float-Board-eagle-import.kicad_sym rfm95adafruitmodule $TMP/rfm95adafruitmodule.kicad_sym
python scripts/import_part.py \
  --symbol $TMP/rfm95adafruitmodule.kicad_sym \
  --footprint $TMP/rf_module.kicad_mod \
  --category Connectors --mfr "Adafruit" \
  --datasheet "https://www.adafruit.com/product/3072" --temp "-20°C to 70°C"
```

The Eagle file holds 60 symbols and `main()` imports only the first, so split `rfm95adafruitmodule` out with the `$TMP/split_symbol.py` helper written in Task 3. The `-20°C to 70°C` figure is the RFM9x module's operational range from Table 3 of the RFM95/96/97/98 datasheet. The MPN field will be empty for this part: there is no manufacturer part number for an Adafruit breakout. Record that in the commit message rather than inventing a value.

- [ ] **Step 4: Import `ESD2CAN24DBZRQ1`**

```bash
python $TMP/split_symbol.py ../Boards/X19-Control-Board/part_files/board_control_manual_lib.kicad_sym ESD2CAN24DBZRQ1 $TMP/ESD2CAN24DBZRQ1.kicad_sym
python scripts/import_part.py \
  --symbol $TMP/ESD2CAN24DBZRQ1.kicad_sym \
  --category Connectors --mpn "ESD2CAN24DBZRQ1" --mfr "Texas Instruments" \
  --datasheet "https://www.ti.com/lit/ds/symlink/esd2can24-q1.pdf" --temp "-55°C to 150°C"
```

`board_control_manual_lib.kicad_sym` holds eight symbols, so split this one out first. No footprint file exists for `SOT-23_DBZ_TEX`, so the Footprint field stays empty.

- [ ] **Step 5: Import `UJ20-C-H-G-SMT-1-P16-TR` and `TYPE-C-31-M-12`**

Both come from `temporary_New_member_lib.kicad_sym` and already carry the full contract set, so no metadata is typed by hand. Split each out and import with only `--category Connectors`:

```bash
python $TMP/split_symbol.py ../Boards/X19-Electrical-New-Member-Board/libs/temporary_New_member_lib.kicad_sym UJ20-C-H-G-SMT-1-P16-TR $TMP/UJ20.kicad_sym
python $TMP/split_symbol.py ../Boards/X19-Electrical-New-Member-Board/libs/temporary_New_member_lib.kicad_sym TYPE-C-31-M-12 $TMP/TYPE-C-31-M-12.kicad_sym
python scripts/import_part.py --symbol $TMP/UJ20.kicad_sym --category Connectors
python scripts/import_part.py --symbol $TMP/TYPE-C-31-M-12.kicad_sym --category Connectors
```

- [ ] **Step 6: Verify and commit**

Run `python scripts/build_symbol_libs.py`, confirm the five names are in `Symbols/rov_connectors.kicad_sym`, then run `python -m unittest discover -s tests -v` expecting `OK`.

```bash
git -C KiCad/Libraries add Symbols/parts/connectors Symbols/rov_connectors.kicad_sym Footprints/rov_connectors.pretty
git -C KiCad/Libraries commit -m "feat(connectors): promote five board-local connector parts

USB4220-03-1040-C_REVA, rfm95adafruitmodule, ESD2CAN24DBZRQ1,
UJ20-C-H-G-SMT-1-P16-TR, TYPE-C-31-M-12.

rfm95adafruitmodule is an Adafruit module with no manufacturer part
number, so its MPN is intentionally empty. Its footprint is imported as
rf_module.kicad_mod, renamed from 'rf module.kicad_mod' because a space
in a footprint name is a quoting hazard in library tables and scripts.

The generic Eagle content (GND, passives, headers, fiducials, the A4
frame) is not promoted; boards use KiCad's bundled power and Device
libraries for those."
```

---

### Task 5: Promote the logic and sensor parts

**Files:**
- Source: `KiCad/Boards/X19-Control-Board/part_files/board_control_manual_lib.kicad_sym`
- Source: `KiCad/Boards/X19-Float-Board/CustomComponents/INA228_pwr_monitor.kicad_sym`
- Source: `KiCad/Boards/X19-Float-Board/CustomComponents/old_STM32G431RBT6.kicad_sym`
- Create: `KiCad/Libraries/Symbols/parts/logic/TCAN1044VDRQ1.kicad_sym`, `.../logic/STM32G431RBT6.kicad_sym`
- Create: `KiCad/Libraries/Symbols/parts/sensors/INA228AIDGSR.kicad_sym`
- Create: `KiCad/Libraries/Footprints/rov_logic.pretty/D0008A-IPC_A.kicad_mod`

**Interfaces:**
- Consumes: Task 2's rebuild-safe import.
- Produces: `rov_logic:TCAN1044VDRQ1`, `rov_logic:STM32G431RBT6`, `rov_sensors:INA228AIDGSR`. Tasks 7 and 10 depend on these.

- [ ] **Step 1: Import `TCAN1044VDRQ1` with its footprint**

```bash
python $TMP/split_symbol.py ../Boards/X19-Control-Board/part_files/board_control_manual_lib.kicad_sym TCAN1044VDRQ1 $TMP/TCAN1044VDRQ1.kicad_sym
python scripts/import_part.py \
  --symbol $TMP/TCAN1044VDRQ1.kicad_sym \
  --footprint "../Boards/X19-Power-Slab-Board/DigikeyParts/TCAN1044/footprints.pretty/D0008A-IPC_A.kicad_mod" \
  --category Logic --mpn "TCAN1044VDRQ1" --mfr "Texas Instruments" \
  --datasheet "https://www.ti.com/lit/ds/symlink/tcan1044v.pdf" --temp "-40°C to 125°C"
```

`TCAN1044VDRQ1` is TI's `TCAN1044V-Q1`, the variant with a VIO pin. The library already holds `TCAN1044AVDRQ1`, which is `TCAN1044AV-Q1`, a different part with a different I/O voltage range. They are not interchangeable. Import this one separately, do not merge them, and do not repoint any reference from one to the other.

- [ ] **Step 2: Import `STM32G431RBT6`**

```bash
python scripts/import_part.py \
  --symbol ../Boards/X19-Float-Board/CustomComponents/old_STM32G431RBT6.kicad_sym \
  --category Logic --mpn "STM32G431RBT6" --mfr "STMicroelectronics" \
  --datasheet "https://www.st.com/resource/en/datasheet/stm32g431rb.pdf" --temp "-40°C to 85°C"
```

The `-40°C to 85°C` range is the grade ST specifies for STM32G431RBT6; the 125°C figure found on some distributor pages applies to other STM32G4 variants, not this one. This symbol has zero references in any schematic. It is imported because the source file exists, not because a board needs it. Record that in the commit message.

- [ ] **Step 3: Import `INA228AIDGSR`**

```bash
python scripts/import_part.py \
  --symbol ../Boards/X19-Float-Board/CustomComponents/INA228_pwr_monitor.kicad_sym \
  --category Sensors --mpn "INA228AIDGSR" --mfr "Texas Instruments" \
  --datasheet "https://www.ti.com/lit/ds/symlink/ina228.pdf" --temp "-40°C to 125°C"
```

Also zero references. Import for the same reason as Step 2.

- [ ] **Step 4: Verify the whole library and commit**

Run `python scripts/build_symbol_libs.py`, then confirm the library now holds 20 + 12 = 32 top-level symbols:

```bash
python -c "import re,pathlib; print(sum(len(re.findall(r'^  \(symbol \"', p.read_text(encoding='utf-8'), re.M)) for p in pathlib.Path('Symbols').glob('rov_*.kicad_sym')))"
```

Expected: `32`. Run `python -m unittest discover -s tests -v`, expecting `OK`.

```bash
git -C KiCad/Libraries add Symbols/parts Symbols/rov_logic.kicad_sym Symbols/rov_sensors.kicad_sym Footprints/rov_logic.pretty
git -C KiCad/Libraries commit -m "feat(logic,sensors): promote three board-local parts

TCAN1044VDRQ1, STM32G431RBT6, INA228AIDGSR.

TCAN1044VDRQ1 is a different MPN from the TCAN1044AVDRQ1 already in the
library and is kept as its own part. Choosing between the two is a
hardware decision, so no reference is moved from one to the other.

STM32G431RBT6 and INA228AIDGSR have no schematic references anywhere.
They are imported because the source files exist in the Float board, so
the parts stop living in a board-local scratch library. Their presence
in the shared library is not a claim that either board uses them."
```

---

### Task 6: Build the reference consolidation transform

**Files:**
- Create: `KiCad/DevOps/scripts/consolidate_symbol_refs.py`
- Create: `KiCad/DevOps/tests/test_consolidate_symbol_refs.py`

**Interfaces:**
- Consumes: nothing from earlier tasks, but the mapping table must only name parts that Tasks 3 to 5 actually promoted.
- Produces: `repoint_schematic(text: str, library_dir: Path) -> Result`, the `Result` fields `text`, `changed`, `unmapped`, `value_mismatches`, and the CLI `python scripts/consolidate_symbol_refs.py --board <board-dir> --library <library-dir> [--apply]`. Tasks 7 to 10 call that CLI, and every task re-runs it without `--apply` to confirm the change is idempotent.

- [ ] **Step 1: Write the failing tests**

Create `KiCad/DevOps/tests/test_consolidate_symbol_refs.py`:

```python
import os
import sys
import tempfile
import unittest
from pathlib import Path

DEVOPS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DEVOPS_DIR / "scripts"))

import consolidate_symbol_refs as csr


SCHEMATIC = '''(kicad_symbol_lib (version 20211014) (generator eeschema)

  (lib_symbols
    (symbol "board_control_manual_lib:INA237AIDGSR" (in_bom yes) (on_board yes)
      (property "Value" "INA237AIDGSR" (id 0) (at 0 0 0))
    )
    (symbol "INA237AIDGSR_0_1"
      (rectangle (start -1 1) (end 1 -1))
    )
  )

  (symbol (lib_id "board_control_manual_lib:INA237AIDGSR") (at 10 10 0)
    (property "Reference" "U1" (at 10 10 0))
    (property "Footprint" "VSSOP_IDGSR_TEX" (at 10 10 0) (hide yes))
  )
)
'''


class TestSexprHelpers(unittest.TestCase):
    def test_find_block_end_handles_nested_parens(self):
        text = '(a (b (c 1)) (d 2))'
        self.assertEqual(csr.find_block_end(text, 0), len(text))

    def test_find_block_end_ignores_parens_inside_strings(self):
        text = '(a (property "has (paren" 1))'
        self.assertEqual(csr.find_block_end(text, 0), len(text))

    def test_find_block_end_handles_escaped_quote(self):
        text = '(a (property "say \\"hi\\"" 1) (b 2))'
        self.assertEqual(csr.find_block_end(text, 0), len(text))

    def test_find_block_end_raises_on_unbalanced(self):
        with self.assertRaises(ValueError):
            csr.find_block_end("(a (b", 0)


class TestRepoint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lib = Path(self.tmp.name)
        syms = self.lib / "Symbols"
        syms.mkdir()
        (syms / "rov_sensors.kicad_sym").write_text(
            '(kicad_symbol_lib (version 20211014) (generator "kicad_symbol_editor")\n'
            '  (symbol "INA237AIDGSR" (in_bom yes) (on_board yes)\n'
            '    (property "MPN" "INA237AIDGSR" (id 0) (at 0 0 0))\n'
            '    (property "Footprint" "rov_sensors:VSSOP_IDGSR_TEX" (id 1) (at 0 0 0))\n'
            '  )\n'
            ')\n',
            encoding="utf-8",
        )

    def test_rewrites_lib_id_and_cached_key(self):
        result = csr.repoint_schematic(SCHEMATIC, self.lib)
        self.assertIn('(lib_id "rov_sensors:INA237AIDGSR")', result.text)
        self.assertIn('(symbol "rov_sensors:INA237AIDGSR"', result.text)
        self.assertNotIn("board_control_manual_lib:INA237AIDGSR", result.text)

    def test_leaves_derived_subsymbols_alone(self):
        result = csr.repoint_schematic(SCHEMATIC, self.lib)
        self.assertIn('(symbol "INA237AIDGSR_0_1"', result.text)

    def test_is_idempotent(self):
        once = csr.repoint_schematic(SCHEMATIC, self.lib)
        twice = csr.repoint_schematic(once.text, self.lib)
        self.assertEqual(once.text, twice.text)
        self.assertEqual(twice.changed, [])

    def test_reports_replaced_bodies_for_review(self):
        result = csr.repoint_schematic(SCHEMATIC, self.lib)
        self.assertIn("board_control_manual_lib:INA237AIDGSR -> rov_sensors:INA237AIDGSR",
                      result.changed)

    def test_normalizes_footprint_when_library_provides_one(self):
        result = csr.repoint_schematic(SCHEMATIC, self.lib)
        self.assertIn('(property "Footprint" "rov_sensors:VSSOP_IDGSR_TEX"', result.text)

    def test_unmapped_reference_is_reported_not_guessed(self):
        bad = SCHEMATIC.replace(
            '(lib_id "board_control_manual_lib:INA237AIDGSR")',
            '(lib_id "leak_probe_connector:BM02B-GHS-TBT")',
        )
        result = csr.repoint_schematic(bad, self.lib)
        self.assertIn("leak_probe_connector:BM02B-GHS-TBT", result.unmapped)
        self.assertIn("leak_probe_connector:BM02B-GHS-TBT", result.text)

    def test_value_mismatch_is_reported_and_skipped(self):
        mism = SCHEMATIC.replace('(property "Value" "INA237AIDGSR" (id 0) (at 0 0 0))\n    )',
                                 '(property "Value" "SOMETHINGELSE" (id 0) (at 0 0 0))\n    )')
        result = csr.repoint_schematic(mism, self.lib)
        self.assertTrue(any("SOMETHINGELSE" in note for note in result.value_mismatches),
                        f"mismatch not reported: {result.value_mismatches}")
        self.assertIn("board_control_manual_lib:INA237AIDGSR", result.text)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m unittest tests.test_consolidate_symbol_refs -v`
Expected: import error, `No module named 'consolidate_symbol_refs'`.

- [ ] **Step 3: Write the S-expression helpers**

Create `KiCad/DevOps/scripts/consolidate_symbol_refs.py` with this header and the two helpers:

```python
#!/usr/bin/env python3
"""Repoint dangling KiCad symbol references onto the shared rov_* libraries.

A board schematic stores each symbol twice: once as a `lib_id` reference on
the instance, and once as a full cached copy under `lib_symbols`. Rewriting
only the `lib_id` leaves the cached body describing the old library, so this
tool replaces both and reports every substitution for review.

The mapping is explicit. A reference with no entry is reported as unmapped
and left exactly as it is, because guessing a destination can silently
substitute one part for another.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# (old nickname, old symbol name) -> (shared nickname, shared symbol name)
REPOINT_MAP = {
    # already in the shared library, only the nickname is wrong
    ("board_control_manual_lib", "BMI270"): ("rov_sensors", "BMI270"),
    ("board_control_manual_lib", "ECS-400-18-33-JGN-TR"): ("rov_passives", "ECS-400-18-33-JGN-TR"),
    ("board_control_manual_lib", "INA237AIDGSR"): ("rov_sensors", "INA237AIDGSR"),
    ("board_control_manual_lib", "MIC5504-3.3YM5-TR"): ("rov_power", "MIC5504-3.3YM5-TR"),
    ("board_control_manual_lib", "STM32C542CCT6"): ("rov_logic", "STM32C542CCT6"),
    ("INA237AIDGSR", "INA237AIDGSR"): ("rov_sensors", "INA237AIDGSR"),
    ("MCP2518FDT_E_QBB", "MCP2518FDT-E_QBB"): ("rov_logic", "MCP2518FDT-E_QBB"),
    ("STM32C542CCT6", "STM32C542CCT6"): ("rov_logic", "STM32C542CCT6"),
    ("tmpsensor", "TMP1075NDRLR"): ("rov_sensors", "TMP1075NDRLR"),
    # promoted by tasks 3 to 5
    ("AZ1117IH_3_3TRG1", "AZ1117IH-3.3TRG1"): ("rov_power", "AZ1117IH-3.3TRG1"),
    ("board_control_manual_lib", "ESD2CAN24DBZRQ1"): ("rov_connectors", "ESD2CAN24DBZRQ1"),
    ("board_control_manual_lib", "TCAN1044VDRQ1"): ("rov_logic", "TCAN1044VDRQ1"),
    ("TCAN1044VDRQ1", "TCAN1044VDRQ1"): ("rov_logic", "TCAN1044VDRQ1"),
    ("temporary_New_member_lib", "UJ20-C-H-G-SMT-1-P16-TR"): ("rov_connectors", "UJ20-C-H-G-SMT-1-P16-TR"),
    ("tpsbuckconv561208", "TPS561208DDCR"): ("rov_power", "TPS561208DDCR"),
    ("usbc", "USB4220-03-1040-C_REVA"): ("rov_connectors", "USB4220-03-1040-C"),
}

# Referenced by a board schematic, but no symbol file exists anywhere in the
# workspace. Left untouched on purpose and reported every run.
KNOWN_UNMAPPABLE = {
    ("2024-10-08_01-41-07", "BM04B-GHS-TBT"),
    ("ADT7410TRZ-REEL7", "ADT7410TRZ-REEL7"),
    ("ECS-120-18-33-JGN-TR", "ECS-120-18-33-JGN-TR"),
    ("INA260AIPW", "INA260AIPW"),
    ("leak_probe_connector", "BM02B-GHS-TBT"),
}


def find_block_end(text: str, start: int) -> int:
    """Return the index just past the s-expression opening at `start`.

    Parentheses inside quoted strings are ignored, and a backslash escapes
    the next character, because KiCad property values contain both.
    """
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
                return index + 1
    raise ValueError(f"unbalanced s-expression starting at offset {start}")


def extract_top_symbols(text: str) -> list[tuple[str, str, int, int]]:
    """Return (name, body, start, end) for every top-level symbol in a library."""
    found = []
    for match in re.finditer(r'^  \(symbol "([^"]+)"', text, re.M):
        start = match.start() + 2
        end = find_block_end(text, start)
        found.append((match.group(1), text[start:end], start, end))
    return found
```

- [ ] **Step 4: Write the library loader**

Append to the same file:

```python
def load_shared_symbols(library_dir: Path) -> dict[str, str]:
    """Map 'nickname:symbol' to its body from the shared category libraries."""
    shared: dict[str, str] = {}
    symbols_dir = Path(library_dir) / "Symbols"
    for lib in sorted(symbols_dir.glob("rov_*.kicad_sym")):
        nickname = lib.stem
        for name, body, _start, _end in extract_top_symbols(lib.read_text(encoding="utf-8")):
            shared[f"{nickname}:{name}"] = body
    return shared
```

- [ ] **Step 5: Write the repoint result and the main transform**

Append to the same file:

```python
class Result:
    def __init__(self, text: str, changed: list[str], unmapped: list[str],
                 value_mismatches: list[str]) -> None:
        self.text = text
        self.changed = changed
        self.unmapped = unmapped
        self.value_mismatches = value_mismatches


def repoint_schematic(text: str, library_dir: Path) -> Result:
    shared = load_shared_symbols(library_dir)
    changed: list[str] = []
    unmapped: list[str] = []
    value_mismatches: list[str] = []

    # Skip any instance whose Value names a different part than its lib_id.
    skipped: set[str] = set()
    for match in re.finditer(r'\(lib_id "([^":]+):([^"]+)"', text):
        key = (match.group(1), match.group(2))
        if key not in REPOINT_MAP:
            continue
        window = text[match.start():match.start() + 4000]
        value_match = re.search(r'\(property "Value" "([^"]*)"', window)
        if value_match and value_match.group(1) and value_match.group(1) != key[1]:
            value_mismatches.append(
                f"{key[0]}:{key[1]} has Value {value_match.group(1)}; "
                "skipped so a human decides which part this is"
            )
            skipped.add(f"{key[0]}:{key[1]}")

    def rewrite_ids(chunk: str) -> str:
        def sub(match: re.Match[str]) -> str:
            nick, name = match.group(1), match.group(2)
            if (nick, name) in skipped:
                return match.group(0)
            if (nick, name) not in REPOINT_MAP:
                if (nick, name) in KNOWN_UNMAPPABLE:
                    if f"{nick}:{name}" not in unmapped:
                        unmapped.append(f"{nick}:{name}")
                return match.group(0)
            new_nick, new_name = REPOINT_MAP[(nick, name)]
            if f"{nick}:{name}" not in unmapped:
                unmapped.pop(f"{nick}:{name}", None)
            return f'(lib_id "{new_nick}:{new_name}")'
        return re.sub(r'\(lib_id "([^":]+):([^"]+)"', sub, chunk)

    out = rewrite_ids(text)
    if skipped:
        pass  # the skipped references keep their original text by construction

    # Replace cached bodies, longest match first so nested symbols are safe.
    for (old_nick, old_name), (new_nick, new_name) in REPOINT_MAP.items():
        if f"{old_nick}:{old_name}" in skipped:
            continue
        key = f"{old_nick}:{old_name}"
        replacement = shared.get(f"{new_nick}:{new_name}")
        if replacement is None:
            value_mismatches.append(
                f"{key} maps to {new_nick}:{new_name}, which the library does not define; skipped"
            )
            skipped.add(key)
            continue
        pattern = re.compile(r'^  \(symbol "' + re.escape(key) + r'"', re.M)
        while True:
            match = pattern.search(out)
            if match is None:
                break
            start = match.start() + 2
            end = find_block_end(out, start)
            out = out[:start] + replacement + out[end:]
            note = f"{key} -> {new_nick}:{new_name}"
            if note not in changed:
                changed.append(note)

    # Normalize footprints to the shared library's reference.
    for key, (new_nick, new_name) in REPOINT_MAP.items():
        if key in skipped:
            continue
        body = shared.get(f"{new_nick}:{new_name}")
        if body is None:
            continue
        footprint = re.search(r'\(property "Footprint" "([^"]*)"', body)
        if not footprint or not footprint.group(1).startswith("rov_"):
            continue
        target = footprint.group(1)
        out = re.sub(
            r'\(property "Footprint" "(?!' + re.escape(target) + r')[^"]*"',
            f'(property "Footprint" "{target}"',
            out,
        )

    return Result(out, changed, unmapped, value_mismatches)
```

- [ ] **Step 6: Add the CLI**

Append to the same file:

```python
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", required=True, help="board repository root")
    parser.add_argument("--library", required=True, help="path to the shared library checkout")
    parser.add_argument("--apply", action="store_true",
                        help="write changes; without this flag only report")
    args = parser.parse_args()

    board = Path(args.board)
    library = Path(args.library)
    total_changed: list[str] = []
    total_unmapped: list[str] = []
    total_mismatch: list[str] = []

    for sch in sorted(board.rglob("*.kicad_sch")):
        original = sch.read_text(encoding="utf-8")
        if not any(f'"{nick}:{name}"' in original for (nick, name) in REPOINT_MAP):
            continue
        result = repoint_schematic(original, library)
        for note in result.changed:
            total_changed.append(f"{sch.name}: {note}")
        for note in result.unmapped:
            total_unmapped.append(f"{sch.name}: {note}")
        for note in result.value_mismatches:
            total_mismatch.append(f"{sch.name}: {note}")
        if args.apply and result.text != original:
            sch.write_text(result.text, encoding="utf-8", newline="")

    print(f"[INFO] references repointed: {len(total_changed)}")
    for note in total_changed:
        print(f"  [REPOINT] {note}")
    print(f"[INFO] references needing a human decision: {len(total_unmapped)}")
    for note in sorted(set(total_unmapped)):
        print(f"  [UNMAPPED] {note}")
    print(f"[INFO] value mismatches skipped: {len(total_mismatch)}")
    for note in sorted(set(total_mismatch)):
        print(f"  [MISMATCH] {note}")
    if args.apply:
        print("[OK] changes written")
    else:
        print("[INFO] dry run, pass --apply to write")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m unittest tests.test_consolidate_symbol_refs -v`
Expected: all tests PASS.

- [ ] **Step 8: Add the preservation tests the review focus calls for**

Add these three tests to the same file, because each pins a failure mode named in the plan's Review Focus:

```python
    def test_crlf_schematic_keeps_its_line_endings(self):
        self.setUp()
        crlf = SCHEMATIC.replace("\n", "\r\n")
        result = csr.repoint_schematic(crlf, self.lib)
        self.assertNotIn("\r\n", result.text.replace("\r\n", ""),
                         "line endings were mixed rather than preserved")
        self.assertIn("\r\n", result.text)

    def test_old_cache_key_does_not_survive(self):
        self.setUp()
        result = csr.repoint_schematic(SCHEMATIC, self.lib)
        self.assertNotIn('(symbol "board_control_manual_lib:INA237AIDGSR"', result.text)

    def test_missing_shared_symbol_is_reported_not_guessed(self):
        self.setUp()
        original = csr.REPOINT_MAP[("board_control_manual_lib", "INA237AIDGSR")]
        csr.REPOINT_MAP[("board_control_manual_lib", "INA237AIDGSR")] = ("rov_sensors", "NOPE")
        self.addCleanup(
            csr.REPOINT_MAP.__setitem__,
            ("board_control_manual_lib", "INA237AIDGSR"),
            original,
        )
        result = csr.repoint_schematic(SCHEMATIC, self.lib)
        self.assertTrue(any("NOPE" in note for note in result.value_mismatches),
                        f"missing shared symbol not reported: {result.value_mismatches}")
        self.assertIn("board_control_manual_lib:INA237AIDGSR", result.text)
```

- [ ] **Step 9: Run the full DevOps suite**

Run: `python -m unittest discover -s tests -v`
Expected: `OK`.

- [ ] **Step 10: Commit**

```bash
git -C KiCad/DevOps add scripts/consolidate_symbol_refs.py tests/test_consolidate_symbol_refs.py
git -C KiCad/DevOps commit -m "feat(boards): add the shared-library reference consolidation tool

A schematic stores each symbol twice, as a lib_id on the instance and as
a full cached copy under lib_symbols. Rewriting only the lib_id leaves
the cached body describing the old library, which then fails library
validation for the contract fields it lacks. This replaces both and
reports every substitution.

The mapping is explicit rather than inferred. A reference with no entry
is reported and left untouched, because inferring a destination can
silently substitute one part for another. Instances whose Value property
names a different part than their lib_id are reported and skipped for a
human decision; three on the Pi Shield behave this way.

The tool is self-contained so its tests need no library checkout."
```

---

### Task 7: Consolidate X19-Control-Board

**Files:**
- Modify: `KiCad/Boards/X19-Control-Board/*.kicad_sch` (7 dangling references)
- Modify: `KiCad/Boards/X19-Control-Board/sym-lib-table` (remove the `New_Library` entry, which points at a file that does not exist)

**Interfaces:**
- Consumes: `consolidate_symbol_refs.py` from Task 6; `rov_sensors:BMI270`, `rov_passives:ECS-400-18-33-JGN-TR`, `rov_sensors:INA237AIDGSR`, `rov_power:MIC5504-3.3YM5-TR`, `rov_logic:STM32C542CCT6` (all pre-existing), `rov_connectors:ESD2CAN24DBZRQ1`, `rov_logic:TCAN1044VDRQ1` (Task 5).
- Produces: a board whose only unresolved references are the five known-unmappable parts.

- [ ] **Step 1: Dry run and read the report**

Run from `KiCad/DevOps`:

```bash
python scripts/consolidate_symbol_refs.py \
  --board ../Boards/X19-Control-Board --library ../Libraries
```

Expected: 5 distinct repoints listed, one for each of `board_control_manual_lib`'s symbols. Confirm no `[UNMAPPED]` or `[MISMATCH]` line for this board.

- [ ] **Step 2: Confirm the worktree is clean and the branch is current**

```bash
git -C ../Boards/X19-Control-Board status --porcelain
git -C ../Boards/X19-Control-Board fetch origin
git -C ../Boards/X19-Control-Board log --oneline HEAD..origin/master
```

Expected: empty status and empty log. If the board has uncommitted work, stop and report it rather than stashing.

- [ ] **Step 3: Apply**

```bash
python scripts/consolidate_symbol_refs.py \
  --board ../Boards/X19-Control-Board --library ../Libraries --apply
```

- [ ] **Step 4: Remove the dead `New_Library` entry**

In `KiCad/Boards/X19-Control-Board/sym-lib-table`, delete the line registering nickname `New_Library`, which points at `part_files/New_Library.kicad_sym`. That file does not exist; the file on disk is `part_files/board_control_manual_lib.kicad_sym`. No schematic references nickname `New_Library`, so the entry is dead either way.

- [ ] **Step 5: Verify the result**

```bash
python scripts/consolidate_symbol_refs.py \
  --board ../Boards/X19-Control-Board --library ../Libraries
```

Expected: `[INFO] references repointed: 0` on the second run, proving idempotence.

Then confirm no old nickname survives:

```bash
Select-String -Path ../Boards/X19-Control-Board/*.kicad_sch -Pattern 'board_control_manual_lib'
```

Expected: no matches.

- [ ] **Step 6: Run the board's validation and the contract suite**

```bash
python -m unittest discover -s tests -v
```

Expected: `OK`, including `tests.test_board_repositories`.

- [ ] **Step 7: Commit and push**

```bash
git -C ../Boards/X19-Control-Board add -A
git -C ../Boards/X19-Control-Board commit -m "fix(schematics): resolve 10 symbol references onto the shared library

Seven distinct board_control_manual_lib references and their ten
instances now resolve through rov_sensors, rov_passives, rov_power, and
rov_logic instead of a library nickname registered in no sym-lib-table.
The cached symbol bodies are replaced with the shared library's, so the
cached definitions carry the required metadata fields.

Also removes the dead New_Library sym-lib-table entry, which pointed at
part_files/New_Library.kicad_sym, a file that does not exist.

Reviewer: confirm the footprint each instance now names, since the
transform prefers the shared library's footprint over the board-local
one. STM32C542CCT6 instances are normalized to rov_logic:LQFP48-7X7,
including one that had an empty footprint field."
git -C ../Boards/X19-Control-Board push origin master
```

---

### Task 8: Consolidate X19-Electrical-New-Member-Board

**Files:**
- Modify: `KiCad/Boards/X19-Electrical-New-Member-Board/*.kicad_sch` (10 dangling references across 6 distinct pairs)

**Interfaces:**
- Consumes: Task 6's tool; `rov_power:AZ1117IH-3.3TRG1` (Task 3), `rov_connectors:UJ20-C-H-G-SMT-1-P16-TR` (Task 4).
- Produces: a board with three promoted parts resolving, three references still needing a hardware decision.

- [ ] **Step 1: Dry run**

```bash
python scripts/consolidate_symbol_refs.py \
  --board ../Boards/X19-Electrical-New-Member-Board --library ../Libraries
```

Expected: 2 repoints (`AZ1117IH_3_3TRG1:AZ1117IH-3.3TRG1` and `temporary_New_member_lib:UJ20-C-H-G-SMT-1-P16-TR`) and 3 unmapped (`BM04B-GHS-TBT`, `ADT7410TRZ-REEL7`, `INA260AIPW`, plus `BM02B-GHS-TBT`).

- [ ] **Step 2: Apply**

```bash
python scripts/consolidate_symbol_refs.py \
  --board ../Boards/X19-Electrical-New-Member-Board --library ../Libraries --apply
```

- [ ] **Step 3: Verify idempotence and check for value mismatches**

Re-run without `--apply`. Expected: 0 repoints. If any `[MISMATCH]` line appears, stop and report it; this board is the one with `C_INA_Curr` malformed references, so a mismatch here needs human eyes.

- [ ] **Step 4: Commit and push**

```bash
git -C ../Boards/X19-Electrical-New-Member-Board add -A
git -C ../Boards/X19-Electrical-New-Member-Board commit -m "fix(schematics): resolve two symbol references onto the shared library

AZ1117IH-3.3TRG1 and UJ20-C-H-G-SMT-1-P16-TR now resolve through
rov_power and rov_connectors. Both were referenced through nicknames
registered in no sym-lib-table, so they could not resolve for anyone who
cloned this board.

Four references stay unresolved on purpose: BM04B-GHS-TBT,
BM02B-GHS-TBT, ADT7410TRZ-REEL7, and INA260AIPW have no symbol file
anywhere in the workspace. Their symbols need to be authored from
datasheets, which is a hardware task, not a library change."
git -C ../Boards/X19-Electrical-New-Member-Board push origin master
```

---

### Task 9: Consolidate X19-Float-Board

**Files:**
- Modify: `KiCad/Boards/X19-Float-Board/*.kicad_sch` (4 dangling references across 3 distinct pairs)
- Modify: `KiCad/Boards/X19-Float-Board/sym-lib-table` (remove the absolute macOS path entry)

**Interfaces:**
- Consumes: Task 6's tool; `rov_power:TPS561208DDCR` (Task 3), `rov_connectors:USB4220-03-1040-C_REVA` (Task 4), `rov_sensors:TMP1075NDRLR` (pre-existing).
- Produces: a board with no absolute paths in its library table.

- [ ] **Step 1: Dry run**

```bash
python scripts/consolidate_symbol_refs.py \
  --board ../Boards/X19-Float-Board --library ../Libraries
```

Expected: 3 repoints: `tpsbuckconv561208:TPS561208DDCR`, `usbc:USB4220-03-1040-C_REVA`, `tmpsensor:TMP1075NDRLR`.

- [ ] **Step 2: Remove the absolute macOS path**

In `KiCad/Boards/X19-Float-Board/sym-lib-table`, delete the entry for nickname `Stm32`, which points at `/Users/mattmswang/Downloads/ul_STM32G431RBT6/KiCADv6/2026-08-12_16-17-23.kicad_sym`. It is already marked `(disabled)` and `(hidden)`, and it references a machine that does not exist for any other member. Also delete the `ROV_CONN` entry, which duplicates the `rov_connectors` registration by pointing at the same shared file under a second nickname.

- [ ] **Step 3: Apply and verify**

```bash
python scripts/consolidate_symbol_refs.py \
  --board ../Boards/X19-Float-Board --library ../Libraries --apply
```

Re-run without `--apply`. Expected: 0 repoints.

```bash
Select-String -Path ../Boards/X19-Float-Board/sym-lib-table -Pattern '/Users/|ROV_CONN'
```

Expected: no matches.

- [ ] **Step 4: Commit and push**

```bash
git -C ../Boards/X19-Float-Board add -A
git -C ../Boards/X19-Float-Board commit -m "fix(schematics): resolve three symbol references and drop an absolute path

TPS561208DDCR, USB4220-03-1040-C_REVA, and TMP1075NDRLR now resolve
through rov_power, rov_connectors, and rov_sensors rather than nicknames
registered in no sym-lib-table.

Removes the Stm32 entry, which pointed at /Users/mattmswang/Downloads
on one teammate's laptop, and the ROV_CONN entry, which registered the
shared rov_connectors library under a second nickname."
git -C ../Boards/X19-Float-Board push origin master
```

---

### Task 10: Consolidate X19-Pi-Shield-Board

This board has the most delicate cases in the plan. Two of its references have a `Value` that names a different part than the `lib_id`, and the transform skips both by design.

**Files:**
- Modify: `KiCad/Boards/X19-Pi-Shield-Board/*.kicad_sch` (8 dangling references across 6 distinct pairs)

**Interfaces:**
- Consumes: Task 6's tool; `rov_logic:MCP2518FDT-E_QBB`, `rov_logic:STM32C542CCT6`, `rov_sensors:INA237AIDGSR` (pre-existing), `rov_connectors:ESD2CAN24DBZRQ1`, `rov_logic:TCAN1044VDRQ1` (Task 5).
- Produces: a board with 4 repointed references and 2 reported for a human decision.

- [ ] **Step 1: Dry run and read the mismatch report**

```bash
python scripts/consolidate_symbol_refs.py \
  --board ../Boards/X19-Pi-Shield-Board --library ../Libraries
```

Expected: 4 repoints, and two `[MISMATCH]` lines:

- `ECS-120-18-33-JGN-TR:ECS-120-18-33-JGN-TR` has `Value` `ECS-400-18-33-JGN-TR`
- `MCP2518FDT_E_QBB:MCP2518FDT-E_QBB` has `Value` `MCP2518FDT-E/QBB`

Both are left untouched.

- [ ] **Step 2: Stop and get a hardware answer before applying**

Do not guess. Report both to the user and ask:

- Is the crystal on this board an `ECS-400-18-33-JGN-TR` or an `ECS-120-18-33-JGN-TR`? The shared library holds `ECS-400-18-33-JGN-TR` and no `ECS-120` part exists anywhere.
- Is the `MCP2518FDT-E/QBB` value a typo for `MCP2518FDT-E_QBB`?

Record the answer in the commit message. If the answer is "ECS-400", add the mapping `("ECS-120-18-33-JGN-TR", "ECS-120-18-33-JGN-TR") -> ("rov_passives", "ECS-400-18-33-JGN-TR")` to `REPOINT_MAP` in Task 6's file and re-run this task's dry run. If the answer is "ECS-120", leave it unresolved and add it to the hardware task.

- [ ] **Step 3: Apply and verify**

```bash
python scripts/consolidate_symbol_refs.py \
  --board ../Boards/X19-Pi-Shield-Board --library ../Libraries --apply
```

Re-run without `--apply`. Expected: 0 repoints, with the same mismatch lines still reported.

- [ ] **Step 4: Commit and push**

```bash
git -C ../Boards/X19-Pi-Shield-Board add -A
git -C ../Boards/X19-Pi-Shield-Board commit -m "fix(schematics): resolve symbol references onto the shared library

IN237AIDGSR, STM32C542CCT6, ESD2CAN24DBZRQ1, and TCAN1044VDRQ1 now
resolve through the rov_ libraries. All six distinct references on this
board used nicknames registered in no sym-lib-table, so none of them
resolved for anyone who cloned it.

One reference is left unresolved pending a hardware answer:
ECS-120-18-33-JGN-TR carries the Value ECS-400-18-33-JGN-TR, so the
tool cannot tell which part the board actually carries and did not
touch it. The MCP2518FDT-E/QBB value is a typo for MCP2518FDT-E_QBB and
is left as found."
git -C ../Boards/X19-Pi-Shield-Board push origin master
```

---

### Task 11: Retire the board-local libraries and remove the dead directory

Only after Tasks 7 to 10 have landed, so no board is left pointing at a file that was deleted before it was repointed.

**Files:**
- Delete: `KiCad/Boards/X19-Float-Board/X19-Float-Board-eagle-import.kicad_sym`
- Delete: `KiCad/Boards/X19-Float-Board/CustomComponents/rfimp-eagle-import.kicad_sym`
- Delete: `KiCad/Boards/X19-Float-Board/CustomComponents/USB4220-03-1040-C_REVA.kicad_sym`
- Delete: `KiCad/Boards/X19-Float-Board/CustomComponents/buck_tps561208.kicad_sym`
- Delete: `KiCad/Boards/X19-Float-Board/CustomComponents/INA228_pwr_monitor.kicad_sym`
- Delete: `KiCad/Boards/X19-Float-Board/CustomComponents/old_STM32G431RBT6.kicad_sym`
- Delete: `KiCad/Boards/X19-Float-Board/CustomComponents/tmpsensor.kicad_sym`
- Delete: `KiCad/Boards/X19-Float-Board/CustomComponents/footprints.pretty/`
- Delete: `KiCad/Boards/X19-Control-Board/part_files/`
- Delete: `KiCad/Boards/X19-Electrical-New-Member-Board/libs/temporary_New_member_lib.kicad_sym`
- Modify: `KiCad/Boards/X19-Float-Board/sym-lib-table`, `KiCad/Boards/X19-Pi-Shield-Board/sym-lib-table`
- Delete: `KiCad/Libraries/Footprints/rov_parts.pretty/`

**Interfaces:**
- Consumes: Tasks 7 to 10.
- Produces: no board-local symbol library anywhere; a final audit that reports what remains.

- [ ] **Step 1: Confirm nothing still references the files to be deleted**

```bash
Select-String -Path ../Boards/*/*.kicad_sch -Pattern 'X19-Float-Board-eagle-import|RF_EAGLE|usbc|tpsbuckconv561208|tmpsensor|temporary_New_member_lib|board_control_manual_lib|New_Library'
```

Expected: no matches. Any match means a board rewrite did not land; stop and fix that board first.

- [ ] **Step 2: Remove the now-unused table entries**

In `X19-Float-Board/sym-lib-table`, remove the entries for `X19-Float-Board-eagle-import`, `STM32`, `RF_EAGLE`, and `USB-C`. In `X19-Pi-Shield-Board/sym-lib-table`, check whether any board-local entry is still registered and remove it.

Keep every `rov_*` entry. Keep `STm32` only if `new_stm_2026-09-05_17-40-33.kicad_sym` is still referenced; it holds `STM32C542CCT6`, which now resolves through `rov_logic`, so it is removed too.

- [ ] **Step 3: Delete the files**

```bash
git -C ../Boards/X19-Float-Board rm -q X19-Float-Board-eagle-import.kicad_sym CustomComponents/rfimp-eagle-import.kicad_sym CustomComponents/USB4220-03-1040-C_REVA.kicad_sym CustomComponents/buck_tps561208.kicad_sym CustomComponents/INA228_pwr_monitor.kicad_sym CustomComponents/old_STM32G431RBT6.kicad_sym CustomComponents/tmpsensor.kicad_sym
git -C ../Boards/X19-Float-Board rm -q -r CustomComponents/footprints.pretty
git -C ../Boards/X19-Control-Board rm -q -r part_files
git -C ../Boards/X19-Electrical-New-Member-Board rm -q libs/temporary_New_member_lib.kicad_sym
git -C KiCad/Libraries rm -q -r Footprints/rov_parts.pretty
```

`rov_parts.pretty` is empty and unregistered, so removing it changes no reference.

- [ ] **Step 4: Commit and push, one repository at a time**

```bash
git -C ../Boards/X19-Float-Board commit -m "chore: retire board-local symbol and footprint libraries

Every part in these files now resolves through the shared library, and
both Eagle imports were near-duplicates of each other (60 and 59
symbols). Their generic content is not promoted; boards use KiCad's
bundled power and Device libraries for those. Only rfm95adafruitmodule
was promoted."
git -C ../Boards/X19-Float-Board push origin master
git -C ../Boards/X19-Control-Board commit -m "chore: retire the board-local part_files library

All eight parts resolve through the shared library after the reference
consolidation."
git -C ../Boards/X19-Control-Board push origin master
git -C ../Boards/X19-Electrical-New-Member-Board commit -m "chore: retire the temporary new-member symbol library

Its four parts now resolve through the shared library. Two of the four
are promoted; the other two are still unresolved pending authored
symbols."
git -C ../Boards/X19-Electrical-New-Member-Board push origin master
git -C KiCad/Libraries commit -m "chore: remove the empty rov_parts.pretty directory

It held no footprints and was registered in no library table."
git -C KiCad/Libraries push origin master
```

---

### Task 12: Final audit and report

**Files:**
- No new files. This task produces the report the owner reads.

**Interfaces:**
- Consumes: everything above.
- Produces: the counts the spec requires, stated as measurements rather than assertions.

- [ ] **Step 1: Count remaining unresolved symbol references**

```bash
python -c "
import re,pathlib
known={'2024-10-08_01-41-07','ADT7410TRZ-REEL7','AZ1117IH_3_3TRG1','board_control_manual_lib','ECS-120-18-33-JGN-TR','INA237AIDGSR','INA260AIPW','leak_probe_connector','MCP2518FDT_E_QBB','STM32C542CCT6','TCAN1044VDRQ1','temporary_New_member_lib','tmpsensor','tpsbuckconv561208','usbc'}
std={'Device','power','Connector','Connector_Generic','Connector_Generic_MountingPin','Mechanical','Switch','Diode','Transistor_FET','Power_Management','Power_Protection','Regulator_Linear','Regulator_Switching','Sensor','Oscillator','MCU_ST_STM32F0','Driver_Motor','Jumper','Simulation_SPICE'}
total=0
for f in sorted(pathlib.Path('../Boards').rglob('*.kicad_sch')):
    t=f.read_text(encoding='utf-8',errors='ignore')
    for m in re.finditer(r'\(lib_id \"([^:\"]+):',t):
        n=m.group(1)
        if n in known or n in std: continue
        total+=1; print(f'{f.parent.name}/{f.name}: {n}')
print('TOTAL unresolved custom references:',total)
"
```

Expected: `0`. Any non-zero result names a nickname this work did not cover; report it rather than suppressing it.

- [ ] **Step 2: Count the five known-unmappable references**

```bash
python -c "
import re,pathlib
bad={'2024-10-08_01-41-07','ADT7410TRZ-REEL7','ECS-120-18-33-JGN-TR','INA260AIPW','leak_probe_connector'}
for f in sorted(pathlib.Path('../Boards').rglob('*.kicad_sch')):
    t=f.read_text(encoding='utf-8',errors='ignore')
    for m in re.finditer(r'\(lib_id \"([^:\"]+):([^\"]+)\"',t):
        if m.group(1) in bad: print(f'{f.parent.name}: {m.group(1)}:{m.group(2)}')
"
```

Expected: the 12 references across the four boards, all of which are the five parts with no symbol file. Report this as the hardware task, with the board and count for each part.

- [ ] **Step 3: Run every test suite**

```bash
python -m unittest discover -s tests -v
```
from `KiCad/DevOps`, then from `KiCad/Libraries`. Expected: both `OK`.

- [ ] **Step 4: Confirm the library survived a rebuild and reports its count**

```bash
cd ../Libraries
python scripts/build_symbol_libs.py
git status --porcelain
```

Expected: `git status` shows no modification to any `Symbols/rov_*.kicad_sym`. A modified aggregate after a rebuild means a part exists only in the aggregate, which Task 2 was meant to make impossible; report it as a defect.

- [ ] **Step 5: Confirm every repository is pushed and clean**

```bash
foreach ($d in Get-ChildItem -Directory) { $l = git -C $d.FullName rev-parse HEAD; $r = git -C $d.FullName ls-remote origin refs/heads/master; "$($d.Name) $(if($l.Trim() -eq $r.Split()[0]){'synced'}else{'BEHIND'})" }
```

Expected: `synced` for all eight boards.

- [ ] **Step 6: Report**

Report to the owner, in this order: the audit counts from Steps 1, 2, and 5; the footprint each previously conflicting part now names; the hardware task listing the five parts with no symbol file, their boards, and their reference counts; and the `DigiKey` contract change with the two linter copies that were changed together.
