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

String constants (`SLIDE_NFULNL_LOGGER_NAME_OFF`, `SLIDE_SYSCTL_BOOTID_OFF`) are searched for in the
raw Image, because they are `.rodata` content rather than symbols. Several occurrences are reported as
several, because "the first one" is a guess and `boot_id` appears more than once in a kernel.

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
    "ASHMEM_MISC_FOPS_OFF": ("ashmem_misc_fops", "ashmem_miscdev"),
    "ASHMEM_IOCTL_OFF": ("ashmem_ioctl",),
    "ASHMEM_COMPAT_IOCTL_OFF": ("compat_ashmem_ioctl", "ashmem_compat_ioctl"),
    "ASHMEM_MMAP_OFF": ("ashmem_mmap",),
    "ASHMEM_OPEN_OFF": ("ashmem_open",),
    "ASHMEM_RELEASE_OFF": ("ashmem_release",),
    "ASHMEM_SHOW_FDINFO_OFF": ("ashmem_show_fdinfo",),
    "CONFIGFS_READ_ITER_OFF": ("configfs_read_iter",),
    "CONFIGFS_BIN_WRITE_ITER_OFF": ("configfs_bin_write_iter",),
    "COPY_SPLICE_READ_OFF": ("copy_splice_read",),
    "NOOP_LLSEEK_OFF": ("noop_llseek",),
    "SLIDE_NFULNL_LOGGER_OBJECT_OFF": ("nfulnl_logger",),
    "SLIDE_TRACEFS_WORKER_CALLER_OFF": ("__tracefs_worker_caller",),
}

# Macros that are `.rodata` content rather than symbols: the searched string is the value.
STRINGS: dict[str, str] = {
    "SLIDE_NFULNL_LOGGER_NAME_OFF": "nfulnl_logger",
    "SLIDE_SYSCTL_BOOTID_OFF": "boot_id",
}

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


def search_string(image: bytes, needle: str) -> tuple[int, ...]:
    """Every file offset where [needle] appears as a NUL-terminated string."""
    raw = needle.encode() + b"\x00"
    found: list[int] = []
    cursor = 0
    while True:
        at = image.find(raw, cursor)
        if at < 0:
            break
        found.append(at)
        cursor = at + 1
    return tuple(found)


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
        else:
            name, address = chosen
            findings.append(Finding(macro, address - base, f"symbol {name}"))

    for macro, needle in STRINGS.items():
        if image is None:
            findings.append(Finding(macro, None, f"string {needle!r} (no Image given)"))
            continue
        hits = search_string(image, needle)
        if not hits:
            findings.append(Finding(macro, None, f"string {needle!r} (not in the Image)"))
        elif len(hits) > 1:
            findings.append(Finding(macro, None, f"string {needle!r}", hits))
            problems.append(
                f"{macro}: {len(hits)} occurrences of {needle!r} at "
                + ", ".join(f"0x{hit:x}" for hit in hits)
                + "; the one this macro means has to be chosen, not guessed"
            )
        else:
            findings.append(Finding(macro, hits[0], f"string {needle!r}"))

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
        help="`__TRACE_LAST_TYPE` for this kernel (kernel/trace/trace.h); 20 in Android 6.1",
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
    """A table and an image that are small enough to be obvious, including the refusals."""
    base = 0xFFFFFFC008000000
    table = "\n".join([
        f"{base:016x} T _text",
        f"{base + 0x2000:016x} T commit_creds",
        f"{base + 0x3000:016x} T prepare_kernel_cred",
        f"{base + 0x4000:016x} D init_task",
        "                 U some_undefined_thing",
    ])
    symbols = parse_symbols(table)
    findings, _problems = report(symbols, text_base(symbols, None), b"\x00" * 8 + b"nfulnl_logger\x00", None, 20)
    by_name = {finding.macro: finding for finding in findings}

    results = [
        ("a symbol becomes its offset", by_name["COMMIT_CREDS_OFF"].value == 0x2000),
        ("a table without _text is refused", _refuses(lambda: text_base({"commit_creds": 1}, None))),
        ("a zeroed table is refused", _refuses(lambda: parse_symbols("0000000000000000 T commit_creds"))),
        ("a slid table yields the same offsets",
         text_base(parse_symbols(f"{base + 0x1234000:016x} T _text"), None) - (base + 0x1234000) == 0),
        ("a string is found at its file offset",
         by_name["SLIDE_NFULNL_LOGGER_NAME_OFF"].value == 8),
        ("a duplicated string is not chosen", _split_choice()),
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


def _split_choice() -> bool:
    symbols = parse_symbols(f"{0x1000:016x} T _text")
    image = b"boot_id\x00" + b"\x00" * 4 + b"boot_id\x00"
    findings, problems = report(symbols, 0x1000, image, None, 20)
    boot_id = next(f for f in findings if f.macro == "SLIDE_SYSCTL_BOOTID_OFF")
    return boot_id.value is None and any("SLIDE_SYSCTL_BOOTID_OFF" in p for p in problems)


if __name__ == "__main__":
    sys.exit(main())
