"""Non-executing section, archive, filesystem and kernel analysis for any UPD."""
from __future__ import annotations
import hashlib
import bz2
import gzip
import io
import json
import math
import lzma
import shutil
import stat
import struct
import subprocess
import tarfile
import tempfile
import time
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from . import cramfs
from .catalog import filesystem_manifest
from .extract import extract_available_components
from .upd import parse_bytes


@dataclass(frozen=True)
class InventoryLimits:
    expanded_bytes: int = 512 * 1024 * 1024
    files: int = 100000
    depth: int = 64
    decoder_seconds: float = 300

    def __post_init__(self):
        if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0
               for value in (self.expanded_bytes, self.files, self.depth)):
            raise ValueError('inventory byte, file and depth limits must be positive integers')
        if not isinstance(self.decoder_seconds, (int, float)) or not math.isfinite(self.decoder_seconds) or self.decoder_seconds <= 0:
            raise ValueError('inventory decoder timeout must be positive and finite')


class _Budget:
    def __init__(self, limits):
        self.limits, self.bytes, self.files = limits, 0, 0

    def check(self, size, count=1):
        if self.bytes + size > self.limits.expanded_bytes or self.files + count > self.limits.files:
            raise ValueError('expanded inventory exceeds its resource budget')

    def consume(self, size, count=1):
        self.check(size, count)
        self.bytes += size
        self.files += count


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def package_version(data: bytes) -> str:
    headers = [s for s in parse_bytes(data) if s.section_type == 1]
    if len(headers) != 1 or headers[0].payload_length != 12:
        raise ValueError("expected one 12-byte type-1 version header")
    offset = headers[0].offset + 16
    major, minor, build = struct.unpack_from("<III", data, offset)
    return f"{major}.{minor}-{build}"


class _TarInput:
    """Bound even extended-header reads before tarfile interprets their size."""
    def __init__(self, stream, size):
        self.stream, self.size = stream, size

    def read(self, size=-1):
        remaining = self.size - self.stream.tell()
        if size >= 0 and remaining == 0:
            return b''
        if size < 0 or size > remaining:
            raise ValueError('tar metadata read exceeds the bounded archive')
        return self.stream.read(size)

    def seek(self, *args):
        return self.stream.seek(*args)

    def tell(self):
        return self.stream.tell()


def unpack_tar(payload: bytes, directory: Path, *, budget=None, limits=None) -> list[dict]:
    """Write regular members only; retain links and special entries as metadata."""
    inventory = []
    budget = budget or _Budget(limits or InventoryLimits())
    seen = set()
    # Bound the entire decompressed stream, including PAX/GNU metadata that
    # tarfile parses before yielding a member to our per-file budget checks.
    ceiling = budget.limits.expanded_bytes - budget.bytes + budget.limits.files * 1024 + 10240
    raw = io.BytesIO(payload)
    if payload.startswith(b'\x1f\x8b'):
        stream = gzip.GzipFile(fileobj=raw)
    elif payload.startswith(b'BZh'):
        stream = bz2.BZ2File(raw)
    elif payload.startswith(b'\xfd7zXZ\x00'):
        stream = lzma.LZMAFile(raw)
    else:
        stream = raw
    with stream, tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as bounded:
        while chunk := stream.read(min(1024 * 1024, ceiling - bounded.tell() + 1)):
            bounded.write(chunk)
            if bounded.tell() > ceiling:
                raise ValueError('expanded tar exceeds its resource budget')
        size = bounded.tell()
        bounded.seek(0)
        with tarfile.open(fileobj=_TarInput(bounded, size), mode='r:') as archive:
            return _unpack_tar_members(archive, directory, budget, inventory, seen)


def _unpack_tar_members(archive, directory, budget, inventory, seen):
    for member in archive:
        path = PurePosixPath(member.name)
        if not path.parts and member.isdir() and member.name:
            if "." in seen:
                raise ValueError("duplicate tar member: .")
            seen.add(".")
            budget.consume(0)
            inventory.append({"path": ".", "mode": member.mode, "bytes": member.size, "type": "5",
                              'uid': member.uid, 'gid': member.gid})
            continue
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError(f"unsafe tar member: {member.name}")
        if len(path.parts) > budget.limits.depth:
            raise ValueError('tar member depth exceeds the inventory limit')
        name = path.as_posix()
        if name in seen:
            raise ValueError(f"duplicate tar member: {name}")
        seen.add(name)
        record = {"path": name, "mode": member.mode, "bytes": member.size,
                  'uid': member.uid, 'gid': member.gid,
                  "type": member.type.decode("ascii", errors="replace")}
        budget.consume(member.size if member.isfile() else 0)
        if member.issym() or member.islnk():
            record["target"] = member.linkname
        if member.isfile():
            with archive.extractfile(member) as stream:
                content = stream.read()
            destination = directory.joinpath(*path.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            record["sha256"] = sha256(content)
        elif member.isdir():
            directory.joinpath(*path.parts).mkdir(parents=True, exist_ok=True)
        inventory.append(record)
    return inventory


def legacy_kernel_data(payload: bytes, *, limit=128 * 1024 * 1024, seconds=300) -> tuple[int, bytes] | None:
    """Locate the validated gzip stream in a PowerPC self-extracting zImage."""
    offset = payload.find(b"\x1f\x8b\x08")
    deadline = time.monotonic() + seconds
    work = 0
    view = memoryview(payload)
    while offset >= 0:
        if time.monotonic() >= deadline or work >= 64 * 1024 * 1024:
            raise ValueError('legacy kernel scan exceeds its resource budget')
        stream = zlib.decompressobj(31)
        chunks = []
        total = 0
        cursor = offset
        try:
            # Limit candidate input as well as output; repeated false markers
            # must not copy or decompress every suffix of a large wrapper.
            while cursor < len(payload) and not stream.eof:
                if time.monotonic() >= deadline or work >= 64 * 1024 * 1024:
                    raise ValueError('legacy kernel scan exceeds its resource budget')
                length = min(len(payload) - cursor, 65536, 64 * 1024 * 1024 - work)
                work += length
                chunk = stream.decompress(view[cursor:cursor + length], limit - total + 1)
                chunks.append(chunk)
                total += len(chunk)
                if total > limit:
                    raise ValueError('legacy kernel data exceeds its inventory budget')
                cursor += length
        except zlib.error:
            pass
        else:
            if stream.eof:
                return offset, b''.join(chunks)
        offset = payload.find(b"\x1f\x8b\x08", offset + 1)
    return None


def _uimage_data(data, compression, limit):
    # Compression identifiers: U-Boot include/image.h, enum IH_COMP_*.
    # https://github.com/u-boot/u-boot/blob/master/include/image.h
    if compression == 0:
        if len(data) > limit:
            raise ValueError('kernel data exceeds its inventory budget')
        return data
    if compression == 1:
        decoder = zlib.decompressobj(31)
    elif compression == 2:
        decoder = bz2.BZ2Decompressor()
    elif compression == 3:
        decoder = lzma.LZMADecompressor(memlimit=128 * 1024 * 1024)
    else:
        raise ValueError('unsupported uImage compression')
    result = decoder.decompress(data, max_length=limit + 1)
    if not decoder.eof or len(result) > limit:
        raise ValueError('kernel stream is truncated or exceeds its inventory budget')
    return result


def _unpack_cramfs(payload, directory, budget):
    order, offset = cramfs.detect_filesystem(payload)
    root = cramfs.inode_at(payload, offset + cramfs.ROOT_INODE_OFFSET, order)
    entries = []
    stack = [('.', root, frozenset())]
    while stack:
        name, inode, ancestors = stack.pop()
        if len(PurePosixPath(name).parts) > budget.limits.depth:
            raise ValueError('CRAMFS directory depth exceeds the inventory limit')
        mode = inode['mode']
        record = {'path': name, 'mode': stat.S_IMODE(mode), 'uid': inode['uid'], 'gid': inode['gid']}
        if stat.S_ISDIR(mode):
            location = (inode['offset'], inode['size'])
            if location in ancestors:
                raise ValueError('cyclic CRAMFS directory')
            budget.consume(0)
            record['type'] = 'directory'
            (directory / name).mkdir(parents=True, exist_ok=True)
            for child_name, child in reversed(list(cramfs.entries(payload, inode, order))):
                path = PurePosixPath(child_name)
                if len(path.parts) != 1 or child_name != path.name or child_name in {'.', '..'} or path.is_absolute():
                    raise ValueError('unsafe CRAMFS member name')
                relative = child_name if name == '.' else name + '/' + child_name
                stack.append((relative, child, ancestors | {location}))
        elif stat.S_ISREG(mode) or stat.S_ISLNK(mode):
            budget.consume(inode['size'])
            content = cramfs.read_file(payload, inode, order, max_bytes=budget.limits.expanded_bytes)
            if stat.S_ISLNK(mode):
                record.update(type='symlink', target=content.decode('utf-8', 'surrogateescape'))
            else:
                destination = directory / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
                record.update(type='file', size=len(content), sha256=sha256(content))
        else:
            budget.consume(0)
            record.update(type='special', device=inode['size'])
        entries.append(record)
    return {'format': 'cramfs', 'byte_order': order, 'root': directory.name,
            'entries': sorted(entries, key=lambda row: row['path'])}


def _unpack_squashfs(component, directory, budget):
    deadline = time.monotonic() + budget.limits.decoder_seconds
    command = ['unsquashfs', '-no-progress', '-no-xattrs', '-d', str(directory), str(component)]
    def measure():
        size = count = 0
        for path in directory.rglob('*'):
            info = path.lstat()
            count += 1
            if len(path.relative_to(directory).parts) > budget.limits.depth:
                raise ValueError('filesystem directory depth exceeds the inventory limit')
            size += info.st_size if stat.S_ISREG(info.st_mode) else 0
            budget.check(size, count)
        return size, count
    with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as process:
        try:
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    raise TimeoutError('filesystem decoder timed out')
                measure()
                time.sleep(.1)
            if process.returncode:
                raise ValueError('filesystem decoder failed')
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    if not directory.is_dir():
        raise ValueError('filesystem decoder produced no tree')
    budget.consume(*measure())
    manifest = filesystem_manifest(directory)
    manifest['format'] = 'squashfs'
    return manifest


def inventory_package(source, directory, keys=None, *, store_sections=False, limits=None):
    """Extract available payloads and analyze recognized containers for any model."""
    if directory.exists() and any(directory.iterdir()):
        raise ValueError('inventory output directory must be empty')
    directory.mkdir(parents=True, exist_ok=True)
    limits = limits or InventoryLimits()
    if min(limits.expanded_bytes, limits.files, limits.depth, limits.decoder_seconds) <= 0:
        raise ValueError('inventory limits must be positive')
    budget = _Budget(limits)
    data = source.read_bytes()
    sections = parse_bytes(data)
    if not sections:
        raise ValueError('UPD contains no sections')
    available = extract_available_components(source, directory, keys)
    by_index = {row['section_index']: row for row in available['components']}
    try:
        version = package_version(data)
    except ValueError:
        version = None
    report = {'schema_version': 1, 'source': source.name, 'version': version,
              'bytes': len(data), 'sha256': sha256(data), 'sections': [],
              'components': available['components'], 'derived': [], 'errors': [], 'skipped': available['skipped']}
    for section in sections:
        prefix = f'{source.stem}-section-{section.index:02d}-{section.name}'
        record = section.to_dict()
        record['source_span'] = {'offset': section.offset + 16, 'bytes': section.payload_length}
        if store_sections:
            name = prefix + '-stored.bin'
            payload = data[section.offset + 16:section.offset + section.length]
            (directory / name).write_bytes(payload)
            report['derived'].append({'package_id': source.stem, 'section_index': section.index,
                'section_type': section.section_type, 'kind': 'stored-section', 'filename': name,
                'bytes': len(payload), 'sha256': sha256(payload), 'source_encrypted': section.encrypted,
                'recipient_id': section.recipient_id})
        component = by_index.get(section.index)
        if component is None:
            record['extraction'] = 'blocked-key'
            report['sections'].append(record)
            continue
        record['extraction'] = 'decrypted' if section.encrypted else 'plaintext'
        record['component'] = component['filename']
        payload = (directory / component['filename']).read_bytes()
        expanded = directory / (prefix + '-files')
        prior_budget = budget.bytes, budget.files
        try:
            if section.section_type == 23:
                record['members'] = unpack_tar(payload, expanded, budget=budget)
            elif payload[:4] in {b'hsqs', b'sqsh'}:
                record['filesystem'] = _unpack_squashfs(directory / component['filename'], expanded, budget)
            elif any(payload[offset:offset + 4] in cramfs.CRAMFS_MAGICS for offset in (0, 0x200)):
                record['filesystem'] = _unpack_cramfs(payload, expanded, budget)
            elif section.section_type == 4:
                raise ValueError('unsupported root filesystem format')
            if section.section_type == 6:
                kernel = None
                if payload.startswith(bytes.fromhex('27051956')):
                    if len(payload) < 64:
                        raise ValueError('truncated uImage header')
                    size = int.from_bytes(payload[12:16], 'big')
                    if 64 + size > len(payload):
                        raise ValueError('truncated uImage data')
                    compressed = payload[64:64 + size]
                    kernel = _uimage_data(compressed, payload[31], min(128 * 1024 * 1024, limits.expanded_bytes - budget.bytes))
                    header = bytearray(payload[:64])
                    header[4:8] = bytes(4)
                    record['uimage'] = {'name': payload[32:64].rstrip(b'\0').decode(errors='replace'),
                        'compression': payload[31], 'bytes': len(kernel), 'sha256': sha256(kernel),
                        'architecture': payload[29], 'os': payload[28], 'image_type': payload[30],
                        'load_address': int.from_bytes(payload[16:20], 'big'),
                        'entry_point': int.from_bytes(payload[20:24], 'big'),
                        'header_crc_valid': zlib.crc32(header) == int.from_bytes(payload[4:8], 'big'),
                        'data_crc_valid': zlib.crc32(compressed) == int.from_bytes(payload[24:28], 'big')}
                else:
                    decoded = legacy_kernel_data(payload, limit=min(128 * 1024 * 1024, limits.expanded_bytes - budget.bytes),
                                                 seconds=limits.decoder_seconds)
                    if decoded:
                        offset, kernel = decoded
                        record['zimage'] = {'gzip_offset': offset, 'bytes': len(kernel), 'sha256': sha256(kernel)}
                if kernel is not None:
                    budget.consume(len(kernel))
                    name = prefix + '-unpacked.bin'
                    (directory / name).write_bytes(kernel)
                    report['derived'].append({'package_id': source.stem, 'section_index': section.index,
                        'section_type': section.section_type, 'kind': 'kernel-data', 'filename': name,
                        'bytes': len(kernel), 'sha256': sha256(kernel)})
        except Exception as error:
            shutil.rmtree(expanded, ignore_errors=True)
            budget.bytes, budget.files = prior_budget
            record['analysis_error'] = {'type': type(error).__name__, 'message': getattr(error, 'strerror', None) or str(error)}
            report['errors'].append({'section_index': section.index, **record['analysis_error']})
        report['sections'].append(record)
    report['payloads_complete'] = not report['skipped']
    report['complete'] = report['payloads_complete'] and not report['errors']
    return report


def bundle(directory, destination):
    """Bundle regular data reproducibly and retain original firmware modes."""
    modes = {}
    owners = {}
    manifest = directory / 'inventory.json'
    if manifest.exists():
        report = json.loads(manifest.read_text())
        for section in report['sections']:
            prefix = f"{Path(report['source']).stem}-section-{section['index']:02d}-{section['name']}-files/"
            for entry in section.get('members', []) + section.get('filesystem', {}).get('entries', []):
                name = prefix.rstrip('/') if entry['path'] == '.' else prefix + entry['path']
                modes[name] = entry['mode']
                owners[name] = (entry.get('uid', 0), entry.get('gid', 0))
    with tarfile.open(destination, 'w:xz', preset=0) as archive:
        for path in sorted(directory.rglob('*')):
            if path.resolve() == destination.resolve():
                continue
            info = archive.gettarinfo(str(path), arcname=path.relative_to(directory).as_posix())
            info.uid = info.gid = info.mtime = 0
            info.uname = info.gname = ''
            info.mode = modes.get(info.name, 0o755 if info.isdir() else 0o777 if info.issym() else 0o644)
            info.uid, info.gid = owners.get(info.name, (0, 0))
            if info.isfile():
                with path.open('rb') as stream:
                    archive.addfile(info, stream)
            else:
                archive.addfile(info)
