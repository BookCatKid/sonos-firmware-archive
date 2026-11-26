"""Streaming, provenance-preserving GPL reference extraction from actual binaries.

APK/IPA/JAR/ZIP central directories are interpreted before scanning the
uncompressed DEX, ELF, Mach-O, resources and app text. No target code executes.
Byte scanning is deliberately not mistaken for a full control-flow analysis:
indirect, generated or encrypted URLs can remain undiscovered.
"""

from __future__ import annotations

import re
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path

from sonos_firmware.gpl_archive import file_sha256, valid_version

CHUNK_BYTES = 1024 * 1024
CARRY_BYTES = 256
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


def _scan_tar(path, result, ancestry):
    result['format'] = 'tar'
    with tempfile.TemporaryDirectory(prefix='sonos-binary-tar-') as temporary:
        directory = Path(temporary)
        try:
            with tarfile.open(path, 'r|*') as archive:
                for member in archive:
                    if not member.isfile():
                        continue
                    name = Path(member.name)
                    if name.is_absolute() or '..' in name.parts:
                        result['complete'] = False
                        result['limitations'].append(f'unsafe tar member: {member.name}')
                        continue
                    target = directory / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(member) as stream, target.open('wb') as output:
                        shutil.copyfileobj(stream, output, CHUNK_BYTES)
                    child = scan_binary(target, _ancestry=ancestry)
                    _merge_nested(result, child, name.as_posix())
                    target.unlink()
        except (OSError, EOFError, tarfile.TarError) as error:
            result['complete'] = False
            result['limitations'].append(f'tar decoding incomplete: {type(error).__name__}')


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


def _scan_stream(stream, *, entry: str | None, result: dict) -> int:
    carry = b""
    seen = set()
    consumed = 0
    while data := stream.read(CHUNK_BYTES):
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


def scan_binary(path: Path, *, _ancestry=()) -> dict:
    """Scan every in-scope member until EOF, retaining exact provenance."""
    path = Path(path)
    digest = file_sha256(path)
    result = {"filename": path.name, "sha256": digest, "format": "raw",
              "bytes_scanned": 0, "entries_scanned": 0, "complete": True,
              "limitations": [], "version_evidence": {},
              "scope": "decompressed executable and text-like members; no code execution"}
    if digest in _ancestry:
        result.update(complete=False, limitations=['cyclic archive content'])
        return result
    ancestry = (*_ancestry, digest)
    with path.open('rb') as stream:
        prefix = stream.read(512)
    zipped = zipfile.is_zipfile(path)
    archive_signature = prefix.startswith((b'\x1f\x8b', b'BZh', b'\xfd7zXZ\x00')) or prefix[257:262] == b'ustar'
    if not zipped and (path.name.lower().endswith(TAR_SUFFIXES) or archive_signature):
        _scan_tar(path, result, ancestry)
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
                try:
                    with archive.open(info) as stream:
                        if nested_archive:
                            with tempfile.TemporaryDirectory(prefix='sonos-nested-binary-') as temporary:
                                nested = Path(temporary)/('nested' + suffix)
                                with nested.open('wb') as output:
                                    shutil.copyfileobj(stream, output, CHUNK_BYTES)
                                result['bytes_scanned'] += nested.stat().st_size
                                child = scan_binary(nested, _ancestry=ancestry)
                                _merge_nested(result, child, info.filename)
                            scanned = 0
                        else:
                            scanned = _scan_stream(stream, entry=info.filename, result=result)
                except (OSError, RuntimeError, EOFError, zipfile.BadZipFile) as error:
                    result["complete"] = False
                    result["limitations"].append(f"unreadable archive member: {info.filename} ({type(error).__name__})")
                    continue
                result["entries_scanned"] += 1
                result["bytes_scanned"] += scanned
    else:
        if path.suffix.lower() in ARCHIVE_EXTENSIONS:
            result["format"] = "unreadable-zip"
            result["complete"] = False
            result["limitations"].append(
                "ZIP container central directory unreadable; raw bytes only, compressed members not decoded"
            )
        with path.open("rb") as stream:
            result["bytes_scanned"] = _scan_stream(stream, entry=None, result=result)
        result["entries_scanned"] = 1
    if result["format"] == "zip" and result["entries_scanned"] == 0:
        result["complete"] = False
        result["limitations"].append("no executable or text-like archive members were scanned")
    result["version_evidence"] = dict(sorted(result["version_evidence"].items(),
                                              key=lambda item: tuple(map(int, item[0].split(".")))))
    for entries in result["version_evidence"].values():
        entries.sort(key=lambda item: (item["entry"] or "", item["offset"], item["format"]))
    return result
