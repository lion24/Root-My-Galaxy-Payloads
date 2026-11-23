#!/usr/bin/env python3
"""Every firmware-dependent constant a target header needs, measured instead of copied.

Sections 3 to 5 of `docs/PORTING.md` derive a target by hand: recover symbols, dump BTF, disassemble
two functions, generate the P0 table, then type the numbers into `target.h`. That is fine once, and it
is how the existing targets were made. It is a poor way to make the second one, because the numbers are
the same *kind* of number every time and the only thing that changes is the kernel.

This tool does the symbol half. It takes a symbol table - from `vmlinux-to-elf` + `llvm-nm` on the raw
Image, or straight from `/proc/kallsyms` on a device that is already rooted - and answers, for every
`*_OFF` macro in a reference target header, what this kernel's value is.

Two properties are what make it worth having:

- **Offsets, not addresses.** Every constant is measured as `symbol - _text`, which is invariant under
  KASLR. That is why `/proc/kallsyms` works as an input even though it prints slid addresses: the
  subtraction removes the slide, and a device that is rooted can answer for the kernel it is running
  rather than for a file that may or may not be the same build.
- **It refuses rather than guesses.** A symbol table with zeroed addresses (kptr_restrict), a missing
  `_text`, an offset that lands outside the Image, or a symbol the header names that this kernel does
  not export is reported as a failure with the missing names listed - not silently left at the
  reference value, which is how a port ends up writing to the wrong kernel address.

Three of the macros are not a symbol's own address, and each is measured the way its kernel makes it
identifiable:

- **`SLIDE_SYSCTL_BOOTID_OFF`** is `sysctl_bootid`, the `static` UUID storage that `random_table`'s
  `boot_id` entry points at. It is a symbol here only because this build exports `static`s
  (`CONFIG_KALLSYMS_ALL`); a build that does not export it is reported as unresolved, *not* answered
  by the `boot_id` string - that string is the entry's `procname`, and its offset is in `.rodata`,
  several megabytes from the storage the macro means.
- **`SLIDE_NFULNL_LOGGER_NAME_OFF`** is the string that `nfulnl_logger`'s first qword points at, read
  out of the Image. Searching `.rodata` for a name would be searching for the wrong name: the logger
  symbol is `nfulnl_logger` and the string it holds is `"nfnetlink_log"`.
- **`ASHMEM_MISC_FOPS_OFF`** is a *field*, not an object: the `fops` slot of the `miscdevice`
  registered as `"ashmem"` (the exploit writes a fake table there). It is found by the `ashmem_fops`
  pointer the slot holds, because the miscdevice's own symbol is named `ashmem_misc` in this tree,
  `ashmem_miscs` in another port and `ashmem_miscdev` in another - a hardcoded symbol list found
  none of them on this kernel.

    tools/port/target_report.py --symbols vmlinux.nm --image kernel \
        --reference src/targets/dm2q-S916BXXSAFZG1/target.h
    tools/port/target_report.py --symbols kallsyms.txt --trace-event-id 108 --emit-defines
    tools/port/target_report.py --self-test
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

# `#define NAME 0x...ULL` lines in a target header, which is what a report is compared against.
DEFINE = re.compile(r"^#define\s+([A-Z][A-Z0-9_]*)\s+(0x[0-9a-fA-F]+)(?:ULL|UL|U|L)?\b", re.MULTILINE)

# Macro -> the symbols that could carry it, most likely first. Named here rather than inferred from
# the header because the mapping is knowledge about the kernel, not a property of the text: the header
# says `ASHMEM_IOCTL_OFF`, and nothing in that name says which symbol to look up.
SYMBOLS: dict[str, tuple[str, ...]] = {
    "INIT_TASK_OFF": ("init_task",),
    "PREPARE_KERNEL_CRED_OFF": ("prepare_kernel_cred",),
    "COMMIT_CREDS_OFF": ("commit_creds",),
    "OVERRIDE_CREDS_OFF": ("override_creds",),
    "ROOT_TASK_GROUP_OFF": ("root_task_group",),
    # 5.15 moved SELinux's enforcing flag inside `struct selinux_state`; some trees still export the
    # old variable. Both are tried, and which one answered is in the report.
    "SELINUX_ENFORCING_OFF": ("selinux_enforcing", "selinux_state"),
    "KMALLOC_CACHES_OFF": ("kmalloc_caches",),
    "ANON_PIPE_BUF_OPS_OFF": ("anon_pipe_buf_ops",),
    "SYSTEM_UNBOUND_WQ_OFF": ("system_unbound_wq",),
    "CALL_USERMODEHELPER_EXEC_WORK_OFF": ("call_usermodehelper_exec_work",),
    "ASHMEM_FOPS_OFF": ("ashmem_fops",),
    "ASHMEM_IOCTL_OFF": ("ashmem_ioctl",),
    "ASHMEM_COMPAT_IOCTL_OFF": ("compat_ashmem_ioctl", "ashmem_compat_ioctl"),
    "ASHMEM_MMAP_OFF": ("ashmem_mmap",),
    "ASHMEM_OPEN_OFF": ("ashmem_open",),
    "ASHMEM_RELEASE_OFF": ("ashmem_release",),
    "ASHMEM_SHOW_FDINFO_OFF": ("ashmem_show_fdinfo",),
    "CONFIGFS_READ_ITER_OFF": ("configfs_read_iter",),
    "CONFIGFS_BIN_WRITE_ITER_OFF": ("configfs_bin_write_iter",),
    # The fops slot the fake table installs as `->splice_read`. 6.5 replaced `generic_file_splice_read`
    # with `copy_splice_read` and the stable backports followed, so which of the two exists is a
    # property of the tree, not of the macro: both are "the file's splice_read implementation", and the
    # report names the one that answered.
    "COPY_SPLICE_READ_OFF": ("copy_splice_read", "generic_file_splice_read"),
    "NOOP_LLSEEK_OFF": ("noop_llseek",),
    "SLIDE_NFULNL_LOGGER_OBJECT_OFF": ("nfulnl_logger",),
    # `.data`, not `.rodata`: the storage `random_table`'s `boot_id` entry points at, i.e. the UUID the
    # slide overwrites and restores. Only a `CONFIG_KALLSYMS_ALL` build gives it a symbol.
    "SLIDE_SYSCTL_BOOTID_OFF": ("sysctl_bootid",),
    "SLIDE_TRACEFS_WORKER_CALLER_OFF": ("__tracefs_worker_caller",),
}

# A macro whose value is a field inside an object is not in `SYMBOLS`: the number is the address of the
# field, and no symbol names it. Each is measured below by what the field holds.
ASHMEM_MISC_NAME = b"ashmem"

# `struct miscdevice { int minor; const char *name; const struct file_operations *fops; ... }` on 5.15.
# They are not `__randomize_layout`, and the search verifies both of them against the Image, so a tree
# that moves them fails here instead of reporting a slot inside an unrelated global.
MISCDEVICE_NAME_OFF = 0x08
MISCDEVICE_FOPS_OFF = 0x10

# The trace event these macros describe, and what has to be there for the arithmetic to be meaningful.
TRACE_EVENTS = ("__start_ftrace_events", "__stop_ftrace_events", "__event_sched_blocked_reason")


@dataclass
class Finding:
    macro: str
    value: int | None
    source: str
    candidates: tuple[int, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.value is not None


def parse_symbols(text: str) -> dict[str, int]:
    """Symbol table out of `llvm-nm -n` or `/proc/kallsyms`.

    Both print `<address> <type> <name>`, and kallsyms prints module symbols with a trailing `[module]`
    and undefined ones with a blank address, so the parse is the same shape with two refusals: an
    undefined symbol is not an address, and a zeroed address is kptr_restrict rather than offset zero.
    """
    symbols: dict[str, int] = {}
    zeroed = 0
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[0] == "U" or fields[1] == "U":
            continue
        address_text, _kind, name = fields[0], fields[1], fields[2]
        if not re.fullmatch(r"[0-9a-fA-F]+", address_text):
            continue
        address = int(address_text, 16)
        if address == 0:
            zeroed += 1
            continue
        symbols[name] = address
    if not symbols:
        raise ValueError(
            "no symbol records parsed from this table"
            + ("; every address is zero (kptr_restrict), which is a refusal rather than an answer"
               if zeroed else "")
        )
    return symbols


def text_base(symbols: dict[str, int], override: int | None) -> int:
    """The address every offset is measured from.

    `_text` is the only honest default: the porting document's offsets are symbol minus image text
    base, and the table it recovers carries `_text` itself. An override exists for a table that named
    the image base differently, and is checked to be page-aligned so a typo cannot become a port.
    """
    if override is not None:
        if override & 0xFFF:
            raise ValueError(f"text base 0x{override:x} is not page-aligned")
        return override
    if "_text" not in symbols:
        raise ValueError("the symbol table has no `_text`: offsets cannot be measured from a guess")
    return symbols["_text"]


def image_offset(base: int, address: int, image: bytes) -> int | None:
    """The file offset a link-time address has in the Image, or None when it lands outside it.

    Every pointer a port follows is checked with this first: a value that does not land inside the
    Image is a pointer into this kernel's `.bss`, a symbol from a different build, or an offset
    measured from the wrong base, and each of those is a refusal rather than an answer.
    """
    offset = address - base
    return offset if 0 <= offset < len(image) else None


def c_string(image: bytes, offset: int, limit: int = 96) -> bytes | None:
    """The NUL-terminated printable string at [offset], or None when there is not one.

    The printability check is the evidence that the pointer followed really is a `char *`: a wrong
    slot would otherwise resolve to a plausible offset inside an unrelated string and produce a
    `_text` that is wrong by a constant, which is the failure the whole slide exists to avoid.
    """
    if offset < 0 or offset >= len(image):
        return None
    end = image.find(b"\x00", offset, min(offset + limit, len(image)))
    if end <= offset:
        return None
    text = image[offset:end]
    return text if all(0x20 <= byte < 0x7F for byte in text) else None


def logger_name(
    symbols: dict[str, int], base: int, image: bytes | None
) -> tuple[Finding, str | None]:
    """`SLIDE_NFULNL_LOGGER_NAME_OFF`: the string `nfulnl_logger`'s first member points at.

    `struct nf_logger` begins with `char *name`, so the object *is* a pointer to the string the slide
    needs: the exploit leaks that pointer from the kernel, subtracts this offset and recovers `_text`.
    The string is not searched for, because the name a reader would guess is the symbol's
    (`nfulnl_logger`) and the name the kernel holds is the logger's (`nfnetlink_log`); only the pointer
    in the object says which of the two `.rodata` strings is the one the leak will produce.
    """
    macro = "SLIDE_NFULNL_LOGGER_NAME_OFF"
    if image is None:
        return Finding(macro, None, "no Image given"), None
    if "nfulnl_logger" not in symbols:
        return Finding(macro, None, "symbol nfulnl_logger"), None
    at = image_offset(base, symbols["nfulnl_logger"], image)
    if at is None or at + 8 > len(image):
        return Finding(macro, None, "symbol nfulnl_logger (outside the stored Image)"), None
    pointer = struct.unpack_from("<Q", image, at)[0]
    target = image_offset(base, pointer, image)
    text = c_string(image, target) if target is not None else None
    if text is None:
        return (
            Finding(macro, None, f"`nfulnl_logger`+0x0 = 0x{pointer:016x}"),
            f"{macro}: `nfulnl_logger`+0x0 is 0x{pointer:016x}, which does not point at a string "
            "inside the Image; `struct nf_logger`'s first member is `char *name`, so this symbol is "
            "not the object the macro assumes and its qword zero cannot be the leak's string",
        )
    return Finding(macro, target, f'`nfulnl_logger.name` -> "{text.decode()}"'), None


def ashmem_misc_fops(
    symbols: dict[str, int], base: int, image: bytes | None
) -> tuple[Finding, str | None]:
    """`ASHMEM_MISC_FOPS_OFF`: the `fops` slot of the `miscdevice` registered as `"ashmem"`.

    The chain overwrites this slot, so it needs the address of a *field*, and no symbol names a field.
    It is found by what the field holds: an occurrence of the address of `ashmem_fops` whose
    `name` field points at the string `"ashmem"`. Matching the miscdevice's symbol instead is what
    the porting documents do by hand, and it is why they had to write
    `ashmem_miscs + offsetof(miscdevice, fops)` - the static is called `ashmem_misc` here and
    `ashmem_miscs` there, and this kernel would have had no hit at all.
    """
    macro = "ASHMEM_MISC_FOPS_OFF"
    if image is None:
        return Finding(macro, None, "no Image given"), None
    if "ashmem_fops" not in symbols:
        return Finding(macro, None, "symbol ashmem_fops"), None
    needle = struct.pack("<Q", symbols["ashmem_fops"])
    hits: list[int] = []
    cursor = 0
    while True:
        at = image.find(needle, cursor)
        if at < 0:
            break
        cursor = at + 1
        if at % 8 or at < MISCDEVICE_NAME_OFF:
            continue
        name_pointer = struct.unpack_from("<Q", image, at - MISCDEVICE_NAME_OFF)[0]
        target = image_offset(base, name_pointer, image)
        if target is not None and c_string(image, target) == ASHMEM_MISC_NAME:
            hits.append(at)
    if len(hits) == 1:
        return Finding(macro, hits[0], '`ashmem_fops` pointer in the `"ashmem"` miscdevice'), None
    if not hits:
        return Finding(macro, None, "ashmem miscdevice"), (
            f"{macro}: no 8-byte-aligned occurrence of `ashmem_fops` has a `name` of "
            f'"{ASHMEM_MISC_NAME.decode()}" at -0x{MISCDEVICE_NAME_OFF:x}, so either this build'
            " orders `struct miscdevice` differently or the object is in `.bss`, which a boot dump"
            " does not store; measure `ashmem_misc* + offsetof(miscdevice, fops)` from BTF and pass"
            " it with --extra"
        )
    return Finding(macro, None, "ashmem miscdevice", tuple(hits)), (
        f"{macro}: {len(hits)} slots hold `ashmem_fops` and are named "
        f'"{ASHMEM_MISC_NAME.decode()}" at '
        + ", ".join(f"0x{hit:x}" for hit in hits)
        + "; the one the macro means has to be chosen, not guessed"
    )


def report(
    symbols: dict[str, int],
    base: int,
    image: bytes | None,
    trace_event_id: int | None,
    trace_last_type: int,
) -> tuple[list[Finding], list[str]]:
    """One finding per macro this tool knows how to measure, and the problems with what it could not."""
    findings: list[Finding] = []
    problems: list[str] = []

    for macro, candidates in SYMBOLS.items():
        if macro == "SLIDE_TRACEFS_WORKER_CALLER_OFF":
            continue  # derived below, from the event table rather than a symbol
        chosen = next(((name, symbols[name]) for name in candidates if name in symbols), None)
        if chosen is None:
            findings.append(Finding(macro, None, "symbol " + "/".join(candidates)))
            if macro == "SLIDE_SYSCTL_BOOTID_OFF":
                problems.append(
                    "SLIDE_SYSCTL_BOOTID_OFF: this build does not export `sysctl_bootid`; its address "
                    "is the pointer `random_table`'s `boot_id` entry holds, i.e. the value at the slot "
                    "`tools/port/random_table.py` locates, so read that pointer out of the Image and "
                    "pass it with --extra"
                )
        else:
            name, address = chosen
            findings.append(Finding(macro, address - base, f"symbol {name}"))

    for measure in (logger_name, ashmem_misc_fops):
        finding, problem = measure(symbols, base, image)
        findings.append(finding)
        if problem:
            problems.append(problem)

    missing = [name for name in TRACE_EVENTS if name not in symbols]
    if trace_event_id is not None:
        findings.append(Finding("SLIDE_TRACEFS_EVENT_ID", trace_event_id, "read on the device"))
    elif missing:
        findings.append(Finding("SLIDE_TRACEFS_EVENT_ID", None, "event table symbols: " + ", ".join(missing)))
        problems.append(
            "SLIDE_TRACEFS_EVENT_ID: the event table is not in this symbol table; read it from a "
            "running device (`cat /sys/kernel/tracing/events/sched/sched_blocked_reason/id`) and pass "
            "--trace-event-id"
        )
    else:
        index = (symbols["__event_sched_blocked_reason"] - symbols["__start_ftrace_events"]) // 8
        if index < 0 or symbols["__event_sched_blocked_reason"] >= symbols["__stop_ftrace_events"]:
            problems.append("SLIDE_TRACEFS_EVENT_ID: the event is outside the ftrace event section")
            findings.append(Finding("SLIDE_TRACEFS_EVENT_ID", None, "event table"))
        else:
            findings.append(
                Finding(
                    "SLIDE_TRACEFS_EVENT_ID",
                    trace_last_type + index,
                    f"__TRACE_LAST_TYPE({trace_last_type}) + index({index})",
                )
            )

    return findings, problems


def read_defines(path: Path) -> dict[str, int]:
    text = path.read_text(encoding="utf-8")
    return {name: int(value, 16) for name, value in DEFINE.findall(text)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbols", help="an `llvm-nm -n` dump or `/proc/kallsyms`")
    parser.add_argument("--image", help="the raw ARM64 Image, for the string constants")
    parser.add_argument("--reference", help="a target.h to compare against")
    parser.add_argument("--text-base", help="override the base offsets are measured from, as hex")
    parser.add_argument("--trace-event-id", type=int, help="the event id read on the device")
    parser.add_argument(
        "--trace-last-type",
        type=int,
        default=20,
        help="`__TRACE_LAST_TYPE` for this kernel (kernel/trace/trace.h). 20 for ACK "
             "android13-5.15.197_r00, whose enum has 19 members after `__TRACE_FIRST_TYPE`; the "
             "porting document quotes the same number for 6.1, which is a coincidence, not a rule",
    )
    parser.add_argument("--json", help="write the findings here")
    parser.add_argument(
        "--extra",
        action="append",
        default=[],
        metavar="MACRO=0xVALUE",
        help="a value measured some other way (e.g. derived from the OSRC source); repeatable",
    )
    parser.add_argument("--grep", help="print symbols matching this regular expression, with offsets")
    parser.add_argument("--emit-defines", action="store_true", help="print the resolved values as C")
    parser.add_argument("--self-test", action="store_true", help="run against a synthetic table")
    arguments = parser.parse_args()

    if arguments.self_test:
        return self_test()
    if not arguments.symbols:
        parser.error("--symbols is required")

    symbols = parse_symbols(Path(arguments.symbols).read_text(encoding="utf-8"))
    base = text_base(symbols, int(arguments.text_base, 16) if arguments.text_base else None)
    image = Path(arguments.image).read_bytes() if arguments.image else None
    findings, problems = report(symbols, base, image, arguments.trace_event_id, arguments.trace_last_type)
    for given in arguments.extra:
        name, _, raw = given.partition("=")
        if not name or not raw:
            parser.error(f"--extra wants MACRO=0xVALUE, got {given!r}")
        findings = [finding for finding in findings if finding.macro != name]
        findings.append(Finding(name, int(raw, 16), "given on the command line"))
        problems = [problem for problem in problems if not problem.startswith(name + ":")]

    if arguments.grep:
        pattern = re.compile(arguments.grep)
        print("matching symbols:")
        for name in sorted(symbols):
            if pattern.search(name):
                print(f"  {name:48s} 0x{symbols[name] - base:08x}")
        return 0

    reference = read_defines(Path(arguments.reference)) if arguments.reference else {}
    print(f"text base: 0x{base:016x} ({len(symbols)} symbols)")
    print(f"{'macro':42s} {'measured':>12s}  {'reference':>12s}  source")
    for finding in findings:
        measured = f"0x{finding.value:08x}" if finding.resolved else "-"
        known = reference.get(finding.macro)
        shown = f"0x{known:08x}" if known is not None else "-"
        verdict = ""
        if known is not None and finding.resolved:
            verdict = "same" if known == finding.value else "CHANGED"
        print(f"{finding.macro:42s} {measured:>12s}  {shown:>12s}  {finding.source} {verdict}")

    unresolved = [finding.macro for finding in findings if not finding.resolved]
    if problems:
        print("\nneeds a decision:")
        for problem in problems:
            print(f"  - {problem}")
    if unresolved:
        print("\nunresolved: " + ", ".join(unresolved))

    payload = {
        "text_base": base,
        "symbols": len(symbols),
        "findings": [
            {
                "macro": finding.macro,
                "value": finding.value,
                "source": finding.source,
                "candidates": list(finding.candidates),
            }
            for finding in findings
        ],
        "problems": problems,
        "unresolved": unresolved,
    }
    if arguments.json:
        Path(arguments.json).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {arguments.json}")
    if arguments.emit_defines:
        for finding in findings:
            if finding.resolved:
                print(f"#define {finding.macro} 0x{finding.value:08x}ULL")
    return 0 if not problems and not unresolved else 1


def self_test() -> int:
    """A table and an image small enough to be obvious, including the refusals."""
    base = 0xFFFFFFC008000000

    def build(logger_target: int = 0x3000, extra_miscdevice: bool = False) -> bytes:
        """The image a target's data looks like: an object holding a string pointer, a miscdevice, junk."""
        image = bytearray(0x4000)
        struct.pack_into("<Q", image, 0x2000, base + logger_target)  # nfulnl_logger.name
        struct.pack_into("<Q", image, 0x2008, 1)  # nf_logger.type, so +0x0 is not the whole object
        for at in ((0x2400, 0x2600) if extra_miscdevice else (0x2400,)):
            struct.pack_into("<Q", image, at + MISCDEVICE_NAME_OFF, base + 0x3020)
            struct.pack_into("<Q", image, at + MISCDEVICE_FOPS_OFF, base + 0x1200)
        struct.pack_into("<Q", image, 0x2500, base + 0x1200)  # a bare pointer, with no name in front
        image[0x3000:0x300e] = b"nfnetlink_log\x00"
        image[0x3020:0x3027] = b"ashmem\x00"
        image[0x3030:0x3040] = b"0123456789abcdef"  # sysctl_bootid's UUID storage
        image[0x3040:0x3048] = b"boot_id\x00"  # the entry's procname, which the macro does not mean
        return bytes(image)

    table = "\n".join([
        f"{base:016x} T _text",
        f"{base + 0x1000:016x} T commit_creds",
        f"{base + 0x1100:016x} T prepare_kernel_cred",
        f"{base + 0x1200:016x} t ashmem_fops",
        f"{base + 0x1300:016x} T generic_file_splice_read",
        f"{base + 0x2000:016x} d nfulnl_logger",
        f"{base + 0x3030:016x} b sysctl_bootid",
        "                 U some_undefined_thing",
    ])
    symbols = parse_symbols(table)
    image = build()
    findings, _problems = report(symbols, text_base(symbols, None), image, None, 20)
    by_name = {finding.macro: finding for finding in findings}
    missing_bootid, missing_problems = report(
        {name: value for name, value in symbols.items() if name != "sysctl_bootid"},
        base, image, None, 20,
    )
    bad_logger, bad_logger_problem = logger_name(symbols, base, build(logger_target=0x2008))
    ambiguous, ambiguous_problem = ashmem_misc_fops(symbols, base, build(extra_miscdevice=True))

    results = [
        ("a symbol becomes its offset", by_name["COMMIT_CREDS_OFF"].value == 0x1000),
        ("a table without _text is refused", _refuses(lambda: text_base({"commit_creds": 1}, None))),
        ("a zeroed table is refused", _refuses(lambda: parse_symbols("0000000000000000 T commit_creds"))),
        ("a slid table yields the same offsets",
         text_base(parse_symbols(f"{base + 0x1234000:016x} T _text"), None) - (base + 0x1234000) == 0),
        ("the logger's name is the string its first qword points at",
         by_name["SLIDE_NFULNL_LOGGER_NAME_OFF"].value == 0x3000
         and "nfnetlink_log" in by_name["SLIDE_NFULNL_LOGGER_NAME_OFF"].source),
        ("a qword zero that is not a string pointer is refused",
         bad_logger.value is None and bad_logger_problem is not None),
        ("the miscdevice is found by the fops it holds, not by a bare pointer",
         by_name["ASHMEM_MISC_FOPS_OFF"].value == 0x2410),
        ("a second miscdevice named ashmem is not chosen",
         ambiguous.value is None and len(ambiguous.candidates) == 2 and ambiguous_problem is not None),
        ("sysctl_bootid is the storage, not the procname string",
         by_name["SLIDE_SYSCTL_BOOTID_OFF"].value == 0x3030),
        ("without sysctl_bootid the macro is reported, not answered by the string",
         next(f for f in missing_bootid if f.macro == "SLIDE_SYSCTL_BOOTID_OFF").value is None
         and any("SLIDE_SYSCTL_BOOTID_OFF" in problem for problem in missing_problems)),
        ("a renamed splice_read is reported by the name that answered",
         by_name["COPY_SPLICE_READ_OFF"].value == 0x1300
         and "generic_file_splice_read" in by_name["COPY_SPLICE_READ_OFF"].source),
        ("a missing symbol is reported, not defaulted", by_name["NOOP_LLSEEK_OFF"].value is None),
        ("a missing event table asks for the device value",
         by_name["SLIDE_TRACEFS_EVENT_ID"].value is None),
    ]
    for label, passed in results:
        print(f"self-test: {label} {'ok' if passed else 'FAILED'}")
    if all(passed for _, passed in results):
        print("self-test: the report measures offsets and refuses to guess the rest")
        return 0
    return 1


def _refuses(call) -> bool:
    try:
        call()
    except ValueError:
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
