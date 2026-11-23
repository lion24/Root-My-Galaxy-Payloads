#!/usr/bin/env python3
"""`random_table`'s `boot_id` entry, found in the Image instead of counted in the source.

`SLIDE_RANDOM_TABLE_BOOT_ID_DATA_PTR_OFF` is the address of the `data` pointer of the `boot_id` sysctl
entry, and it is the one number a port cannot get from a symbol table: no symbol names a field inside a
struct. `docs/PORTING.md` derives it as `random_table + index * sizeof(struct ctl_table) + 8`, with the
index read out of the tree's own `random_table` - which is correct, and fragile in two ways that this
tool removes:

- **the index moves.** In the ACK `android13-5.15.197_r00` tree the entries are poolsize,
  entropy_avail, write_wakeup_threshold, urandom_min_reseed_secs, **boot_id**, uuid - index 4 - and
  mainline's array has more entries in front of it. A port that copies "index 4" without re-reading
  the tree is one config change away from pointing at `uuid`.
- **the +8 can move.** `struct ctl_table` is `__randomize_layout`, so on a kernel with struct layout
  randomisation the field order is not the declaration order and `data` is not at 8. Samsung's GKI
  builds do not enable that, which is why the sibling port's `+8` worked, but "it worked last time" is
  not the reason to keep writing it.

So this searches. With `random_table` and `sysctl_bootid` in the symbol table, the entry is the one
whose slots hold `sysctl_bootid`'s address, and the answer is the offset of the slot that holds it -
whatever the layout and whatever the index. Without `sysctl_bootid` (it is `static`, so it needs
`CONFIG_KALLSYMS_ALL` to appear) it falls back to the `boot_id` string, which must be unique.

It refuses rather than picking: no terminator in the expected entry stride, no entry holding the
pointer, or more than one, are all failures - because every one of them means the assumption behind
the search is wrong for this kernel, and the number it would produce is a pointer into the middle of
an unrelated global.

    tools/port/random_table.py --symbols vmlinux.nm --image kernel
    tools/port/random_table.py --symbols kallsyms.txt --image kernel --emit-define
    tools/port/random_table.py --self-test
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
from pathlib import Path

# `sizeof(struct ctl_table)` on arm64 with this header: nine fields, five pointers either side of
# `maxlen`/`mode`, which pad to one eight-byte word. Checked against the source, and then checked
# again here against the array's own terminator, because a stride that is wrong walks off the array.
ENTRY_SIZE = 0x40

# Enough entries for any config of this array (mainline has about half a dozen more than ACK 5.15);
# the terminator is what actually ends the walk.
MAX_ENTRIES = 32

# The address the Image's own pointers are written with: the kernel's link address, which is not the
# runtime `_text` once KASLR has slid the image. A symbol table from `/proc/kallsyms` is slid and a
# table from `llvm-nm` is not, so both are tried and exactly one has to work.
DEFAULT_LINK_BASE = 0xFFFFFFC008000000

SYMBOL = re.compile(r"^(?P<address>[0-9a-fA-F]+)\s+\S+\s+(?P<name>\S+)(?:\s+\[.*\])?$")


def parse_symbols(text: str) -> dict[str, int]:
    symbols: dict[str, int] = {}
    for line in text.splitlines():
        match = SYMBOL.match(line.strip())
        if not match:
            continue
        address = int(match.group("address"), 16)
        if address:
            symbols[match.group("name")] = address
    return symbols


def find_entry(table: bytes, pointer: int, entry_size: int) -> list[tuple[int, int]]:
    """Every (entry index, byte offset into the entry) whose slot holds [pointer].

    The slot is searched rather than assumed: `struct ctl_table` is `__randomize_layout`, so the
    field a source file calls `data` is wherever the build's seed put it.
    """
    hits: list[tuple[int, int]] = []
    for entry in range(MAX_ENTRIES):
        base = entry * entry_size
        if base + entry_size > len(table):
            break
        for slot in range(0, entry_size, 8):
            if struct.unpack_from("<Q", table, base + slot)[0] == pointer:
                hits.append((entry, slot))
    return hits


def terminator(table: bytes, entry_size: int) -> int | None:
    """Index of the empty entry that ends the array, if the stride finds one."""
    for entry in range(MAX_ENTRIES):
        base = entry * entry_size
        if base + entry_size > len(table):
            return None
        if struct.unpack_from("<Q", table, base)[0] == 0:
            return entry
    return None


def strings(image: bytes, needle: str) -> list[int]:
    raw = needle.encode() + b"\x00"
    found: list[int] = []
    cursor = 0
    while True:
        at = image.find(raw, cursor)
        if at < 0:
            return found
        found.append(at)
        cursor = at + 1


def derive(
    symbols: dict[str, int],
    image: bytes,
    entry_size: int,
    link_base: int | None = None,
) -> tuple[int, str, list[str]]:
    """The macro's value, where it came from, and anything worth telling the reader."""
    notes: list[str] = []
    for required in ("_text", "random_table"):
        if required not in symbols:
            raise ValueError(
                f"the symbol table has no `{required}`: "
                + ("offsets cannot be measured from a guess" if required == "_text"
                   else "the array has to be located, and its name is the only handle on it")
            )
    base = symbols["_text"]
    table_off = symbols["random_table"] - base
    if table_off <= 0 or table_off >= len(image):
        raise ValueError(
            f"`random_table` at 0x{table_off:x} is outside the {len(image)}-byte Image: either the "
            "symbol table and the Image are not the same build, or the offsets are not `symbol - _text`"
        )

    table = image[table_off:table_off + MAX_ENTRIES * entry_size]
    end = terminator(table, entry_size)
    if end is None:
        raise ValueError(
            f"no empty entry within {MAX_ENTRIES} entries of {entry_size:#x} bytes: the stride or the "
            "start of the array is wrong, and every offset from here would be inside another global"
        )
    notes.append(f"the array ends at entry {end} ({end} populated entries)")

    if "sysctl_bootid" in symbols:
        pointed_at = symbols["sysctl_bootid"] - base
        source = "`sysctl_bootid`"
    else:
        found = strings(image, "boot_id")
        if len(found) != 1:
            raise ValueError(
                "`sysctl_bootid` is not in the symbol table (it is `static`, so this build may not "
                f"export it) and the `boot_id` string has {len(found)} occurrences; there is "
                "nothing left to identify the entry by"
            )
        pointed_at = found[0]
        source = "the unique `boot_id` string"
        notes.append("`sysctl_bootid` is not exported by this build; the string identified the entry")

    # The Image holds link addresses; a symbol table from a running phone is slid. Both bases are
    # tried, and exactly one is allowed to find the entry - an answer that needs a choice between two
    # consistent stories is not an answer.
    bases = [link_base] if link_base is not None else sorted({base, DEFAULT_LINK_BASE})
    attempts: list[tuple[int, list[tuple[int, int]]]] = [
        (candidate, find_entry(table[:end * entry_size], candidate + pointed_at, entry_size))
        for candidate in bases
    ]
    resolved = [(candidate, hits[0]) for candidate, hits in attempts if len(hits) == 1]
    if len(resolved) != 1:
        detail = "; ".join(
            f"link base 0x{candidate:x}: {len(hits)} match(es)" for candidate, hits in attempts
        )
        raise ValueError(
            f"no unambiguous entry holds the address of {source} ({detail}): the search is looking at "
            "the wrong array, or the Image's pointers are not link addresses of this build"
        )
    found_base, (entry, slot) = resolved[0]
    if found_base != base:
        notes.append(
            f"the Image's pointers use link base 0x{found_base:x} and the symbol table is at "
            f"0x{base:x}: that is KASLR, and the offsets are the same under both"
        )
    notes.append(f"entry {entry} holds it at +0x{slot:x}")
    if slot != 8:
        notes.append(
            f"the pointer is at +0x{slot:x} rather than the source order's +0x8: this kernel "
            "randomises struct layouts, and the search is why that is not a problem"
        )
    if entry != 4:
        notes.append(f"the entry is {entry}, not the 4 it is in ACK android13-5.15: reordering happens")
    return table_off + entry * entry_size + slot, source, notes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbols", help="an `llvm-nm -n` dump, or `/proc/kallsyms`")
    parser.add_argument("--image", help="the raw ARM64 Image offset 0 of which is `_text`")
    parser.add_argument("--entry-size", type=lambda text: int(text, 0), default=ENTRY_SIZE,
                        help=f"`sizeof(struct ctl_table)` for this kernel (default {ENTRY_SIZE:#x})")
    parser.add_argument("--link-base", type=lambda text: int(text, 0),
                        help="the address the Image's pointers are written with, if it is not "
                             f"{DEFAULT_LINK_BASE:#x} and the symbol table is slid")
    parser.add_argument("--json", help="write the finding here")
    parser.add_argument("--emit-define", action="store_true", help="print the macro line")
    parser.add_argument("--self-test", action="store_true", help="run against a synthetic array")
    arguments = parser.parse_args()

    if arguments.self_test:
        return self_test()
    if not arguments.symbols or not arguments.image:
        parser.error("--symbols and --image are required")

    symbols = parse_symbols(Path(arguments.symbols).read_text(encoding="utf-8", errors="replace"))
    image = Path(arguments.image).read_bytes()
    try:
        value, source, notes = derive(symbols, image, arguments.entry_size, arguments.link_base)
    except ValueError as error:
        print(f"cannot derive {MACRO}: {error}", file=sys.stderr)
        return 1

    print(f"{MACRO} = 0x{value:08x}  (from {source})")
    for note in notes:
        print(f"  {note}")
    if arguments.json:
        import json

        Path(arguments.json).write_text(
            json.dumps(
                {"macro": MACRO, "value": value, "source": source, "notes": notes}, indent=2
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"wrote {arguments.json}")
    if arguments.emit_define:
        print(f"#define {MACRO} 0x{value:08x}ULL  /* {source} */")
    return 0


def self_test() -> int:
    """A synthetic array, including the layouts the search exists to survive."""

    def build(bootid_slot: int, bootid_entry: int, entries: int = 6, text_base: int = 0xFFFF0000):
        image = bytearray(0x4000)
        table_off = 0x1000
        random_table = text_base + table_off
        bootid = text_base + 0x2000
        for entry in range(entries):
            base = table_off + entry * ENTRY_SIZE
            for slot in range(0, ENTRY_SIZE, 8):
                struct.pack_into("<Q", image, base + slot, 0x1111_0000 + entry * 0x100 + slot)
        struct.pack_into("<Q", image, table_off + bootid_entry * ENTRY_SIZE + bootid_slot, bootid)
        # The terminator, and the pointer the search must land on rather than the string it names.
        struct.pack_into("<Q", image, table_off + entries * ENTRY_SIZE, 0)
        image[0x2000:0x2008] = b"boot_id\x00"
        return bytes(image), random_table, text_base, table_off, bootid_entry, bootid_slot

    results: list[tuple[str, bool]] = []

    image, random_table, base, table_off, entry, slot = build(8, 4)
    symbols = {"_text": base, "random_table": random_table, "sysctl_bootid": base + 0x2000}
    value, _source, notes = derive(symbols, image, ENTRY_SIZE)
    results.append(
        ("the value is random_table + the entry's data slot",
         value == table_off + entry * ENTRY_SIZE + slot)
    )

    # A field order the source does not have: `data` at +0x18.
    image2, random_table2, base2, table_off2, entry2, slot2 = build(0x18, 2)
    value2, _s, notes2 = derive(
        {"_text": base2, "random_table": random_table2, "sysctl_bootid": base2 + 0x2000},
        image2, ENTRY_SIZE,
    )
    results.append(
        ("a different index and slot still resolve to the holding slot",
         value2 == table_off2 + entry2 * ENTRY_SIZE + slot2)
    )
    results.append(
        ("a randomised layout is reported rather than assumed away",
         any("randomises" in note for note in notes2))
    )
    results.append(
        ("the source order's slot is not reported as a surprise",
         not any("randomises" in note for note in notes))
    )

    results.append((
        "without sysctl_bootid the string identifies the entry",
        derive({"_text": base, "random_table": random_table}, image, ENTRY_SIZE)[0]
        == table_off + entry * ENTRY_SIZE + slot,
    ))
    results.append((
        "an array with no terminator is refused",
        _refuses(lambda: derive(
            {"_text": base, "random_table": random_table, "sysctl_bootid": base + 0x2000},
            b"\x01" * 0x4000, ENTRY_SIZE,
        )),
    ))
    results.append((
        "a pointer that appears twice is not chosen",
        _refuses(lambda: derive(
            {"_text": base, "random_table": random_table, "sysctl_bootid": base + 0x3000},
            _duplicate(image, base + 0x3000), ENTRY_SIZE,
        )),
    ))
    results.append((
        "an array outside the Image is refused",
        _refuses(lambda: derive(
            {"_text": base, "random_table": 0x10000, "sysctl_bootid": base + 0x2000}, image, ENTRY_SIZE
        )),
    ))
    results.append((
        "a missing random_table is refused",
        _refuses(lambda: derive({"_text": base}, image, ENTRY_SIZE)),
    ))

    for label, passed in results:
        print(f"self-test: {label} {'ok' if passed else 'FAILED'}")
    if all(passed for _, passed in results):
        print("self-test: the entry is found by what it points at, and nothing is guessed")
        return 0
    return 1


def _duplicate(image: bytes, pointer: int) -> bytes:
    copy = bytearray(image)
    struct.pack_into("<Q", copy, 0x1000 + 1 * ENTRY_SIZE + 8, pointer)
    struct.pack_into("<Q", copy, 0x1000 + 3 * ENTRY_SIZE + 8, pointer)
    return bytes(copy)


def _refuses(call) -> bool:
    try:
        call()
    except ValueError:
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
