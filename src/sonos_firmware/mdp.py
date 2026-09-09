"""Inspect Sonos manufacturing data pages and recover verified model keys."""

from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .extract import key_recipient_id

MDP1_MAGIC = 0xCE10E47D
MDP3_MAGIC = b"\xf0\x79\xa9\xcb"
MDP1_BYTES = 0x200
MDP2_BYTES = 0x1000
MDP3_BYTES = 0x4000
MDP3_OFFSET_IN_SMDP = MDP1_BYTES + MDP2_BYTES
SMDP_BYTES = MDP3_OFFSET_IN_SMDP + MDP3_BYTES

OTP_BYTES = 0x100
MDP_KEY_HEADER_BYTES = 12
MDP_KEY_FIELD_BYTES = 0x100
MODEL_KEY_FIELD_BYTES = 0x800
CAAM_BLOB_OVERHEAD = 48
AMLOGIC_BLOB_OVERHEAD = 12 + 16

FS_KEY_MAGIC = 0x618C4DE8
MODEL_KEY_MAGIC = 0xF4CCC68D
JFFS_KEY_OFFSET = 0x580
ROOTFS_KEY_OFFSET = 0x680
MODEL_KEY_OFFSET = 0x780


@dataclass(frozen=True)
class MDPImage:
    """A located MDP3 page and optional adjacent MDP1 identity data."""

    data: bytes
    mdp3_offset: int
    source_kind: str
    model: int | None = None
    submodel: int | None = None
    revision: int | None = None

    @property
    def mdp3(self) -> bytes:
        return self.data[self.mdp3_offset : self.mdp3_offset + MDP3_BYTES]

    @property
    def version(self) -> int:
        return struct.unpack_from("<I", self.mdp3, 4)[0]


@dataclass(frozen=True)
class MDPField:
    name: str
    offset: int
    magic: int
    length: int
    blob: bytes
    expected_magic: int
    field_bytes: int

    @property
    def valid(self) -> bool:
        maximum = self.field_bytes - MDP_KEY_HEADER_BYTES
        return self.magic == self.expected_magic and self.length <= maximum and self.length == len(self.blob)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "offset": self.offset,
            "magic": f"{self.magic:08x}",
            "expected_magic": f"{self.expected_magic:08x}",
            "length": self.length,
            "valid": self.valid,
        }


def _identity_before_mdp3(data: bytes, mdp3_offset: int) -> tuple[int | None, int | None, int | None]:
    mdp1_offset = mdp3_offset - MDP3_OFFSET_IN_SMDP
    if mdp1_offset < 0 or mdp1_offset + MDP1_BYTES > len(data):
        return None, None, None
    if struct.unpack_from("<I", data, mdp1_offset)[0] != MDP1_MAGIC:
        return None, None, None
    return struct.unpack_from("<III", data, mdp1_offset + 8)


def locate_mdp3(data: bytes) -> MDPImage:
    """Locate one MDP3 in a standalone page, SMDP, or larger device dump."""

    candidates = [
        offset
        for offset in range(0, len(data) - len(MDP3_MAGIC) + 1)
        if data.startswith(MDP3_MAGIC, offset) and offset + MDP3_BYTES <= len(data)
    ]
    if not candidates:
        raise ValueError("no complete MDP3 page found")
    if len(candidates) > 1:
        rendered = ", ".join(f"0x{offset:x}" for offset in candidates)
        raise ValueError(f"multiple MDP3 pages found at {rendered}")

    offset = candidates[0]
    model, submodel, revision = _identity_before_mdp3(data, offset)
    if offset == 0 and len(data) == MDP3_BYTES:
        kind = "standalone-mdp3"
    elif model is not None:
        kind = "smdp-or-device-dump"
    else:
        kind = "embedded-mdp3"
    return MDPImage(data, offset, kind, model, submodel, revision)


def parse_field(image: MDPImage, name: str, offset: int, expected_magic: int, field_bytes: int) -> MDPField:
    mdp3 = image.mdp3
    magic, length, _reserved = struct.unpack_from("<III", mdp3, offset)
    maximum = field_bytes - MDP_KEY_HEADER_BYTES
    readable_length = min(length, maximum)
    blob = mdp3[offset + MDP_KEY_HEADER_BYTES : offset + MDP_KEY_HEADER_BYTES + readable_length]
    return MDPField(name, offset, magic, length, blob, expected_magic, field_bytes)


def inspect_mdp(data: bytes) -> dict:
    image = locate_mdp3(data)
    fields = [
        parse_field(image, "jffs", JFFS_KEY_OFFSET, FS_KEY_MAGIC, MDP_KEY_FIELD_BYTES),
        parse_field(image, "rootfs", ROOTFS_KEY_OFFSET, FS_KEY_MAGIC, MDP_KEY_FIELD_BYTES),
        parse_field(image, "model-private", MODEL_KEY_OFFSET, MODEL_KEY_MAGIC, MODEL_KEY_FIELD_BYTES),
    ]
    return {
        "bytes": len(data),
        "source_kind": image.source_kind,
        "mdp3_offset": image.mdp3_offset,
        "mdp3_version": image.version,
        "model": image.model,
        "submodel": image.submodel,
        "revision": image.revision,
        "fields": [field.to_dict() for field in fields],
    }


def _xor_repeating(left: bytes, right: bytes) -> bytes:
    return bytes(value ^ right[index % len(right)] for index, value in enumerate(left))


def decrypt_amlogic_blob(blob: bytes, otp: bytes, key_modifier: bytes) -> bytes:
    """Decrypt the legacy Amlogic Sonos blob construction used by sonostool."""

    if len(otp) != OTP_BYTES:
        raise ValueError(f"OTP dump must be exactly {OTP_BYTES} bytes")
    if not key_modifier or len(key_modifier) > 8:
        raise ValueError("key modifier must contain 1 to 8 bytes")
    if len(blob) <= AMLOGIC_BLOB_OVERHEAD:
        raise ValueError("wrapped blob is too short")

    ciphertext_length = len(blob) - 12 - 16
    if ciphertext_length <= 0 or ciphertext_length % 16:
        raise ValueError("wrapped blob ciphertext is not AES-block aligned")
    ciphertext = blob[:ciphertext_length]
    iv = blob[ciphertext_length : ciphertext_length + 12]
    modifier = key_modifier.ljust(8, b"\x00")
    counter_iv = _xor_repeating(iv, modifier)
    counters = b"".join(
        counter_iv + struct.pack(">I", 2 + index)
        for index in range(ciphertext_length // 16)
    )
    aes_key = hashlib.sha256(otp[0xD0:0xE0]).digest()[:16]
    encryptor = Cipher(algorithms.AES(aes_key), modes.ECB()).encryptor()
    keystream = encryptor.update(counters) + encryptor.finalize()
    padded = bytes(a ^ b for a, b in zip(ciphertext, keystream, strict=True))
    padding_length = padded[-1]
    if not 1 <= padding_length <= 16:
        raise ValueError("wrapped blob has invalid PKCS#7 padding")
    if padded[-padding_length:] != bytes([padding_length]) * padding_length:
        raise ValueError("wrapped blob has invalid PKCS#7 padding")
    return padded[:-padding_length]


def recover_amlogic_model_key(
    mdp_data: bytes,
    otp: bytes,
    *,
    expected_model: int | None = None,
) -> rsa.RSAPrivateKey:
    """Recover and validate an RSA-2048 model key from an Amlogic MDP3/OTP pair."""

    image = locate_mdp3(mdp_data)
    if expected_model is not None:
        if image.model is None:
            raise ValueError(
                "cannot validate the expected model from a standalone MDP3 page; "
                "supply a complete SMDP or device dump"
            )
        if image.model != expected_model:
            raise ValueError(
                f"manufacturing-page model mismatch: got {image.model}, expected {expected_model}"
            )
    field = parse_field(image, "model-private", MODEL_KEY_OFFSET, MODEL_KEY_MAGIC, MODEL_KEY_FIELD_BYTES)
    if field.magic != MODEL_KEY_MAGIC:
        raise ValueError(f"model key field has unexpected magic {field.magic:08x}")
    if not field.valid:
        raise ValueError(f"model key field length {field.length} exceeds its storage")
    if field.length < AMLOGIC_BLOB_OVERHEAD:
        raise ValueError("model key blob is shorter than its wrapper overhead")
    key_der = decrypt_amlogic_blob(field.blob, otp, b"model")
    try:
        key = serialization.load_der_private_key(key_der, password=None)
    except (TypeError, ValueError) as error:
        raise ValueError("unwrapped model key is not DER private-key material") from error
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size != 2048:
        raise ValueError("unwrapped model key is not RSA-2048")
    return key


def private_key_pem(key: rsa.RSAPrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )


def write_private_key_exclusive(path: str | Path, key: rsa.RSAPrivateKey) -> None:
    """Create a PEM file with mode 0600, refusing to replace an existing path."""

    destination = Path(path)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(private_key_pem(key))
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def recipient_id_for_key_file(path: str | Path) -> str:
    material = Path(path).read_bytes()
    loaders = (serialization.load_pem_private_key, serialization.load_der_private_key)
    for loader in loaders:
        try:
            key = loader(material, password=None)
            break
        except (TypeError, ValueError):
            key = None
    if key is None or not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("file does not contain an RSA private key")
    return key_recipient_id(key)
