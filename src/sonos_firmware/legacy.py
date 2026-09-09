"""Recover legacy Sonos model keys embedded in plaintext updater binaries."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .extract import key_recipient_id

UPDATER_MARKER = b"Detected /jffs/VERSION, perfoming no-op update\n\x00"
WRAPPER_BYTES = 0x4F0
SYSTEM_WORD = 0x1996
MDP1_MAGIC = bytes.fromhex("ce10e47d")
MDP2_MAGIC = bytes.fromhex("ca989b4a")
COPYRIGHT = b"Copyright 2016 Sonos, Inc."
PKCS12_RC4_128_OID = bytes.fromhex("2a864886f70d010c0101")


@dataclass(frozen=True)
class LegacyRecovery:
    key: rsa.RSAPrivateKey
    wrapper_offset: int
    seed: bytes
    aes_key: bytes
    iv: bytes
    password: bytes
    wrapper: bytes
    encrypted_key_info: bytes
    byte_order: str
    seed_source: str = "fixed-system-word"


def _aes_ecb(key: bytes, block: bytes) -> bytes:
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(block) + encryptor.finalize()


def _bcc(key: bytes, data: bytes) -> bytes:
    chain = bytes(16)
    for offset in range(0, len(data), 16):
        block = bytes(a ^ b for a, b in zip(chain, data[offset : offset + 16]))
        chain = _aes_ecb(key, block)
    return chain


def _block_cipher_df(data: bytes, output_bytes: int = 48) -> bytes:
    material = (
        len(data).to_bytes(4, "big")
        + output_bytes.to_bytes(4, "big")
        + data
        + b"\x80"
    )
    material += bytes((-len(material)) % 16)
    key = bytes(range(32))
    temporary = b""
    counter = 0
    while len(temporary) < 48:
        temporary += _bcc(key, counter.to_bytes(4, "big") + bytes(12) + material)
        counter += 1
    key = temporary[:32]
    block = temporary[32:48]
    output = b""
    while len(output) < output_bytes:
        block = _aes_ecb(key, block)
        output += block
    return output[:output_bytes]


class _CTRDRBG:
    """The AES-256 CTR-DRBG configuration used by the legacy updater."""

    def __init__(self, entropy: bytes):
        self.key = bytes(32)
        self.counter = bytes(16)
        self._update(_block_cipher_df(entropy))

    def _update(self, provided: bytes = bytes(48)) -> None:
        temporary = b""
        while len(temporary) < 48:
            self.counter = (int.from_bytes(self.counter, "big") + 1).to_bytes(16, "big")
            temporary += _aes_ecb(self.key, self.counter)
        temporary = bytes(a ^ b for a, b in zip(temporary, provided))
        self.key = temporary[:32]
        self.counter = temporary[32:48]

    def random(self, size: int) -> bytes:
        output = b""
        while len(output) < size:
            self.counter = (int.from_bytes(self.counter, "big") + 1).to_bytes(16, "big")
            output += _aes_ecb(self.key, self.counter)
        self._update()
        return output[:size]


def legacy_seed(
    model: int,
    system_word: int = SYSTEM_WORD,
    *,
    byte_order: str = "big",
) -> bytes:
    """Reproduce the updater's fixed MDP/RSAref seed selection."""
    if not 0 <= model <= 0xFFFFFFFF:
        raise ValueError("model must fit in an unsigned 32-bit integer")
    if not 0 <= system_word <= 0xFFFFFFFF:
        raise ValueError("system word must fit in an unsigned 32-bit integer")
    if byte_order not in {"big", "little"}:
        raise ValueError("byte order must be 'big' or 'little'")

    # MDP2[0xec:0x10c] selects the final 32 bytes of RSAref's fixed-width
    # public exponent. For exponent 65537, only its final three bytes are set.
    seed = bytearray(bytes(29) + b"\x01\x00\x01")
    seed[2:6] = int.from_bytes(MDP2_MAGIC, "big").to_bytes(4, byte_order)
    seed[0:2] = COPYRIGHT[10:12]
    seed[8:12] = int.from_bytes(MDP1_MAGIC, "big").to_bytes(4, byte_order)
    seed[12:16] = model.to_bytes(4, byte_order)
    seed[16:26] = COPYRIGHT[:10]
    for index in range(4):
        seed[26 + index] ^= (system_word >> (8 * index)) & 0xFF
    return bytes(seed)


def legacy_flash_seed(
    mtd_prefix: bytes,
    model: int,
    *,
    byte_order: str = "big",
) -> bytes:
    """Reproduce the flash-derived seed used by the model-5 updater path.

    The updater hashes exactly the first 16 KiB read from ``/dev/mtd/0``, then
    overlays the same MDP1 fields used by the fixed legacy construction. The
    twelve digest bytes at offsets 2..7 and 26..31 remain device/platform
    specific.
    """
    if len(mtd_prefix) != 0x4000:
        raise ValueError("MTD prefix must be exactly 16 KiB (0x4000 bytes)")
    if not 0 <= model <= 0xFFFFFFFF:
        raise ValueError("model must fit in an unsigned 32-bit integer")
    if byte_order not in {"big", "little"}:
        raise ValueError("byte order must be 'big' or 'little'")

    seed = bytearray(hashlib.sha256(mtd_prefix).digest())
    seed[0:2] = COPYRIGHT[10:12]
    seed[8:12] = int.from_bytes(MDP1_MAGIC, "big").to_bytes(4, byte_order)
    seed[12:16] = model.to_bytes(4, byte_order)
    seed[16:26] = COPYRIGHT[:10]
    return bytes(seed)


def _random_material_from_seed(seed: bytes) -> tuple[bytes, bytes, bytes]:
    if len(seed) != 32:
        raise ValueError("legacy DRBG seed must be exactly 32 bytes")
    drbg = _CTRDRBG(seed)
    iv = drbg.random(16)
    key = drbg.random(16)
    password = bytearray(31)
    for index in range(len(password)):
        while password[index] in (0, 10):
            password[index] = drbg.random(1)[0]
    return key, iv, bytes(password)


def _legacy_random_material(
    model: int,
    system_word: int,
    *,
    byte_order: str = "big",
) -> tuple[bytes, bytes, bytes]:
    return _random_material_from_seed(legacy_seed(model, system_word, byte_order=byte_order))


def _read_length(data: bytes, offset: int) -> tuple[int, int]:
    first = data[offset]
    if first < 0x80:
        return first, offset + 1
    count = first & 0x7F
    if count == 0 or count > 4 or offset + 1 + count > len(data):
        raise ValueError("invalid DER length")
    return int.from_bytes(data[offset + 1 : offset + 1 + count], "big"), offset + 1 + count


def _read_tlv(data: bytes, offset: int, tag: int) -> tuple[bytes, int]:
    if offset >= len(data) or data[offset] != tag:
        raise ValueError(f"expected DER tag 0x{tag:02x}")
    length, start = _read_length(data, offset + 1)
    end = start + length
    if end > len(data):
        raise ValueError("truncated DER value")
    return data[start:end], end


def _parse_encrypted_private_key_info(data: bytes) -> tuple[bytes, int, bytes]:
    outer, end = _read_tlv(data, 0, 0x30)
    if end != len(data):
        raise ValueError("trailing bytes after EncryptedPrivateKeyInfo")
    algorithm, position = _read_tlv(outer, 0, 0x30)
    encrypted, position = _read_tlv(outer, position, 0x04)
    if position != len(outer):
        raise ValueError("unexpected EncryptedPrivateKeyInfo fields")
    oid, position = _read_tlv(algorithm, 0, 0x06)
    if oid != PKCS12_RC4_128_OID:
        raise ValueError("legacy wrapper does not use pbeWithSHA1And128BitRC4")
    parameters, position = _read_tlv(algorithm, position, 0x30)
    if position != len(algorithm):
        raise ValueError("unexpected PBE algorithm fields")
    salt, position = _read_tlv(parameters, 0, 0x04)
    iterations_raw, position = _read_tlv(parameters, position, 0x02)
    if position != len(parameters) or not iterations_raw:
        raise ValueError("invalid PBE parameters")
    return salt, int.from_bytes(iterations_raw, "big"), encrypted


def _repeat_to_multiple(data: bytes, block_bytes: int) -> bytes:
    if not data:
        return b""
    length = block_bytes * ((len(data) + block_bytes - 1) // block_bytes)
    return (data * ((length + len(data) - 1) // len(data)))[:length]


def _pkcs12_key(password: bytes, salt: bytes, iterations: int) -> bytes:
    if iterations < 1:
        raise ValueError("PBE iteration count must be positive")
    block_bytes = 64
    password_bmp = b"".join(bytes((0, value)) for value in password) + b"\x00\x00"
    repeated = bytearray(
        _repeat_to_multiple(salt, block_bytes)
        + _repeat_to_multiple(password_bmp, block_bytes)
    )
    digest = hashlib.sha1(bytes([1]) * block_bytes + repeated).digest()
    for _ in range(1, iterations):
        digest = hashlib.sha1(digest).digest()
    adjustment = (digest * ((block_bytes + len(digest) - 1) // len(digest)))[:block_bytes]
    modulus = 1 << (8 * block_bytes)
    for offset in range(0, len(repeated), block_bytes):
        value = int.from_bytes(repeated[offset : offset + block_bytes], "big")
        value = (value + int.from_bytes(adjustment, "big") + 1) % modulus
        repeated[offset : offset + block_bytes] = value.to_bytes(block_bytes, "big")
    return digest[:16]


def _rc4(key: bytes, data: bytes) -> bytes:
    state = list(range(256))
    cursor = 0
    for index in range(256):
        cursor = (cursor + state[index] + key[index % len(key)]) & 0xFF
        state[index], state[cursor] = state[cursor], state[index]
    left = right = 0
    output = bytearray()
    for value in data:
        left = (left + 1) & 0xFF
        right = (right + state[left]) & 0xFF
        state[left], state[right] = state[right], state[left]
        output.append(value ^ state[(state[left] + state[right]) & 0xFF])
    return bytes(output)


def _recover_legacy_updater_key_with_seed(
    updater: bytes,
    seed: bytes,
    *,
    wrapper_offset: int | None = None,
    byte_order: str = "big",
    seed_source: str,
) -> LegacyRecovery:
    if wrapper_offset is None:
        marker_offset = updater.find(UPDATER_MARKER)
        if marker_offset < 0:
            raise ValueError("legacy updater marker not found")
        if updater.find(UPDATER_MARKER, marker_offset + 1) >= 0:
            raise ValueError("multiple legacy updater markers found")
        wrapper_offset = marker_offset + len(UPDATER_MARKER)
    if wrapper_offset < 0:
        raise ValueError("legacy wrapper offset must be non-negative")
    wrapper = updater[wrapper_offset : wrapper_offset + WRAPPER_BYTES]
    if len(wrapper) != WRAPPER_BYTES:
        raise ValueError("truncated legacy updater wrapper")

    aes_key, iv, password = _random_material_from_seed(seed)
    decryptor = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(wrapper) + decryptor.finalize()
    padding = padded[-1]
    if not 0 < padding <= 16 or padded[-padding:] != bytes([padding]) * padding:
        raise ValueError("legacy wrapper AES padding is invalid")
    encrypted_key_info = padded[:-padding]
    salt, iterations, encrypted_key = _parse_encrypted_private_key_info(encrypted_key_info)
    private_der = _rc4(_pkcs12_key(password, salt, iterations), encrypted_key)
    private_key = serialization.load_der_private_key(private_der, password=None)
    if not isinstance(private_key, rsa.RSAPrivateKey) or private_key.key_size != 2048:
        raise ValueError("legacy wrapper did not contain an RSA-2048 private key")
    return LegacyRecovery(
        private_key,
        wrapper_offset,
        seed,
        aes_key,
        iv,
        password,
        wrapper,
        encrypted_key_info,
        byte_order,
        seed_source,
    )


def recover_legacy_updater_key(
    updater: bytes,
    model: int,
    *,
    system_word: int = SYSTEM_WORD,
    wrapper_offset: int | None = None,
    byte_order: str = "big",
) -> LegacyRecovery:
    """Recover a key from a legacy updater using the fixed system-word seed."""
    seed = legacy_seed(model, system_word, byte_order=byte_order)
    return _recover_legacy_updater_key_with_seed(
        updater,
        seed,
        wrapper_offset=wrapper_offset,
        byte_order=byte_order,
        seed_source="fixed-system-word",
    )


def recover_legacy_flash_updater_key(
    updater: bytes,
    mtd_prefix: bytes,
    model: int,
    *,
    wrapper_offset: int | None = None,
    byte_order: str = "big",
) -> LegacyRecovery:
    """Recover a key using the updater's flash-hash seed construction."""
    seed = legacy_flash_seed(mtd_prefix, model, byte_order=byte_order)
    return _recover_legacy_updater_key_with_seed(
        updater,
        seed,
        wrapper_offset=wrapper_offset,
        byte_order=byte_order,
        seed_source="mtd-prefix-sha256",
    )


def write_recovery_evidence_exclusive(
    directory: str | Path,
    recovery: LegacyRecovery,
) -> None:
    """Create a mode-0700 directory containing mode-0600 recovery intermediates."""
    destination = Path(directory)
    os.mkdir(destination, 0o700)
    files = {
        "seed.bin": recovery.seed,
        "aes-key.bin": recovery.aes_key,
        "iv.bin": recovery.iv,
        "password.bin": recovery.password,
        "wrapper.ct": recovery.wrapper,
        "wrapper.der": recovery.encrypted_key_info,
        "metadata.json": (
            json.dumps(
                {
                    "wrapper_offset": recovery.wrapper_offset,
                    "recipient_id": key_recipient_id(recovery.key),
                    "byte_order": recovery.byte_order,
                    "seed_source": recovery.seed_source,
                },
                indent=2,
            )
            + "\n"
        ).encode(),
    }
    for name, content in files.items():
        descriptor = os.open(destination / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
