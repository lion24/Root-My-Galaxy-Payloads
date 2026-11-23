# Galaxy S23+ SM-S916B / S916BXXUAGZIF port

## Status

**Scaffolded, syntax-verified, not measured.** The target directory exists and refuses to build:
`measured.h` holds an `#error`, and `tools/port/emit_target.py` will replace it only from a report in
which every value names the symbol, string or device reading it came from. What *has* been checked is
the half that does not need a firmware: the carried-over header compiles in all four build shapes and
lost no macro relative to the sibling port. Nothing here has run on hardware.

The sibling port `dm2q-S916BXXSAFZG1` (`docs/SM-S916B-S916BXXSAFZG1.md`) is the reference: same device,
same kernel branch, previous release. It is where the layout constants in `target.h` come from, and its
`target.h` is what the report diffs against.

## Exact target

| Field | Value | Source |
| --- | --- | --- |
| Model | `SM-S916B` Galaxy S23+ | device |
| Kernel | `5.15.197-android13-8-34343818-abS916BXXUAGZIF` | `uname -r` on the device |
| Kernel version | `5.15.197` | leading three parts of the above |
| KMI | `android13-5.15` | `android13-8` in the release string |
| Firmware | `S916BXXUAGZIF` | kernel build id |
| Payload profile | `dm2q-S916BXXUAGZIF` | this directory |
| Reference | `dm2q-S916BXXSAFZG1` (5.15.189) | `docs/SM-S916B-S916BXXSAFZG1.md` |

## What is carried over, and why that is not a guess

`src/targets/dm2q-S916BXXUAGZIF/target.h` holds the texture of the chain rather than the numbers:

- slab geometry (`MM_ORDER`, `SKB_*`, the S918_* bank sizes) - a property of the allocator and of the
  Samsung config, not of a release;
- `MM_STRUCT_SZ` - a size rather than an offset, so it cannot live in `measured.h`, but it moves with
  the config; BTF answers it (`sizeof(struct mm_struct)`) and step 4 checks it;
- the fake-object layouts (`struct page`, `file_operations`, `pipe_buffer`, `pool_workqueue`,
  `configfs_buffer`) - 5.15 ABI, checked against this build's BTF before anything runs;
- the reclaim delays and page-setup attempt counts - tuned on hardware on this device.

The `_OFF` macros are *not* here. They are in `measured.h`, which the port generates, and until it
does, the target does not compile.

## The measurement, in order

```sh
# 0. the scaffold itself, before it has numbers (also the CI pre-check)
tools/port/syntax_check.py --target dm2q-S916BXXUAGZIF --reference dm2q-S916BXXSAFZG1

# 1. the Image this phone runs
adb shell su -c 'dd if=/dev/block/by-name/boot of=/data/local/tmp/boot.img'
adb pull /data/local/tmp/boot.img
tools/port/kernel_image.py --boot boot.img --out kernel

# 2. symbols, and the P0 rows the oracle compares against
vmlinux-to-elf kernel vmlinux.elf
llvm-nm --numeric-sort vmlinux.elf > vmlinux.nm
tools/generate_p0_fingerprint.pl kernel 0x1f0000 \
  src/targets/dm2q-S916BXXUAGZIF/p0_fingerprint.h

# 3. what this kernel exports, against the previous release's header
tools/port/target_report.py --symbols vmlinux.nm --image kernel \
  --reference src/targets/dm2q-S916BXXSAFZG1/target.h --json report.json

# 4. BTF layout check - the carried-over struct offsets have to still be right
tools/port/btf.py --image kernel --struct task_struct --struct rt_mutex_waiter \
  --struct page --struct file_operations --struct configfs_buffer \
  --struct pool_workqueue --struct worker_pool --struct pipe_buffer --json btf.json
# 5. the header
tools/port/random_table.py --symbols vmlinux.nm --image kernel --json random_table.json
tools/port/emit_target.py --report report.json --extra-json random_table.json \
  --extra SLIDE_TRACEFS_WORKER_CALLER_OFF=<instruction after `bl schedule`> \
  --kimage-text-base 0xffffffc008000000 \
  --physical-load 0x80000000 --build-fingerprint "<ro.build.fingerprint>" \
  --fingerprint src/targets/dm2q-S916BXXUAGZIF/p0_fingerprint.h \
  --out src/targets/dm2q-S916BXXUAGZIF/measured.h

# 6. build, at the fixed release size
make TARGET=dm2q-S916BXXUAGZIF ANDROID_NDK_HOME=<ndk> release
```

`tools/port/README.md` has the two input routes (boot partition, or `/proc/kallsyms` for the symbol
half) and the constants that stay a decision rather than a measurement.

## The numbers this port still owes

Two are not a command. Both are recorded here with where they come from so the header cannot end up
carrying a value nobody can trace:

| Constant | Derived from | Passed in as |
| --- | --- | --- |
| `SLIDE_TRACEFS_EVENT_ID` | `cat /sys/kernel/tracing/events/sched/sched_blocked_reason/id` on the device, or `__TRACE_LAST_TYPE + index` from the Image's event table | `--trace-event-id` |
| `SLIDE_TRACEFS_WORKER_CALLER_OFF` | the instruction after `bl schedule` in `worker_thread`, minus the image base | `--extra` |
| `SLIDE_TRACEFS_VFORK_CALLER_OFF` | the same `bl schedule` on the `vfork` path; optional in the code, carried by the sibling port | `--extra` |

Two more were on that list until they stopped being judgement calls:

- `SLIDE_RANDOM_TABLE_BOOT_ID_DATA_PTR_OFF` is measured by `tools/port/random_table.py`, which finds the
  `boot_id` entry by the pointer it holds rather than by the index and `+8` the porting document
  derives. Both of those move: in ACK `android13-5.15.197_r00` the entries are poolsize,
  entropy_avail, write_wakeup_threshold, urandom_min_reseed_secs, **boot_id**, uuid - index 4, not
  mainline's - and `struct ctl_table` is `__randomize_layout`, so `+8` is a property of the config.
- `__TRACE_LAST_TYPE`, the first term of the offline event id, is 20 for this KMI: ACK
  `android13-5.15.197_r00`'s `enum trace_type` has 19 members after `__TRACE_FIRST_TYPE`, so the last
  type is index 20. The porting document quotes 20 for a 6.1 kernel too, which is a coincidence worth
  knowing rather than a constant to copy.

## Gates before this is published anywhere

Each of these is a refusal, not a warning - the sibling port's record is explicit about what happens
when one is skipped:

1. **Every P0 row read back from the Image.** The generator verifies each of the 256 qwords through a
   second file handle; a table that cannot be read back is a table generated from the wrong file.
2. **Every symbol resolved, none defaulted.** `target_report.py` exits non-zero and lists what is
   missing; `emit_target.py` refuses to write a partial header.
3. **Struct offsets agree with this build's BTF.** A layout macro that changed between releases is a
   chain rewrite, not a number to paste.
4. **One hardware attempt, in `adb shell`, per boot.** The S916B chain's slide discovery needs
   tracefs, and the record for the sibling port says what that means: the proven domain is `uid=2000`
   `u:r:shell:s0`, and a payload that failed after the stack writer left PI state behind that a second
   attempt on the same boot must not disturb.
5. **The KernelSU pair matched to this build** (`docs/PORTING.md` section 7): module and `ksud` from
   the same source and release, with the kallsyms-aware late-load path - not a pair assembled from two
   builds.

## Why this is not in `support/targets-v3.json`

It must not be, yet, and the reason is not caution about the port's quality:

- The app reads `kernelsu-generic.json` **only when a device has no entry of its own**. Listing
  SM-S916B at `5.15.197` would therefore *replace* the generic tier that currently serves this phone
  with a profile the app cannot execute.
- The chain needs `tracefs` for the KASLR slide, which an app uid cannot open. The sibling port's
  README says the same thing about the same device: an APK frontend would have to delegate the runner
  to an ADB-shell or Shizuku shell service, and that integration does not exist.

So the ordering is: port and validate over `adb shell` first; then, if the payload is to be offered in
the app, build the shell-bridge launch path and add the feed entry in the same change - never the entry
first, because a feed entry is what makes the app stop using the path that works.

## Log

| Date | What happened |
| --- | --- |
| 2026-11-23 | Target scaffolded: `target.h` (carried layout), `measured.h` (an `#error`), `tools/port/` pipeline with self-tests. |
| 2026-11-23 | Scaffold verified without a firmware: `tools/port/syntax_check.py` compiles the header four ways (app/standalone x with/without the optional measurements) against stub measurements, and diffs its macro names against `dm2q-S916BXXSAFZG1` - every macro the sibling header defines is defined or measured here. |
