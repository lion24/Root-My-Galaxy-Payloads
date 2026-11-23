# Porting a firmware: the mechanical half

`docs/PORTING.md` is the record of how a target was ported by hand - read it first, because it is the
reason each number below exists. This directory is the same procedure with the typing taken out, for
the parts of it that are arithmetic rather than judgement.

The split is the point. A port has two kinds of number:

- **Measured**: symbol offsets, the tracefs event id and its caller, the P0 fingerprint rows. These are
  the firmware's, and there is exactly one right answer for each, obtainable from the kernel this
  phone runs.
- **Decided**: which of several `boot_id` strings the constant means, which pselect word shift the
  stack layout implies, whether a chain gate is needed. These need a person looking at disassembly and
  a device, and no tool here will guess one.

Everything in the first list is produced by a command in this directory. Anything in the second list
is reported as a problem, not filled in.

## The inputs, and why there are two routes

A target can be measured from two sources, and they are not equivalent:

| Source | Gives | Needs |
| --- | --- | --- |
| `boot` partition of the running phone (`dd` of `by-name/boot`) | the exact Image: symbols, BTF, `.rodata` strings, P0 rows | root, and a way to pull 64 MB off the device |
| the firmware's AP archive (`boot.img.lz4`) | the same | a Samsung FUS download (several GB) |
| `/proc/kallsyms` on the running phone | every symbol offset, slide-invariant | root (and `kptr_restrict` not hiding addresses) |

The first two are one route: the Image is the whole answer. The third is a shortcut for the symbol
half only - it cannot give the P0 table or the strings, because those are Image bytes - but it has a
property worth knowing: an offset is `symbol - _text`, and the slide cancels, so a table read from a
running phone is exact for the phone even though the addresses in it are randomised.

## From the phone's own boot partition

```sh
adb shell su -c 'dd if=/dev/block/by-name/boot of=/data/local/tmp/boot.img'
adb pull /data/local/tmp/boot.img
tools/port/kernel_image.py --boot boot.img --out kernel
```

Then the three Image-derived steps:

```sh
vmlinux-to-elf kernel vmlinux.elf                    # symbols, from the Image's own kallsyms
llvm-nm --numeric-sort vmlinux.elf > vmlinux.nm
tools/generate_p0_fingerprint.pl kernel 0x1f0000 \
  src/targets/dm2q-S916BXXUAGZIF/p0_fingerprint.h
```

`generate_p0_fingerprint.pl` is the repository's own generator, from the firmware that was already
ported; it verifies each row by reading the Image back through a second file handle, so the table it
writes is checked against the bytes it claims.

`vmlinux-to-elf` also recovers the Image's BTF, but `tools/port/btf.py` reads the blob directly and
does not need `bpftool` - which is the difference between running this on Linux CI and running it on a
laptop.

## From `/proc/kallsyms`

```sh
adb shell su -c 'cat /proc/kallsyms' > kallsyms.txt
tools/port/target_report.py --symbols kallsyms.txt --grep 'ashmem|configfs|nfulnl'
```

The offsets are usable directly. The two things this route cannot answer are the P0 table and the
`.rodata` strings; both are reported as unresolved rather than skipped quietly.

## The report

```sh
tools/port/target_report.py --symbols vmlinux.nm --image kernel \
  --reference src/targets/dm2q-S916BXXSAFZG1/target.h --json report.json
```

The table it prints is every macro in a reference target header, its measured value, the reference's
value, and whether they differ. `CHANGED` on a layout macro (`TASK_STRUCT_CRED_OFF`, `FOPS_IOCTL_OFF`)
means this kernel's ABI moved and the chain in `src/` has to be re-read against it - that is a
finding, not a number to paste.

It exits non-zero when anything is unresolved, and lists what is missing. That is deliberate: the
output of a partial run is not a header.

## What is still a decision

Two constants are not derivable from a symbol table, and the report says so by leaving them out:

- **`SLIDE_TRACEFS_EVENT_ID`** - the event id is assigned at boot in linker order. With the event
  table in the symbol table the tool computes it (`__TRACE_LAST_TYPE + index`); on a device that is
  already rooted there is a better answer: `cat /sys/kernel/tracing/events/sched/sched_blocked_reason/id`,
  passed back in with `--trace-event-id`.
- **`SLIDE_RANDOM_TABLE_BOOT_ID_DATA_PTR_OFF`** - the address of the `data` pointer in `random_table`'s
  `boot_id` entry. It is `random_table + index * sizeof(struct ctl_table) + 8`, where the index comes
  from the tree's own `random_table` definition; no symbol carries it. Derive it from the source and
  pass it with `--extra`, so the value in the report names where it came from instead of appearing
  as a constant.

`SLIDE_TRACEFS_WORKER_CALLER_OFF` is the instruction after `bl schedule` in `worker_thread` - section 5
of the porting document. Disassemble that symbol in `vmlinux.elf`, take the following instruction's
address, subtract the image base, and pass it with `--extra` until that step is automated too.

`SLIDE_TRACEFS_VFORK_CALLER_OFF` is the optional second return address the slide search also tries: the
same `bl schedule`, reached from the `vfork` path. It is optional in the code (`#ifdef`), and the port
should measure it anyway - with one caller the search only sees records the worker path produced, and
the sibling port carries both. Same derivation, also via `--extra`.

## The header, before it has numbers

The scaffold commits before it is measured, so there is a check for the half of it that is not a
number:

```sh
tools/port/syntax_check.py --target dm2q-S916BXXUAGZIF --reference dm2q-S916BXXSAFZG1
```

It copies the header into a temporary directory next to stub `measured.h` and `p0_fingerprint.h` files
and compiles it four ways - app payload and standalone binary, each with and without the optional
measurements - so a typo, a lost macro or a name used but never defined fails here instead of in the CI
run after the measurement. It also diffs the macro names against the reference header in both
directions, because the failure that matters is a constant the chain needs that the new header simply
forgot to carry.

## Writing the header

```sh
tools/port/emit_target.py --report report.json --kimage-text-base 0xffffffc008000000 \
  --physical-load 0x80000000 --build-fingerprint "$(adb shell getprop ro.build.fingerprint)" \
  --fingerprint src/targets/dm2q-S916BXXUAGZIF/p0_fingerprint.h \
  --out src/targets/dm2q-S916BXXUAGZIF/measured.h
```

It writes the file whole or not at all: every macro the target header needs must be in the report, the
image base must be 2 MiB aligned (arm64 KASLR places the image on such a boundary, so a base that is
not is a typo), and the P0 table has to already exist beside it.

Until that runs, `src/targets/dm2q-S916BXXUAGZIF/measured.h` holds an `#error`, and a build of the
target fails. That is the guard: the rest of the header is the shape of a 5.15 Samsung kernel and
compiles fine without this firmware's numbers, which is exactly the failure that is hard to see - a
payload that builds, loads, and writes into whatever now lives at the previous release's addresses.

## Self-tests

Each tool has one, because their real inputs only exist once a firmware has been pulled and a parser
that is wrong produces confident numbers rather than an error:

```sh
for tool in kernel_image btf target_report emit_target syntax_check; do
  tools/port/$tool.py --self-test
done
```
