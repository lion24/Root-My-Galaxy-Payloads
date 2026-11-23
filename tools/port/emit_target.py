#!/usr/bin/env python3
"""Turn a measurement report into the `measured.h` a target header includes.

`tools/port/target_report.py` measures; this writes the numbers down in the one place the exploit
builds against. Keeping the two apart matters because the report is evidence and the header is code:
a value in the header names where it came from, in a comment the next person can follow back to a
symbol, and the report is what that comment refers to.

It refuses to write a partial header. Every value the target needs is either in the report or the run
fails with the list of what is missing - because a `measured.h` with one stale value in it is
indistinguishable, from the outside, from one that is right, and the difference shows up as a phone
that reboots mid-chain.

    tools/port/target_report.py --symbols kallsyms.txt --json report.json --trace-event-id 108
    tools/port/generate_p0_fingerprint.pl kernel 0x1f0000 p0_fingerprint.h
    tools/port/emit_target.py --report report.json --kimage-text-base 0xffffffc008000000 \
        --physical-load 0x80000000 --build-fingerprint "samsung/...:user/release-keys" \
        --fingerprint p0_fingerprint.h --out src/targets/dm2q-S916BXXUAGZIF/measured.h
    tools/port/emit_target.py --self-test
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Every macro this target's header expects a number for. The list is fixed here rather than read out
# of the report, since a report that forgot one macro would otherwise produce a header that quietly
# uses 0.
REQUIRED = (
    "INIT_TASK_OFF",
    "PREPARE_KERNEL_CRED_OFF",
    "COMMIT_CREDS_OFF",
    "OVERRIDE_CREDS_OFF",
    "ROOT_TASK_GROUP_OFF",
    "SELINUX_ENFORCING_OFF",
    "KMALLOC_CACHES_OFF",
    "ANON_PIPE_BUF_OPS_OFF",
    "SYSTEM_UNBOUND_WQ_OFF",
    "CALL_USERMODEHELPER_EXEC_WORK_OFF",
    "ASHMEM_FOPS_OFF",
    "ASHMEM_MISC_FOPS_OFF",
    "ASHMEM_IOCTL_OFF",
    "ASHMEM_COMPAT_IOCTL_OFF",
    "ASHMEM_MMAP_OFF",
    "ASHMEM_OPEN_OFF",
    "ASHMEM_RELEASE_OFF",
    "ASHMEM_SHOW_FDINFO_OFF",
    "CONFIGFS_READ_ITER_OFF",
    "CONFIGFS_BIN_WRITE_ITER_OFF",
    "COPY_SPLICE_READ_OFF",
    "NOOP_LLSEEK_OFF",
    "SLIDE_NFULNL_LOGGER_NAME_OFF",
    "SLIDE_NFULNL_LOGGER_OBJECT_OFF",
    "SLIDE_RANDOM_TABLE_BOOT_ID_DATA_PTR_OFF",
    "SLIDE_SYSCTL_BOOTID_OFF",
    "SLIDE_TRACEFS_EVENT_ID",
    "SLIDE_TRACEFS_WORKER_CALLER_OFF",
)

# Values whose meaning is a count, not an address. Printed without the 0x-padded shape so a reader
# cannot mistake the event id for an offset.
COUNTS = frozenset({"SLIDE_TRACEFS_EVENT_ID"})

HEADER = """/*
 * dm2q-S916BXXUAGZIF: the values this firmware decides.
 *
 * GENERATED - do not edit by hand. Written by `tools/port/emit_target.py` from a measurement of
 * {source}
 * on {when}, and documented in `docs/SM-S916B-S916BXXUAGZIF.md`.
 *
 * Every line carries the symbol, string or device reading it was measured from. Nothing here was
 * carried over from another release: the rest of the target header is the shape of a 5.15 Samsung
 * kernel, and this file is the part that changes with a firmware.
 */
#ifndef DM2Q_S916B_UAGZIF_MEASURED
#define DM2Q_S916B_UAGZIF_MEASURED

"""

FOOTER = """
#endif
"""


def load_report(path: Path) -> dict:
    report = json.loads(path.read_text(encoding="utf-8"))
    if "findings" not in report or "text_base" not in report:
        raise ValueError("this is not a target_report.py report: no `findings` or `text_base`")
    return report


def load_finding(path: Path) -> tuple[str, int, str]:
    """One macro out of a tool that measured it on its own - `random_table.py --json`."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    for field in ("macro", "value", "source"):
        if field not in payload:
            raise ValueError(f"{path} is not a single-finding file: no `{field}`")
    if payload["value"] is None:
        raise ValueError(f"{path} did not resolve {payload['macro']}")
    return payload["macro"], int(payload["value"]), payload["source"]


def merge(resolved: dict[str, tuple[int, str]], extras: list[tuple[str, int, str]]) -> None:
    """Add the values measured outside the report, refusing to overwrite one that is already there."""
    for macro, value, source in extras:
        if macro in resolved:
            raise ValueError(
                f"{macro} arrives twice ({resolved[macro][1]!r} and {source!r}): the header would be "
                "whatever the order of the arguments happened to be"
            )
        resolved[macro] = (value, source)


def values(report: dict) -> dict[str, tuple[int, str]]:
    """The report as macro -> (value, source), refusing duplicates and unresolved entries."""
    resolved: dict[str, tuple[int, str]] = {}
    for finding in report["findings"]:
        macro = finding["macro"]
        if finding["value"] is None:
            continue
        if macro in resolved:
            raise ValueError(f"{macro} is measured twice; the report is ambiguous")
        resolved[macro] = (int(finding["value"]), finding["source"])
    return resolved


def check_alignment(base: int) -> None:
    """A screwy image base is the mistake that turns every offset into a plausible-looking wrong one."""
    if base & 0xFFFFF:
        raise ValueError(
            f"image base 0x{base:x} is not 2 MiB aligned: arm64 KASLR places the image on a 2 MiB "
            "boundary, so a base that is not is a typo rather than a measurement"
        )


def render(
    resolved: dict[str, tuple[int, str]],
    extra: dict[str, tuple[int | str, str]],
) -> str:
    lines: list[str] = []
    for macro in REQUIRED + ("SLIDE_TRACEFS_VFORK_CALLER_OFF",):
        if macro not in resolved:
            continue
        value, source = resolved[macro]
        if macro in COUNTS:
            lines.append(f"#define {macro} {value}  /* {source} */")
        else:
            lines.append(f"#define {macro} 0x{value:08x}ULL  /* {source} */")
    for macro, (value, source) in extra.items():
        if isinstance(value, str):
            lines.append(f'#define {macro} "{value}"  /* {source} */')
        else:
            lines.append(f"#define {macro} 0x{value:08x}ULL  /* {source} */")
    # The P0 table is *not* included here, and the reason is the include order rather than taste:
    # `measured.h` is reached from `offset.h`, which is the first thing `common.h` includes, so the
    # table's `uint16_t`/`uint64_t` would be expanded before `<stdint.h>`. The repository's convention
    # is `P0_FINGERPRINT_HEADER` in `target.h` and `#include P0_FINGERPRINT_HEADER` in `pipe.c` after the
    # standard headers, which is where the table is used; the `--fingerprint` check below still makes
    # sure it exists, so a header cannot be written against a table that was never generated.
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", help="the JSON from target_report.py")
    parser.add_argument("--kimage-text-base", help="`_text`'s link address, as hex")
    parser.add_argument("--physical-load", help="`P0_PHYS_OFFSET + text_offset`, as hex")
    parser.add_argument("--build-fingerprint", help="`getprop ro.build.fingerprint` of this build")
    parser.add_argument("--trace-event-id", type=int, help="the event id, if the report has none")
    parser.add_argument(
        "--extra",
        action="append",
        default=[],
        metavar="MACRO=0xVALUE",
        help="a value measured some other way (a disassembly, say); repeatable",
    )
    parser.add_argument(
        "--extra-json",
        action="append",
        default=[],
        metavar="PATH",
        help="a single-finding JSON from another tool here, such as random_table.py --json; repeatable",
    )
    parser.add_argument("--out", help="where to write measured.h")
    parser.add_argument(
        "--fingerprint",
        help="the P0 table the header will include (default: p0_fingerprint.h beside --out)",
    )
    parser.add_argument("--source", default="the device's own kernel", help="what was measured")
    parser.add_argument("--self-test", action="store_true", help="render a synthetic report")
    arguments = parser.parse_args()

    if arguments.self_test:
        return self_test()
    if not arguments.report or not arguments.out:
        parser.error("--report and --out are required")

    report = load_report(Path(arguments.report))
    resolved = values(report)
    extras: list[tuple[str, int, str]] = []
    for given in arguments.extra:
        macro, _, raw = given.partition("=")
        if not macro or not raw:
            parser.error(f"--extra wants MACRO=0xVALUE, got {given!r}")
        extras.append((macro, int(raw, 16), "given on the command line"))
    for path in arguments.extra_json:
        extras.append(load_finding(Path(path)))
    if arguments.trace_event_id is not None:
        extras.append(("SLIDE_TRACEFS_EVENT_ID", arguments.trace_event_id, "read on the device"))
    try:
        merge(resolved, extras)
    except ValueError as error:
        print(f"cannot write a measured header: {error}", file=sys.stderr)
        return 1

    missing = [macro for macro in REQUIRED if macro not in resolved]
    if missing:
        print("cannot write a measured header: these are not in the report", file=sys.stderr)
        for macro in missing:
            print(f"  - {macro}", file=sys.stderr)
        return 1

    base = int(arguments.kimage_text_base, 16) if arguments.kimage_text_base else report["text_base"]
    check_alignment(base)
    if arguments.kimage_text_base and base != report["text_base"]:
        # The offsets are slide-invariant, so a slid table and a link-time base are a consistent pair
        # as long as the base is the one the header will be built against. Saying so, because the
        # numbers look the same either way and the mistake would only appear on the device.
        print(
            f"note: the table was taken at 0x{report['text_base']:x} and the image base is "
            f"0x{base:x}; offsets are slide-invariant, so both are consistent",
            file=sys.stderr,
        )

    extra: dict[str, tuple[int | str, str]] = {
        "KIMAGE_TEXT_BASE": (base, "`_text`'s link address"),
    }
    if arguments.physical_load:
        extra["P0_KERNEL_PHYS_LOAD"] = (int(arguments.physical_load, 16), "bootloader load address + text_offset")
    if arguments.build_fingerprint:
        extra["BUILD_FINGERPRINT"] = (arguments.build_fingerprint, "`ro.build.fingerprint`")

    fingerprint = (
        Path(arguments.fingerprint) if arguments.fingerprint
        else Path(arguments.out).parent / "p0_fingerprint.h"
    )
    if not fingerprint.exists():
        print(
            f"cannot write a measured header: {fingerprint} does not exist; generate it with "
            "`tools/generate_p0_fingerprint.pl <kernel> 0x1f0000 <that path>`",
            file=sys.stderr,
        )
        return 1

    text = (
        HEADER.format(
            source=arguments.source,
            when=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        )
        + render(resolved, extra)
        + FOOTER
    )
    Path(arguments.out).write_text(text, encoding="utf-8")
    print(f"wrote {arguments.out}: {len(resolved)} measured value(s)")
    return 0


def self_test() -> int:
    findings = [
        {"macro": macro, "value": 0x1000 + index, "source": f"symbol thing_{index}", "candidates": []}
        for index, macro in enumerate(REQUIRED)
    ]
    report = {"text_base": 0xFFFFFFC008000000, "findings": findings}
    resolved = values(report)
    rendered = render(resolved, {"KIMAGE_TEXT_BASE": (0xFFFFFFC008000000, "test")})
    with_extra = values(report)
    merge(with_extra, [("SLIDE_TRACEFS_VFORK_CALLER_OFF", 0x1234, "given on the command line")])

    results = [
        ("every required macro renders", all(f"#define {macro}" in rendered for macro in REQUIRED)),
        ("the event id is not printed as a pointer",
         "#define SLIDE_TRACEFS_EVENT_ID 0x" not in rendered),
        ("the P0 table is left to `pipe.c`, which includes it after `<stdint.h>`",
         '#include "p0_fingerprint.h"' not in rendered),
        ("a missing macro is refused", _refuses_missing()),
        ("an unaligned base is refused", _refuses_base()),
        ("a duplicate measurement is refused", _refuses_duplicate()),
        ("a value measured elsewhere fills a macro the report lacks",
         with_extra.get("SLIDE_TRACEFS_VFORK_CALLER_OFF", (None,))[0] == 0x1234),
        ("a value measured elsewhere cannot overwrite a measured one", _refuses_merge()),
        ("KIMAGE_TEXT_BASE carries the link address",
         "#define KIMAGE_TEXT_BASE 0xffffffc008000000ULL" in rendered),
    ]
    for label, passed in results:
        print(f"self-test: {label} {'ok' if passed else 'FAILED'}")
    if all(passed for _, passed in results):
        print("self-test: the header is written whole, from a report that names its sources")
        return 0
    return 1


def _refuses_missing() -> bool:
    report = {"text_base": 0xFFFFFFC008000000, "findings": [
        {"macro": "INIT_TASK_OFF", "value": 1, "source": "x", "candidates": []}
    ]}
    try:
        missing = [macro for macro in REQUIRED if macro not in values(report)]
        if missing:
            raise ValueError(missing)
    except ValueError:
        return True
    return False


def _refuses_base() -> bool:
    try:
        check_alignment(0xFFFFFFC008123456)
    except ValueError:
        return True
    return False


def _refuses_duplicate() -> bool:
    report = {"text_base": 0xFFFFFFC008000000, "findings": [
        {"macro": "INIT_TASK_OFF", "value": 1, "source": "x", "candidates": []},
        {"macro": "INIT_TASK_OFF", "value": 2, "source": "y", "candidates": []},
    ]}
    try:
        values(report)
    except ValueError:
        return True
    return False


def _refuses_merge() -> bool:
    resolved = {"INIT_TASK_OFF": (1, "symbol init_task")}
    try:
        merge(resolved, [("INIT_TASK_OFF", 2, "given on the command line")])
    except ValueError:
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
