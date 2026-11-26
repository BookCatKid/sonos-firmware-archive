"""Bounded, provenance-preserving GPL reference extraction from actual binaries.

APK/IPA/JAR/ZIP central directories are interpreted before scanning the
uncompressed DEX, ELF, Mach-O, resources and app text. No target code executes.
Byte scanning is deliberately not mistaken for a full control-flow analysis:
indirect, generated or encrypted URLs can remain undiscovered.
"""

from __future__ import annotations

import re
import tempfile
import zipfile
from pathlib import Path

from . import firmware_inventory

from sonos_firmware.gpl_archive import file_sha256, valid_version

CHUNK_BYTES = 1024 * 1024
CARRY_BYTES = 256
DEFAULT_SCAN_BUDGET = 768 * 1024 * 1024
DEFAULT_ENTRY_BUDGET = 300 * 1024 * 1024
ASCII_PATTERN = re.compile(rb"(?i)/documents/gpl/([0-9]+\.[0-9]+)/")
PREFIX_UTF16 = b"".join(bytes((c, 0)) for c in b"/documents/gpl/")
UTF16_PATTERN = re.compile(re.escape(PREFIX_UTF16) +
                           rb"((?:[0-9]\x00)+\.\x00(?:[0-9]\x00)+)/\x00")
SCANNABLE_EXTENSIONS = {
    ".dex", ".so", ".dylib", ".dll", ".exe", ".elf", ".bin", ".wasm",
    ".js", ".json", ".xml", ".plist", ".html", ".txt", ".dart", ".map",
    ".cfg", ".ini", ".strings", ".bundle", ".class", ".pak", ".jsbundle",
}
ARCHIVE_EXTENSIONS = {'.zip', '.apk', '.ipa', '.jar', '.aab', '.xapk'}
TAR_SUFFIXES = ('.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tar.xz', '.txz')


def _merge_nested(result, child, entry):
    result['bytes_scanned'] += child['bytes_scanned']
    result['entries_scanned'] += child['entries_scanned']
    if not child['complete']:
        result['complete'] = False
        result['limitations'].append(f'nested scan incomplete: {entry}')
    for version, refs in child['version_evidence'].items():
        for ref in refs:
            nested = entry + ('!' + ref['entry'] if ref['entry'] else '')
            result['version_evidence'].setdefault(version, []).append({**ref, 'entry': nested})


def _scan_tar(path, result, max_bytes, max_entry_bytes, depth):
    result['format'] = 'tar'
    with tempfile.TemporaryDirectory(prefix='sonos-binary-tar-') as temporary:
        directory = Path(temporary)
        try:
            firmware_inventory.unpack_tar(path.read_bytes(), directory,
                limits=firmware_inventory.InventoryLimits(expanded_bytes=max_bytes))
        except Exception as error:
            result['complete'] = False
            result['limitations'].append(f'tar decoding incomplete: {type(error).__name__}')
        for member in sorted(directory.rglob('*')):
            if not member.is_file():
                continue
            remaining = max_bytes - result['bytes_scanned']
            if remaining <= 0:
                result['complete'] = False
                result['limitations'].append('aggregate uncompressed byte budget reached')
                break
            entry = member.relative_to(directory).as_posix()
            if member.stat().st_size > max_entry_bytes:
                result['complete'] = False
                result['limitations'].append(f'member exceeds per-entry budget: {entry}')
                continue
            child = scan_binary(member, max_bytes=remaining, max_entry_bytes=max_entry_bytes, _depth=depth + 1)
            _merge_nested(result, child, entry)


def is_binary_member(name: str) -> bool:
    leaf = name.rsplit("/", 1)[-1]
    if Path(leaf).suffix.lower() in SCANNABLE_EXTENSIONS:
        return True
    # Unextended main Mach-O executable in an iOS application bundle.
    if ".app/" in name:
        app_basename = name.split(".app/", 1)[0].rsplit("/", 1)[-1]
        if leaf == app_basename:
            return True
    return False


def _scan_stream(stream, *, max_bytes: int, entry: str | None, result: dict) -> int:
    carry = b""
    seen = set()
    consumed = 0
    while consumed < max_bytes:
        data = stream.read(min(CHUNK_BYTES, max_bytes - consumed))
        if not data:
            break
        offset = consumed - len(carry)
        chunk = carry + data
        for fmt, pattern in (("ascii", ASCII_PATTERN), ("utf16le", UTF16_PATTERN)):
            for match in pattern.finditer(chunk):
                try:
                    version = match.group(1).replace(b"\x00", b"").decode("ascii")
                    valid_version(version)
                except (UnicodeDecodeError, ValueError):
                    continue
                position = offset + match.start()
                key = (version, entry, position, fmt)
                if key in seen:
                    continue
                seen.add(key)
                evidence = {"entry": entry, "format": fmt, "offset": position}
                result.setdefault("version_evidence", {}).setdefault(version, []).append(evidence)
        consumed += len(data)
        carry = chunk[-CARRY_BYTES:]
    return consumed


def scan_binary(path: Path, *, max_bytes: int = DEFAULT_SCAN_BUDGET,
                max_entry_bytes: int = DEFAULT_ENTRY_BUDGET, _depth=0) -> dict:
    """Return evidence, exact file hash, and whether in-scope members were all scanned."""
    path = Path(path)
    if max_bytes < 1 or max_entry_bytes < 1:
        raise ValueError("scan byte budgets must be positive")
    result = {"filename": path.name, "sha256": file_sha256(path), "format": "raw",
              "bytes_scanned": 0, "entries_scanned": 0, "complete": True,
              "limitations": [], "version_evidence": {},
              "scope": "decompressed executable and text-like members; no code execution"}
    if _depth > 4:
        result.update(complete=False, limitations=['archive nesting exceeds the scan depth limit'])
        return result
    with path.open('rb') as stream:
        prefix = stream.read(512)
    zipped = zipfile.is_zipfile(path)
    archive_signature = prefix.startswith((b'\x1f\x8b', b'BZh', b'\xfd7zXZ\x00')) or prefix[257:262] == b'ustar'
    if not zipped and (path.name.lower().endswith(TAR_SUFFIXES) or archive_signature):
        _scan_tar(path, result, max_bytes, max_entry_bytes, _depth)
    elif zipped:
        result["format"] = "zip"
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                suffix = next((suffix for suffix in TAR_SUFFIXES if info.filename.lower().endswith(suffix)), '')
                if not suffix and Path(info.filename).suffix.lower() in ARCHIVE_EXTENSIONS:
                    suffix = Path(info.filename).suffix.lower()
                nested_archive = bool(suffix)
                if info.is_dir() or not (is_binary_member(info.filename) or nested_archive):
                    continue
                remaining = max_bytes - result["bytes_scanned"]
                if remaining <= 0:
                    result["complete"] = False
                    result["limitations"].append("aggregate uncompressed byte budget reached")
                    break
                if info.file_size > max_entry_bytes:
                    result["complete"] = False
                    result["limitations"].append(f"member exceeds per-entry budget: {info.filename}")
                    continue
                try:
                    with archive.open(info) as stream:
                        if nested_archive:
                            if info.file_size > remaining:
                                result['complete'] = False
                                result['limitations'].append(f'nested archive exceeds remaining byte budget: {info.filename}')
                                continue
                            with tempfile.TemporaryDirectory(prefix='sonos-nested-binary-') as temporary:
                                nested = Path(temporary)/('nested' + suffix)
                                with nested.open('wb') as output:
                                    while chunk := stream.read(CHUNK_BYTES):
                                        output.write(chunk)
                                scanned = nested.stat().st_size
                                result['bytes_scanned'] += scanned
                                if scanned >= remaining:
                                    result['complete'] = False
                                    result['limitations'].append(f'nested archive leaves no remaining scan budget: {info.filename}')
                                else:
                                    child = scan_binary(nested, max_bytes=remaining-scanned,
                                                        max_entry_bytes=max_entry_bytes, _depth=_depth + 1)
                                    _merge_nested(result, child, info.filename)
                                scanned = 0
                        else:
                            scanned = _scan_stream(stream, max_bytes=min(info.file_size, remaining),
                                                   entry=info.filename, result=result)
                except (OSError, RuntimeError, EOFError, zipfile.BadZipFile) as error:
                    result["complete"] = False
                    result["limitations"].append(f"unreadable archive member: {info.filename} ({type(error).__name__})")
                    continue
                result["entries_scanned"] += 1
                result["bytes_scanned"] += scanned
                if not nested_archive and scanned < info.file_size:
                    result["complete"] = False
                    result["limitations"].append(f"partial member scan due to byte budget: {info.filename}")
    else:
        if path.suffix.lower() in ARCHIVE_EXTENSIONS:
            # An IPA/APK with missing or displaced ZIP central directory is
            # not equivalent to a scanned, decompressed application. Scan the
            # remaining raw bytes for partial evidence, but mark it incomplete.
            result["format"] = "unreadable-zip"
            result["complete"] = False
            result["limitations"].append(
                "ZIP container central directory unreadable; raw bytes only, compressed members not decoded"
            )
        with path.open("rb") as stream:
            read = _scan_stream(stream, max_bytes=min(max_bytes, path.stat().st_size),
                                entry=None, result=result)
        result["entries_scanned"] = 1
        result["bytes_scanned"] = read
        if read < path.stat().st_size:
            result["complete"] = False
            result["limitations"].append("raw binary exceeds scan byte budget")
    if result["format"] == "zip" and result["entries_scanned"] == 0:
        result["complete"] = False
        result["limitations"].append("no executable or text-like archive members were scanned")
    result["version_evidence"] = dict(sorted(result["version_evidence"].items(),
                                              key=lambda item: tuple(map(int, item[0].split(".")))))
    for entries in result["version_evidence"].values():
        entries.sort(key=lambda item: (item["entry"] or "", item["offset"], item["format"]))
    return result
