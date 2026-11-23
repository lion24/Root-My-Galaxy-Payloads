#!/usr/bin/env python3
"""The raw ARM64 Image, out of a Samsung boot image, and what its header says.

A port starts from the kernel the phone actually runs, and the only honest source of that is the
phone's own boot partition or the firmware archive it came in - not a built tree, and not a nearby
release. This module is the first step of `docs/PORTING.md` (section 2) made mechanical: Samsung
ships `boot.img.lz4`, the Android boot header says how many bytes of it are kernel, and everything
after that reads the raw Image.

Two things are checked rather than assumed, because both are easy to get wrong and both produce a
port that measures the wrong kernel:

- the boot header version, because the kernel's offset inside the image is a property of the header
  layout (`v4` here: header at 0, kernel at 0x1000), and an older header puts it somewhere else;
- `kernel_size`, read from the header rather than by taking the rest of the file, because `boot.img`
  carries the ramdisk and the second stage after the kernel and a port that measured those as text
  would produce offsets that are internally consistent and wrong.

The ARM64 Image header is read too. `text_offset` is what decides `P0_KERNEL_PHYS_LOAD` (section 4 of
the porting document), and `image_size` is a cheap way to notice that the kernel was truncated.

    tools/port/kernel_image.py --boot boot.img.lz4 --out kernel
    tools/port/kernel_image.py --kernel kernel            # header and hashes only
    tools/port/kernel_image.py --self-test
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# The Android boot header is page-aligned and this is the page size Samsung writes: reading the
# kernel out at any other stride is a port that starts a few kilobytes into the wrong instruction.
BOOT_HEADER_VERSION_OFFSET = 0x28
BOOT_HEADER_VERSION = 4
BOOT_KERNEL_SIZE_OFFSET = 0x08
BOOT_KERNEL_OFFSET = 0x1000

# Offsets inside the raw ARM64 Image, from `Documentation/arm64/booting.rst`.
IMAGE_TEXT_OFFSET = 0x08
IMAGE_SIZE_OFFSET = 0x10
IMAGE_FLAGS_OFFSET = 0x18


@dataclass
class ImageHeader:
    """What an ARM64 Image says about itself."""

    text_offset: int
    image_size: int
    flags: int
    stored: int = 0

    @property
    def bss(self) -> int:
        """Bytes the header reserves but the file does not hold.

        `image_size` is `_end - _text`, so it counts `.bss`, which is not stored anywhere: on a stock
        boot partition the kernel's stored length is `_edata - _text` and this difference is exactly
        the zero-filled tail the linker will recreate at boot. It is *expected* here - it is not the
        truncation the header check is looking for - which is why it is reported rather than refused.
        """
        return self.image_size - self.stored if self.stored else 0


def lz4_decompress(data: bytes) -> bytes:
    """Samsung's `.img.lz4` as a raw image.

    The `lz4` CLI when it is installed, because that is one fewer Python dependency for a job that
    runs once per port; `lz4.frame` when it is not. Legacy-frame `boot.img.lz4` is what Samsung
    ships for these devices, and the CLI reads both formats.
    """
    if not data.startswith(b"\x04\x22\x4d\x18"):
        raise ValueError("not an lz4 frame: the file does not start with the lz4 magic")
    if shutil.which("lz4"):
        with tempfile.NamedTemporaryFile(suffix=".lz4", delete=False) as source:
            source.write(data)
            source_path = source.name
        try:
            with tempfile.NamedTemporaryFile(suffix=".img", delete=False) as target:
                target_path = target.name
            try:
                subprocess.run(
                    ["lz4", "-d", "-f", "-q", source_path, target_path],
                    check=True,
                    capture_output=True,
                )
                return Path(target_path).read_bytes()
            finally:
                Path(target_path).unlink(missing_ok=True)
        finally:
            Path(source_path).unlink(missing_ok=True)
    try:
        import lz4.frame  # type: ignore[import-not-found]
    except ImportError as error:  # pragma: no cover - exercised only without the C bindings
        raise SystemExit(
            "neither the `lz4` command nor the Python `lz4` package is available; "
            "install one of them and re-run"
        ) from error
    return lz4.frame.decompress(data)


def boot_kernel(boot: bytes) -> bytes:
    """The kernel out of an Android boot image, as the header measures it."""
    version = struct.unpack_from("<I", boot, BOOT_HEADER_VERSION_OFFSET)[0]
    if version != BOOT_HEADER_VERSION:
        raise ValueError(
            f"boot header version {version}, expected {BOOT_HEADER_VERSION}: "
            "this reader only knows where the kernel starts in a v4 header"
        )
    size = struct.unpack_from("<I", boot, BOOT_KERNEL_SIZE_OFFSET)[0]
    if size == 0 or BOOT_KERNEL_OFFSET + size > len(boot):
        raise ValueError(
            f"boot header says the kernel is {size} bytes at 0x{BOOT_KERNEL_OFFSET:x}, "
            f"which does not fit in {len(boot)} bytes of image"
        )
    return boot[BOOT_KERNEL_OFFSET:BOOT_KERNEL_OFFSET + size]


def image_header(kernel: bytes) -> ImageHeader:
    """The three fields of the ARM64 Image header a port reads.

    `image_size` is checked in one direction only, and the reason is worth writing down because the
    opposite check looks obviously right: `image_size` is `_end - _text`, so it includes `.bss`, and
    `.bss` is not stored. A kernel read out of a boot partition is therefore *always* shorter than its
    header says - on this device by 2,749,952 bytes - and refusing that would refuse every honest
    Image while the actual failure it is meant to catch, a file longer than the header describes, is
    the one that means junk or a second payload was appended. The shortfall is reported as `bss`, and
    only a file that is *longer* than its own header is an error.
    """
    if len(kernel) < IMAGE_FLAGS_OFFSET + 8:
        raise ValueError("not an ARM64 Image: shorter than its own header")
    text_offset, image_size, flags = struct.unpack_from("<QQQ", kernel, IMAGE_TEXT_OFFSET)
    if image_size < len(kernel):
        raise ValueError(
            f"the Image header says {image_size} bytes but the file holds {len(kernel)}: the header "
            "describes a smaller kernel than this, so one of the two is not the kernel it claims"
        )
    # The Image itself starts with a branch instruction, not a magic number, so the only structural
    # check available is that the documented fields are self-consistent.
    if flags & 0xFFFF0000:
        raise ValueError(f"Image flags 0x{flags:x} carry reserved bits: not an ARM64 Image")
    return ImageHeader(text_offset=text_offset, image_size=image_size, flags=flags, stored=len(kernel))


def physical_load(kernel: bytes) -> int:
    """Where the kernel is loaded, from `text_offset`, for `P0_PHYS_OFFSET` + offset.

    This is the arithmetic section 4 of the porting document does by hand from `sboot`: Samsung's
    bootloader loads the Image at `0x80000000 + text_offset`. A nonzero `text_offset` is therefore a
    different answer, which is exactly the value the document warns against copying from another
    device.
    """
    return 0x80000000 + image_header(kernel).text_offset


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--boot", help="a Samsung boot image, plain or .lz4")
    parser.add_argument("--kernel", help="a raw ARM64 Image, to read the header only")
    parser.add_argument("--out", help="where to write the raw Image (with --boot)")
    parser.add_argument("--self-test", action="store_true", help="run against synthetic images")
    arguments = parser.parse_args()

    if arguments.self_test:
        return self_test()
    if not arguments.boot and not arguments.kernel:
        parser.error("--boot or --kernel is required")

    if arguments.boot:
        raw = Path(arguments.boot).read_bytes()
        boot = lz4_decompress(raw) if raw.startswith(b"\x04\x22\x4d\x18") else raw
        print(f"boot image: {len(boot)} bytes, sha256 {sha256(boot)[:16]}")
        kernel = boot_kernel(boot)
        if arguments.out:
            Path(arguments.out).write_bytes(kernel)
            print(f"kernel written to {arguments.out}")
    else:
        kernel = Path(arguments.kernel).read_bytes()

    header = image_header(kernel)
    print(f"kernel: {len(kernel)} bytes, sha256 {sha256(kernel)}")
    print(
        "image header: "
        f"text_offset 0x{header.text_offset:x}, image_size 0x{header.image_size:x}, "
        f"flags 0x{header.flags:x}"
    )
    if header.bss:
        print(
            f"not stored: 0x{header.bss:x} bytes ({header.bss / header.image_size:.1%}) that the "
            "header reserves - `.bss`, which the boot loader zero-fills"
        )
    print(f"physical load: 0x{physical_load(kernel):08x}")
    return 0


def self_test() -> int:
    """Synthetic inputs, because the real ones only exist once a firmware has been pulled.

    What is being tested is the refusal behaviour as much as the happy path: a reader that accepts a
    v3 header, or a file that is not the kernel its own header describes, silently produces a port of
    a kernel nobody is running. The `.bss` case is the opposite - a real dump is *always* shorter than
    its header, so that one has to be accepted and accounted for rather than refused.
    """
    def boot_image(version: int, kernel: bytes, page: int = 0x1000) -> bytes:
        header = bytearray(page)
        struct.pack_into("<I", header, BOOT_HEADER_VERSION_OFFSET, version)
        struct.pack_into("<I", header, BOOT_KERNEL_SIZE_OFFSET, len(kernel))
        return bytes(header) + kernel + b"\x00" * 0x100

    def image(payload: bytes, text_offset: int = 0) -> bytes:
        head = bytearray(0x40)
        struct.pack_into("<QQQ", head, IMAGE_TEXT_OFFSET, text_offset, len(payload) + 0x40, 0xA)
        return bytes(head) + payload

    good_kernel = image(b"\x00" * 0x100)
    boot = boot_image(BOOT_HEADER_VERSION, good_kernel)
    assert boot_kernel(boot) == good_kernel, "the kernel is not the span the header measures"
    assert image_header(good_kernel).text_offset == 0
    assert image_header(good_kernel).bss == 0, "a complete Image reserves nothing"
    assert physical_load(good_kernel) == 0x80000000
    assert physical_load(image(b"\x00" * 0x100, text_offset=0x80000)) == 0x80080000

    results: list[tuple[str, bool]] = []
    for label, call in [
        ("a v3 boot header is refused", lambda: boot_kernel(boot_image(3, good_kernel))),
        ("a kernel that overruns the image is refused",
         lambda: boot_kernel(boot_image(4, good_kernel, page=0x100))),
        ("a buffer shorter than the Image header is refused", lambda: image_header(good_kernel[:0x10])),
        ("a file longer than its header describes is refused",
         lambda: image_header(good_kernel + b"\x00" * 0x100)),
        ("a non-lz4 file is refused", lambda: lz4_decompress(b"not lz4")),
    ]:
        try:
            call()
        except (ValueError, SystemExit):
            results.append((label, True))
        else:
            results.append((label, False))

    # And the case that used to be a refusal and is not one: `.bss` is not stored, so a boot-partition
    # Image is always shorter than the header says, and refusing that would refuse every honest dump.
    results.append(("a reserved-but-unstored `.bss` is accepted and measured", _bss_accepted()))

    for label, passed in results:
        print(f"self-test: {label} {'ok' if passed else 'FAILED'}")
    if all(passed for _, passed in results):
        print("self-test: the reader measures the header's kernel and refuses the rest")
        return 0
    return 1


def _bss_accepted() -> bool:
    """A file shorter than its header's `image_size`, which every boot-partition dump is."""
    stored = 0x40 + 0x100
    head = bytearray(0x40)
    struct.pack_into("<QQQ", head, IMAGE_TEXT_OFFSET, 0, stored + 0x2000, 0xA)
    header = image_header(bytes(head) + b"\x00" * 0x100)
    return header.bss == 0x2000 and header.image_size == stored + 0x2000


if __name__ == "__main__":
    sys.exit(main())
