"""Complete, non-executing inventory and extraction of ConnectX UPDs."""
from __future__ import annotations

import gzip
import hashlib
import io
import struct
import subprocess
import tarfile
import zlib
from pathlib import Path, PurePosixPath

from .catalog import filesystem_manifest
from .extract import decrypt_envelope, key_recipient_id, load_private_key
from .upd import parse_bytes

RECIPIENT = "f2acc70d8db3ee388c534d99d4bff64d9dab8154"
STATIC_URL = "https://update-firmware.sonos.com/firmware/Prod/JFFS_Static_Link/connectx.upd"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def package_version(data: bytes) -> str:
    headers = [s for s in parse_bytes(data) if s.section_type == 1]
    if len(headers) != 1 or headers[0].payload_length != 12:
        raise ValueError("expected one 12-byte type-1 version header")
    offset = headers[0].offset + 16
    major, minor, build = struct.unpack_from("<III", data, offset)
    return f"{major}.{minor}-{build}"


def unpack_tar(payload: bytes, directory: Path) -> list[dict]:
    """Write regular members only; retain links and special entries as metadata."""
    inventory = []
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if not path.parts and member.isdir() and member.name:
                if "." in seen:
                    raise ValueError("duplicate tar member: .")
                seen.add(".")
                inventory.append({"path": ".", "mode": member.mode, "bytes": member.size, "type": "5"})
                continue
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise ValueError(f"unsafe tar member: {member.name}")
            name = path.as_posix()
            if name in seen:
                raise ValueError(f"duplicate tar member: {name}")
            seen.add(name)
            record = {"path": name, "mode": member.mode, "bytes": member.size,
                      "type": member.type.decode("ascii", errors="replace")}
            if member.issym() or member.islnk():
                record["target"] = member.linkname
            if member.isfile():
                with archive.extractfile(member) as stream:
                    content = stream.read()
                destination = directory.joinpath(*path.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
                record["sha256"] = sha256(content)
            inventory.append(record)
    return inventory


def legacy_kernel_data(payload: bytes) -> tuple[int, bytes] | None:
    """Locate the validated gzip stream in a PowerPC self-extracting zImage."""
    offset = payload.find(b"\x1f\x8b\x08")
    while offset >= 0:
        stream = zlib.decompressobj(31)
        try:
            kernel = stream.decompress(payload[offset:], 128 * 1024 * 1024)
        except zlib.error:
            pass
        else:
            if stream.eof:
                return offset, kernel
        offset = payload.find(b"\x1f\x8b\x08", offset + 1)
    return None


def extract_package(source: Path, directory: Path, key_path: Path | None = None) -> dict:
    data = source.read_bytes()
    sections = parse_bytes(data)
    key = load_private_key(key_path) if key_path else None
    if key is not None and key_recipient_id(key) != RECIPIENT:
        raise ValueError("private key does not match ConnectX recipient")
    directory.mkdir(parents=True, exist_ok=True)
    result = {"source": source.name, "version": package_version(data), "bytes": len(data),
              "sha256": sha256(data), "sections": [], "components": []}

    def save(payload, filename, section, kind, **extra):
        (directory / filename).write_bytes(payload)
        item = {"package_id": source.stem, "section_index": section.index,
                "section_type": section.section_type, "kind": kind, "filename": filename,
                "bytes": len(payload), "sha256": sha256(payload), **extra}
        result["components"].append(item)
        return item

    for section in sections:
        payload = data[section.offset + 16:section.offset + section.length]
        record = section.to_dict()
        prefix = f"{source.stem}-section-{section.index:02d}-{section.name}"
        save(payload, prefix + "-stored.bin", section, "stored-section",
             source_encrypted=section.encrypted, recipient_id=section.recipient_id)
        if section.encrypted:
            if key is None or section.recipient_id != RECIPIENT:
                record["extraction"] = "blocked-key"
                result["sections"].append(record)
                continue
            payload, recipient = decrypt_envelope(payload, key)
        else:
            recipient = None
        extension = {3: ".sh", 4: ".bin", 6: ".uImage", 23: ".tar"}.get(section.section_type, ".bin")
        if payload[:4] in {b"hsqs", b"sqsh"}:
            extension = ".squashfs"
        component = save(payload, prefix + extension, section, section.name,
                         source_encrypted=section.encrypted, recipient_id=recipient)
        record["extraction"] = "decrypted" if section.encrypted else "plaintext"
        if section.section_type == 23:
            record["members"] = unpack_tar(payload, directory / (prefix + "-files"))
        if extension == ".squashfs":
            rootfs = directory / (prefix + "-files")
            subprocess.run(["unsquashfs", "-no-progress", "-no-xattrs", "-d", str(rootfs),
                            str(directory / component["filename"])], check=True,
                           stdout=subprocess.DEVNULL)
            record["filesystem"] = filesystem_manifest(rootfs)
        if section.section_type == 6 and payload.startswith(bytes.fromhex("27051956")):
            if len(payload) < 64:
                raise ValueError("truncated uImage header")
            length = int.from_bytes(payload[12:16], "big")
            if 64 + length > len(payload):
                raise ValueError("truncated uImage data")
            kernel = payload[64:64 + length]
            if payload[31] == 1:
                kernel = gzip.decompress(kernel)
            save(kernel, prefix + "-unpacked.bin", section, "kernel-data")
            record["uimage"] = {"name": payload[32:64].rstrip(b"\0").decode(errors="replace"),
                                 "compression": payload[31], "bytes": len(kernel), "sha256": sha256(kernel)}
        elif section.section_type == 6:
            decoded = legacy_kernel_data(payload)
            if decoded is not None:
                offset, kernel = decoded
                save(kernel, prefix + "-unpacked.bin", section, "kernel-data")
                record["zimage"] = {"gzip_offset": offset, "bytes": len(kernel), "sha256": sha256(kernel)}
        result["sections"].append(record)
    result["complete"] = all(s["extraction"] != "blocked-key" for s in result["sections"])
    return result
