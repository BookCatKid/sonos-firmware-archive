#!/usr/bin/env python3
"""Match firmware binary evidence to indexed GPL component source candidates.

A matching component version is only a candidate, not proof of exact source
correspondence or GPL compliance. This analyzer never executes target binaries.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import struct
import subprocess
import sys
import zlib
from pathlib import Path

from sonos_firmware.gpl_archive import atomic_json, file_sha256, load_json, timestamp

ROOT = Path(__file__).resolve().parents[1]


def inspect_uimage(path: Path) -> dict:
    with path.open("rb") as f:
        header = f.read(64)
        if len(header) != 64:
            raise ValueError("truncated uImage header")
        magic, expected_hcrc, created, length, load, entry, expected_dcrc, os_type, arch, image_type, compression, name = struct.unpack(">7I4B32s", header)
        if magic != 0x27051956:
            raise ValueError("not a u-boot legacy uImage")
        actual_hcrc = zlib.crc32(header[:4] + b"\x00\x00\x00\x00" + header[8:])
        actual_dcrc = 0
        payload = b""
        remaining = length
        while remaining:
            chunk = f.read(min(1024 * 1024, remaining))
            if not chunk:
                raise ValueError("truncated uImage payload")
            actual_dcrc = zlib.crc32(chunk, actual_dcrc)
            if length <= 32 * 1024 * 1024:
                payload += chunk
            remaining -= len(chunk)
    result = {"file": path.name, "sha256": file_sha256(path), "image_name": name.split(b"\0", 1)[0].decode("ascii", errors="replace"),
              "created_unix": created, "payload_bytes": length, "compression_id": compression,
              "architecture_id": arch, "os_id": os_type, "image_type_id": image_type,
              "header_crc_valid": actual_hcrc == expected_hcrc, "payload_crc_valid": actual_dcrc == expected_dcrc}
    if not (result["header_crc_valid"] and result["payload_crc_valid"]):
        raise ValueError("uImage CRC mismatch; do not use as correspondence evidence")
    if compression == 1 and payload:
        try:
            inflated = gzip.decompress(payload)
        except (OSError, EOFError):
            inflated = b""
        matches = re.findall(rb"Linux version [^\x00\r\n]{4,150}", inflated)
        result["linux_version_strings"] = [m.decode("latin-1") for m in matches[:6]]
    return result


def elf_metadata(binary: bytes) -> dict:
    """Read ELF note/architecture metadata from the target binary itself.

    Supports 32-bit and 64-bit little/big-endian ELF without external tools.
    Does not execute the target image or rely on a host-side `file` guess.
    """
    if len(binary) < 52 or binary[:4] != b"\x7fELF" or binary[4] not in (1, 2) or binary[5] not in (1, 2):
        return {"state": "not-elf"}
    endian = "<" if binary[5] == 1 else ">"
    cls = binary[4]
    machine = struct.unpack_from(endian + "H", binary, 18)[0]
    if cls == 1:
        if len(binary) < 52:
            return {"state": "invalid-elf-header"}
        phoff = struct.unpack_from(endian + "I", binary, 28)[0]
        shoff = struct.unpack_from(endian + "I", binary, 32)[0]
        phentsize, phnum, shentsize, shnum = struct.unpack_from(endian + "HHHH", binary, 42)
        ph_format = endian + "IIIIIIII"
        sh_format = endian + "IIIIIIIIII"
    else:
        if len(binary) < 64:
            return {"state": "invalid-elf-header"}
        phoff = struct.unpack_from(endian + "Q", binary, 32)[0]
        shoff = struct.unpack_from(endian + "Q", binary, 40)[0]
        phentsize, phnum, shentsize, shnum = struct.unpack_from(endian + "HHHH", binary, 54)
        ph_format = endian + "IIQQQQQQ"
        sh_format = endian + "IIQQQQIIQQ"
    result = {"state": "elf", "class": 32 if cls == 1 else 64,
              "endian": "little" if endian == "<" else "big", "machine": machine,
              "sha256": __import__("hashlib").sha256(binary).hexdigest()}
    notes = []
    phsize = struct.calcsize(ph_format)
    if 0 < phentsize and phentsize >= phsize and phnum < 4096 and phoff + phentsize * phnum <= len(binary):
        for index in range(phnum):
            values = struct.unpack_from(ph_format, binary, phoff + index * phentsize)
            if values[0] == 4:  # PT_NOTE
                offset, size = (values[1], values[4]) if cls == 1 else (values[2], values[5])
                if offset + size <= len(binary):
                    notes.append(binary[offset:offset + size])
    shsize = struct.calcsize(sh_format)
    if 0 < shentsize and shentsize >= shsize and shnum < 16384 and shoff + shentsize * shnum <= len(binary):
        for index in range(shnum):
            values = struct.unpack_from(sh_format, binary, shoff + index * shentsize)
            if values[1] == 7:  # SHT_NOTE
                offset, size = values[4], values[5]
                if offset + size <= len(binary):
                    notes.append(binary[offset:offset + size])
    for note in notes:
        cursor = 0
        while cursor + 12 <= len(note):
            namesz, descsz, kind = struct.unpack_from(endian + "III", note, cursor)
            cursor += 12
            if namesz > len(note) - cursor:
                break
            name = note[cursor:cursor + namesz]
            cursor += (namesz + 3) & ~3
            if descsz > len(note) - cursor:
                break
            desc = note[cursor:cursor + descsz]
            cursor += (descsz + 3) & ~3
            if name.rstrip(b"\x00") == b"GNU" and kind == 3:
                result["gnu_build_id"] = desc.hex()
                return result
    result["gnu_build_id"] = None
    return result


def inspect_squashfs(path: Path, targets: list[str]) -> dict:
    listing = subprocess.run(["unsquashfs", "-ll", str(path)], check=True, capture_output=True, text=True).stdout
    matches = {}
    for suffix in targets:
        candidates = [line.split()[ -1] for line in listing.splitlines() if line.split() and line.split()[-1] == "squashfs-root/" + suffix]
        if len(candidates) != 1:
            matches[suffix] = {"state": "not-unique-or-missing", "paths": candidates[:5]}
            continue
        command = ["unsquashfs", "-cat", str(path), suffix]
        binary = subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
        patterns = [rb"FFmpeg version [0-9]+\.[0-9]+(?:\.[0-9]+)?", rb"BusyBox v[0-9]+\.[0-9]+(?:\.[0-9]+)?",
                    rb"/usr/src/debug/ffmpeg/[0-9]+\.[0-9]+(?:\.[0-9]+)?-r[0-9]+"]
        strings = []
        for pattern in patterns:
            strings += [x.decode("ascii", errors="replace") for x in re.findall(pattern, binary)[:4]]
        matches[suffix] = {"state": "inspected", "bytes": len(binary), "version_evidence": sorted(set(strings)),
                           "elf": elf_metadata(binary)}
    return {"file": path.name, "sha256": file_sha256(path), "targets": matches}


def correlate(catalog: dict, kernel: dict | None, rootfs: dict | None) -> list[dict]:
    results = []
    evidence = []
    if kernel:
        pattern = re.search(r"Linux-(\d+\.\d+\.\d+)", kernel["image_name"])
        if pattern:
            evidence.append(("kernel", pattern.group(1), ["linux-" + pattern.group(1), "linux-sonos-" + pattern.group(1)]))
    if rootfs:
        for key, value in rootfs["targets"].items():
            for string in value.get("version_evidence", []):
                ffmpeg = re.search(r"ffmpeg[/ ](\d+\.\d+(?:\.\d+)?)", string, re.I)
                busybox = re.search(r"busybox v(\d+\.\d+(?:\.\d+)?)", string, re.I)
                if ffmpeg:
                    evidence.append((key, string, ["ffmpeg-" + ffmpeg.group(1)]))
                if busybox:
                    evidence.append((key, string, ["busybox-" + busybox.group(1)]))
    for component, binary_string, prefixes in evidence:
        for version, record in catalog["releases"].items():
            if record["state"] != "verified":
                continue
            for item in record["links"]:
                if item["origin"] == "sonos" and any(item["filename"].lower().startswith(prefix) for prefix in prefixes):
                    results.append({"firmware_component": component, "binary_evidence": binary_string,
                                    "gpl_version": version, "source_file": item["filename"],
                                    "source_url": item["fetch_url"], "evidence_level": "component-version-candidate",
                                    "exact_corresponding_source_proven": False})
    grouped = {}
    for match in results:
        identity = (match["firmware_component"], match["gpl_version"], match["source_file"])
        record = grouped.setdefault(identity, {**match, "binary_evidence": []})
        if match["binary_evidence"] not in record["binary_evidence"]:
            record["binary_evidence"].append(match["binary_evidence"])
    return sorted(grouped.values(), key=lambda m: (m["firmware_component"], m["gpl_version"], m["source_file"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", type=Path)
    parser.add_argument("--rootfs", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "data/gpl/live/binary-analysis.json")
    parser.add_argument("--catalog", type=Path, default=ROOT / "data/gpl/live/catalog.json")
    args = parser.parse_args()
    if not args.kernel and not args.rootfs:
        parser.error("specify --kernel and/or --rootfs")
    kernel = inspect_uimage(args.kernel) if args.kernel else None
    rootfs = inspect_squashfs(args.rootfs, ["bin/busybox", "lib/libavcodec.so.59"]) if args.rootfs else None
    matches = correlate(load_json(args.catalog, {"releases": {}}), kernel, rootfs)
    report = {"schema_version": 1, "generated": timestamp(), "kernel": kernel, "rootfs": rootfs,
              "source_candidates": matches,
              "conclusion": "Binary version and build-path matches identify candidate GPL source packages but do not establish bit-for-bit or complete corresponding source."}
    atomic_json(args.output, report)
    print(json.dumps({"candidate_matches": len(matches), "kernel": kernel["image_name"] if kernel else None,
                      "rootfs_version_evidence": {k: v.get("version_evidence") for k, v in rootfs["targets"].items()} if rootfs else None}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"GPL firmware analysis: {error}", file=sys.stderr)
        raise SystemExit(1)
