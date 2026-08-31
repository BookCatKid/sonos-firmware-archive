"""Read-only parser for Sonos UPD section containers."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import asdict, dataclass
from pathlib import Path

UPD_MAGIC = bytes.fromhex("497f1635")
ENVELOPE_MAGIC = bytes.fromhex("886499ca")

SECTION_NAMES = {
    3: "preinstall",
    4: "rootfs",
    6: "kernel",
    13: "device-payload",
    16: "metadata",
    17: "application",
}


@dataclass(frozen=True)
class Section:
    index: int
    offset: int
    section_type: int
    name: str
    length: int
    payload_length: int
    flags: int
    encrypted: bool
    sha256: str
    payload_magic: str
    recipient_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _recipient_id(payload: bytes) -> str | None:
    if not payload.startswith(ENVELOPE_MAGIC) or len(payload) < 0x16:
        return None
    length = payload[0x14]
    end = 0x16 + length
    if end > len(payload):
        return None
    return payload[0x16:end].hex()


def parse_bytes(data: bytes) -> list[Section]:
    sections: list[Section] = []
    pos = 0
    while pos < len(data):
        if len(data) - pos < 16:
            raise ValueError(f"truncated section header at offset {pos}")
        if data[pos : pos + 4] != UPD_MAGIC:
            raise ValueError(f"bad UPD magic at offset {pos}")
        section_type, section_len, flags = struct.unpack_from("<III", data, pos + 4)
        if section_len < 16 or pos + section_len > len(data):
            raise ValueError(f"invalid section length {section_len} at offset {pos}")
        payload = data[pos + 16 : pos + section_len]
        sections.append(
            Section(
                index=len(sections),
                offset=pos,
                section_type=section_type,
                name=SECTION_NAMES.get(section_type, "unknown"),
                length=section_len,
                payload_length=len(payload),
                flags=flags,
                encrypted=bool(flags & 0x4),
                sha256=hashlib.sha256(payload).hexdigest(),
                payload_magic=payload[:8].hex(),
                recipient_id=_recipient_id(payload),
            )
        )
        pos += section_len
    return sections


def parse_file(path: str | Path) -> list[Section]:
    return parse_bytes(Path(path).read_bytes())

