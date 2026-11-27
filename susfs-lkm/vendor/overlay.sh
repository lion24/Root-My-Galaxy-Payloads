#!/bin/bash
# The `samsung` variant's overlay, applied to a checkout of upstream's sources.
#
# This is the same set of edits the `SUSFS LKM` workflow applies, moved into a file so the vendor
# build applies exactly the same ones rather than a second, drifting copy of them. Every edit is
# asserted after it is made: a sed that silently matched nothing is an artifact that is upstream's
# install path with our name on it, which is the one failure that would look like success.
#
#   $1  path to the susfs sources checkout (the tree with `kernel/lsm_hook.c`)
set -eu

SUSFS_SRC=${1:?path to the susfs sources checkout}
HERE=$(cd "$(dirname "$0")" && pwd)
tp=$SUSFS_SRC/kernel

# The header our hook layer is written against, asserted by name before anything is copied: a
# branch can move it, and a build that succeeds against a renamed field installs nothing.
for token in \
  "struct ksu_lsm_hook" "KSU_LSM_HOOK_INSERT" "KSU_LSM_HOOK_INIT" \
  "head_name" "hook_offset" "replacement" "insert" "struct security_hook_list" \
  "ksu_register_lsm_hook" "ksu_unregister_lsm_hook" "ksu_lsm_hook_init" "ksu_lsm_hook_exit" \
; do
  grep -q "$token" "$tp/lsm_hook.h" || {
    echo "::error::upstream's lsm_hook.h no longer declares '$token', which lsm_hook.c is written"
    echo "::error::against. Update susfs-lkm/lsm_hook.c for the new API before building."
    exit 1
  }
done
echo "hook header surface present: $(grep -c . "$tp/lsm_hook.h") lines"

# The one thing `samsung` is: our install path in place of upstream's write into read-only memory.
cp "$HERE/../lsm_hook.c" "$tp/lsm_hook.c"
grep -q "install path is kprobe-based" "$tp/lsm_hook.c" || {
	echo "::error::susfs-lkm/lsm_hook.c was not copied into the checkout"
	exit 1
}
if grep -q "ksu_lsm_hook_patch_slot" "$tp/lsm_hook.c"; then
	echo "::error::the checkout still holds upstream's read-only-memory install path"
	exit 1
fi
echo "hook layer: this repository's kprobe install path"

# The control nodes: not created at all, rather than created and hidden per caller.
sed -i 's/\r$//' "$tp/susfs_main.c"
grep -qx 'bool susfs_expose_proc = true;' "$tp/susfs_main.c" || {
	echo "::error::upstream moved the expose_proc default; this variant must not ship the control nodes"
	exit 1
}
sed -i 's/^bool susfs_expose_proc = true;$/bool susfs_expose_proc = false;/' "$tp/susfs_main.c"
grep -qx 'bool susfs_expose_proc = false;' "$tp/susfs_main.c" || {
	echo "::error::the expose_proc default did not change in the samsung variant"
	exit 1
}
echo "control nodes: expose_proc defaults to false"

# The one thing a load here cannot survive: a second `__this_module` relocation.
#
# `apply_relocations()` writes `cleanup_module` into `__this_module` at this KDIR's own
# `struct module.exit` offset. This tree compiles `exit` at 0x368; the phone keeps
# `target_list` at 0x368 (`module_unload_init` has just set its list head to point at
# itself), so that write corrupts the list and `mod_sysfs_setup` walks it as a
# `struct module_use` and dereferences two instructions of `.exit.text`. That is exactly
# RWC 117/119: `mod_sysfs_setup+0x25c`, one boot per attempt, no module code ever run.
# The checked-in `check-this-module.py` refuses a `.ko` whose only second slot is 0x368;
# this edit makes the build one that cannot fail that gate: no `module_exit` -> no
# `cleanup_module` symbol -> no relocation but `init` at 0x178 -> `target_list` stays the
# empty list `module_unload_init` made -> the inlined `add_usage_links` hits
# `list_empty()` and skips the dereference by construction. `exit` stays NULL, so
# `delete_module` refuses to unload: the hiding module is not unloadable either way once
# armed. `susfs_exit` has no other caller (the supercalls use `THIS_MODULE` only as a
# pointer for `module_put`/`try_module_get`), so the function goes too - an unused
# `__exit` function is a warning this build permits, but a dangling one is not honest.
grep -q '^static void __exit susfs_exit(void)$' "$tp/susfs_main.c" || {
	echo "::error::upstream moved or renamed susfs_exit in susfs_main.c; re-check this edit "
	echo "::error::against the new shape before building - it decides RWC 117/119's bug"
	exit 1
}
grep -q '^module_exit(susfs_exit);$' "$tp/susfs_main.c" || {
	echo "::error::the module_exit(susfs_exit) line moved in susfs_main.c; re-check this edit"
	exit 1
}
sed -i '/^module_exit(susfs_exit);$/d' "$tp/susfs_main.c"
sed -i '/^static void __exit susfs_exit(void)$/,/^}$/d' "$tp/susfs_main.c"
if grep -q 'susfs_exit' "$tp/susfs_main.c"; then
	echo "::error::susfs_exit is still referenced in susfs_main.c after the removal edit"
	exit 1
fi
grep -q '^module_init(susfs_init);$' "$tp/susfs_main.c" || {
	echo "::error::module_init(susfs_init) is gone from susfs_main.c - the edit removed too much"
	exit 1
}
echo "module exit: none - no second relocation, exit stays the device's business"


# The stat family and the dirent rewrite, from kretprobes on the syscall entries.
cp "$HERE/../susfs_kp_stat.c" "$tp/susfs_kp_stat.c"
sed -i 's/^static __nocfi void susfs_kstat_post(/__nocfi void susfs_kstat_post(/' "$tp/susfs_kstat.c"
grep -q '^__nocfi void susfs_kstat_post(' "$tp/susfs_kstat.c" || {
	echo "::error::susfs_kstat_post() is still static - the tracepoint overlay cannot call it"; exit 1; }
sed -i 's/^static int kstat_hooks_armed;$/extern int susfs_kp_stat_init(void);\nextern void susfs_kp_stat_exit(void);\n\nint kstat_hooks_armed;/' "$tp/susfs_kstat.c"
grep -qx 'int kstat_hooks_armed;' "$tp/susfs_kstat.c" || {
	echo "::error::kstat_hooks_armed is still static - the overlay cannot see whether the wrappers armed"; exit 1; }
grep -q '^extern int susfs_kp_stat_init(void);' "$tp/susfs_kstat.c" || {
	echo "::error::the overlay's prototypes did not land in susfs_kstat.c"; exit 1; }
sed -i 's/^\([[:space:]]*\)if (!kstat_hooks_armed) {/\1susfs_kp_stat_init();\n\1if (!kstat_hooks_armed) {/' "$tp/susfs_kstat.c"
grep -q '^[[:space:]]*susfs_kp_stat_init();' "$tp/susfs_kstat.c" || {
	echo "::error::susfs_kstat_init() does not call the tracepoint overlay"; exit 1; }
sed -i 's/^\([[:space:]]*\)kstat_maps_disarm();$/\1susfs_kp_stat_exit();\n\n\1kstat_maps_disarm();/' "$tp/susfs_kstat.c"
grep -q '^[[:space:]]*susfs_kp_stat_exit();' "$tp/susfs_kstat.c" || {
	echo "::error::susfs_kstat_exit() does not tear the tracepoint overlay down"; exit 1; }
sed -i 's/^\([[:space:]]*\)fsnotify_lifetime\.o btf_layout\.o sus_dirent\.o$/\1fsnotify_lifetime.o btf_layout.o sus_dirent.o susfs_kp_stat.o/' "$tp/Makefile"
grep -q 'susfs_kp_stat\.o' "$tp/Makefile" || {
	echo "::error::the Makefile does not build susfs_kp_stat.o"; exit 1; }
echo "kp_stat overlay: in place, post-processing on kretprobes"

# RWC 120: Samsung's CONFIG_KDP_NS splits `struct mount`.
#
# Under CONFIG_KDP_NS (this phone's own config carries it) a mount is
# `struct kdp_mount { struct mount mount; struct vfsmount *mnt; }`, and the vfsmount
# fields (mnt_root / mnt_sb / mnt_flags) live in the separately allocated
# `struct kdp_vfsmount`. fs/namespace.c reads every one of them through
# `((struct kdp_mount *)mnt)->mnt`; the embedded copy at mount+0x20 is never
# initialized for a real mount. Upstream's module reads them from the embedded
# copy, so its d_path() call got a NULL dentry and the phone died in the first
# namespace the marking scan reached: `sus_mount_mark_ksu_mounts+0x578` ->
# `d_path+0x30` (RWC 120). The device BTF is the evidence: `struct kdp_mount`
# is 360 bytes with `mnt` at 0x160, `struct kdp_vfsmount` is 72 bytes with
# `bp_mount` at 0x40.
#
# The patch routes those reads through sus_mount_mnt_of(), which returns the kdp
# pointer when the config says KDP and `&r->mnt` otherwise - the #else branch is
# the old behaviour verbatim, so the patch is inert on a non-KDP config. The
# cursor test keeps reading the embedded mnt_flags, exactly like the kernel's own
# mnt_is_cursor(): proc_namespace.c sets MNT_CURSOR on the embedded copy, and a
# cursor's kdp pointer is past the end of its object and must never be read.
PATCH="$HERE/sus_mount-kdp.patch"
test -f "$PATCH" || { echo "::error::sus_mount-kdp.patch is missing from the repository"; exit 1; }
git -C "$SUSFS_SRC" apply --check "$PATCH" || {
	echo "::error::sus_mount-kdp.patch does not apply to this pin's kernel/sus_mount.c;"
	echo "::error::RWC 120's fix would be silently absent. Rebase the patch on the new source."
	exit 1
}
git -C "$SUSFS_SRC" apply "$PATCH"
grep -q 'static inline struct vfsmount \*sus_mount_mnt_of(struct mount \*r)' "$tp/sus_mount.c" || {
	echo "::error::sus_mount_mnt_of() did not land in sus_mount.c"; exit 1; }
n=$(grep -c 'sus_mount_mnt_of(r)' "$tp/sus_mount.c" || true)
test "$n" -eq 16 || {
	echo "::error::sus_mount_mnt_of(r) appears $n times, not the 16 field reads the patch converts"
	exit 1
}
for pat in 'r->mnt\.mnt_root' 'r->mnt\.mnt_sb' 'p\.mnt = &r->mnt;' 'mnt_path\.mnt = &r->mnt;'; do
	grep -q "$pat" "$tp/sus_mount.c" && {
		echo "::error::sus_mount.c still reads an embedded vfsmount field ($pat) - the KDP fix is incomplete"
		exit 1
	}
done
echo "sus_mount: KDP-aware vfsmount reads in place (RWC 120)"

# RWC 121: a kprobe must not sit on a BTI landing pad.
#
# /proc/modules and /proc/kallsyms are served by seq_read_iter(), which calls the
# seq_operations callback through a POINTER: `ldr x8, [op, #0x18] ; blr x8`.  This
# kernel enforces BTI at EL1, so that blr is a BTYPE=c guarded branch and the
# instruction at the target must be a landing pad - `bti c`/`bti jc` or
# `paciasp`/`pacibsp`.  m_show and s_show both begin with `paciasp`.  Upstream's
# probe was armed at byte 0, which replaces that landing pad with BRK, and the
# first read of the file then dies: RWC 121 was `grep` reading /proc/modules,
# "Internal error: Oops - BTI", pc = seq_read_iter+0x14c (the instruction after
# the blr, i.e. LR) with x8 = m_show+0.  /proc/kallsyms carried the same landmine.
#
# (Direct branches are not BTI-checked.  The LSM probes are NOT in that class, whatever
# upstream's comment said: measured at security_inode_permission+0x34 this kernel does
# `ldr x8,[x21,#0x18] ; blr x8`, an indirect branch whose target must be a landing pad.  They
# therefore go through the pad-entry rule in lsm_hook.c / susfs_bti.h and never byte 0.)
#
# The patch arms the probe on the first instruction AFTER the landing-pad prefix
# and strips the PAC field out of LR before resuming, because `paciasp` has
# already run and LR is signed at that point.  Only landing pads are skipped, so
# no frame exists yet and leaving the function early is exactly "was not called".
PATCH="$HERE/susfs-hide-syms-bti.patch"
test -f "$PATCH" || { echo "::error::susfs-hide-syms-bti.patch is missing from the repository"; exit 1; }
git -C "$SUSFS_SRC" apply --check "$PATCH" || {
	echo "::error::susfs-hide-syms-bti.patch does not apply to this pin's kernel/susfs_hide_syms.c;"
	echo "::error::RWC 121's fix would be silently absent. Rebase the patch on the new source."
	exit 1
}
git -C "$SUSFS_SRC" apply "$PATCH"
grep -q 'static int hide_syms_probe_point(unsigned long body' "$tp/susfs_hide_syms.c" || {
	echo "::error::hide_syms_probe_point() did not land in susfs_hide_syms.c"; exit 1; }
grep -q 'asm volatile(".inst 0xdac143e0"' "$tp/susfs_hide_syms.c" || {
	echo "::error::the PAC strip (xpaci) did not land in susfs_hide_syms.c"; exit 1; }
n=$(grep -c 'hide_syms_skip_caller(regs, hide_syms_' "$tp/susfs_hide_syms.c" || true)
if [ "$n" -ne 3 ]; then
	echo "::error::hide_syms_skip_caller(regs, ...) appears $n times, not the 3 skip sites the patch converts"
	exit 1
fi
if grep -q 'regs->pc = regs->regs\[30\]' "$tp/susfs_hide_syms.c"; then
	echo "::error::a probe still resumes through raw x30 - the landing-pad fix is incomplete"
	exit 1
fi
echo "hide_syms: probes armed after the BTI landing pad (RWC 121)"

# RWC 123: the module's own probes were the BTI landmine, and the return probes cannot all be
# moved.
#
# Two device panics (`Internal error: Oops - BTI`, pc/lr = invoke_syscall+0x58, thread
# libksud.so) had the same single cause: reboot_kp sat on byte 0 of __arm64_sys_reboot, whose
# paciasp IS the BTYPE=c landing pad invoke_syscall() needs for its
# `ldr x20,[x2,w8,uxtw #3] ; blr x20`.  Every reboot(2) - the real one and the SUSFS supercall -
# replaced that pad with BRK and took a BTI exception before any handler could run.  It is armed
# once from the fatal layer, so it fired on the first boot that loaded the module.
#
# The general rule, and why it cannot be "move everything": a KPROBE may be moved past the
# prologue prefix (landing pads, an early-exit guard and paciasp); with no frame yet, leaving
# early is exactly "never called", and the resume strips the PAC paciasp left in LR (xpaci; the
# strip is unconditional because over-stripping is the identity, under-stripping faults).  A
# KRETPROBE may not: arch_prepare_kretprobe() substitutes &kretprobe_trampoline into x30, and the
# function's own paciasp must sign that value for the epilogue's autiasp to authenticate it.  So
# a return probe has exactly one safe offset - the paciasp - and only when a `bti` landing pad
# precedes it (copy_mnt_ns is `bti c; cbz; paciasp`).  A function whose byte 0 IS paciasp has no
# safe offset at all: byte 0 is both the pad and the signer.  That is refused for anything a
# table or a resolved pointer can reach (sys_call_table, seq_operations/i_op slots,
# single_open's show argument, this module's own pfn_d_path/or_vfs_statfs_fn), and kept only for
# functions proven direct-call-only from the device's disassembly (attach_recursive_mnt and
# do_statx: static/unexported, two or one `bl` call sites, no address materialised in the image).
#
# Every refusal is an honest, logged failure.  The refused disguises (statx stx_mnt_id, fdinfo
# mnt_id, maps dev:ino, map_files readlink, uname, open_redirect's readlink/statfs) stay
# un-disguised until a PAC-safe return hook exists - never armed "somewhere useful", which is
# what the two panics cost.
PATCH="$HERE/susfs-bti-probe-policy.patch"
test -f "$PATCH" || { echo "::error::susfs-bti-probe-policy.patch is missing from the repository"; exit 1; }
git -C "$SUSFS_SRC" apply --check "$PATCH" || {
	echo "::error::susfs-bti-probe-policy.patch does not apply to this pin's kernel sources;"
	echo "::error::RWC 123's fix would be silently absent. Rebase the patch on the new source."
	exit 1
}
git -C "$SUSFS_SRC" apply "$PATCH"

H=$tp/susfs_bti.h
test -f "$H" || { echo "::error::kernel/susfs_bti.h did not land - the probe-point policy is missing"; exit 1; }
for tok in \
  'susfs_bti_landing_pad' 'susfs_bti_pac_pad' 'susfs_bti_unsteppable' 'susfs_bti_guard' \
  'susfs_bti_probe_point' 'susfs_bti_probe_point_pad_entry' 'susfs_bti_kretprobe_point' \
  'susfs_bti_arm_kprobe' 'susfs_bti_arm_kprobe_pad_entry' \
  'susfs_bti_arm_kretprobe' 'susfs_bti_refuse_kretprobe' 'susfs_bti_skip_caller' \
  'susfs_bti_log_words' 'susfs_bti_kprobes_disarmed' 'susfs_bti_verify_armed' \
  'asm volatile(".inst 0xdac143e0"' 'kp->offset = (unsigned int)(probe - body);' \
  'kp->symbol_name'; do
	grep -q "$tok" "$H" || {
		echo "::error::susfs_bti.h is missing '$tok' - the probe-point policy is incomplete"
		exit 1
	}
	done
if grep -q 'kp->symbol_name = NULL' "$H"; then
	echo "::error::susfs_bti.h clears .symbol_name, but .symbol_name + .offset is the arming form"
	exit 1
fi

# xpaci must exist in exactly two places: the device-proven hide_syms copy (RWC 121) and the
# shared header (RWC 123).  A third hand-rolled copy is a path that will drift from the rule.
xpaci_files=$(grep -rl 'asm volatile(".inst 0xdac143e0"' "$tp" | wc -l | tr -d ' ')
test "$xpaci_files" -eq 2 || {
	echo "::error::xpaci appears in $xpaci_files files, not the 2 that own it"
	exit 1
}

count_is() {	# file pattern expected label
	n=$(grep -c "$2" "$tp/$1" || true)
	test "$n" -eq "$3" || {
		echo "::error::$4 appears $n time(s) in $1, expected $3"
		exit 1
	}
}
# Every probe goes through the classifier; the counts are the call sites the patch installs.
count_is sus_map.c 'susfs_bti_arm_kprobe(' 1 "susfs_bti_arm_kprobe()"
count_is sus_map.c 'susfs_bti_skip_caller(regs)' 3 "susfs_bti_skip_caller(regs)"
count_is sus_map.c 'susfs_bti_refuse_kretprobe(' 1 "susfs_bti_refuse_kretprobe()"
count_is sus_mount.c 'susfs_bti_arm_kprobe(' 2 "susfs_bti_arm_kprobe()"
count_is sus_mount.c 'susfs_bti_arm_kretprobe(&' 3 "susfs_bti_arm_kretprobe()"
count_is sus_mount.c 'susfs_bti_refuse_kretprobe(' 2 "susfs_bti_refuse_kretprobe()"
count_is sus_mount.c 'susfs_bti_skip_caller(regs)' 1 "susfs_bti_skip_caller(regs)"
count_is susfs_avc_spoof.c 'susfs_bti_arm_kprobe(' 1 "susfs_bti_arm_kprobe()"
# The reboot wrapper is reached through sys_call_table, so it takes the pad-entry form; a plain
# arm there is the RWC 123 arming again, whatever the classifier answers.
count_is susfs_supercall.c 'susfs_bti_arm_kprobe_pad_entry(' 1 "susfs_bti_arm_kprobe_pad_entry()"
count_is susfs_supercall.c 'susfs_bti_arm_kprobe(' 0 "a non-pad arm helper in the supercall layer"
count_is susfs_supercall.c 'susfs_bti_kprobes_disarmed(' 1 "the disarmed-switch read"
count_is susfs_supercall.c 'susfs_bti_verify_armed(' 1 "the read-back verifier"
count_is susfs_supercall.c 'susfs_bti_log_words(' 1 "the raw-word evidence on a failed read-back"
count_is susfs_supercall.c 'susfs_bti_skip_caller(regs)' 1 "susfs_bti_skip_caller(regs)"
count_is susfs_supercall.c 'probe=%px' 1 "the arming log's probe address"
# The LSM slots are pointer-reached too (security_inode_permission+0x34 is `ldr x8,[x21,#0x18];
# blr x8`), so the same three statements must be in the copied hook file.
count_is lsm_hook.c 'susfs_bti_probe_point_pad_entry(' 1 "the pad-entry classifier in the LSM layer"
count_is lsm_hook.c 'susfs_bti_verify_armed(' 1 "the LSM read-back verifier"
count_is lsm_hook.c 'susfs_bti_strip_pac(' 1 "the deny path's PAC strip"
count_is susfs_open_redirect.c 'susfs_bti_refuse_kretprobe(' 4 "susfs_bti_refuse_kretprobe()"
count_is susfs_kstat.c 'susfs_bti_refuse_kretprobe(' 1 "susfs_bti_refuse_kretprobe()"
count_is susfs_uname.c 'susfs_bti_refuse_kretprobe(' 1 "susfs_bti_refuse_kretprobe()"

# The registrations those helpers replace must be gone: a re-added one bypasses every decision
# above, and `register_kprobe(&kp_...)` alone would arm at byte 0 again.
fatal=""
for pat in \
  '[^n]register_kretprobe(&kr_statx)' '[^n]register_kretprobe(&kr_map_files)' \
  '[^n]register_kretprobe(&krp_kstat_map_vma)' '[^n]register_kretprobe(&kr_or_maps)' \
  '[^n]register_kretprobe(&kr_or_dpath)' '[^n]register_kretprobe(&kr_or_vfs_statfs)' \
  '[^n]register_kretprobe(&krp)' 'ksu_find_symbol_all("seq_show"' \
  'susfs_bti_set_offset'; do
	if grep -rq "$pat" "$tp"; then
		echo "::error::a probe bypasses the BTI/PAC classifier ('$pat' is still present)"
		fatal=1
	fi
done
test -z "$fatal" || exit 1
if grep -rq 'regs->pc = regs->regs\[30\]' "$tp"; then
	echo "::error::a probe still resumes through raw x30 - the landing-pad fix is incomplete"
	exit 1
fi
# ... and the refusals must be visible, not silent.
refusals=$(grep -rl 'PAC-safe return hook' "$tp" | wc -l | tr -d ' ')
test "$refusals" -eq 4 || {
	echo "::error::the PAC-safe-return-hook refusal note is in $refusals files, expected 4"
	exit 1
}
echo "probe policy: every probe classified, return probes that cannot move refused (RWC 123)"
