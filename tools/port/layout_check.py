#!/usr/bin/env python3
"""Every layout constant a target carries, checked against the build's own BTF.

A target header is in two halves. `measured.h` holds what a firmware build decides - symbols, strings,
the P0 rows - and `tools/port/target_report.py` measures those. The rest of the header *carries* the
layout of a 5.15 Samsung kernel: the fake-object layouts, `struct page`, `struct file_operations`, the
workqueue members.

"Not firmware-specific" is a claim about those numbers, and this is what turns it into a check. Every
one of them names a field, and BTF from the Image this phone actually runs says where that field is.
The failure mode it catches is the quiet one: the chain writes a fake `rt_mutex_waiter` at 5.15's
offsets into a kernel whose `ww_ctx` moved, and the symptom is a phone that reboots.

Two kinds of constant are deliberately *not* checked here, because comparing them would "correct" a
right number into a wrong one:

- **`MM_STRUCT_SZ`** is not `sizeof(struct mm_struct)`. This build's BTF says that size is `0x3e0`,
  which selects `kmalloc-1024`, and `0x400` is that cache's *object stride* - the number the reclaim
  arithmetic needs (`slot = (mm - base) / MM_STRUCT_SZ`). A checker that insisted on `0x3e0` would be
  insisting on a different, wrong computation.
- **flags and page positions** (`PIPE_BUF_FLAG_CAN_MERGE`, `MCAST_WAITER_OFF`,
  `SIGRETURN_*_WAITER_OFF`, `LOCK_OFF`...). The first is a value from the source, the rest are places
  the chain chooses inside a reclaimed page. None of them is a member of any struct.

    tools/port/btf.py --image kernel --struct task_struct --struct rt_mutex_waiter ... --json btf.json
    tools/port/layout_check.py --btf btf.json --header src/targets/dm2q-S916BXXUAGZIF/target.h
    tools/port/layout_check.py --self-test
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# A macro's carried value, decimal or hex, with the usual unsigned suffixes: `CFG_PAGE_OFF` is written
# as `16` and `FOPS_IOCTL_OFF` as `0x50`, and both are the same kind of number.
DEFINE = re.compile(r"^#define\s+([A-Z][A-Z0-9_]*)\s+(0x[0-9a-fA-F]+|\d+)", re.MULTILINE)

# macro -> (struct, member). The member is None when the macro is the struct's own size rather than a
# field's offset. The struct names are the ones `btf.py --struct` was given, so a missing one is a
# refusal rather than a pass: a check that silently skips what it was not asked to read is worse than
# no check.
LAYOUT: dict[str, tuple[str, str | None]] = {
    "TASK_STRUCT_CRED_OFF": ("task_struct", "cred"),
    "TASK_STRUCT_REAL_CRED_OFF": ("task_struct", "real_cred"),
    # The fake task is installed as a sched entity's group, so it is `sched_task_group` and not the
    # `group_leader` the name suggests - 0x400 is what BTF says, and it is worth saying which member
    # that is, because "task_group" on its own is ambiguous in `task_struct`.
    "FAKE_TASK_TASK_GROUP_OFF": ("task_struct", "sched_task_group"),
    "FAKE_TASK_USAGE_OFF": ("task_struct", "usage"),
    "FAKE_TASK_PRIO_OFF": ("task_struct", "prio"),
    "FAKE_TASK_NORMAL_PRIO_OFF": ("task_struct", "normal_prio"),
    "FAKE_TASK_PI_LOCK_OFF": ("task_struct", "pi_lock"),
    "FAKE_TASK_PI_WAITERS_OFF": ("task_struct", "pi_waiters"),
    "FAKE_TASK_PI_TOP_TASK_OFF": ("task_struct", "pi_top_task"),
    "FAKE_TASK_PI_BLOCKED_ON_OFF": ("task_struct", "pi_blocked_on"),
    "FAKE_WAITER_PI_TREE_ENTRY_OFF": ("rt_mutex_waiter", "pi_tree_entry"),
    "FAKE_WAITER_TASK_OFF": ("rt_mutex_waiter", "task"),
    "FAKE_WAITER_LOCK_OFF": ("rt_mutex_waiter", "lock"),
    "FAKE_WAITER_WAKE_STATE_OFF": ("rt_mutex_waiter", "wake_state"),
    "FAKE_WAITER_PRIO_OFF": ("rt_mutex_waiter", "prio"),
    "FAKE_WAITER_DEADLINE_OFF": ("rt_mutex_waiter", "deadline"),
    "FAKE_WAITER_WW_CTX_OFF": ("rt_mutex_waiter", "ww_ctx"),
    "FAKE_WAITER_LAYOUT_SIZE": ("rt_mutex_waiter", None),
    "CFG_PAGE_OFF": ("configfs_buffer", "page"),
    "CFG_NEEDS_READ_FILL_OFF": ("configfs_buffer", "needs_read_fill"),
    "CFG_BIN_BUFFER_OFF": ("configfs_buffer", "bin_buffer"),
    "CFG_BIN_BUFFER_SIZE_OFF": ("configfs_buffer", "bin_buffer_size"),
    "CFG_CB_MAX_SIZE_OFF": ("configfs_buffer", "cb_max_size"),
    "WQ_DFL_PWQ_OFF": ("workqueue_struct", "dfl_pwq"),
    "PWQ_POOL_OFF": ("pool_workqueue", "pool"),
    "PWQ_WQ_OFF": ("pool_workqueue", "wq"),
    "PWQ_WORK_COLOR_OFF": ("pool_workqueue", "work_color"),
    "PWQ_REFCNT_OFF": ("pool_workqueue", "refcnt"),
    "PWQ_NR_IN_FLIGHT_OFF": ("pool_workqueue", "nr_in_flight"),
    "PWQ_NR_ACTIVE_OFF": ("pool_workqueue", "nr_active"),
    "PWQ_MAX_ACTIVE_OFF": ("pool_workqueue", "max_active"),
    "POOL_WORKLIST_OFF": ("worker_pool", "worklist"),
    "POOL_NR_IDLE_OFF": ("worker_pool", "nr_idle"),
    "WORK_DATA_OFF": ("work_struct", "data"),
    "WORK_ENTRY_OFF": ("work_struct", "entry"),
    "WORK_FUNC_OFF": ("work_struct", "func"),
    "STRUCT_PAGE_SIZE": ("page", None),
    # Inside an anonymous union; `btf.py` expands those, which is the only way these two have a name.
    "STRUCT_PAGE_COMPOUND_HEAD_OFF": ("page", "compound_head"),
    "STRUCT_SLAB_CACHE_OFF": ("page", "slab_cache"),
    "STRUCT_PAGE_TYPE_OFF": ("page", "page_type"),
    "FOPS_OWNER_OFF": ("file_operations", "owner"),
    "FOPS_LLSEEK_OFF": ("file_operations", "llseek"),
    "FOPS_READ_OFF": ("file_operations", "read"),
    "FOPS_WRITE_OFF": ("file_operations", "write"),
    "FOPS_READ_ITER_OFF": ("file_operations", "read_iter"),
    "FOPS_WRITE_ITER_OFF": ("file_operations", "write_iter"),
    "FOPS_IOCTL_OFF": ("file_operations", "unlocked_ioctl"),
    "FOPS_COMPAT_IOCTL_OFF": ("file_operations", "compat_ioctl"),
    "FOPS_MMAP_OFF": ("file_operations", "mmap"),
    "FOPS_OPEN_OFF": ("file_operations", "open"),
    "FOPS_RELEASE_OFF": ("file_operations", "release"),
    "FOPS_SPLICE_READ_OFF": ("file_operations", "splice_read"),
    "FOPS_SHOW_FDINFO_OFF": ("file_operations", "show_fdinfo"),
}


@dataclass
class Measured:
    """What `btf.py` said, keyed the way the table above asks for it."""

    sizes: dict[str, int]
    members: dict[tuple[str, str], int]
    duplicated: set[tuple[str, str]]

    @classmethod
    def from_payload(cls, payload: list[dict]) -> Measured:
        sizes: dict[str, int] = {}
        members: dict[tuple[str, str], int] = {}
        duplicated: set[tuple[str, str]] = set()
        for struct in payload:
            for field in ("struct", "size", "members"):
                if field not in struct:
                    raise ValueError(
                        f"this is not a `btf.py --json` dump: an entry has no `{field}`"
                    )
            sizes[struct["struct"]] = int(struct["size"])
            for member in struct["members"]:
                key = (struct["struct"], member["name"])
                if member["byte_offset"] is None:
                    continue
                if key in members:
                    duplicated.add(key)
                members[key] = int(member["byte_offset"])
        return cls(sizes, members, duplicated)

    @classmethod
    def load(cls, path: Path) -> Measured:
        return cls.from_payload(json.loads(path.read_text(encoding="utf-8")))

    def size(self, struct: str) -> int:
        if struct not in self.sizes:
            raise KeyError(
                f"`{struct}` is not in the BTF dump: re-run `btf.py --struct {struct}` and read the "
                "answer from the build rather than skipping the check"
            )
        return self.sizes[struct]

    def offset(self, struct: str, member: str) -> int:
        if struct not in self.sizes:
            raise KeyError(self.size(struct))
        if (struct, member) in self.duplicated:
            raise ValueError(
                f"`{struct}` has two members named `{member}` once anonymous aggregates are expanded: "
                "the offset the header carries names one of them and this is not evidence for either"
            )
        if (struct, member) not in self.members:
            raise KeyError(
                f"`{struct}` has no member `{member}` in this build's BTF: the header carries an "
                "offset for a field that no longer exists, which is a rename or a config change"
            )
        return self.members[(struct, member)]


@dataclass
class Verdict:
    macro: str
    carried: int | None
    measured: int | None
    source: str
    problem: str | None = None

    @property
    def agrees(self) -> bool:
        return self.problem is None and self.carried is not None and self.carried == self.measured


def read_defines(text: str) -> dict[str, int]:
    return {name: int(value, 0) for name, value in DEFINE.findall(text)}


def check(defines: dict[str, int], measured: Measured) -> list[Verdict]:
    """One verdict per carried constant, with `measured` left empty when BTF cannot answer."""
    verdicts: list[Verdict] = []
    for macro, (struct, member) in LAYOUT.items():
        carried = defines.get(macro)
        source = f"{struct}.{member}" if member else f"sizeof({struct})"
        try:
            value = measured.size(struct) if member is None else measured.offset(struct, member)
        except (KeyError, ValueError) as error:
            verdicts.append(
                Verdict(macro, carried, None, source, f"{macro}: {error.args[0] if error.args else error}")
            )
            continue
        problem = None
        if carried is None:
            problem = (
                f"{macro}: the header does not carry it, so either the chain stopped using the field "
                f"or the constant was dropped; BTF says {source} is 0x{value:x}"
            )
        verdicts.append(Verdict(macro, carried, value, source, problem))
    return verdicts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--btf", help="the JSON `tools/port/btf.py --json` wrote")
    parser.add_argument("--header", help="the target header the constants are carried in")
    parser.add_argument("--self-test", action="store_true", help="run against a synthetic dump")
    arguments = parser.parse_args()

    if arguments.self_test:
        return self_test()
    if not arguments.btf or not arguments.header:
        parser.error("--btf and --header are required")

    measured = Measured.load(Path(arguments.btf))
    verdicts = check(read_defines(Path(arguments.header).read_text(encoding="utf-8")), measured)

    print(f"{'macro':34s} {'carried':>10s}  {'btf':>10s}  field")
    for verdict in verdicts:
        carried = f"0x{verdict.carried:04x}" if verdict.carried is not None else "-"
        btf = f"0x{verdict.measured:04x}" if verdict.measured is not None else "-"
        if verdict.problem:
            note = "unresolved"
        elif verdict.carried == verdict.measured:
            note = "same"
        else:
            note = "CHANGED"
        print(f"{verdict.macro:34s} {carried:>10s}  {btf:>10s}  {verdict.source} {note}")

    problems = [verdict.problem for verdict in verdicts if verdict.problem]
    changed = [verdict for verdict in verdicts if verdict.problem is None and not verdict.agrees]
    for problem in problems:
        print(f"\nunresolved: {problem}")
    if changed:
        print(
            "\nCHANGED means this build's layout moved. Re-read the chain against the new offsets - a "
            "number pasted in from BTF without that is a fake object the kernel will not interpret "
            "the way the code that fills it expects."
        )
    if not problems and not changed:
        print(f"\n{len(verdicts)} carried constants agree with this build's BTF")
        return 0
    return 1


def self_test() -> int:
    """A dump and a header small enough that every verdict is obvious from the text."""

    payload = [{
        "struct": "task_struct",
        "size": 0x1000,
        "members": [
            {"name": "cred", "byte_offset": 0x798, "bit_offset": 0x798 * 8, "bitfield_bits": 0},
            {"name": "sched_task_group", "byte_offset": 0x400, "bit_offset": 0x400 * 8,
             "bitfield_bits": 0},
            {"name": "page_type", "byte_offset": 0x30, "bit_offset": 0x30 * 8, "bitfield_bits": 0},
        ],
        "unusable": [],
    }]
    header = "\n".join([
        "#define TASK_STRUCT_CRED_OFF      0x798ULL  /* carried */",
        "#define TASK_STRUCT_REAL_CRED_OFF 0x790ULL",
        "#define FAKE_TASK_TASK_GROUP_OFF  0x400ULL",
        "#define STRUCT_PAGE_TYPE_OFF      48",
    ])
    measured = Measured.from_payload(payload)
    verdicts = {verdict.macro: verdict for verdict in check(read_defines(header), measured)}

    # A member BTF does not have, because the dump was asked for fewer structs than the header names.
    shell_of_task = Measured.from_payload([{
        "struct": "task_struct", "size": 0x1000, "members": [], "unusable": [],
    }])
    missing_member = {verdict.macro: verdict for verdict in check(read_defines(header), shell_of_task)}

    # The same field twice after expansion, which is not evidence for either of them.
    ambiguous = Measured.from_payload([{
        "struct": "task_struct",
        "size": 0x1000,
        "members": [
            {"name": "cred", "byte_offset": 0x798, "bit_offset": 0x798 * 8, "bitfield_bits": 0},
            {"name": "cred", "byte_offset": 0x7a0, "bit_offset": 0x7a0 * 8, "bitfield_bits": 0},
        ],
        "unusable": [],
    }])
    duplicated = {verdict.macro: verdict for verdict in check(read_defines(header), ambiguous)}

    results = [
        ("a carried offset that matches is reported as such",
         verdicts["TASK_STRUCT_CRED_OFF"].agrees),
        ("a decimal define is the same kind of number",
         verdicts["STRUCT_PAGE_TYPE_OFF"].carried == 0x30),
        ("a field the dump lacks is unresolved, not a pass",
         missing_member["TASK_STRUCT_CRED_OFF"].measured is None
         and missing_member["TASK_STRUCT_CRED_OFF"].problem is not None),
        ("a constant the header dropped is reported",
         verdicts["TASK_STRUCT_REAL_CRED_OFF"].problem is not None),
        ("a struct that was not dumped is refused", _refuses(lambda: measured.size("page"))),
        ("a member that appears twice is refused",
         duplicated["TASK_STRUCT_CRED_OFF"].problem is not None
         and _refuses(lambda: ambiguous.offset("task_struct", "cred"))),
        ("a dump without sizes is refused", _refuses(lambda: Measured.from_payload([{"struct": "x"}]))),
        ("a moved member would be CHANGED",
         _changed(dict(read_defines(header), TASK_STRUCT_CRED_OFF=0x7A0), measured)),
    ]
    for label, passed in results:
        print(f"self-test: {label} {'ok' if passed else 'FAILED'}")
    if all(passed for _, passed in results):
        print("self-test: the carried layouts are checked, and what BTF cannot answer is refused")
        return 0
    return 1


def _changed(defines: dict[str, int], measured: Measured) -> bool:
    verdict = next(
        verdict for verdict in check(defines, measured) if verdict.macro == "TASK_STRUCT_CRED_OFF"
    )
    return verdict.problem is None and not verdict.agrees and verdict.measured == 0x798


def _refuses(call) -> bool:
    try:
        call()
    except (KeyError, ValueError):
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
