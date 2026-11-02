"""Fetch and parse Sonos update-metadata (UPS) records safely."""

from __future__ import annotations

import hashlib
import struct
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode


UPS_MAGIC = 0x35167F49
BASE_URI_TYPE = 7
MANIFEST_URI_TYPE = 14
UPDATE_METADATA_URL = "https://update.sonos.com/firmware/latest/default-1-1.ups"
USER_AGENT = "sonos-firmware-archive/0.1 (+preservation research)"

# These are the public sonostool placeholders, never values from a real device.
SYNTHETIC_IDENTIFIERS = {
    "serial": "1",
    "sonosid": "111111111",
    "householdid": "X",
}


@dataclass(frozen=True)
class UpdateProfile:
    controller_major: int = 4
    controller_minor: int = 1
    controller_build: int = 1
    submodel: int = 100
    revision: int = 1
    region: int = 2

    def query(self) -> dict[str, str | int]:
        return {
            "cmaj": self.controller_major,
            "cmin": self.controller_minor,
            "cbld": self.controller_build,
            "subm": self.submodel,
            "rev": self.revision,
            "reg": self.region,
            **SYNTHETIC_IDENTIFIERS,
        }

    def redacted(self) -> dict[str, int | str]:
        return {
            "controller_major": self.controller_major,
            "controller_minor": self.controller_minor,
            "controller_build": self.controller_build,
            "submodel": self.submodel,
            "revision": self.revision,
            "region": self.region,
            "identifiers": "synthetic public sonostool placeholders; values omitted",
        }


def parse_update_metadata(data: bytes) -> list[dict]:
    """Parse the size-includes-header TLV records in a Sonos UPS response."""
    records: list[dict] = []
    offset = 0
    while offset < len(data):
        if len(data) - offset < 16:
            raise ValueError(f"truncated UPS header at offset {offset}")
        magic, record_type, size, unknown = struct.unpack_from("<IIII", data, offset)
        if magic != UPS_MAGIC:
            raise ValueError(f"invalid UPS magic at offset {offset}: {magic:#x}")
        if size < 16 or offset + size > len(data):
            raise ValueError(f"invalid UPS record size at offset {offset}: {size}")
        body = data[offset + 16 : offset + size]
        record = {
            "type": record_type,
            "size": size,
            "unknown": unknown,
            "body_sha256": hashlib.sha256(body).hexdigest(),
        }
        if record_type in (BASE_URI_TYPE, MANIFEST_URI_TYPE):
            record["uri"] = body.rstrip(b"\0").decode("utf-8")
        records.append(record)
        offset += size
    return records


def fetch_update_metadata(profile: UpdateProfile | None = None, timeout: float = 30) -> tuple[bytes, dict]:
    profile = profile or UpdateProfile()
    url = f"{UPDATE_METADATA_URL}?{urlencode(profile.query())}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
        response_metadata = {
            "status": response.status,
            "content_type": response.headers.get("Content-Type"),
            "content_length": len(data),
        }
    return data, response_metadata


def snapshot_update_metadata(raw_path: str | Path, receipt_path: str | Path) -> dict:
    """Fetch a synthetic UPS response and retain bytes plus a redacted receipt."""
    from .discovery import write_json

    profile = UpdateProfile()
    data, response = fetch_update_metadata(profile)
    records = parse_update_metadata(data)
    raw_path = Path(raw_path)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(data)
    receipt = {
        "schema_version": 1,
        "retrieved": datetime.now(timezone.utc).isoformat(),
        "source_url": UPDATE_METADATA_URL,
        "request_profile": profile.redacted(),
        "response": {
            **response,
            "sha256": hashlib.sha256(data).hexdigest(),
            "raw_file": raw_path.name,
        },
        "records": records,
    }
    write_json(receipt_path, receipt)
    return receipt
