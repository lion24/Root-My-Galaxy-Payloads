/*
 * dm2q-S916BXXUAGZIF: the values this firmware decides.
 *
 * GENERATED - do not edit by hand. Written by `tools/port/emit_target.py` from a measurement of
 * the boot partition of the SM-S916B (100663296 bytes, sha256 ed1c34b5..., raw Image 46860800 bytes, sha256 cc591b86...)
 * on 2026-11-23, and documented in `docs/SM-S916B-S916BXXUAGZIF.md`.
 *
 * Every line carries the symbol, string or device reading it was measured from. Nothing here was
 * carried over from another release: the rest of the target header is the shape of a 5.15 Samsung
 * kernel, and this file is the part that changes with a firmware.
 */
#ifndef DM2Q_S916B_UAGZIF_MEASURED
#define DM2Q_S916B_UAGZIF_MEASURED

#define INIT_TASK_OFF 0x02c05400ULL  /* symbol init_task */
#define PREPARE_KERNEL_CRED_OFF 0x0011e694ULL  /* symbol prepare_kernel_cred */
#define COMMIT_CREDS_OFF 0x001203d0ULL  /* symbol commit_creds */
#define OVERRIDE_CREDS_OFF 0x0011f4a8ULL  /* symbol override_creds */
#define ROOT_TASK_GROUP_OFF 0x02cb9ac0ULL  /* symbol root_task_group */
#define SELINUX_ENFORCING_OFF 0x02d8e600ULL  /* symbol selinux_state */
#define KMALLOC_CACHES_OFF 0x020671f0ULL  /* symbol kmalloc_caches */
#define ANON_PIPE_BUF_OPS_OFF 0x01e81d20ULL  /* symbol anon_pipe_buf_ops */
#define SYSTEM_UNBOUND_WQ_OFF 0x02a90808ULL  /* symbol system_unbound_wq */
#define CALL_USERMODEHELPER_EXEC_WORK_OFF 0x00104888ULL  /* symbol call_usermodehelper_exec_work */
#define ASHMEM_FOPS_OFF 0x020100f8ULL  /* symbol ashmem_fops */
#define ASHMEM_MISC_FOPS_OFF 0x02bfd278ULL  /* `ashmem_fops` pointer in the `"ashmem"` miscdevice */
#define ASHMEM_IOCTL_OFF 0x011513a0ULL  /* symbol ashmem_ioctl */
#define ASHMEM_COMPAT_IOCTL_OFF 0x011519fcULL  /* symbol compat_ashmem_ioctl */
#define ASHMEM_MMAP_OFF 0x01151a54ULL  /* symbol ashmem_mmap */
#define ASHMEM_OPEN_OFF 0x01151d34ULL  /* symbol ashmem_open */
#define ASHMEM_RELEASE_OFF 0x01151dccULL  /* symbol ashmem_release */
#define ASHMEM_SHOW_FDINFO_OFF 0x01151ee8ULL  /* symbol ashmem_show_fdinfo */
#define CONFIGFS_READ_ITER_OFF 0x005d8a1cULL  /* symbol configfs_read_iter */
#define CONFIGFS_BIN_WRITE_ITER_OFF 0x005d9444ULL  /* symbol configfs_bin_write_iter */
#define COPY_SPLICE_READ_OFF 0x00528e28ULL  /* symbol generic_file_splice_read */
#define NOOP_LLSEEK_OFF 0x004bc6b4ULL  /* symbol noop_llseek */
#define SLIDE_NFULNL_LOGGER_NAME_OFF 0x01d607d9ULL  /* `nfulnl_logger.name` -> "nfnetlink_log" */
#define SLIDE_NFULNL_LOGGER_OBJECT_OFF 0x02a91e50ULL  /* symbol nfulnl_logger */
#define SLIDE_RANDOM_TABLE_BOOT_ID_DATA_PTR_OFF 0x02bbabc8ULL  /* `sysctl_bootid` */
#define SLIDE_SYSCTL_BOOTID_OFF 0x02e6c131ULL  /* symbol sysctl_bootid */
#define SLIDE_TRACEFS_EVENT_ID 108  /* read on the device */
#define SLIDE_TRACEFS_WORKER_CALLER_OFF 0x0010ddfcULL  /* given on the command line */
#define SLIDE_TRACEFS_VFORK_CALLER_OFF 0x000c90bcULL  /* given on the command line */
#define KIMAGE_TEXT_BASE 0xffffffc008000000ULL  /* `_text`'s link address */
#define P0_KERNEL_PHYS_LOAD 0x80000000ULL  /* bootloader load address + text_offset */
#define BUILD_FINGERPRINT "samsung/dm2qxeea/dm2q:17/CP2A.260605.016/S916BXXUAGZIF:user/release-keys"  /* `ro.build.fingerprint` */
#endif
