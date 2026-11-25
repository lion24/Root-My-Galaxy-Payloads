#!/usr/bin/env python3
"""Compare what the vendor tree compiles against with what this phone's kernel actually has.

Input is `values.py`'s output and the BTF decoded from the phone's `/sys/kernel/btf/vmlinux`.
A name the dump does not carry is reported as *unchecked* - this dump is a subset, and
pretending otherwise would be a gate that claims more than it read. A name both carry and
that disagrees fails the run: that is the failure a matching release string cannot catch.

    python3 compare.py btf-S916BXXUAGZIF.json abi.txt

The documented exception: `module.source_list`, `module.target_list`, `module.exit` and
`module.refcnt` are reported but not fatal. That tail is where the S918B-Oneui9 tree
diverges from the S916B's kernel: the tree lacks `btf_data_size`/`btf_data`
(`CONFIG_DEBUG_INFO_BTF_MODULES`, dropped because honouring it needs a vmlinux for
`resolve_btfids`), so everything from `source_list` on sits 0x10 low. Nothing in the
module addresses those fields (its only `THIS_MODULE` uses are pointer identity for
`try_module_get`/`module_put`); the danger is what the *loader* writes into that tail,
and every such write is a relocation in `.rela.gnu.linkonce.this_module` - which
`check-this-module.py` refuses unless it lands on the device's own `0x178`/`0x378`.
That gate is the write audit and it is strict; this one stays a layout audit. RWC 117/119
was caught by the relocation gate on the first build that tried to write `0x368`.
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

    # The four loader-tail names above. A divergence here is a property of a tree that does
    # not carry `CONFIG_DEBUG_INFO_BTF_MODULES`'s two fields (see the docstring), not a
    # module this phone would misload: the writes that matter are relocations, gated
    # strictly by `check-this-module.py` after the build. Keep this list exactly these
    # four: anything else that wants an exception needs the same written justification.
    LOADER_TAIL = {
        "module.source_list",
        "module.target_list",
        "module.exit",
        "module.refcnt",
    }

    mismatches, tail, checked, unchecked = [], [], 0, []
    for line in pathlib.Path(abi_path).read_text().splitlines():
        if not line.strip():
            continue
        name, rest = line.split("=", 1)
        value = int(rest.split()[0])
        if name not in device:
            unchecked.append(name)
            continue
        if name not in LOADER_TAIL:
            checked += 1
        if device[name] != value:
            diff = f"  {name}: tree={value} (0x{value:x}) device={device[name]} (0x{device[name]:x})"
            if name in LOADER_TAIL:
                tail.append(diff)
            else:
                mismatches.append(diff)

    print(f"compared {checked} of {checked + len(tail) + len(unchecked)} names "
          f"against {pathlib.Path(btf_path).name}")
    if tail:
        print(f"loader-tail divergences, reported not fatal - the write audit for these "
              f"is check-this-module.py ({len(tail)}):")
        print("\n".join(tail))
    if unchecked:
        print(f"not in the device dump, so unchecked ({len(unchecked)}): {', '.join(unchecked)}")
    if mismatches:
        print("MISMATCH - the tree is not this phone's kernel:")
        print("\n".join(mismatches))
        sys.exit("refusing: a module built here would run with the wrong structs")
    print("every checked name has the value this device dump carries")


if __name__ == "__main__":
    main()
