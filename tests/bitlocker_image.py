# SPDX-License-Identifier: MIT
"""Test-only generator for small BitLocker volumes (recovery-password protector).

There is no public BitLocker sample image to test an unlock flow against, so this
builds one from the format documentation (libbde's "BitLocker Drive Encryption
(BDE) format") and dislocker's source: a Windows 7+ style volume with AES-XTS-128
or AES-CBC-128, one recovery-password-protected VMK, and a real NTFS filesystem
inside. The *real* `dislocker` binary is the judge of whether this is valid.

Not for production use: no TPM/clear-key protectors, no Elephant diffuser.
"""

from __future__ import annotations

import hashlib
import os
import struct
import time
import zlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESCCM

SECTOR = 512
HEADER_BYTES = 8192  # encrypted-volume-header copy: first 16 sectors
METADATA_SIZE = 0x10000

AES_128_CBC = 0x8002
AES_XTS_128 = 0x8004

INFORMATION_OFFSET_GUID = bytes([0x3B, 0xD6, 0x67, 0x49, 0x29, 0x2E, 0xD8, 0x4A,
                                 0x83, 0x99, 0xF6, 0xA3, 0x39, 0xE3, 0xD0, 0x01])


def filetime() -> int:
    return int((time.time() + 11644473600) * 10_000_000)


def make_recovery_password(seed: int = 7) -> str:
    """A syntactically valid 48-digit recovery password (blocks divisible by 11,
    < 720896, with the check digit dislocker verifies)."""
    import random
    rng = random.Random(seed)
    blocks = []
    while len(blocks) < 8:
        n = rng.randrange(0, 65536) * 11
        digits = f"{n:06d}"
        d = [int(c) for c in digits]
        if (d[0] - d[1] + d[2] - d[3] + d[4]) % 11 == d[5]:
            blocks.append(digits)
    return "-".join(blocks)


def recovery_key_bytes(password: str) -> bytes:
    blocks = password.split("-")
    return b"".join(struct.pack("<H", int(b) // 11) for b in blocks)


def stretch(key16: bytes, salt: bytes) -> bytes:
    """The 2**20-round SHA-256 chain BitLocker uses for the recovery key."""
    password_hash = hashlib.sha256(key16).digest()
    updated = b"\x00" * 32
    for count in range(0x100000):
        updated = hashlib.sha256(updated + password_hash + salt + struct.pack("<Q", count)).digest()
    return updated


def datum(entry_type: int, value_type: int, payload: bytes, version: int = 1) -> bytes:
    size = 8 + len(payload)
    return struct.pack("<HHHH", size, entry_type, value_type, version) + payload


def ccm_datum(entry_type: int, key: bytes, plaintext: bytes, nonce: bytes, version: int = 1) -> bytes:
    """AES-CCM datum: header | nonce(12) | mac(16) | ciphertext (BitLocker puts the tag first)."""
    sealed = AESCCM(key, tag_length=16).encrypt(nonce, plaintext, None)
    ciphertext, tag = sealed[:-16], sealed[-16:]
    return datum(entry_type, 5, nonce + tag + ciphertext, version)


def key_datum(algo: int, key: bytes) -> bytes:
    """The plaintext sealed inside a CCM datum: a KEY datum (entry 0, value 1)."""
    return struct.pack("<HHHHHH", 12 + len(key), 0, 1, 1, algo, 0) + key


def sector_crypt(method: int, fvek: bytes, sector_no: int, data: bytes, encrypt: bool = True) -> bytes:
    if method == AES_XTS_128:
        cipher = Cipher(algorithms.AES(fvek[:32]), modes.XTS(struct.pack("<QQ", sector_no, 0)))
    elif method == AES_128_CBC:
        # IV = AES-ECB(FVEK, sector byte offset as 16-byte little-endian)
        ecb = Cipher(algorithms.AES(fvek[:16]), modes.ECB()).encryptor()
        iv = ecb.update(struct.pack("<QQ", sector_no * SECTOR, 0)) + ecb.finalize()
        cipher = Cipher(algorithms.AES(fvek[:16]), modes.CBC(iv))
    else:
        raise ValueError(method)
    ctx = cipher.encryptor() if encrypt else cipher.decryptor()
    return ctx.update(data) + ctx.finalize()


def build(plain_path: str, out_path: str, recovery_password: str, method: int = AES_XTS_128,
          state: int = 4) -> dict:
    """Wrap the NTFS image at ``plain_path`` into a BitLocker volume at ``out_path``."""
    size = os.path.getsize(plain_path)
    mib = 1024 * 1024
    assert size % SECTOR == 0 and size >= 16 * mib, "use a >= 16 MiB, sector-aligned image"
    # Real BitLocker marks the metadata clusters as in use inside NTFS. This generator instead puts
    # them in the last 4 MiB and expects the NTFS filesystem to end before that (mkfs on size - 4 MiB,
    # then zero-pad the image).
    m_off = [size - 4 * mib, size - 3 * mib, size - 2 * mib]
    backup_off = size - 1 * mib
    reserved = [(o, METADATA_SIZE) for o in m_off] + [(backup_off, HEADER_BYTES)]

    fvek = os.urandom(32 if method == AES_XTS_128 else 16)
    vmk = os.urandom(32)
    salt = os.urandom(16)
    volume_guid = os.urandom(16)
    vmk_guid = os.urandom(16)
    now = struct.pack("<Q", filetime())

    stretched = stretch(recovery_key_bytes(recovery_password), salt)

    # --- datums ---------------------------------------------------------------
    stretch_datum = datum(0, 3, struct.pack("<HH", 0x1001, 0) + salt)
    vmk_ccm = ccm_datum(0, stretched, key_datum(0x2000, vmk), now + struct.pack("<I", 1))
    vmk_header = vmk_guid + now + struct.pack("<HH", 0, 0x0800)  # nonce[10:12] = protection type
    vmk_datum = datum(2, 8, vmk_header + stretch_datum + vmk_ccm)

    fvek_datum = ccm_datum(3, vmk, key_datum(method, fvek), now + struct.pack("<I", 2))
    virt_datum = datum(0xF, 0xF, struct.pack("<QQ", backup_off, HEADER_BYTES))
    datums = vmk_datum + fvek_datum + virt_datum

    def metadata_block(index: int) -> bytes:
        body_len = 0x30 + len(datums)
        total = 0x40 + body_len
        pad = (-total) % 16
        total += pad
        info = b"-FVE-FS-" + struct.pack("<HHHH", total // 16, 2, state, state)
        info += struct.pack("<QII", size, 0, HEADER_BYTES // SECTOR)
        info += struct.pack("<QQQ", *m_off) + struct.pack("<Q", backup_off)
        dataset = struct.pack("<IIII", total - 0x40, 1, 0x30, total - 0x40) + volume_guid
        dataset += struct.pack("<IHH", 8, method, 0) + now
        block = info + dataset + datums + b"\x00" * pad
        assert len(info) == 0x40 and len(dataset) == 0x30 and len(block) == total
        crc = zlib.crc32(block) & 0xFFFFFFFF
        return block + struct.pack("<HHI", 8, 2, crc)

    # --- volume header (sector 0 of the encrypted volume) -----------------------
    hdr = bytearray(SECTOR)
    hdr[0:3] = b"\xeb\x58\x90"
    hdr[3:11] = b"-FVE-FS-"
    struct.pack_into("<HBHBHHBHHH", hdr, 11, SECTOR, 8, 0, 0, 0, 0, 0xF8, 0, 63, 255)
    struct.pack_into("<I", hdr, 28, 0)
    struct.pack_into("<I", hdr, 36, 0x1FE0)
    hdr[160:176] = INFORMATION_OFFSET_GUID
    struct.pack_into("<QQQ", hdr, 176, *m_off)
    hdr[510:512] = b"\x55\xaa"

    # --- encrypt the volume ---------------------------------------------------------
    with open(plain_path, "rb") as src, open(out_path, "wb") as dst:
        dst.truncate(size)
        plain_first = src.read(HEADER_BYTES)
        src.seek(0)
        for sector_no in range(size // SECTOR):
            off = sector_no * SECTOR
            chunk = src.read(SECTOR)
            if off < HEADER_BYTES or any(o <= off < o + n for o, n in reserved):
                continue  # virtualized: zeros on disk
            dst.seek(off)
            dst.write(sector_crypt(method, fvek, sector_no, chunk))
        # Encrypted copy of the original first 16 sectors. It is a normal encrypted region of the volume,
        # so the cipher's sector number is the one of the *backup's* position (dislocker decrypts it that way).
        dst.seek(backup_off)
        for i in range(HEADER_BYTES // SECTOR):
            dst.write(sector_crypt(method, fvek, backup_off // SECTOR + i, plain_first[i * SECTOR:(i + 1) * SECTOR]))
        dst.seek(0)
        dst.write(hdr)
        for off in m_off:
            dst.seek(off)
            dst.write(metadata_block(0))
    return {"size": size, "metadata": m_off, "backup": backup_off, "method": method}
