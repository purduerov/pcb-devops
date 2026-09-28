# ROV Library Part Promotion and Reference Consolidation Design

Date: 2026-09-28
Status: Approved design, not implemented

## Outcome

Make the shared component library the actual single source of truth for every custom
part the team builds with, and eliminate the reference breakage that currently makes
four boards unopenable for anyone who clones them.

Concretely: promote 12 board-local parts (and their footprints) into
`purduerov/purdue-rov-kicad-lib`, repoint 32 dangling symbol references across four
boards onto the shared `rov_*` libraries, retire the board-local libraries, and remove
the vendor-specific `DigiKey` field from the required metadata contract.

This design covers `KiCad/Libraries`, `KiCad/DevOps`, and four board repositories. It
does not change embedded firmware, Surface, or Core.

## Problem statement

### Symbol references do not resolve

Across the eight boards there are 32 occurrences of symbol references whose library
nickname is neither registered in any `sym-lib-table` nor a library bundled with KiCad.
These are 21 distinct `nickname:symbol` pairs.

| Board | Occurrences | Distinct pairs |
| --- | --- | --- |
| `X19-Control-Board` | 10 | 7 |
| `X19-Electrical-New-Member-Board` | 10 | 6 |
| `X19-Pi-Shield-Board` | 8 | 6 |
| `X19-Float-Board` | 4 | 3 |

The 21 pairs, and whether the symbol already exists in the shared library:

| Reference | Resolution target |
| --- | --- |
| `board_control_manual_lib:BMI270` | already shared (`rov_sensors`) |
| `board_control_manual_lib:ECS-400-18-33-JGN-TR` | already shared (`rov_passives`) |
| `board_control_manual_lib:INA237AIDGSR` | already shared (`rov_sensors`) |
| `board_control_manual_lib:MIC5504-3.3YM5-TR` | already shared (`rov_power`) |
| `board_control_manual_lib:STM32C542CCT6` | already shared (`rov_logic`) |
| `INA237AIDGSR:INA237AIDGSR` | already shared (`rov_sensors`) |
| `MCP2518FDT_E_QBB:MCP2518FDT-E_QBB` | already shared (`rov_logic`) |
| `STM32C542CCT6:STM32C542CCT6` | already shared (`rov_logic`) |
| `tmpsensor:TMP1075NDRLR` | already shared (`rov_sensors`) |
| `AZ1117IH_3_3TRG1:AZ1117IH-3.3TRG1` | promote, then `rov_power` |
| `board_control_manual_lib:ESD2CAN24DBZRQ1` | promote, then `rov_connectors` |
| `board_control_manual_lib:TCAN1044VDRQ1` | promote, then `rov_logic` |
| `TCAN1044VDRQ1:TCAN1044VDRQ1` | promote, then `rov_logic` |
| `temporary_New_member_lib:UJ20-C-H-G-SMT-1-P16-TR` | promote, then `rov_connectors` |
| `tpsbuckconv561208:TPS561208DDCR` | promote, then `rov_power` |
| `usbc:USB4220-03-1040-C_REVA` | promote, then `rov_connectors` |
| `2024-10-08_01-41-07:BM04B-GHS-TBT` | no symbol file exists |
| `ADT7410TRZ-REEL7:ADT7410TRZ-REEL7` | no symbol file exists |
| `ECS-120-18-33-JGN-TR:ECS-120-18-33-JGN-TR` | no symbol file exists |
| `INA260AIPW:INA260AIPW` | no symbol file exists |
| `leak_probe_connector:BM02B-GHS-TBT` | no symbol file exists |

Nine pairs need only repointing. Seven need promotion first, because two of them are the
same part reached through two different nicknames (`TCAN1044VDRQ1` alone). Five cannot be
resolved by any tooling and are explicitly out of scope.

### Footprint references do not resolve either

No board registers a single non-`rov_*` footprint library. Nearly every board-local
footprint is therefore unresolvable too, including `DDC0006A_N`, `D0008A-IPC_A`,
`SOT-23_DBZ_TEX`, `VSSOP_IDGSR_TEX`, `SOT5X3-6_DRL_TEX`, `SOT-23-5_MC_MCH`,
`VDFN14_QBB_MCH`, `QFP50P900X900X160-48N`, `SOT223_AZ1117I_6P5X3P5_DIO`, and
`BMI270_BOS`. Only `rov_*` footprints and KiCad's bundled footprints resolve.

### The same part is referenced through up to four different nicknames

`INA237AIDGSR` is reached through `board_control_manual_lib`, its own nickname, and
`rov_sensors`. `STM32C542CCT6` likewise. Org-wide only 6 references actually use a
`rov_*` library, even though every board's `sym-lib-table` registers all six.

### Footprint assignments conflict for the same part

| Part | Conflicting footprints |
| --- | --- |
| `STM32C542CCT6` | *(empty)*, `LQFP48-7X7`, `QFP50P900X900X160-48N`, `rov_logic:LQFP48-7X7` |
| `INA237AIDGSR` | `Package_SO:TSSOP-10_3x3mm_P0.5mm`, `VSSOP_IDGSR_TEX`, `rov_sensors:VSSOP_IDGSR_TEX` |
| `TMP1075NDRLR` | `Package_TO_SOT_SMD:SOT-563`, `SOT5X3-6_DRL_TEX` |
| `MIC5504-3.3YM5-TR` | `Package_TO_SOT_SMD:SOT-23-5`, `SOT-23-5_MC_MCH` |

Most pairs are the same package under a KiCad name and a vendor name, so this is an
opportunity to remove real inconsistency. One `STM32C542CCT6` instance has an empty
footprint field, which is a latent manufacturing defect.

### The contract cannot be satisfied for these parts

`import_part.py` has no network integration. It resolves vendor field aliases already
present in the symbol file, then prompts for anything missing. Of the parts in scope,
four already carry the full contract set, two yield MPN and Manufacturer through the
alias resolver, and three carry nothing usable.

A `DigiKey` part number is an internal vendor SKU and is not derivable from an MPN
without DigiKey's product API, which this project does not use and has no credentials
for. Requiring it would make these parts permanently unimportable.

## Architecture and components

- `KiCad/Libraries/scripts/import_part.py` remains the only import path. Promotion uses
  its existing non-interactive flags; no new import code is introduced.
- `KiCad/Libraries/scripts/kicad_sym_utils.py` remains the parser and the single
  definition of the contract field list.
- `KiCad/Libraries/scripts/rov_bridge.py` remains the only seam to the DevOps CLI.
  Validation after promotion goes through the shared CLI, not a local reimplementation.
- `KiCad/DevOps` holds the board rewrite transform and its tests. The transform is a
  developer tool with tests, not a library behavior, so it belongs with the policy layer
  that already owns board validation.
- Boards keep their `rov_*` `sym-lib-table` and `fp-lib-table` registrations. Only
  board-local entries are removed.

## Contract change

`DigiKey` is removed from `MANDATORY_FIELDS` in `import_part.py` and from the required
set enforced by `linter_validator.py`. The required set becomes:

    MPN, Manufacturer, Datasheet, Temp_Range, Category

Rationale: MPN plus Manufacturer is the identity of a part. A DigiKey part number is one
vendor's SKU among several, and a part is not required to be stocked there.

Existing symbols keep their `DigiKey` values. Nothing is removed from any existing part;
only the requirement to supply the field is dropped. `Datasheet` remains required, which
keeps the one field that lets a member verify the part.

Docs that list the required fields (`CONTRIBUTING.md`, `README.md`) and the tests that
assert the field list are updated in the same change, so the documented contract and the
enforced contract cannot drift.

## Parts promoted

Twelve parts, each with a source file in a board repository. Footprint source is listed
where one exists on disk.

| Part | Category | Source file | Footprint |
| --- | --- | --- | --- |
| `TPS561208DDCR` | power | `X19-Float-Board/CustomComponents/buck_tps561208.kicad_sym` | `DDC0006A_N` |
| `AZ1117IH-3.3TRG1` | power | `X19-Electrical-New-Member-Board/libs/temporary_New_member_lib.kicad_sym` | none on disk |
| `IRS-5_10-Q12P-C` | power | `X19-Electrical-New-Member-Board/libs/temporary_New_member_lib.kicad_sym` | none assigned |
| `E48SC12030NRFH` | power | `X19-Power-Slab-Board/DigikeyParts/E48SC12030NRFH/...` | `E48SC12030NRFH` |
| `USB4220-03-1040-C_REVA` | connectors | `X19-Float-Board/CustomComponents/USB4220-03-1040-C_REVA.kicad_sym` | `GCT_USB4220-03-1040-C_REVA` |
| `UJ20-C-H-G-SMT-1-P16-TR` | connectors | `X19-Electrical-New-Member-Board/libs/temporary_New_member_lib.kicad_sym` | none on disk |
| `ESD2CAN24DBZRQ1` | connectors | `X19-Control-Board/part_files/board_control_manual_lib.kicad_sym` | none on disk |
| `TYPE-C-31-M-12` | connectors | `X19-Electrical-New-Member-Board/libs/temporary_New_member_lib.kicad_sym` | none assigned |
| `TCAN1044VDRQ1` | logic | `X19-Control-Board/part_files/board_control_manual_lib.kicad_sym` | `D0008A-IPC_A` |
| `rfm95adafruitmodule` | connectors | `X19-Float-Board/X19-Float-Board-eagle-import.kicad_sym` | `rf module` |
| `INA228AIDGSR` | sensors | `X19-Float-Board/CustomComponents/INA228_pwr_monitor.kicad_sym` | none assigned |
| `STM32G431RBT6` | logic | `X19-Float-Board/CustomComponents/old_STM32G431RBT6.kicad_sym` | none assigned |

Each promoted symbol carries `MPN`, `Manufacturer`, `Datasheet`, `Temp_Range`, and
`Category`. `Manufacturer`, `MPN`, and `Datasheet` are resolved from manufacturer
sources; `Temp_Range` and `Category` follow the conventions already used by the existing
20 parts.

A part whose footprint file does not exist is imported with an empty `Footprint` and a
`PENDING` note in its description, rather than with a reference that cannot resolve.

One footprint is named `rf module`, which contains a space. It is renamed to
`rf_module.kicad_mod` on promotion, because a space in a footprint identifier is a
persistent source of quoting problems in library tables and scripts. This is a rename of
a file that no longer resolves after cleanup, not a design change.

### Distinct MPNs are never merged

`TCAN1044VDRQ1` (referenced by the boards) and `TCAN1044AVDRQ1` (already shared) are
different manufacturer part numbers, as are `SMCJ58A` and `SMBJ58A-TR`. Merging them
would be an electrical substitution decision. Each is kept as its own part, and each
board keeps whichever MPN it already specifies. Choosing between two MPNs is a hardware
decision for the board owner, not a library decision.

## Board rewrite transform

A script under `KiCad/DevOps/scripts/` performs the rewrite. It is idempotent and
tested. For each dangling `lib_id` in a schematic it makes three coordinated edits:

1. Rewrite the instance's `(lib_id "oldnick:Name"` to `(lib_id "rov_cat:Name"`.
2. Replace the cached `lib_symbols` entry keyed `oldnick:Name` with the shared library's
   definition of that symbol, so the cached body carries the contract properties.
3. Normalize the instance's `Footprint` property to the shared `rov_*` reference when the
   shared library provides one and the current value is either empty or an unresolvable
   board-local reference.

Derived sub-entries such as `Name_0_1` are not renamed. They are referenced from within
their parent definition and carry no nickname.

The transform does not merge or de-duplicate schematic instances, does not change
reference designators, and does not alter net connectivity. Only library binding and
footprint binding change.

### Footprint conflict resolution

Where a part is referenced with conflicting footprints, the shared library's footprint
wins, because it is the one that resolves. The exception is the `STM32C542CCT6` instance
with an empty footprint, which is assigned `rov_logic:LQFP48-7X7` to match the same part's
other references. Every resolution is listed in the commit message so a hardware reviewer
can check the package against the datasheet.

## Cleanup

- Delete retired board-local `*.kicad_sym` and `*.kicad_mod` files and their
  `sym-lib-table` and `fp-lib-table` entries, per board, only after the rewrite lands.
- `X19-Control-Board`: `sym-lib-table` registers nickname `New_Library` pointing at
  `part_files/New_Library.kicad_sym`, which does not exist. The file on disk is
  `board_control_manual_lib.kicad_sym`. This entry is removed as part of the consolidation.
- `X19-Float-Board`: `sym-lib-table` contains an absolute macOS path
  (`/Users/mattmswang/Downloads/...`) for nickname `Stm32`, which is marked disabled and
  hidden but still references a machine that does not exist for any other member. It is
  removed.
- `KiCad/Libraries/Footprints/rov_parts.pretty` is an empty, unregistered directory. It
  is removed.
- The two near-identical Eagle import libraries in `X19-Float-Board`
  (`X19-Float-Board-eagle-import.kicad_sym`, 60 symbols, and
  `CustomComponents/rfimp-eagle-import.kicad_sym`, 59 symbols) are retired. Their generic
  content (`GND`, `3.3V`, `CAP_CERAMIC_*`, `RESISTOR_*`, `HEADER-*`, `FIDUCIAL*`,
  `FRAME_A4_ADAFRUIT`) is not promoted; the boards use KiCad's bundled `power` and
  `Device` libraries for those, with 336 and 355 references respectively. Only
  `rfm95adafruitmodule` is promoted.

## Explicitly out of scope

Five referenced parts have no symbol file anywhere in the workspace:

- `BM04B-GHS-TBT`
- `BM02B-GHS-TBT`
- `ADT7410TRZ-REEL7`
- `INA260AIPW`
- `ECS-120-18-33-JGN-TR`

They are not authored in this work. A symbol authored from a datasheet has an
unverifiable pinout, a wrong pinout fails silently rather than in CI, and this is a
safety-relevant vehicle. They are recorded as a separate hardware task with the board
and reference count for each, and their references are left untouched and reported.

`Archive_and_Legacy/` is not searched or used. It contains a footprint for
`BM04B-GHS-TBT` and nothing for the other four, and the repository guidelines exclude it
from routine work.

## Error handling

- The transform refuses to run on a dirty schematic, and reports rather than guessing when
  a `lib_id` has no mapping.
- A part that cannot satisfy the contract is reported and skipped, never imported with
  placeholder values that look real.
- Promotion failures leave the shared library unchanged; each part is imported and verified
  independently.
- No dangling reference is reported as resolved. The verification step counts what remains
  and states it plainly.

## Testing

- `KiCad/Libraries`: full existing suite stays green, plus tests asserting the required
  field set no longer contains `DigiKey` and that each promoted symbol satisfies the
  contract.
- `KiCad/DevOps`: tests for the rewrite transform covering a single rename, a rename with
  a cached-body replacement, a footprint normalization, an unmappable reference reported
  rather than guessed, and idempotence on a second run.
- Board contract suite stays green.
- Post-change audit, reported as counts, not assertions: zero dangling symbol `lib_id`
  remaining except the five out-of-scope parts, and zero unregistered footprint
  references.

## Delivery

Pushed directly to `master` in `KiCad/Libraries` and the four affected boards, with no
pull request, per the owner's instruction. This departs from the project's usual
reviewable-pull-request policy, so every commit message lists exactly what changed and
which decisions a reviewer should check.

Order: the library change lands first and green, then the board rewrites. If a board
rewrite has to be reverted, the library change is additive and can stand alone.

## Risks

- Replacing cached symbol bodies is the delicate step. Textual validity can be verified in
  CI, but only opening a board in KiCad proves the result. Each board is validated
  independently rather than assumed, and each is committed separately so one failure does
  not take the others with it.
- The four boards differ in schematic nesting, so the transform is validated per board
  against the actual file structure rather than assumed uniform.
- Pushing without a review gate means a mistake is caught by CI after the fact, not
  before. The per-board commits and the reported audit counts are the only compensating
  control.
- `TCAN1044VDRQ1` and `TCAN1044AVDRQ1` both existing in the shared library invites future
  confusion. Recording the distinction in each part's description is the mitigation; a
  future deprecation policy is out of scope here.
