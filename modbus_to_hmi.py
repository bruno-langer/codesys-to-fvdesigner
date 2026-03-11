"""
Modbus CSV → Altus FvDesigner HMI Tags CSV Converter
======================================================
Reads the CoDeSys Modbus Server export CSV and converts it to the
Altus FvDesigner tag import format.

Address mapping (HMI uses 1-based addressing):
  HoldingRegister DataStartAddress N  →  @modbus:4x<N+1>
  Coil            DataStartAddress N  →  @modbus:0x<N+1>

Type mapping:
  IEC Type   HMI Type         Address prefix
  --------   --------         --------------
  BOOL       Bit              0x   (Coil)
  INT        16Bit-INT        4x
  UINT       16Bit-UINT       4x
  WORD       16Bit-INT        4x
  DINT       32Bit-INT        4xD  (double register)
  REAL       32Bit-FLOAT      4xD  (double register)

Length (writeable flag):
  Default: 1 (writeable). Use --readonly to set all to 0,
  or --readonly-coils / --readonly-registers for per-type control.

Usage:
    python modbus_to_hmi.py --in modbus_output.csv --out hmi_tags.csv [options]

Options:
    --in              Input Modbus CSV (required)
    --out             Output HMI CSV  (default: hmi_tags.csv)
    --id-start        Starting Id value (default: 0)
    --writeable       Default writeable flag for all tags: 0 or 1 (default: 1)
    --writeable-coils Override writeable for Coil/BOOL tags (0 or 1)
    --writeable-regs  Override writeable for HoldingRegister tags (0 or 1)
    --name-strip      Comma-separated path prefixes to strip from variable names
                      e.g. --name-strip UserPrg. will turn
                      "UserPrg.DataHoraModbus.oSegundos" → "DataHoraModbus_oSegundos"
    --name-sep        Separator to replace '.' in tag names (default: _)

Example:
    python modbus_to_hmi.py \\
        --in modbus_output.csv \\
        --out hmi_tags.csv \\
        --name-strip UserPrg. \\
        --writeable-coils 0
"""

import argparse
import csv
import re
import sys
from dataclasses import dataclass

# ─────────────────────────────────────────────────────────────
# Type → HMI mapping
# ─────────────────────────────────────────────────────────────
# IEC type → (hmi_type, double_register)
IEC_TO_HMI: dict = {
    "BOOL":  ("Bit",           False),
    "BYTE":   ("16Bit-INT",     False),
    "INT":   ("16Bit-INT",     False),
    "UINT":  ("16Bit-UINT",    False),
    "USINT":  ("16Bit-USINT",    False),
    "DINT":  ("32Bit-INT",     True),
    "WORD":  ("16Bit-INT",     False),
    "UDINT": ("32Bit-INT",     True),
    "REAL":  ("32Bit-FLOAT",   True),
}


# ─────────────────────────────────────────────────────────────
# Address formatter
# ─────────────────────────────────────────────────────────────
def format_address(modbus_type: str, start_address: int, double_reg: bool) -> str:
    """
    Convert Modbus CSV address fields to HMI @modbus notation..
    Double-register types (DINT, REAL) use the 'D' prefix.
    """
    hmi_addr = start_address   # 1-based

    if modbus_type == "Coil":
        return f"@modbus:0x{hmi_addr}"
    else:  # HoldingRegister
        if double_reg:
            return f"@modbus:4xD{hmi_addr}"
        else:
            return f"@modbus:4x{hmi_addr}"


# ─────────────────────────────────────────────────────────────
# Name cleaner
# ─────────────────────────────────────────────────────────────
def clean_name(raw: str, strip_prefixes: list, sep: str) -> str:
    """
    Optionally strip leading path components and replace '.' with sep.
    e.g. "UserPrg.DataHoraModbus.oSegundos" → "DataHoraModbus_oSegundos"
    """
    name = raw
    for prefix in strip_prefixes:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    name = name.replace(".", sep)
    name = name.replace(',',sep)
    return name


# ─────────────────────────────────────────────────────────────
# Parser for Modbus CSV
# ─────────────────────────────────────────────────────────────
@dataclass
class ModbusRow:
    server: str
    variable: str
    data_type: str    # HoldingRegister | Coil
    start_address: int
    absolute_address: int
    iec_type: str


def parse_modbus_csv(path: str) -> list:
    """
    Parse the CoDeSys Modbus Server CSV.
    Lines starting with '#' are section headers — skip them.
    Fields are semicolon-separated, values quoted.
    """
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # Strip surrounding quotes from each field
            parts = [p.strip().strip('"') for p in line.split(";")]

            if len(parts) < 6:
                continue  # Server definition line or malformed — skip

            # Only process mapping rows: field[2] must be HoldingRegister or Coil
            if parts[2] not in ("HoldingRegister", "Coil"):
                continue

            try:
                start_addr   = int(parts[3])
                absolute_addr = int(parts[4])
            except ValueError:
                continue

            rows.append(ModbusRow(
                server=parts[0],
                variable=parts[1],
                data_type=parts[2],
                start_address=start_addr,
                absolute_address=absolute_addr,
                iec_type=parts[5],
            ))
    return rows


# ─────────────────────────────────────────────────────────────
# HMI CSV writer
# ─────────────────────────────────────────────────────────────
HMI_HEADER = [
    "Altus Sistemas de Automacao,FvDesigner",
    "File Type,Tags",
    "File Version,1,0",
    "Id,Name,Type,Address,Length,Comment",
]


def write_hmi_csv(
    rows: list,
    out_path: str,
    id_start: int,
    writeable_default: int,
    writeable_coils: int,
    writeable_regs: int,
    strip_prefixes: list,
    name_sep: str,
):
    skipped = []
    written = 0

    with open(out_path, "w", newline="\r\n", encoding="utf-8") as f:
        # Fixed header
        for line in HMI_HEADER:
            f.write(line + "\n")

        tag_id = id_start
        for row in rows:
            iec = row.iec_type.upper()

            if iec not in IEC_TO_HMI:
                skipped.append((row.variable, iec))
                continue

            hmi_type, double_reg = IEC_TO_HMI[iec]
            address = format_address(row.data_type, row.start_address, double_reg)
            name = clean_name(row.variable, strip_prefixes, name_sep)

            # Writeable flag
            if row.data_type == "Coil":
                length = writeable_coils
            else:
                length = writeable_regs

            f.write(f"{tag_id},{name},{hmi_type},{address},{length},\n")
            tag_id += 1
            written += 1

    return written, skipped


# ─────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────
def print_summary(written: int, skipped: list, out_path: str):
    print(f"\n╔══ HMI Tag Export Summary ═══════════════════════════╗")
    print(f"║  Tags written         : {written:>5}")
    print(f"║  Skipped (unknown)    : {len(skipped):>5}")
    print(f"╚═════════════════════════════════════════════════════╝")
    if skipped:
        print("\n⚠️  Skipped (type not in HMI map):")
        for var, t in skipped:
            print(f"   {var}  →  {t}")
    print(f"\n✅  Written to: {out_path}\n")


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(
        description="Convert CoDeSys Modbus CSV to Altus FvDesigner HMI tags CSV",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--in",              dest="inp", required=True,     help="Input Modbus CSV")
    p.add_argument("--out",             default="hmi_tags.csv",        help="Output HMI CSV")
    p.add_argument("--id-start",        default=1,   type=int,         help="Starting Id value")
    p.add_argument("--writeable",       default=1,   type=int, choices=[0,1],
                   help="Default writeable flag (0=read-only, 1=writeable)")
    p.add_argument("--writeable-coils", default=None, type=int, choices=[0,1],
                   help="Writeable flag override for BOOL/Coil tags")
    p.add_argument("--writeable-regs",  default=None, type=int, choices=[0,1],
                   help="Writeable flag override for HoldingRegister tags")
    p.add_argument("--name-strip",      default="",
                   help="Comma-separated path prefixes to strip from tag names")
    p.add_argument("--name-sep",        default="_",
                   help="Separator to replace '.' in tag names (default: _)")
    return p.parse_args()


def main():
    args = parse_args()

    # Resolve writeable flags
    w_coils = args.writeable_coils if args.writeable_coils is not None else args.writeable
    w_regs  = args.writeable_regs  if args.writeable_regs  is not None else args.writeable

    strip_prefixes = [p.strip() for p in args.name_strip.split(",") if p.strip()]

    print(f"📂 Reading Modbus CSV: {args.inp}")
    rows = parse_modbus_csv(args.inp)
    print(f"   {len(rows)} mapping rows found")

    coils = sum(1 for r in rows if r.data_type == "Coil")
    hrs   = sum(1 for r in rows if r.data_type == "HoldingRegister")
    print(f"   Coils: {coils}  |  HoldingRegisters: {hrs}")

    if strip_prefixes:
        print(f"🔤 Name prefix stripping: {strip_prefixes}")
    print(f"✏️  Writeable flags — Coils: {w_coils}  |  Registers: {w_regs}")

    written, skipped = write_hmi_csv(
        rows, args.out,
        id_start=args.id_start,
        writeable_default=args.writeable,
        writeable_coils=w_coils,
        writeable_regs=w_regs,
        strip_prefixes=strip_prefixes,
        name_sep=args.name_sep,
    )

    print_summary(written, skipped, args.out)


if __name__ == "__main__":
    main()