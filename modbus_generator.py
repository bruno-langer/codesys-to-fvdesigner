"""
CoDeSys → Modbus CSV Generator
================================
Parses a CoDeSys Symbol Configuration XML and generates a Modbus Server
mapping CSV ready to import into CoDeSys Modbus Server plugin.

Supported IEC types → Modbus:
  INT, UINT, WORD          → HoldingRegister  (1 register / 16-bit)
  DINT, REAL               → HoldingRegister  (2 registers / 32-bit)
  BOOL                     → Coil             (1 coil / 1-bit)

Usage:
    python modbus_generator.py --xml <path_to_xml> [options]

Options:
    --xml           Path to the .xml symbol config file  (required)
    --out           Output CSV path                      (default: modbus_output.csv)
    --server        Modbus server name                   (default: MODBUS_IHM)
    --parent        Parent device name                   (default: XP325)
    --connector     Connector name                       (default: NET 1)
    --port          TCP port                             (default: 502)
    --hr-start      HoldingRegister start address        (default: 0  → absolute 400001)
    --coil-start    Coil start address                   (default: 0  → absolute 1)
    --filter        Comma-separated root node prefixes   (default: all)
                    e.g. --filter UserPrg,GVL

Example:
    python modbus_generator.py \
        --xml CLP.Device.Application.default.xml \
        --server MODBUS_IHM \
        --parent XP325 \
        --out modbus_table.csv
"""

import xml.etree.ElementTree as ET
import argparse
import sys
import re
from dataclasses import dataclass, field

# ─────────────────────────────────────────────────────────────
# Namespace
# ─────────────────────────────────────────────────────────────
NS = {"c": "http://www.3s-software.com/schemas/Symbolconfiguration.xsd"}

# ─────────────────────────────────────────────────────────────
# Allowed IEC types → (internal_label, register_count)
# Only these types will be mapped; everything else is warned/skipped.
# ─────────────────────────────────────────────────────────────
ALLOWED_TYPES: dict = {
    "BOOL":  ("BOOL",  1),   # → Coil
    "INT":   ("INT",   1),   # → HoldingRegister x1  (16-bit)
    "UINT":  ("UINT",  1),   # → HoldingRegister x1  (16-bit)
    "WORD":  ("WORD",  1),   # → HoldingRegister x1  (16-bit)
    "DINT":  ("DINT",  2),   # → HoldingRegister x2  (32-bit)
    "REAL":  ("REAL",  2),   # → HoldingRegister x2  (32-bit)
}

# Detect ARRAY types, e.g. "ARRAY [0..9] OF INT"
ARRAY_RE = re.compile(r"^ARRAY\s*\[", re.IGNORECASE)


def normalize_type(raw: str) -> str:
    """
    CoDeSys XML sometimes stores types with a 'T_' prefix in NodeList
    (e.g. 'T_FB_DATAHORA', 'T_INT', 'T_BYTE') while TypeList defines
    them without it ('FB_DATAHORA', 'INT', 'BYTE').
    Strip the leading T_ so lookups resolve correctly.
    """
    t = raw.upper().strip()
    if t.startswith("T_"):
        t = t[2:]
    return t


# ─────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────
@dataclass
class ModbusEntry:
    server: str
    variable: str
    modbus_type: str    # "HoldingRegister" | "Coil"
    start_address: int
    iec_type: str

    @property
    def absolute_address(self) -> int:
        if self.modbus_type == "HoldingRegister":
            return 400001 + self.start_address
        return 1 + self.start_address   # Coil

    def to_csv_line(self) -> str:
        return ";".join([
            f'"{self.server}"',
            f'"{self.variable}"',
            self.modbus_type,
            str(self.start_address),
            str(self.absolute_address),
            f'"{self.iec_type}"',
        ])


@dataclass
class State:
    hr_cursor:        int  = 0
    coil_cursor:      int  = 0
    entries:          list = field(default_factory=list)
    skipped_unknown:  list = field(default_factory=list)   # (path, type)
    skipped_array:    list = field(default_factory=list)   # (path, type)
    skipped_filtered: int  = 0


# ─────────────────────────────────────────────────────────────
# XML helpers
# ─────────────────────────────────────────────────────────────
def load_user_types(root) -> dict:
    """
    Returns { 'TYPENAME': [('member_name', 'MEMBER_TYPE'), ...] }
    from the <TypeList> section of the XML.
    """
    result = {}
    types_node = root.find("c:TypeList", NS)
    if types_node is None:
        return result
    for typedef in types_node.findall("c:TypeUserDef", NS):
        # Normalize key: strip T_ prefix so lookups always match
        typename = normalize_type(typedef.attrib.get("iecname", ""))
        members = []
        for elem in typedef.findall("c:UserDefElement", NS):
            mname     = elem.attrib.get("iecname", "")
            raw_mtype = elem.attrib.get("type", "")   # kept raw; emit() normalizes
            vartype_class = elem.attrib.get("vartype", "")
            if vartype_class != "VAR_IN_OUT":
                if vartype_class != "VAR_INPUT":   
                    members.append((mname, raw_mtype))
        result[typename] = members
    return result


# ─────────────────────────────────────────────────────────────
# Core walker
# ─────────────────────────────────────────────────────────────
def walk_node(node, user_types: dict, state: State, server: str,
              path: str = "", filter_prefixes: list = None):
    name     = node.attrib.get("name", "")
    raw_type = (node.attrib.get("type") or "").strip()
    new_path = f"{path}.{name}" if path else name
    children = node.findall("c:Node", NS)

    # Top-level prefix filter
    if not path and filter_prefixes:
        if not any(name.startswith(p) for p in filter_prefixes):
            state.skipped_filtered += 1
            return

    # Intermediate node → recurse into XML children
    if children:
        for child in children:
            walk_node(child, user_types, state, server, new_path, filter_prefixes)
        return

    # Leaf node → resolve type and emit
    emit(new_path, raw_type, user_types, state, server, visited=set())


def emit(path: str, raw_type: str, user_types: dict, state: State,
         server: str, visited: set):
    """
    Resolve a type string and append ModbusEntry objects to state.

    Resolution order:
      1. Normalize: strip leading 'T_' prefix (CoDeSys XML artifact)
      2. Array?          → skip (size unknown)
      3. Known primitive → emit HoldingRegister or Coil
      4. User struct     → explode members recursively
      5. No match        → warn
    """
    iec_type = normalize_type(raw_type)

    # Array → skip
    if ARRAY_RE.match(iec_type):
        state.skipped_array.append((path, raw_type))
        return

    # Known primitive in allowlist
    if iec_type in ALLOWED_TYPES:
        _, reg_count = ALLOWED_TYPES[iec_type]
        if iec_type == "BOOL":
            state.entries.append(ModbusEntry(
                server=server, variable=path,
                modbus_type="Coil",
                start_address=state.coil_cursor,
                iec_type="BOOL",
            ))
            state.coil_cursor += 1
        else:
            state.entries.append(ModbusEntry(
                server=server, variable=path,
                modbus_type="HoldingRegister",
                start_address=state.hr_cursor,
                iec_type=iec_type,
            ))
            state.hr_cursor += reg_count
        return

    # User-defined struct → explode members recursively
    if iec_type in user_types:
        if iec_type in visited:
            # Circular reference guard
            state.skipped_unknown.append((path, f"{iec_type} [circular]"))
            return
        visited = visited | {iec_type}
        for member_name, member_raw_type in user_types[iec_type]:
            emit(f"{path}.{member_name}", member_raw_type,
                 user_types, state, server, visited)
        return

    # Nothing matched
    state.skipped_unknown.append((path, raw_type))


# ─────────────────────────────────────────────────────────────
# CSV writer
# ─────────────────────────────────────────────────────────────
SERVER_HEADER  = (
    "#ModbusServer;Parent;Connector;TcpPort;ConnectionMode;TaskCycle;"
    "ConnectionInactivityTimeOut;WriteFilterIPAddress;WriteFilterMask;"
    "ReadFilterIPAddress;ReadFilterMask;KeepRunningOnCpuStop"
)
MAPPING_HEADER = (
    "#ModbusServerMapping;ValueVariable;DataType;"
    "DataStartAddress;AbsoluteAddress;IecDataType"
)


def write_csv(state: State, out_path: str, server: str,
              parent: str, connector: str, port: int):
    coils = [e for e in state.entries if e.modbus_type == "Coil"]
    hrs   = [e for e in state.entries if e.modbus_type == "HoldingRegister"]

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        f.write(SERVER_HEADER + "\n")
        f.write(
            f'"{server}";"{parent}";"{connector}";{port};Tcp;100;10;'
            '"[0, 0, 0, 0]";"[0, 0, 0, 0]";"[0, 0, 0, 0]";"[0, 0, 0, 0]";False\n'
        )
        f.write(MAPPING_HEADER + "\n")
        for entry in coils:         # Coils first
            f.write(entry.to_csv_line() + "\n")
        for entry in hrs:           # then HoldingRegisters
            f.write(entry.to_csv_line() + "\n")


# ─────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────
def print_summary(state: State, out_path: str):
    hrs   = [e for e in state.entries if e.modbus_type == "HoldingRegister"]
    coils = [e for e in state.entries if e.modbus_type == "Coil"]

    print("\n╔══ Modbus Mapping Summary ═══════════════════════════╗")
    print(f"║  Coils (BOOL)         : {len(coils):>5} entries")
    if coils:
        print(f"║    Addr range         : {coils[0].start_address} → {state.coil_cursor - 1}"
              f"  (abs {coils[0].absolute_address} → {state.coil_cursor})")
    print(f"║  HoldingRegisters     : {len(hrs):>5} entries")
    if hrs:
        print(f"║    Addr range         : {hrs[0].start_address} → {state.hr_cursor - 1}"
              f"  (abs {hrs[0].absolute_address} → {400000 + state.hr_cursor})")
    print(f"║  Total mapped         : {len(state.entries):>5}")
    print(f"╠══ Skipped ══════════════════════════════════════════╣")
    print(f"║  Arrays               : {len(state.skipped_array):>5}  (no size info in XML)")
    print(f"║  Unsupported types    : {len(state.skipped_unknown):>5}")
    print(f"║  Filtered nodes       : {state.skipped_filtered:>5}  (prefix filter)")
    print(f"╚═════════════════════════════════════════════════════╝")

    if state.skipped_unknown:
        print("\n⚠️  Unsupported / unknown types — not mapped:")
        seen = {}
        for path, t in state.skipped_unknown:
            seen.setdefault(t, []).append(path)
        for t, paths in seen.items():
            print(f"   {t:20s}  ({len(paths)} variable(s))")
            for p in paths[:3]:
                print(f"              {p}")
            if len(paths) > 3:
                print(f"              ... and {len(paths)-3} more")

    if state.skipped_array:
        print("\n📋 Arrays skipped:")
        for path, t in state.skipped_array[:8]:
            print(f"   {path}  →  {t}")
        if len(state.skipped_array) > 8:
            print(f"   ... and {len(state.skipped_array)-8} more")

    print(f"\n✅  Written to: {out_path}\n")


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(
        description="Generate Modbus CSV from CoDeSys Symbol Configuration XML",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--xml",        required=True,               help="Path to XML symbol config")
    p.add_argument("--out",        default="modbus_output.csv", help="Output CSV file")
    p.add_argument("--server",     default="MODBUS_IHM",        help="Modbus server name")
    p.add_argument("--parent",     default="XP325",             help="Parent device name")
    p.add_argument("--connector",  default="NET 1",             help="Connector name")
    p.add_argument("--port",       default=502,  type=int,      help="TCP port")
    p.add_argument("--hr-start",   default=1,    type=int,      help="HoldingRegister start address")
    p.add_argument("--coil-start", default=1,    type=int,      help="Coil start address")
    p.add_argument("--filter",     default="",
                   help="Comma-separated root prefixes to include, e.g. UserPrg,GVL")
    return p.parse_args()


def main():
    args = parse_args()

    print(f"📂 Parsing: {args.xml}")
    tree = ET.parse(args.xml)
    root = tree.getroot()

    header = root.find("c:Header", NS)
    if header is not None:
        proj = header.find("c:ProjectInfo", NS)
        if proj is not None:
            print(f"📋 Project: {proj.attrib}")

    user_types = load_user_types(root)
    print(f"🔧 User-defined types: {len(user_types)}  →  {list(user_types.keys())}")

    state = State(hr_cursor=args.hr_start, coil_cursor=args.coil_start)

    filter_prefixes = [p.strip() for p in args.filter.split(",") if p.strip()]
    if filter_prefixes:
        print(f"🔍 Filter prefixes: {filter_prefixes}")

    nodes = root.find("c:NodeList", NS)
    if nodes is None:
        print("❌ No NodeList found in XML.", file=sys.stderr)
        sys.exit(1)

    for node in nodes.findall("c:Node", NS):
        walk_node(node, user_types, state, args.server,
                  path="", filter_prefixes=filter_prefixes)

    write_csv(state, args.out, args.server, args.parent, args.connector, args.port)
    print_summary(state, args.out)


if __name__ == "__main__":
    main()