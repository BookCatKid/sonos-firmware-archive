"""Non-executing section, archive, filesystem and kernel analysis for any UPD."""
from __future__ import annotations
import hashlib
import bz2
import io
import json
import lzma
import shutil
import stat
import struct
import subprocess
import tarfile
import zlib
from pathlib import Path, PurePosixPath
from . import cramfs
from .catalog import filesystem_manifest
from .extract import extract_available_components
from .upd import parse_bytes


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def elf_header(data: bytes) -> dict | None:
    """Describe an ELF header only; never load or execute the object.

    Layout: https://gabi.xinuos.com/elf/02-eheader.html
    Machine registry: https://gabi.xinuos.com/elf/a-emachine.html
    Unknown machine IDs remain usable metadata, not a decoding restriction.
    """
    if not data.startswith(b'\x7fELF'):
        return None
    result = {'format': 'ELF'}
    if len(data) < 16:
        return {**result, 'header_error': 'truncated ELF identification'}
    bits = {1: 32, 2: 64}.get(data[4])
    order = {1: 'little', 2: 'big'}.get(data[5])
    if bits is None or order is None or data[6] != 1:
        return {**result, 'header_error': 'unsupported ELF class, byte order or identification version',
                'class_id': data[4], 'data_encoding': data[5], 'identification_version': data[6]}
    result.update(bits=bits, byte_order=order, os_abi=data[7], abi_version=data[8])
    layout = ('<' if order == 'little' else '>') + ('HHIIIIIHHHHHH' if bits == 32 else 'HHIQQQIHHHHHH')
    if len(data) < 16 + struct.calcsize(layout):
        return {**result, 'header_error': 'truncated ELF header'}
    kind, machine, version, entry, phoff, shoff, flags, size, *_ = struct.unpack_from(layout, data, 16)
    architectures = {3: 'x86', 8: 'MIPS', 20: 'PowerPC', 21: 'PowerPC64',
                     40: 'ARM', 62: 'x86-64', 183: 'AArch64', 243: 'RISC-V'}
    result.update(machine=machine, architecture=architectures.get(machine), object_type=kind,
                  object_type_name={1: 'relocatable', 2: 'executable', 3: 'shared-object', 4: 'core'}.get(kind),
                  version=version, entry_point=entry, flags=flags, header_bytes=size,
                  program_header_offset=phoff, section_header_offset=shoff)
    if version != 1 or size != 16 + struct.calcsize(layout):
        result['header_error'] = 'invalid ELF header size or version'
    return result


def _annotate_elf(record, data):
    if (metadata := elf_header(data[:64])) is not None:
        record['elf'] = metadata


def package_version(data: bytes) -> str:
    headers = [s for s in parse_bytes(data) if s.section_type == 1]
    if len(headers) != 1 or headers[0].payload_length != 12:
        raise ValueError("expected one 12-byte type-1 version header")
    offset = headers[0].offset + 16
    major, minor, build = struct.unpack_from("<III", data, offset)
    return f"{major}.{minor}-{build}"


def unpack_tar(payload: bytes, directory: Path) -> list[dict]:
    """Stream regular members and preserve metadata without extraction quotas."""
    with tarfile.open(fileobj=io.BytesIO(payload), mode='r|*') as archive:
        return _unpack_tar_members(archive, directory)


def _unpack_tar_members(archive, directory):
    inventory, seen = [], set()
    for member in archive:
        path = PurePosixPath(member.name)
        if not path.parts and member.isdir() and member.name:
            if "." in seen:
                raise ValueError("duplicate tar member: .")
            seen.add(".")
            inventory.append({"path": ".", "mode": member.mode, "bytes": member.size, "type": "5",
                              'uid': member.uid, 'gid': member.gid})
            continue
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError(f"unsafe tar member: {member.name}")
        name = path.as_posix()
        if name in seen:
            raise ValueError(f"duplicate tar member: {name}")
        seen.add(name)
        record = {"path": name, "mode": member.mode, "bytes": member.size,
                  'uid': member.uid, 'gid': member.gid,
                  "type": member.type.decode("ascii", errors="replace")}
        if member.issym() or member.islnk():
            record["target"] = member.linkname
        if member.isfile():
            destination = directory.joinpath(*path.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            first_bytes = bytearray()
            with archive.extractfile(member) as stream, destination.open('wb') as output:
                while block := stream.read(1024 * 1024):
                    if len(first_bytes) < 64:
                        first_bytes.extend(block[:64-len(first_bytes)])
                    digest.update(block)
                    output.write(block)
            if destination.stat().st_size != member.size:
                raise ValueError(f'truncated tar member: {name}')
            record["sha256"] = digest.hexdigest()
            _annotate_elf(record, first_bytes)
        elif member.isdir():
            directory.joinpath(*path.parts).mkdir(parents=True, exist_ok=True)
        inventory.append(record)
    return inventory


def legacy_kernel_data(payload: bytes) -> tuple[int, bytes] | None:
    """Locate a complete gzip stream in a legacy PowerPC kernel wrapper."""
    offset = payload.find(b"\x1f\x8b\x08")
    view = memoryview(payload)
    while offset >= 0:
        decoder = zlib.decompressobj(31)
        chunks = []
        cursor = offset
        try:
            while cursor < len(payload) and not decoder.eof:
                length = min(len(payload)-cursor, 65536)
                chunks.append(decoder.decompress(view[cursor:cursor+length]))
                cursor += length
        except zlib.error:
            pass
        else:
            if decoder.eof:
                return offset, b''.join(chunks)
        offset = payload.find(b"\x1f\x8b\x08", offset+1)
    return None


def _uimage_data(data, compression):
    if compression == 0:
        return data
    if compression == 1:
        decoder = zlib.decompressobj(31)
    elif compression == 2:
        decoder = bz2.BZ2Decompressor()
    elif compression == 3:
        decoder = lzma.LZMADecompressor()
    else:
        raise ValueError('unsupported uImage compression')
    result = decoder.decompress(data)
    if not decoder.eof:
        raise ValueError('kernel stream is truncated')
    return result


def _unpack_cramfs(payload, directory):
    order, offset = cramfs.detect_filesystem(payload)
    root = cramfs.inode_at(payload, offset + cramfs.ROOT_INODE_OFFSET, order)
    entries = []
    stack = [('.', root, frozenset())]
    while stack:
        name, inode, ancestors = stack.pop()
        mode = inode['mode']
        record = {'path': name, 'mode': stat.S_IMODE(mode), 'uid': inode['uid'], 'gid': inode['gid']}
        if stat.S_ISDIR(mode):
            location = (inode['offset'], inode['size'])
            if location in ancestors:
                raise ValueError('cyclic CRAMFS directory')
            record['type'] = 'directory'
            (directory / name).mkdir(parents=True, exist_ok=True)
            for child_name, child in reversed(list(cramfs.entries(payload, inode, order))):
                path = PurePosixPath(child_name)
                if len(path.parts) != 1 or child_name != path.name or child_name in {'.', '..'} or path.is_absolute():
                    raise ValueError('unsafe CRAMFS member name')
                relative = child_name if name == '.' else name + '/' + child_name
                stack.append((relative, child, ancestors | {location}))
        elif stat.S_ISREG(mode) or stat.S_ISLNK(mode):
            content = cramfs.read_file(payload, inode, order)
            if stat.S_ISLNK(mode):
                record.update(type='symlink', target=content.decode('utf-8', 'surrogateescape'))
            else:
                destination = directory / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
                record.update(type='file', size=len(content), sha256=sha256(content))
                _annotate_elf(record, content)
        else:
            record.update(type='special', device=inode['size'])
        entries.append(record)
    return {'format': 'cramfs', 'byte_order': order, 'root': directory.name,
            'entries': sorted(entries, key=lambda row: row['path'])}


def _unpack_squashfs(component, directory):
    command = ['unsquashfs', '-no-progress', '-no-xattrs', '-d', str(directory), str(component)]
    subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    if not directory.is_dir():
        raise ValueError('filesystem decoder produced no tree')
    manifest = filesystem_manifest(directory)
    manifest['format'] = 'squashfs'
    for record in manifest['entries']:
        if record['type'] == 'file':
            with (directory / record['path']).open('rb') as stream:
                _annotate_elf(record, stream.read(64))
    return manifest


def inventory_package(source, directory, keys=None, *, store_sections=False):
    """Extract available payloads and analyze recognized containers for any model."""
    if directory.exists() and any(directory.iterdir()):
        raise ValueError('inventory output directory must be empty')
    directory.mkdir(parents=True, exist_ok=True)
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
        _annotate_elf(component, payload)
        expanded = directory / (prefix + '-files')
        try:
            if section.section_type == 23:
                record['members'] = unpack_tar(payload, expanded)
            elif payload[:4] in {b'hsqs', b'sqsh'}:
                record['filesystem'] = _unpack_squashfs(directory / component['filename'], expanded)
            elif any(payload[offset:offset + 4] in cramfs.CRAMFS_MAGICS for offset in (0, 0x200)):
                record['filesystem'] = _unpack_cramfs(payload, expanded)
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
                    kernel = _uimage_data(compressed, payload[31])
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
                    decoded = legacy_kernel_data(payload)
                    if decoded:
                        offset, kernel = decoded
                        record['zimage'] = {'gzip_offset': offset, 'bytes': len(kernel), 'sha256': sha256(kernel)}
                if kernel is not None:
                    name = prefix + '-unpacked.bin'
                    (directory / name).write_bytes(kernel)
                    report['derived'].append({'package_id': source.stem, 'section_index': section.index,
                        'section_type': section.section_type, 'kind': 'kernel-data', 'filename': name,
                        'bytes': len(kernel), 'sha256': sha256(kernel)})
                    _annotate_elf(report['derived'][-1], kernel)
        except Exception as error:
            shutil.rmtree(expanded, ignore_errors=True)
            record['analysis_error'] = {'type': type(error).__name__, 'message': getattr(error, 'strerror', None) or str(error)}
            report['errors'].append({'section_index': section.index, **record['analysis_error']})
        binaries = [component, *record.get('members', []), *record.get('filesystem', {}).get('entries', []),
                    *(row for row in report['derived'] if row['section_index'] == section.index)]
        for binary in binaries:
            if error := binary.get('elf', {}).get('header_error'):
                report['errors'].append({'section_index': section.index,
                    'path': binary.get('path', binary.get('filename')), 'type': 'ELFHeaderError', 'message': error})
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
