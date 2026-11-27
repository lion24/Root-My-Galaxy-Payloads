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
