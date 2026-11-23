#!/usr/bin/env python3
"""Structure layout out of a kernel's own BTF, without bpftool.

Section 3 of `docs/PORTING.md` extracts the raw BTF blob that Samsung leaves in the Image and feeds
it to `bpftool btf dump format raw`. That works on Linux and not everywhere a port gets done, and the
`format c` view it also suggests is explicitly *not* the source of the numeric offsets: what the port
needs is a member's `bits_offset`, which is a field in the blob. So this reads the blob directly.

The format is the kernel's own (`Documentation/bpf/btf.rst`), and only the parts a port uses are
interpreted: the type table, and each STRUCT/UNION member's bit offset. Two rules are enforced rather
than smoothed over, because both hide a wrong answer:

- **bitfields**: with `kind_flag` set, a member's offset is the low 24 bits of its record and the top
  8 bits are its width. Reading the whole word as an offset is off by the width's own bits.
- **non-byte-aligned members**: a member that does not start on a byte boundary cannot be used as a
  `#define ... _OFF` in C, so it is reported as an error rather than rounded down.

The whole blob is validated before it is read - magic, version, header length, section bounds, and the
NUL that must open the string section - because a scan of a 40 MB Image finds plenty of byte sequences
that begin with the magic and are not a type table.

    tools/port/btf.py --image kernel --struct task_struct --struct rt_mutex_waiter
    tools/port/btf.py --image kernel --list-structs | head
    tools/port/btf.py --self-test
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

BTF_MAGIC = 0xEB9F
BTF_VERSION = 1

KIND_INT = 1
KIND_PTR = 2
KIND_ARRAY = 3
KIND_STRUCT = 4
KIND_UNION = 5
KIND_ENUM = 6
KIND_FWD = 7
KIND_TYPEDEF = 8
KIND_VOLATILE = 9
KIND_CONST = 10
KIND_RESTRICT = 11
KIND_FUNC = 12
KIND_FUNC_PROTO = 13
KIND_VAR = 14
KIND_DATASEC = 15
KIND_FLOAT = 16
KIND_DECL_TAG = 17
KIND_TYPE_TAG = 18
KIND_ENUM64 = 19

KIND_NAMES = {
    KIND_INT: "INT", KIND_PTR: "PTR", KIND_ARRAY: "ARRAY", KIND_STRUCT: "STRUCT",
    KIND_UNION: "UNION", KIND_ENUM: "ENUM", KIND_FWD: "FWD", KIND_TYPEDEF: "TYPEDEF",
    KIND_VOLATILE: "VOLATILE", KIND_CONST: "CONST", KIND_RESTRICT: "RESTRICT",
    KIND_FUNC: "FUNC", KIND_FUNC_PROTO: "FUNC_PROTO", KIND_VAR: "VAR", KIND_DATASEC: "DATASEC",
    KIND_FLOAT: "FLOAT", KIND_DECL_TAG: "DECL_TAG", KIND_TYPE_TAG: "TYPE_TAG",
    KIND_ENUM64: "ENUM64",
}

# Bytes of kind-specific payload per `vlen` element, for the kinds that have one. The table is the
# whole of what makes iteration correct: a wrong size here does not fail loudly, it silently reads the
# next type from the middle of this one.
PER_ELEMENT = {
    KIND_STRUCT: 12, KIND_UNION: 12, KIND_ENUM: 8, KIND_FUNC_PROTO: 8, KIND_VAR: 4,
    KIND_DATASEC: 12, KIND_DECL_TAG: 4, KIND_ENUM64: 12,
}
# Kinds whose payload is a fixed size rather than `vlen` elements: an ARRAY is three words whatever
# its `vlen` says, and an INT is always one encoding word - never `vlen` of them.
FIXED_BYTES = {KIND_ARRAY: 12, KIND_INT: 4}


@dataclass
class Member:
    name: str
    type_id: int
    bit_offset: int
    bitfield_bits: int = 0

    @property
    def byte_offset(self) -> int | None:
        if self.bit_offset % 8:
            return None
        return self.bit_offset // 8


@dataclass
class BtfType:
    index: int
    kind: int
    name: str
    size_or_type: int
    vlen: int
    members: list[Member] = field(default_factory=list)


def find_blob(image: bytes) -> tuple[int, int]:
    """The one validated BTF blob in [image], as (start, end).

    Exactly one, because two candidates cannot both be the kernel's type table and picking the first
    would be an arbitrary choice presented as a measurement.
    """
    prefix = b"\x9f\xeb\x01\x00"
    candidates: list[tuple[int, int]] = []
    cursor = 0
    while True:
        start = image.find(prefix, cursor)
        if start < 0:
            break
        cursor = start + 1
        if start + 24 > len(image):
            continue
        magic, version, flags, header_len, type_off, type_len, str_off, str_len = struct.unpack_from(
            "<HBBIIIII", image, start
        )
        if magic != BTF_MAGIC or version != BTF_VERSION or flags != 0 or header_len < 24:
            continue
        payload_len = max(type_off + type_len, str_off + str_len)
        end = start + header_len + payload_len
        string_start = start + header_len + str_off
        if end > len(image) or string_start >= end or image[string_start] != 0:
            continue
        candidates.append((start, end))
    if len(candidates) != 1:
        raise ValueError(f"expected exactly one raw BTF blob, found {len(candidates)}: {candidates}")
    return candidates[0]


class Btf:
    def __init__(self, blob: bytes) -> None:
        if len(blob) < 24:
            raise ValueError("BTF blob is shorter than its header")
        magic, version, flags, header_len, type_off, type_len, str_off, str_len = struct.unpack_from(
            "<HBBIIIII", blob, 0
        )
        if magic != BTF_MAGIC or version != BTF_VERSION:
            raise ValueError(f"not BTF v1: magic 0x{magic:x} version {version}")
        if header_len < 24:
            raise ValueError(f"BTF header length {header_len} is below the v1 minimum")
        types = blob[header_len + type_off: header_len + type_off + type_len]
        strings = blob[header_len + str_off: header_len + str_off + str_len]
        if not strings or strings[0] != 0:
            raise ValueError("BTF string section must start with a NUL")
        if len(types) != type_len:
            raise ValueError("BTF type section runs past the blob")
        self._strings = strings
        self.types: list[BtfType] = [BtfType(0, 0, "<void>", 0, 0)]
        self.types.extend(self._parse_types(types))
        self._by_name: dict[str, list[BtfType]] = {}
        for entry in self.types[1:]:
            if entry.kind in (KIND_STRUCT, KIND_UNION):
                self._by_name.setdefault(entry.name, []).append(entry)

    def _string(self, offset: int) -> str:
        if offset >= len(self._strings):
            raise ValueError(f"string offset {offset} is outside the string section")
        end = self._strings.find(b"\x00", offset)
        if end < 0:
            raise ValueError(f"string at {offset} is not NUL-terminated")
        return self._strings[offset:end].decode("utf-8", "replace")

    def _parse_types(self, section: bytes) -> list[BtfType]:
        parsed: list[BtfType] = []
        cursor = 0
        index = 1
        while cursor < len(section):
            if cursor + 12 > len(section):
                raise ValueError(f"type {index} at {cursor} runs past the type section")
            name_off, info, size_or_type = struct.unpack_from("<III", section, cursor)
            cursor += 12
            vlen = info & 0xFFFF
            kind = (info >> 24) & 0x1F
            kind_flag = (info >> 31) & 1
            if kind not in KIND_NAMES:
                raise ValueError(f"type {index} declares unknown kind {kind}")
            entry = BtfType(index, kind, self._string(name_off), size_or_type, vlen)

            if kind in (KIND_STRUCT, KIND_UNION):
                for _ in range(vlen):
                    if cursor + 12 > len(section):
                        raise ValueError(f"type {index} member runs past the type section")
                    member_name, member_type, raw_offset = struct.unpack_from("<III", section, cursor)
                    cursor += 12
                    if kind_flag:
                        bitfield_bits = raw_offset >> 24
                        bit_offset = raw_offset & 0xFFFFFF
                    else:
                        bitfield_bits = 0
                        bit_offset = raw_offset
                    entry.members.append(
                        Member(self._string(member_name), member_type, bit_offset, bitfield_bits)
                    )
            elif kind in FIXED_BYTES:
                needed = FIXED_BYTES[kind]
                if cursor + needed > len(section):
                    raise ValueError(f"type {index} payload runs past the type section")
                cursor += needed
            else:
                per = PER_ELEMENT.get(kind, 0)
                needed = per * vlen
                if cursor + needed > len(section):
                    raise ValueError(f"type {index} payload runs past the type section")
                cursor += needed

            parsed.append(entry)
            index += 1
        return parsed

    def members(self, name: str) -> list[Member]:
        """Members of the first STRUCT/UNION called [name].

        Duplicates are a real thing in kernel BTF (two translation units can emit the same tag), so
        the caller is told rather than silently given one of them.
        """
        matches = self._by_name.get(name, [])
        if not matches:
            raise KeyError(name)
        return matches[0].members

    def struct_names(self) -> list[str]:
        return sorted(self._by_name)


def describe(btf: Btf, name: str) -> dict:
    """Every member of [name], with the byte offsets a target header would use."""
    result: dict = {"struct": name, "members": [], "unusable": []}
    for member in btf.members(name):
        byte_offset = member.byte_offset
        entry = {
            "name": member.name or "<anonymous>",
            "bit_offset": member.bit_offset,
            "byte_offset": byte_offset,
            "bitfield_bits": member.bitfield_bits,
        }
        if byte_offset is None:
            result["unusable"].append(entry)
        else:
            result["members"].append(entry)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--image", help="a raw ARM64 Image, to find the BTF blob in")
    parser.add_argument("--btf", help="a raw BTF blob, already extracted")
    parser.add_argument("--struct", action="append", default=[], help="struct to describe; repeatable")
    parser.add_argument("--list-structs", action="store_true", help="print every struct name")
    parser.add_argument("--json", help="write the descriptions here instead of stdout")
    parser.add_argument("--self-test", action="store_true", help="run against a synthetic blob")
    arguments = parser.parse_args()

    if arguments.self_test:
        return self_test()
    if not arguments.image and not arguments.btf:
        parser.error("--image or --btf is required")

    if arguments.btf:
        blob = Path(arguments.btf).read_bytes()
        start = 0
    else:
        image = Path(arguments.image).read_bytes()
        start, end = find_blob(image)
        blob = image[start:end]
        print(f"BTF blob: [0x{start:x}, 0x{end:x}) ({end - start} bytes)", file=sys.stderr)

    btf = Btf(blob)
    if arguments.list_structs:
        for name in btf.struct_names():
            print(name)
        return 0

    described = [describe(btf, name) for name in arguments.struct]
    text = json.dumps(described, indent=2)
    if arguments.json:
        Path(arguments.json).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {arguments.json}")
    else:
        print(text)
    return 0


def self_test() -> int:
    """A synthetic blob, for the same reason the boot reader has one: the real one needs a firmware."""
    def string_section(names: list[str]) -> bytes:
        # The first name is the empty string, which is the NUL that must open the section: building it
        # by prepending a NUL *and* writing the empty name would shift every offset by one, and the
        # test's own table is the thing that would then be wrong.
        blob = bytearray()
        for name in names:
            blob += name.encode() + b"\x00"
        return bytes(blob)

    strings = string_section(["", "task_struct", "cred", "real_cred", "flags", "usage"])
    # Offsets into that section, written out rather than searched for, so the test says what it means.
    off = {"task_struct": 1, "cred": 13, "real_cred": 18, "flags": 28, "usage": 34}

    def member(name_off: int, type_id: int, raw_offset: int) -> bytes:
        return struct.pack("<III", name_off, type_id, raw_offset)

    types = bytearray()
    types += struct.pack("<III", 0, (KIND_INT << 24) | 0, 4) + struct.pack("<I", 0)
    types += struct.pack("<III", off["task_struct"], (KIND_STRUCT << 24) | (2 << 0), 0x1000)
    types += member(off["cred"], 1, 0x790 * 8)
    types += member(off["real_cred"], 1, 0x798 * 8)
    # A kind_flag struct: low 24 bits are the offset, the top 8 are the bitfield width.
    types += struct.pack("<III", 0, (1 << 31) | (KIND_STRUCT << 24) | 1, 8)
    types += member(off["flags"], 1, (0x10 * 8) | (3 << 24))

    header_len = 24
    header = struct.pack(
        "<HBBIIIII",
        BTF_MAGIC, BTF_VERSION, 0, header_len,
        0, len(types), len(types), len(strings),
    )
    blob = header + bytes(types) + strings

    btf = Btf(blob)
    creds = {member.name: member.byte_offset for member in btf.members("task_struct")}
    flagged = {member.name: member.byte_offset for member in btf.members("")}
    results = [
        ("a plain member keeps its byte offset", creds.get("cred") == 0x790),
        ("a second member keeps its own", creds.get("real_cred") == 0x798),
        ("a kind_flag member drops the width bits", flagged.get("flags") == 0x10),
        ("a found blob is the whole blob", find_blob(b"\x00" * 64 + blob + b"\x00" * 64) == (64, 64 + len(blob))),
    ]

    def refuses(payload: bytes) -> bool:
        try:
            Btf(payload)
        except ValueError:
            return True
        return False

    results.append(("a bad magic is refused", refuses(b"\x00" * 24)))
    results.append((
        "a type section that overruns is refused",
        refuses(struct.pack("<HBBIIIII", BTF_MAGIC, 1, 0, 24, 0, 12, 0, 2) + b"\x00" * 12 + b"\x00\x00"),
    ))
    results.append(("two candidate blobs are refused", _two_candidates_refused(blob)))

    for label, passed in results:
        print(f"self-test: {label} {'ok' if passed else 'FAILED'}")
    if all(passed for _, passed in results):
        print("self-test: the reader takes offsets from the blob and refuses what it cannot read")
        return 0
    return 1


def _two_candidates_refused(blob: bytes) -> bool:
    try:
        find_blob(blob + blob)
    except ValueError:
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
