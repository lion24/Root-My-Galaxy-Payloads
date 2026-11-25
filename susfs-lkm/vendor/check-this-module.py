#!/usr/bin/env python3
"""Refuse a `.ko` whose `.gnu.linkonce.this_module` relocations are not the device's slots.

Every value `apply_relocations()` writes into `__this_module` lands at a `struct module`
offset *this KDIR* compiled. If the tree's layout disagrees with the phone's about where
`init`/`exit` live, the write lands on a kernel field it must not touch. On the S916B that
was precisely RWC 117/119: the tree's `exit` at 0x368 overwrote the device's
`target_list.next` there, and `mod_sysfs_setup` died dereferencing it two steps later -
while vermagic, `init` at 0x178, and 111 resolved symbols all said "compatible".

The device's own slots (from its BTF, `work-module-btf.json`):

    0x178  init
    0x378  exit        (absent, and never relocated, when the module has no module_exit)
    0x368  target_list <- where a wrong `exit` lands; what killed RWC 117/119

So this gate allows:

    - 0x178: required. A module without a relocated `init` does not load at all.
    - 0x378: allowed, once. The only correct slot a second relocation can have.
    - anything else, including 0x368: a fatal error, before the artifact is offered.

Byte-level ELF64 parsing rather than `readelf` output scraping: the relocation tables a
runner's `llvm-readelf` prints are stable, but the check below is the one version of this
gate that no formatting change or section ordering can fool.

    python3 check-this-module.py path/to/module.ko
"""
import pathlib
import struct
import sys

ALLOWED = {0x178, 0x378}   # device slots for init / exit
REQUIRED = 0x178           # init must be relocated: it is the call a load depends on
THIS_MODULE = 0x3C0        # device sizeof(struct module) - a .KO whose __this_module section
                           # is another size was not laid out for this phone's struct at all


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    ko = pathlib.Path(sys.argv[1])
    data = ko.read_bytes()
    if data[:4] != b"\x7fELF" or data[5] != 1 or data[6] != 1:  # ELF64, little-endian (EI_DATA=1)
        sys.exit("not an ELF64 little-endian object - wrong file or wrong endianness")

    e_shoff, = struct.unpack_from("<Q", data, 0x28)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", data, 0x3A)
    sections = []
    for i in range(e_shnum):
        b = e_shoff + i * e_shentsize
        sh_name, sh_type = struct.unpack_from("<II", data, b)
        sh_offset, sh_size, sh_link = struct.unpack_from("<QQI", data, b + 0x18)
        sections.append({"name_off": sh_name, "type": sh_type, "offset": sh_offset,
                         "size": sh_size, "link": sh_link})

    strtab = sections[e_shstrndx]
    names = data[strtab["offset"]: strtab["offset"] + strtab["size"]]
    for s in sections:
        end = names.find(b"\0", s["name_off"])
        s["name"] = names[s["name_off"]: end].decode("ascii", "replace")

    this_module = next((s for s in sections
                        if s["name"] == ".gnu.linkonce.this_module"), None)
    if this_module is None:
        sys.exit("no .gnu.linkonce.this_module section - this is not a loadable module form")
    if this_module["size"] != THIS_MODULE:
        sys.exit(f"__this_module is 0x{this_module['size']:x} bytes; this phone's "
                 f"struct module is 0x{THIS_MODULE:x} - this KDIR laid it out for another "
                 "device, whatever the vermagic says")

    def syms_of(sec):
        # A .rela section's sh_link is its symbol table; each Elf64_Sym is 24 bytes with
        # st_name at 0 and st_value at 8.
        st = sections[sec["link"]]
        sstr = sections[st["link"]]
        sdata = data[sstr["offset"]: sstr["offset"] + sstr["size"]]
        syms = []
        for i in range(st["size"] // 24):
            b = st["offset"] + i * 24
            st_name, = struct.unpack_from("<I", data, b)
            end = sdata.find(b"\0", st_name)
            syms.append(sdata[st_name: end].decode("ascii", "replace"))
        return syms

    relas = [s for s in sections
             if s["name"].startswith(".rela") and "this_module" in s["name"]]
    if len(relas) != 1:
        sys.exit(f"expected exactly one this_module relocation section, found {len(relas)}: "
                 + ", ".join(s["name"] for s in relas))
    syms = syms_of(relas[0])

    entries = []
    for i in range(relas[0]["size"] // 24):   # Elf64_Rela is 24 bytes: r_offset, r_info, r_addend
        b = relas[0]["offset"] + i * 24
        r_offset, r_info = struct.unpack_from("<QQ", data, b)
        symno = r_info >> 32
        rtype = r_info & 0xFFFFFFFF
        if rtype != 257:                      # R_AARCH64_ABS64
            sys.exit(f"reloc at 0x{r_offset:x} is type {rtype}, not R_AARCH64_ABS64 (257) - "
                     "another loader shape than the one this gate understands")
        entries.append((r_offset, syms[symno] if symno < len(syms) else f"sym {symno}"))

    print(f"{ko.name}: {len(entries)} relocation(s) in [{relas[0]['name']}]:")
    for r_offset, name in entries:
        mark = "device slot" if r_offset in ALLOWED else "NOT A DEVICE SLOT"
        print(f"  0x{r_offset:04x}  {name:<16s} [{mark}]")

    if REQUIRED not in [r for r, _ in entries]:
        sys.exit("no relocation at 0x178: this module carries no init it can run")
    if [r for r, _ in entries].count(0x378) > 1:
        sys.exit("exit relocated twice at 0x378 - that is a bug, not a module layout")
    for r_offset, name in entries:
        if r_offset not in ALLOWED:
            fail = (f"apply_relocations() writes {name} into __this_module + 0x{r_offset:x}. "
                    f"This phone keeps other fields there (0x368 is target_list.next, which "
                    "is exactly where RWC 117/119 panicked in mod_sysfs_setup after this "
                    "write put cleanup_module on it).")
            print(f"::error::{fail}")
            sys.exit(fail)
    print("relocations are the device's own slots")


if __name__ == "__main__":
    main()
