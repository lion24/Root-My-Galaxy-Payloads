#!/usr/bin/env python3
"""Read the numbers the ABI probe was compiled with out of the built module.

The probe (`abi_probe.c`) emits one array of `unsigned long`, in the order `schema.txt` names.
This prints them as `name=value` lines, which is what the device comparison reads.

Two things are looked up rather than assumed:

- the *section*, because `-fdata-sections` puts the array in `.rodata.susfs_abi_values` rather
  than `.rodata`, and reading the wrong section would read the wrong bytes without an error;
- the *size*, because `llvm-readelf` prints it decimal while `llvm-nm` prints it hex, and a
  base taken on faith is a silent off-by-16.

So the numbers come from `llvm-nm` (hex, section-relative) and the section index from
`llvm-readelf`, and the two must name the same symbol.

    python3 values.py [path/to/abi_probe.ko]
"""
import pathlib
import re
import struct
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
SYMBOL = "susfs_abi_values"

SECTION_RE = re.compile(r"^\s*\[\s*(\d+)\]\s+(\S+)")


def nm_numbers(ko):
    """(offset, size) in bytes, section-relative, from `llvm-nm -S` (both hex)."""
    out = subprocess.run(["llvm-nm", "--defined-only", "-S", str(ko)],
                         capture_output=True, text=True, check=True).stdout
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[-1] == SYMBOL:
            return int(parts[0], 16), int(parts[1], 16)
    return None, None


def section_of(ko):
    """The section `susfs_abi_values` lives in: `readelf -s` gives its index, `-S` its name."""
    symtab = subprocess.run(["llvm-readelf", "-s", str(ko)],
                            capture_output=True, text=True, check=True).stdout
    ndx = None
    for line in symtab.splitlines():
        parts = line.split()
        if len(parts) >= 8 and parts[-1] == SYMBOL:
            ndx = parts[6]
            break
    if ndx is None:
        return None
    if ndx in ("ABS", "UND"):
        sys.exit(f"{SYMBOL} is not in an allocated section (Ndx={ndx})")
    headers = subprocess.run(["llvm-readelf", "-S", str(ko)],
                             capture_output=True, text=True, check=True).stdout
    for line in headers.splitlines():
        match = SECTION_RE.match(line)
        if match and int(match.group(1)) == int(ndx):
            return match.group(2)
    return f"<section {ndx}>"


def main():
    ko = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "abi_probe.ko"
    names = [line.strip() for line in (HERE / "schema.txt").read_text().splitlines()
             if line.strip() and not line.strip().startswith("#")]

    offset, size = nm_numbers(ko)
    if offset is None:
        sys.exit(f"{SYMBOL} is not a defined symbol in {ko}")
    section = section_of(ko)

    dump = pathlib.Path("/tmp/susfs-abi-section.bin")
    subprocess.run(["llvm-objcopy", f"--dump-section={section}={dump}", str(ko)], check=True)
    data = dump.read_bytes()

    count = size // 8
    print(f"{SYMBOL}: {section}+0x{offset:x}, {size} bytes, {count} values", file=sys.stderr)
    if count != len(names):
        sys.exit(f"schema.txt has {len(names)} names, the module has {count} values - "
                 "the two files drifted and a gate would read a number into the wrong name")
    if len(data) < offset + size:
        sys.exit(f"{section} is {len(data)} bytes and the symbol claims {size} at 0x{offset:x}")

    values = [struct.unpack_from("<Q", data, offset + 8 * i)[0] for i in range(count)]
    for name, value in zip(names, values):
        print(f"{name}={value}")


if __name__ == "__main__":
    main()
