"""Extract plaintext or RSA-wrapped components from Sonos UPD containers."""

from __future__ import annotations

import hashlib
import io
import tarfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.exceptions import UnsupportedAlgorithm
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


def _extract_components(
    upd_path: str | Path,
    output_dir: str | Path,
    private_key_path: str | Path | None = None,
    legacy_model8: bool = False,
    skip_encrypted: bool = False,
    all_sections: bool = False,
    skipped: list | None = None,
) -> list[dict]:
    upd_path = Path(upd_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    data = upd_path.read_bytes()
    sections = parse_bytes(data)
    private_keys = {}
    paths = private_key_path if isinstance(private_key_path, Mapping) else {None: private_key_path}
    for recipient, path in paths.items():
        if path:
            try:
                private_keys[recipient] = load_private_key(path)
            except (ValueError, TypeError, OSError, UnsupportedAlgorithm):
                if not all_sections:
                    raise
    package_id = upd_path.stem
    records: list[dict] = []

    for section in sections:
        component = COMPONENT_NAMES.get(section.section_type)
        if component is None:
            if not all_sections:
                continue
            component = (f'section-{section.section_type}', '.bin')
        payload = data[section.offset + 16 : section.offset + section.length]
        recipient = None
        if section.encrypted:
            private_key = private_keys.get(section.recipient_id, private_keys.get(None))
            if private_key is None:
                if skip_encrypted:
                    if skipped is not None:
                        skipped.append({'section_index': section.index, 'section_type': section.section_type,
                                        'recipient_id': section.recipient_id, 'reason': 'no matching model key'})
                    continue
                raise ValueError(f"section {section.index} requires recipient {section.recipient_id}")
            try:
                payload, recipient = decrypt_envelope(payload, private_key, legacy_model8=legacy_model8)
            except ValueError as error:
                if not all_sections:
                    raise
                if skipped is not None:
                    skipped.append({'section_index': section.index, 'section_type': section.section_type,
                                    'recipient_id': section.recipient_id, 'reason': str(error)})
                continue

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


def extract_components(upd_path, output_dir, private_key_path=None, legacy_model8=False, skip_encrypted=False):
    return _extract_components(upd_path, output_dir, private_key_path, legacy_model8, skip_encrypted)


def extract_available_components(upd_path, output_dir, private_key_path=None):
    """Extract available sections using a key path or recipient-to-path mapping."""
    skipped = []
    components = _extract_components(upd_path, output_dir, private_key_path,
                                     skip_encrypted=True, all_sections=True, skipped=skipped)
    return {'components': components, 'skipped': skipped}


def extract_anacapad(upd_path: str | Path, output_dir: str | Path) -> dict:
    """Read bin/anacapad from a plaintext type-23 tar without unpacking paths."""
    upd_path = Path(upd_path)
    data = upd_path.read_bytes()
    matches = []
    for section in parse_bytes(data):
        if section.section_type != 23 or section.encrypted:
            continue
        payload = data[section.offset + 16 : section.offset + section.length]
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
            for member in archive:
                if PurePosixPath(member.name) != PurePosixPath("bin/anacapad"):
                    continue
                if not member.isfile():
                    raise ValueError("bin/anacapad must be a regular tar member")
                with archive.extractfile(member) as handle:
                    binary = handle.read()
                if len(binary) != member.size:
                    raise ValueError("truncated bin/anacapad tar member")
                matches.append((section, binary))
    if len(matches) != 1:
        raise ValueError(f"expected one plaintext bin/anacapad member, found {len(matches)}")

    section, binary = matches[0]
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{upd_path.stem}-anacapad"
    (output_dir / filename).write_bytes(binary)
    return {
        "package_id": upd_path.stem,
        "section_index": section.index,
        "section_type": section.section_type,
        "kind": "anacapad",
        "filename": filename,
        "bytes": len(binary),
        "sha256": _sha256(binary),
        "source_encrypted": False,
        "recipient_id": None,
        "archive_member": "bin/anacapad",
        "archive_sha256": section.sha256,
        "source_sha256": _sha256(data),
    }
