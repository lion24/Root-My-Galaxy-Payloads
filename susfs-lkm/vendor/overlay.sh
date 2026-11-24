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
