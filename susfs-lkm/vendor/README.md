# susfs-lkm/vendor

The `samsung` variant built against **Samsung's own tree at this device's release**, instead of the
tier's per-KMI KDIR. Everything here exists to make that build checkable before a phone sees it.

## Why the per-KMI build is not enough on this device

`susfs-lkm.yml` builds each variant inside a DDK image: the Android common kernel at that KMI's
branch head, with `gki_defconfig`, and `vermagic` is patched to the target release by the loader.

Measured on the SM-S916B this directory was written for (`5.15.197-android13-8-34343818-abS916BXXUAGZIF`):

- the GKI-built `samsung` module (`susfs_guard_lkm-android13-5.15.ko`, 692768 bytes,
  `13e8458d…b44b`) **loads** — the release string matches, and the kernel's only check is the
  release string — and then **takes the phone down**, with no diagnostic.
- the phone's config is not a GKI config: `CONFIG_RKP=y`, `CONFIG_KDP=y`, `CONFIG_KDP_CRED=y`,
  `CONFIG_SECURITY_DEFEX=y`, `CONFIG_DDAR=y`, `CONFIG_SECURITY_SELINUX_*`, `CONFIG_CFI_CLANG=y`,
  `CONFIG_LTO_CLANG_FULL=y`, `CONFIG_ARM64_PTR_AUTH_KERNEL=y`, `CONFIG_ARM64_BTI_KERNEL=y`,
  `CONFIG_SHADOW_CALL_STACK=y`, `CONFIG_MODVERSIONS=y`, `CONFIG_TRIM_UNUSED_KSYMS=y`.
- the module's `__versions` is empty, so the kernel cannot reject it on ABI either: it runs with
  whatever offsets it was compiled against.

So: the tree and the config have to be Samsung's, not GKI's. That is this directory.

## What is built against what

| thing | where it comes from | why that one |
| --- | --- | --- |
| tree | `WildKernels/Samsung_Kernels`, branch `SM-S918B-Oneui9`, `kernel_platform/common` | Samsung's OSRC drop per device and One UI release, with the toolchain it was built with. The S916B branch for One UI 9 does not exist yet, and the S23 Ultra is the same `sm8550`/`kalama` vendor tree; the config, which is what actually differs per device, is this phone's own. `common` rather than `msm-kernel`: this phone's vmlinux carries `init_defex_context`, `rkp_ro_alloc`, `kdp_*`, and `security/samsung` + `security/ddar` exist only in `common` - `msm-kernel/security/Makefile` lists neither. |
| config | `S916BXXUAGZIF.config` | `/proc/config.gz` read off the phone. Not a defconfig: the built config, including Samsung's security options. |
| compiler | the tree's `prebuilts/clang/host/linux-x86/clang-r450784e` | the compiler `CONFIG_CC_VERSION_TEXT` in the phone's config names. |
| release | composed by the tree's `scripts/setlocalversion` | Samsung's own four inputs: `android_release=android13`, `kmi_generation=8`, `LOCALVERSION=-34343818`, `BUILD_NUMBER=S916BXXUAGZIF`. |
| `__versions` | empty, by construction | a KDIR is not a vmlinux, so there are no CRCs to carry. Same shape as the exact-source KernelSU modules in `kernelsu/`; loading is the kallsyms-aware late load, not `insmod`. |

## The gates, in the order they run

1. **release** — `include/config/kernel.release` must equal the phone's `uname -r` exactly. A KDIR
   that is a nearby release is a module for a nearby kernel.
2. **symbols** — every undefined symbol of the built module must be a name in
   `vmlinux-S916BXXUAGZIF.nm.gz`, the recovered `llvm-nm` of this phone's kernel. A name that is
   missing is a load that fails at `init_module` — cheap to learn here, expensive on the phone.
3. **offsets** — `abi/` is a data-only module built against the same KDIR; `abi/values.py` reads its
   `sizeof`/`offsetof` values out of the built `.ko` (by section, because `-fdata-sections` moves the
   array), and `abi/compare.py` compares them with the phone's BTF
   (`btf-S916BXXUAGZIF.json`). A disagreement fails the run. Of the 72 values, 36 are names the
   device dump carries - all of `task_struct`'s checked fields, `mm_struct`'s, `file_operations`',
   `work_struct`'s - so those are checked and the other 36 are reported as unchecked rather than
   implied. This is the check that catches what the release string cannot: a module compiled
   against the wrong structs.
4. **identity** — `vermagic`, the `samsung` variant's own marker string, and its CFI symbols are
   printed into the artifact's evidence.
5. **config** — `config-dropped.txt` lists the phone's config symbols this tree did not set. It is a
   diagnostic, not a gate: a dropped symbol that moves a struct shows up as an offset mismatch in
   gate 3, and this file says which option to blame.

`btf-S916BXXUAGZIF.json` is the BTF decoded from the phone's `/sys/kernel/btf/vmlinux` (task_struct,
mm_struct, page, file_operations and the rest of the structs the payload port measured). It is a
subset, not the whole of the phone's BTF: the offset gate therefore reports which names it
*checked*, and says so rather than implying it covered the rest.

`vmlinux-S916BXXUAGZIF.nm.gz` is `llvm-nm --numeric-sort` of the ELF recovered from the phone's
boot image by `vmlinux-to-elf` — symbols only, no debug info: 126451 lines, 120628 distinct names
after dropping the `.NNNNN` suffixes the compiler's static-local cloning adds.

## What is still not proved

The module has not been loaded on the phone. A load is one attempt, needs root (the payload is
volatile across reboot), and a crash costs the phone's uptime — so it stays a deliberate step, with
the evidence above first.
