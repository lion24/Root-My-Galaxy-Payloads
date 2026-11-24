#!/usr/bin/env python3
"""Compare what the vendor tree compiles against with what this phone's kernel actually has.

Input is `values.py`'s output and the BTF decoded from the phone's `/sys/kernel/btf/vmlinux`.
A name the dump does not carry is reported as *unchecked* - this dump is a subset, and
pretending otherwise would be a gate that claims more than it read. A name both carry and
that disagrees fails the run: that is the failure a matching release string cannot catch.

    python3 compare.py btf-S916BXXUAGZIF.json abi.txt
"""
import json
import pathlib
import sys


def device_offsets(path):
    device = {}
    for entry in json.loads(pathlib.Path(path).read_text()):
        device[f"{entry['struct']}.size"] = entry["size"]
        for member in entry.get("members", []):
            device[f"{entry['struct']}.{member['name']}"] = member["bit_offset"] // 8
    return device


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    btf_path, abi_path = sys.argv[1], sys.argv[2]
    device = device_offsets(btf_path)

    mismatches, checked, unchecked = [], 0, []
    for line in pathlib.Path(abi_path).read_text().splitlines():
        if not line.strip():
            continue
        name, rest = line.split("=", 1)
        value = int(rest.split()[0])
        if name not in device:
            unchecked.append(name)
            continue
        checked += 1
        if device[name] != value:
            mismatches.append(f"  {name}: tree={value} (0x{value:x}) device={device[name]} (0x{device[name]:x})")

    print(f"compared {checked} of {checked + len(unchecked)} names against {pathlib.Path(btf_path).name}")
    if unchecked:
        print(f"not in the device dump, so unchecked ({len(unchecked)}): {', '.join(unchecked)}")
    if mismatches:
        print("MISMATCH - the tree is not this phone's kernel:")
        print("\n".join(mismatches))
        sys.exit("refusing: a module built here would run with the wrong structs")
    print("every name the device dump carries has the value this tree compiles")


if __name__ == "__main__":
    main()
