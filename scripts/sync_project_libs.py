"""
Purdue ROV KiCad Library Sync Utility
Ensures project sym-lib-table and fp-lib-table contain all 6 central Purdue ROV libraries:
- rov_passives
- rov_power
- rov_logic
- rov_connectors
- rov_sensors
- rov_mech

Also ensures .gitmodules tracks the master branch for purdue-rov-kicad-lib.
Preserves existing custom/project-specific libraries.

The standard library list comes from rov_core.STANDARD_LIBS, the shared platform
contract used by the CLI, bootstrap flow, and Library Manager GUI.
"""

import sys
import os
import re
from pathlib import Path

from rov_core import STANDARD_LIBS


def sync_lib_table(table_path, table_type="sym"):
    root_tag = "sym_lib_table" if table_type == "sym" else "fp_lib_table"
    uri_key = "sym_uri" if table_type == "sym" else "fp_uri"
    descr_key = "sym_descr" if table_type == "sym" else "fp_descr"

    if not table_path.exists():
        lines = [f"({root_tag}"]
        for lib in STANDARD_LIBS:
            lines.append(f'  (lib (name "{lib["name"]}")(type "KiCad")(uri "{lib[uri_key]}")(options "")(descr "{lib[descr_key]}"))')
        lines.append(")\n")
        table_path.write_text("\n".join(lines), encoding="utf-8")
        return True, [lib["name"] for lib in STANDARD_LIBS]

    content = table_path.read_text(encoding="utf-8", errors="ignore")
    existing_names = set(re.findall(r'\(lib\s+\(name\s+"([^"]+)"\)', content, re.IGNORECASE))
    
    added = []
    updated_content = content
    to_add = []
    for lib in STANDARD_LIBS:
        if lib["name"] not in existing_names:
            entry = f'  (lib (name "{lib["name"]}")(type "KiCad")(uri "{lib[uri_key]}")(options "")(descr "{lib[descr_key]}"))'
            to_add.append(entry)
            added.append(lib["name"])

    if to_add:
        last_paren = updated_content.rfind(')')
        if last_paren != -1:
            insertion = "\n" + "\n".join(to_add) + "\n"
            updated_content = updated_content[:last_paren].rstrip() + insertion + ")\n"
        else:
            updated_content = f"({root_tag}\n" + "\n".join(to_add) + "\n)\n"
            
        table_path.write_text(updated_content, encoding="utf-8")

    return bool(added), added


def sync_gitmodules(gitmodules_path):
    if not gitmodules_path.exists():
        return False
    content = gitmodules_path.read_text(encoding="utf-8", errors="ignore")
    
    if "purdue-rov-kicad-lib" in content:
        submod_pattern = re.compile(
            r'(\[submodule\s+"libs/purdue-rov-kicad-lib"\][^\[]*?path\s*=\s*libs/purdue-rov-kicad-lib[^\[]*)',
            re.DOTALL
        )
        match = submod_pattern.search(content)
        if match:
            block = match.group(1)
            if "branch" not in block:
                new_block = block.rstrip() + "\n\tbranch = master\n"
                new_content = content.replace(block, new_block)
                gitmodules_path.write_text(new_content, encoding="utf-8")
                return True
    return False


def sync_project(project_dir):
    p_dir = Path(project_dir).resolve()
    sym_changed, sym_added = sync_lib_table(p_dir / "sym-lib-table", "sym")
    fp_changed, fp_added = sync_lib_table(p_dir / "fp-lib-table", "fp")
    gitmod_changed = sync_gitmodules(p_dir / ".gitmodules")
    
    return {
        "sym_changed": sym_changed,
        "sym_added": sym_added,
        "fp_changed": fp_changed,
        "fp_added": fp_added,
        "gitmodules_changed": gitmod_changed
    }


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    res = sync_project(target)
    if res["sym_added"]:
        print(f"✅ Injected missing symbol libraries into sym-lib-table: {', '.join(res['sym_added'])}")
    if res["fp_added"]:
        print(f"✅ Injected missing footprint libraries into fp-lib-table: {', '.join(res['fp_added'])}")
    if res["gitmodules_changed"]:
        print("✅ Configured .gitmodules to track master branch for purdue-rov-kicad-lib")
    if not res["sym_added"] and not res["fp_added"] and not res["gitmodules_changed"]:
        print("✅ Project library tables are already complete and verified.")
