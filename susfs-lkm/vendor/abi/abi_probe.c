// SPDX-License-Identifier: GPL-2.0
//
// The offsets this KDIR compiles the hiding module against, as numbers, so they can be compared
// with the *device's* recovered BTF before anything is loaded on the phone.
//
// This is not a test and it is never loaded on a phone. It is a data-only module: one array of
// `unsigned long` emitted from the vendor tree's own headers, read out of the built `.ko` by the
// workflow. If a value here disagrees with the device's `vmlinux` BTF, the module that carries
// the same headers would run with the wrong offsets - which is exactly the failure a vermagic
// string cannot catch, and the one this gate exists to refuse.
//
// The order is fixed and is mirrored, line for line, by `schema.txt` beside this file. A value
// added here without the name added there (or the other way round) is a gate that reads a number
// into the wrong name, so both files are counted and the workflow compares the counts.
//
#include <linux/module.h>
#include <linux/sched.h>
#include <linux/sched/task.h>
#include <linux/cred.h>
#include <linux/fs.h>
#include <linux/file.h>
#include <linux/mm_types.h>
#include <linux/kprobes.h>
#include <linux/seq_file.h>
#include <linux/path.h>
#include <linux/lsm_hooks.h>
#include <linux/user_namespace.h>
#include <linux/stat.h>
#include <linux/dcache.h>
#include <linux/workqueue.h>

#define V(x) ((unsigned long)(x))

const unsigned long susfs_abi_values[] __used = {
	/* --- the struct the hiding reads and writes most: task_struct --- */
	V(sizeof(struct task_struct)),
	V(offsetof(struct task_struct, usage)),
	V(offsetof(struct task_struct, stack)),
	V(offsetof(struct task_struct, flags)),
	V(offsetof(struct task_struct, sched_task_group)),
	V(offsetof(struct task_struct, tasks)),
	V(offsetof(struct task_struct, mm)),
	V(offsetof(struct task_struct, active_mm)),
	V(offsetof(struct task_struct, pid)),
	V(offsetof(struct task_struct, tgid)),
	V(offsetof(struct task_struct, group_leader)),
	V(offsetof(struct task_struct, real_cred)),
	V(offsetof(struct task_struct, cred)),
	V(offsetof(struct task_struct, comm)),
	V(offsetof(struct task_struct, fs)),
	V(offsetof(struct task_struct, files)),
	V(offsetof(struct task_struct, nsproxy)),
	V(offsetof(struct task_struct, pi_lock)),
	V(offsetof(struct task_struct, pi_waiters)),
	V(offsetof(struct task_struct, pi_top_task)),
	V(offsetof(struct task_struct, pi_blocked_on)),
	V(offsetof(struct task_struct, robust_list)),
	V(offsetof(struct task_struct, thread)),

	/* --- credentials: the struct Samsung's KDP_CRED is about --- */
	V(sizeof(struct cred)),
	V(offsetof(struct cred, usage)),
	V(offsetof(struct cred, uid)),
	V(offsetof(struct cred, gid)),
	V(offsetof(struct cred, cap_effective)),

	/* --- mm, whose tail is where Samsung's KABI reservations sit --- */
	V(sizeof(struct mm_struct)),
	V(offsetof(struct mm_struct, mm_users)),
	V(offsetof(struct mm_struct, mm_count)),
	V(offsetof(struct mm_struct, pgd)),
	V(offsetof(struct mm_struct, mmap_lock)),
	V(offsetof(struct mm_struct, arg_start)),
	V(offsetof(struct mm_struct, env_start)),

	/* --- the VFS types the path and inode hooks are given --- */
	V(sizeof(struct super_block)),
	V(offsetof(struct super_block, s_root)),
	V(offsetof(struct super_block, s_op)),
	V(sizeof(struct inode)),
	V(offsetof(struct inode, i_ino)),
	V(offsetof(struct inode, i_sb)),
	V(sizeof(struct dentry)),
	V(offsetof(struct dentry, d_name)),
	V(offsetof(struct dentry, d_parent)),
	V(sizeof(struct qstr)),
	V(offsetof(struct qstr, name)),
	V(sizeof(struct file)),
	V(offsetof(struct file, f_path)),
	V(offsetof(struct file, f_inode)),
	V(offsetof(struct file, private_data)),
	V(sizeof(struct file_operations)),
	V(offsetof(struct file_operations, read)),
	V(offsetof(struct file_operations, write)),
	V(offsetof(struct file_operations, unlocked_ioctl)),
	V(offsetof(struct file_operations, mmap)),
	V(sizeof(struct path)),

	/* --- the LSM surface the samsung variant installs hooks into --- */
	V(sizeof(struct security_hook_heads)),
	V(offsetof(struct security_hook_heads, inode_getattr)),
	V(offsetof(struct security_hook_heads, inode_permission)),
	V(sizeof(struct security_hook_list)),

	/* --- the probe machinery: kprobe and its return instance --- */
	V(sizeof(struct kprobe)),
	V(sizeof(struct kretprobe)),
	V(sizeof(struct kretprobe_instance)),
	V(offsetof(struct kretprobe_instance, data)),

	/* --- misc types the stat and dirent rewrite pass through --- */
	V(sizeof(struct kstat)),
	V(offsetof(struct kstat, ino)),
	V(offsetof(struct kstat, size)),
	V(sizeof(struct seq_file)),
	V(offsetof(struct seq_file, private)),
	V(sizeof(struct work_struct)),
	V(sizeof(struct user_namespace)),
	V(offsetof(struct user_namespace, uid_map)),
};

static int __init abi_probe_init(void)
{
	return 0;
}

static void __exit abi_probe_exit(void)
{
}

module_init(abi_probe_init);
module_exit(abi_probe_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Offsets this KDIR compiles the hiding module against; data only, never loaded on a phone.");
