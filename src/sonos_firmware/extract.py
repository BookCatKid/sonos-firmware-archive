"""Extract plaintext or RSA-wrapped components from Sonos UPD containers."""

from __future__ import annotations

import hashlib
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .upd import ENVELOPE_MAGIC, parse_bytes

COMPONENT_NAMES = {
    3: ("preinstall", ".sh"),
    4: ("rootfs", ".bin"),
    6: ("kernel", ".uImage"),
    13: ("device-payload", ".bin"),
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def key_recipient_id(private_key) -> str:
    public_der = private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha1(public_der).hexdigest()  # Sonos envelope identifier.


def load_private_key(path: str | Path):
    return serialization.load_pem_private_key(Path(path).read_bytes(), password=None)


def decrypt_envelope(payload: bytes, private_key, *, legacy_model8: bool = False) -> tuple[bytes, str]:
    if not payload.startswith(ENVELOPE_MAGIC):
        raise ValueError("encrypted section has no Sonos envelope")
    header_len = int.from_bytes(payload[4:8], "big")
    if header_len < 0x3A or header_len > len(payload):
        raise ValueError(f"implausible envelope header length {header_len}")

    recipient_len = payload[0x14]
    recipient = payload[0x16 : 0x16 + recipient_len].hex()
    if recipient != key_recipient_id(private_key):
        raise ValueError(f"private key recipient mismatch: section={recipient}")

    rsa_len_offset = 0x16 + recipient_len + 4
    rsa_len = int.from_bytes(payload[rsa_len_offset : rsa_len_offset + 4], "big")
    rsa_offset = rsa_len_offset + 4
    rsa_ciphertext = payload[rsa_offset : rsa_offset + rsa_len]
    if rsa_offset + rsa_len + 8 != header_len:
        raise ValueError("unexpected envelope layout")
    cipher_id = int.from_bytes(payload[rsa_offset + rsa_len : rsa_offset + rsa_len + 4], "big")
    data_len = int.from_bytes(payload[rsa_offset + rsa_len + 4 : header_len], "big")
    encrypted = payload[header_len : header_len + data_len]
    if cipher_id != 1 or len(encrypted) != data_len or len(encrypted) < 16:
        raise ValueError("unsupported or truncated encrypted payload")

    aes_key = private_key.decrypt(
        rsa_ciphertext,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA1()),
            algorithm=hashes.SHA1(),
            label=None,
        ),
    )
    if len(aes_key) != 16:
        raise ValueError("unexpected wrapped AES key length")
    if legacy_model8:
        # Model-8 OTA envelopes store no IV alongside the ciphertext. Their
        # updater uses an all-zero CBC IV and discards a 16-byte plaintext
        # prefix after decrypting and unpadding.
        iv, ciphertext = bytes(16), encrypted
    else:
        iv, ciphertext = encrypted[:16], encrypted[16:]
    decryptor = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).decryptor()
    plaintext = decryptor.update(ciphertext) + decryptor.finalize()
    padding_length = plaintext[-1]
    if not 1 <= padding_length <= 16 or plaintext[-padding_length:] != bytes([padding_length]) * padding_length:
        raise ValueError("invalid PKCS#7 padding")
    plaintext = plaintext[:-padding_length]
    if legacy_model8:
        if len(plaintext) < 16:
            raise ValueError("legacy model-8 plaintext lacks its random prefix")
        plaintext = plaintext[16:]
    return plaintext, recipient


def extract_components(
    upd_path: str | Path,
    output_dir: str | Path,
    private_key_path: str | Path | None = None,
    legacy_model8: bool = False,
    skip_encrypted: bool = False,
) -> list[dict]:
    upd_path = Path(upd_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    data = upd_path.read_bytes()
    sections = parse_bytes(data)
    private_key = load_private_key(private_key_path) if private_key_path else None
    package_id = upd_path.stem
    records: list[dict] = []

    for section in sections:
        component = COMPONENT_NAMES.get(section.section_type)
        if component is None:
            continue
        payload = data[section.offset + 16 : section.offset + section.length]
        recipient = None
        if section.encrypted:
            if private_key is None:
                if skip_encrypted:
                    continue
                raise ValueError(f"section {section.index} requires recipient {section.recipient_id}")
            payload, recipient = decrypt_envelope(payload, private_key, legacy_model8=legacy_model8)

        kind, extension = component
        if kind == "rootfs" and payload.startswith(b"hsqs"):
            extension = ".squashfs"
        duplicate = sum(1 for item in records if item["kind"] == kind)
        suffix = f"-section-{section.index:02d}" if duplicate else ""
        filename = f"{package_id}-{kind}{suffix}{extension}"
        destination = output_dir / filename
        destination.write_bytes(payload)
        records.append(
            {
                "package_id": package_id,
                "section_index": section.index,
                "section_type": section.section_type,
                "kind": kind,
                "filename": filename,
                "bytes": len(payload),
                "sha256": _sha256(payload),
                "source_encrypted": section.encrypted,
                "recipient_id": recipient,
            }
        )
    return records
