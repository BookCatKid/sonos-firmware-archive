#!/usr/bin/env python3
"""Produce format-aware semantic diffs for extracted application bundles.

The detector is content-first: a file's extension is only a hint. This matters for
Sonos desktop releases where Resources-osx.bundle/.../libutils.so is not a shared
library at all, but an AES-encrypted SQLite resource database.

The output is intentionally JSON-only so the static archive viewer can render the
same analysis without executing binaries or parsing opaque formats in-browser.
"""

from __future__ import annotations

import argparse
import collections
import difflib
import hashlib
import json
import os
import plistlib
import re
import sqlite3
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

SCLIB_KEY = bytes.fromhex("0fee0c5fe8a7c905b727dd383d20e61d")
SCLIB_PAGE_SIZE = 4096
SQLITE_MAGIC = b"SQLite format 3\x00"
MAX_FIELD_DIFFS = 80
MAX_COMPILED_UI_FIELD_DIFFS = 3
MAX_TEXT_DIFF_LINES = 160
MAX_RESOURCE_CHANGES = 30
MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_text(args: list[str]) -> str:
    try:
        proc = subprocess.run(args, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() or proc.stderr.strip()


def json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"type": "bytes", "bytes": len(value), "sha256": sha256_bytes(value)}
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def role_for_path(path: str) -> str:
    lower = path.lower()
    name = Path(path).name.lower()
    if ".nib/" in lower or name.endswith(".nib") or name.endswith(".storyboardc"):
        return "compiled-ui"
    if ".lproj/" in lower:
        return "localization"
    if "/frameworks/" in lower or ".framework/" in lower:
        return "framework"
    if "/macos/" in lower:
        return "native-code"
    if name == "info.plist" or name.endswith(".entitlements"):
        return "bundle-metadata"
    if name.endswith((".png", ".jpg", ".jpeg", ".gif", ".pdf", ".icns", ".svg")):
        return "visual-resource"
    if "/resources/" in lower:
        return "resource"
    return "other"


def decode_text_bytes(data: bytes) -> str | None:
    if not data:
        return ""
    encodings = []
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings.append("utf-16")
    if data.startswith(b"\xef\xbb\xbf"):
        encodings.append("utf-8-sig")
    encodings.append("utf-8")
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def read_text_file(path: Path) -> str:
    data = path.read_bytes()
    decoded = decode_text_bytes(data)
    if decoded is None:
        return data.decode("utf-8", errors="replace")
    return decoded


def is_probably_text(data: bytes) -> bool:
    return decode_text_bytes(data) is not None


_APPLE_STRINGS_RE = re.compile(
    r'"((?:\\.|[^"\\])*)"\s*=\s*"((?:\\.|[^"\\])*)"\s*;',
    re.S,
)


def _unescape_apple_string(value: str) -> str:
    replacements = {
        r"\n": "\n",
        r"\r": "\r",
        r"\t": "\t",
        r'\"': '"',
        r"\\": "\\",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    return value

def parse_apple_strings_text(text: str) -> dict[str, str]:
    return {
        _unescape_apple_string(key): _unescape_apple_string(value)
        for key, value in _APPLE_STRINGS_RE.findall(text)
    }


def _decrypt_sclib_page(ciphertext: bytes, iv: bytes) -> bytes:
    dec = Cipher(algorithms.AES(SCLIB_KEY), modes.CBC(iv)).decryptor()
    return dec.update(ciphertext) + dec.finalize()


def is_sonos_resource_db(path: Path) -> bool:
    """Content probe for the Sonos SQLite-SEE resource format.

    The filename is irrelevant.  Decode only page 1 and validate the SQLite
    header fields that immediately follow the reconstructed 16-byte magic.
    """
    try:
        size = path.stat().st_size
        if size < SCLIB_PAGE_SIZE or size % SCLIB_PAGE_SIZE:
            return False
        with path.open("rb") as handle:
            first = handle.read(SCLIB_PAGE_SIZE)
        if len(first) != SCLIB_PAGE_SIZE:
            return False
        decoded_tail = _decrypt_sclib_page(first[16:], first[:16])
        # SQLite bytes 16..20: 4096-byte page size, write/read version 1,
        # zero reserved bytes.  Random ciphertext will not satisfy this.
        return decoded_tail[:5] == b"\x10\x00\x01\x01\x00"
    except (OSError, ValueError):
        return False


def decrypt_sclib_to_file(source: Path, destination: Path) -> bool:
    """Stream-decrypt the Sonos resource DB without holding the 40+ MiB DB in RAM."""
    try:
        size = source.stat().st_size
        if size < SCLIB_PAGE_SIZE or size % SCLIB_PAGE_SIZE:
            return False
        with source.open("rb") as src, destination.open("wb") as dst:
            first = src.read(SCLIB_PAGE_SIZE)
            if len(first) != SCLIB_PAGE_SIZE:
                return False
            iv = first[:16]
            dst.write(SQLITE_MAGIC)
            dst.write(_decrypt_sclib_page(first[16:], iv))
            while True:
                page = src.read(SCLIB_PAGE_SIZE)
                if not page:
                    break
                if len(page) != SCLIB_PAGE_SIZE:
                    return False
                dst.write(_decrypt_sclib_page(page, iv))
        with destination.open("rb") as handle:
            return handle.read(16) == SQLITE_MAGIC
    except (OSError, ValueError):
        return False


def detect_type(path: Path) -> tuple[str, dict[str, Any]]:
    with path.open("rb") as handle:
        head = handle.read(8192)
    ext = path.suffix.lower()
    if head.startswith(SQLITE_MAGIC):
        return "sqlite", {"description": "SQLite database"}
    if is_sonos_resource_db(path):
        return "sonos-resource-db", {
                "description": "Sonos SCLib encrypted SQLite resource database",
                "encrypted": True,
                "content": ["translations", "images", "json-resources"],
            }
    if head.startswith(b"NIBArchive"):
        return "nibarchive", {"description": "Apple NIBArchive compiled UI"}
    if head[:4] in MACHO_MAGICS:
        return "mach-o", {"description": "Mach-O executable/library"}
    if head.startswith(b"bplist00") or ext in {".plist", ".nib"}:
        try:
            plistlib.loads(path.read_bytes())
            return "plist", {"description": "Apple property list / keyed archive"}
        except Exception:
            pass
    if head.startswith(b"PK\x03\x04"):
        return "zip", {"description": "ZIP archive"}
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", {"description": "PNG image"}
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg", {"description": "JPEG image"}
    if ext == ".json" or head.lstrip().startswith((b"{", b"[")):
        try:
            json.loads(read_text_file(path))
            return "json", {"description": "JSON document"}
        except Exception:
            pass
    if ext == ".strings":
        decoded = read_text_file(path)
        parsed = parse_apple_strings_text(decoded)
        if parsed:
            return "strings-table", {
                "description": "Apple localized strings table",
                "entries": len(parsed),
            }
    if is_probably_text(head):
        description = "UTF-16 text" if head.startswith((b"\xff\xfe", b"\xfe\xff")) else "UTF-8 text"
        return "text", {"description": description}
    file_desc = run_text(["file", "-b", str(path)]).splitlines()[0] if path.exists() else ""
    return "binary", {"description": file_desc or "opaque binary"}


def inventory(root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            rel = path.relative_to(root).as_posix()
            result[rel] = {
                "path": rel, "kind": "symlink", "role": role_for_path(rel),
                "target": os.readlink(path),
            }
            continue
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        kind, type_meta = detect_type(path)
        role = "packed-resources" if kind == "sonos-resource-db" else role_for_path(rel)
        result[rel] = {
            "path": rel,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
            "kind": kind,
            "role": role,
            "type": type_meta,
        }
    return result


def flatten(value: Any, prefix: str = "", out: dict[str, Any] | None = None) -> dict[str, Any]:
    if out is None:
        out = {}
    if isinstance(value, dict):
        if not value:
            out[prefix or "(root)"] = {}
        for key, item in value.items():
            flatten(item, f"{prefix}.{key}" if prefix else str(key), out)
    elif isinstance(value, list):
        if not value:
            out[prefix or "(root)"] = []
        for idx, item in enumerate(value):
            flatten(item, f"{prefix}[{idx}]", out)
    else:
        out[prefix or "(value)"] = json_safe(value)
    return out


def structured_diff(old: Any, new: Any, limit: int = MAX_FIELD_DIFFS) -> dict[str, Any]:
    a, b = flatten(json_safe(old)), flatten(json_safe(new))
    rows = []
    counts = collections.Counter()
    for key in sorted(set(a) | set(b)):
        if key not in a:
            status = "added"
        elif key not in b:
            status = "removed"
        elif a[key] != b[key]:
            status = "changed"
        else:
            status = "same"
        counts[status] += 1
        if status != "same" and len(rows) < limit:
            rows.append({"path": key, "old": a.get(key), "new": b.get(key), "status": status})
    return {"counts": dict(counts), "changes": rows, "truncated": counts["added"] + counts["removed"] + counts["changed"] > len(rows)}


def plist_value(path: Path) -> Any:
    return plistlib.loads(path.read_bytes())


def json_value(path: Path) -> Any:
    return json.loads(read_text_file(path))


def strings_table_diff(old: Path, new: Path) -> dict[str, Any]:
    return {
        "type": "strings-table",
        **structured_diff(
            parse_apple_strings_text(read_text_file(old)),
            parse_apple_strings_text(read_text_file(new)),
        ),
    }


def text_diff(old: Path, new: Path) -> dict[str, Any]:
    a = read_text_file(old).splitlines()
    b = read_text_file(new).splitlines()
    lines = list(difflib.unified_diff(a, b, fromfile=old.name, tofile=new.name, lineterm=""))
    return {
        "old_lines": len(a), "new_lines": len(b),
        "diff": lines[:MAX_TEXT_DIFF_LINES],
        "truncated": len(lines) > MAX_TEXT_DIFF_LINES,
    }


def nibarchive_tokens(path: Path) -> dict[str, list[str]]:
    """Extract stable human-readable identifiers from Apple's compiled NIBArchive.

    This is intentionally not a byte-level parser. NIBArchive is a compiled object
    graph; the useful stable surface for release comparison is the set of embedded
    class names, selectors/keys, and string values rather than offsets or record IDs.
    """
    data = path.read_bytes()
    ascii_tokens = {
        match.decode("utf-8", errors="ignore").strip()
        for match in re.findall(rb"[\x20-\x7e]{4,}", data)
    }
    utf16_tokens = set()
    for match in re.findall(rb"(?:[\x20-\x7e]\x00){4,}", data):
        try:
            utf16_tokens.add(match.decode("utf-16le").strip())
        except UnicodeDecodeError:
            pass
    tokens = {
        token for token in ascii_tokens | utf16_tokens
        if token and len(token) <= 512
    }
    classes = {
        token for token in tokens
        if re.fullmatch(r"(?:NS|SM|SC|Sonos|SCLib)[A-Za-z0-9_$]+", token)
    }
    return {
        "tokens": sorted(tokens),
        "classes": sorted(classes),
    }


def nibarchive_diff(old: Path, new: Path) -> dict[str, Any]:
    left = nibarchive_tokens(old)
    right = nibarchive_tokens(new)
    token_changes = _mapping_changes(
        {token: True for token in left["tokens"]},
        {token: True for token in right["tokens"]},
        render_key=lambda key: key,
        limit=1000,
    )
    class_changes = _mapping_changes(
        {token: True for token in left["classes"]},
        {token: True for token in right["classes"]},
        render_key=lambda key: key,
        limit=500,
    )
    return {
        "type": "nibarchive",
        "description": "Apple compiled Interface Builder object archive",
        "old_tokens": len(left["tokens"]),
        "new_tokens": len(right["tokens"]),
        "tokens": token_changes,
        "classes": class_changes,
    }


def macho_metadata(path: Path) -> dict[str, Any]:
    archs = run_text(["lipo", "-archs", str(path)]).split()
    deps_raw = run_text(["otool", "-L", str(path)]).splitlines()
    deps = sorted({
        line.strip().split(" (compatibility version ", 1)[0]
        for line in deps_raw
        if " (compatibility version " in line
    })
    load = run_text(["otool", "-l", str(path)])
    min_versions = sorted(set(re.findall(r"\b(?:minos|version)\s+([0-9.]+)", load)))
    rpaths = []
    lines = load.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "cmd LC_RPATH" and i + 2 < len(lines):
            match = re.search(r"path (.+?) \(offset", lines[i + 2].strip())
            if match:
                rpaths.append(match.group(1))
    codesign = run_text(["codesign", "-d", "--verbose=4", str(path)])
    signing = {}
    for key in ("Identifier", "TeamIdentifier", "Authority", "Runtime Version"):
        match = re.search(rf"^{re.escape(key)}=(.+)$", codesign, re.M)
        if match:
            signing[key] = match.group(1)
    exported_symbols = sorted(set(
        line.strip()
        for line in run_text(["nm", "-gjU", str(path)]).splitlines()
        if line.strip()
    ))
    return {
        "architectures": archs,
        "dependencies": deps,
        "rpaths": sorted(set(rpaths)),
        "minimum_versions": min_versions,
        "signing": signing,
        "exported_symbols": exported_symbols,
    }


def macho_diff(old: Path, new: Path) -> dict[str, Any]:
    a, b = macho_metadata(old), macho_metadata(new)
    # Keep only compact semantic state plus the actual symbol deltas. Storing both
    # complete symbol inventories for every release pair duplicates megabytes of
    # unchanged metadata and makes historical backfill needlessly expensive.
    old_symbols, new_symbols = set(a["exported_symbols"]), set(b["exported_symbols"])
    old_compact = {key: value for key, value in a.items() if key != "exported_symbols"}
    new_compact = {key: value for key, value in b.items() if key != "exported_symbols"}
    return {
        "old": old_compact,
        "new": new_compact,
        "architectures_added": sorted(set(b["architectures"]) - set(a["architectures"])),
        "architectures_removed": sorted(set(a["architectures"]) - set(b["architectures"])),
        "dependencies_added": sorted(set(b["dependencies"]) - set(a["dependencies"])),
        "dependencies_removed": sorted(set(a["dependencies"]) - set(b["dependencies"])),
        "rpaths_added": sorted(set(b["rpaths"]) - set(a["rpaths"])),
        "rpaths_removed": sorted(set(a["rpaths"]) - set(b["rpaths"])),
        "symbols_added": sorted(new_symbols - old_symbols),
        "symbols_removed": sorted(old_symbols - new_symbols),
        "old_exported_symbol_count": len(old_symbols),
        "new_exported_symbol_count": len(new_symbols),
    }


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def sqlite_summary(path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [row[0] for row in conn.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name"
        )]
        result = {}
        for table in tables:
            schema = conn.execute(
                "select sql from sqlite_master where type='table' and name=?", (table,)
            ).fetchone()
            info = conn.execute(f"pragma table_info({_quote_identifier(table)})").fetchall()
            try:
                count = conn.execute(f"select count(*) from {_quote_identifier(table)}").fetchone()[0]
            except sqlite3.DatabaseError:
                count = None
            result[table] = {
                "rows": count,
                "schema": schema[0] if schema else None,
                "columns": [
                    {
                        "name": row[1],
                        "type": row[2],
                        "notnull": bool(row[3]),
                        "default": row[4],
                        "pk": row[5],
                    }
                    for row in info
                ],
            }
        return {"tables": result}
    finally:
        conn.close()


def sqlite_diff(old: Path, new: Path) -> dict[str, Any]:
    """Diff SQLite schemas and keyed rows when stable primary keys exist."""
    old_conn = sqlite3.connect(f"file:{old}?mode=ro", uri=True)
    new_conn = sqlite3.connect(f"file:{new}?mode=ro", uri=True)
    try:
        old_summary = sqlite_summary(old)["tables"]
        new_summary = sqlite_summary(new)["tables"]
        table_rows = []
        total = collections.Counter()

        for table in sorted(set(old_summary) | set(new_summary)):
            if table not in old_summary:
                total["added"] += 1
                table_rows.append({
                    "table": table, "status": "added",
                    "old_rows": 0, "new_rows": new_summary[table]["rows"],
                })
                continue
            if table not in new_summary:
                total["removed"] += 1
                table_rows.append({
                    "table": table, "status": "removed",
                    "old_rows": old_summary[table]["rows"], "new_rows": 0,
                })
                continue

            old_meta, new_meta = old_summary[table], new_summary[table]
            schema_changed = old_meta["schema"] != new_meta["schema"]
            old_columns = old_meta["columns"]
            new_columns = new_meta["columns"]
            old_pk = [
                row["name"] for row in sorted(old_columns, key=lambda r: r["pk"] or 9999)
                if row["pk"]
            ]
            new_pk = [
                row["name"] for row in sorted(new_columns, key=lambda r: r["pk"] or 9999)
                if row["pk"]
            ]
            row_diff = None

            # Stable primary keys let us stream a meaningful row-level diff without
            # loading an entire application database into memory.
            if old_pk and old_pk == new_pk and [r["name"] for r in old_columns] == [r["name"] for r in new_columns]:
                all_names = [row["name"] for row in old_columns]
                value_names = [name for name in all_names if name not in old_pk]
                select_names = old_pk + value_names
                select_sql = ", ".join(_quote_identifier(name) for name in select_names)
                order_sql = ", ".join(_quote_identifier(name) for name in old_pk)
                table_sql = _quote_identifier(table)
                query = f"select {select_sql} from {table_sql} order by {order_sql}"
                row_diff = _merge_rows(
                    _ordered_rows(old_conn, query),
                    _ordered_rows(new_conn, query),
                    key_len=len(old_pk),
                    render_key=lambda key, names=old_pk: {
                        name: key[index] for index, name in enumerate(names)
                    },
                    render_value=lambda values, names=value_names: {
                        name: values[index] for index, name in enumerate(names)
                    },
                    limit=MAX_RESOURCE_CHANGES,
                )

            row_changed = row_diff and (
                row_diff["counts"].get("added", 0)
                + row_diff["counts"].get("removed", 0)
                + row_diff["counts"].get("changed", 0)
            )
            status = "changed" if schema_changed or row_changed else "same"
            total[status] += 1
            table_rows.append({
                "table": table,
                "status": status,
                "schema_changed": schema_changed,
                "old_rows": old_meta["rows"],
                "new_rows": new_meta["rows"],
                "primary_key": old_pk if old_pk == new_pk else [],
                "rows": row_diff,
            })

        return {
            "counts": dict(total),
            "tables": table_rows,
            "semantic_changed": bool(
                total.get("added") or total.get("removed") or total.get("changed")
            ),
        }
    finally:
        old_conn.close()
        new_conn.close()


def _ordered_rows(conn: sqlite3.Connection, query: str):
    cursor = conn.execute(query)
    for row in cursor:
        yield row


def _merge_rows(old_rows, new_rows, *, key_len: int, render_key,
                render_value=lambda value: value,
                language_index: int | None = None,
                limit: int = MAX_RESOURCE_CHANGES) -> dict[str, Any]:
    """Merge two SQL ORDER BY streams using O(1) row memory."""
    old_iter, new_iter = iter(old_rows), iter(new_rows)
    old_row = next(old_iter, None)
    new_row = next(new_iter, None)
    counts = collections.Counter()
    changes = []
    by_language: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)

    def note(key, status, old_value=None, new_value=None):
        counts[status] += 1
        if language_index is not None and len(key) > language_index:
            lang = key[language_index]
            if isinstance(lang, str):
                by_language[lang][status] += 1
        if status != "same" and len(changes) < limit:
            changes.append({
                "key": render_key(key),
                "status": status,
                "old": json_safe(render_value(old_value)) if old_value is not None else None,
                "new": json_safe(render_value(new_value)) if new_value is not None else None,
            })

    while old_row is not None or new_row is not None:
        if old_row is None:
            key = tuple(new_row[:key_len]); note(key, "added", new_value=new_row[key_len:])
            new_row = next(new_iter, None); continue
        if new_row is None:
            key = tuple(old_row[:key_len]); note(key, "removed", old_value=old_row[key_len:])
            old_row = next(old_iter, None); continue
        old_key, new_key = tuple(old_row[:key_len]), tuple(new_row[:key_len])
        # SQLite's ORDER BY and Python tuple ordering agree for these schemas:
        # integer index first for strings/images; text first for jsons.
        if old_key < new_key:
            note(old_key, "removed", old_value=old_row[key_len:])
            old_row = next(old_iter, None)
        elif new_key < old_key:
            note(new_key, "added", new_value=new_row[key_len:])
            new_row = next(new_iter, None)
        else:
            old_value, new_value = old_row[key_len:], new_row[key_len:]
            note(old_key, "same" if old_value == new_value else "changed",
                 old_value=old_value, new_value=new_value)
            old_row = next(old_iter, None)
            new_row = next(new_iter, None)

    changed_total = counts["added"] + counts["removed"] + counts["changed"]
    return {
        "counts": dict(counts),
        "changes": changes,
        "truncated": changed_total > len(changes),
        "by_language": {lang: dict(c) for lang, c in sorted(by_language.items())},
    }


def sonos_resource_db_diff(old: Path, new: Path) -> dict[str, Any]:
    old_tmp = Path(tempfile.mkstemp(prefix="sonos-resource-old-", suffix=".sqlite")[1])
    new_tmp = Path(tempfile.mkstemp(prefix="sonos-resource-new-", suffix=".sqlite")[1])
    try:
        if not decrypt_sclib_to_file(old, old_tmp) or not decrypt_sclib_to_file(new, new_tmp):
            return {"type": "sonos-sclib-resource-db", "error": "resource database decryption failed"}
        old_conn = sqlite3.connect(f"file:{old_tmp}?mode=ro", uri=True)
        new_conn = sqlite3.connect(f"file:{new_tmp}?mode=ro", uri=True)
        try:
            required_columns = {
                "strings": {
                    "string_index", "string_name", "string_language",
                    "string_gender", "string_plurality", "string_value",
                },
                "images": {
                    "image_index", "image_scale_factor", "image_name",
                    "image_type", "image_blob",
                },
                "jsons": {"json_name", "json_language", "json_content"},
            }

            def table_columns(conn: sqlite3.Connection) -> dict[str, set[str]]:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "select name from sqlite_master where type='table'"
                    )
                }
                return {
                    table: {
                        row[1]
                        for row in conn.execute(
                            f"pragma table_info({_quote_identifier(table)})"
                        )
                    }
                    for table in tables
                }

            old_columns = table_columns(old_conn)
            new_columns = table_columns(new_conn)
            modern_schema = all(
                required_columns[table].issubset(old_columns.get(table, set()))
                and required_columns[table].issubset(new_columns.get(table, set()))
                for table in required_columns
            )
            if not modern_schema:
                generic = sqlite_diff(old_tmp, new_tmp)
                return {
                    "type": "sqlite",
                    "wrapper": "sonos-sclib-resource-db",
                    "description": (
                        "Decrypted Sonos packed resource database with a "
                        "legacy/nonstandard SQLite schema"
                    ),
                    "encrypted_wrapper": True,
                    "schema": {
                        "old_tables": sorted(old_columns),
                        "new_tables": sorted(new_columns),
                    },
                    **generic,
                }

            string_query = """select string_index, coalesce(string_name,''), coalesce(string_language,''),
                                     coalesce(string_gender,''), coalesce(string_plurality,''),
                                     coalesce(string_value,'')
                              from strings
                              order by string_index, string_name, string_language, string_gender, string_plurality"""
            image_query = """select image_index, coalesce(image_scale_factor,''), coalesce(image_name,''),
                                    coalesce(image_type,''), image_blob
                             from images
                             order by image_index, image_scale_factor, image_name, image_type"""
            json_query = """select coalesce(json_name,''), coalesce(json_language,''),
                                   coalesce(json_content,'')
                            from jsons order by json_name, json_language"""

            strings = _merge_rows(
                _ordered_rows(old_conn, string_query), _ordered_rows(new_conn, string_query),
                key_len=5,
                render_key=lambda k: {"index": k[0], "name": k[1], "language": k[2],
                                      "gender": k[3], "plurality": k[4]},
                render_value=lambda v: v[0] if v else "",
                language_index=2,
            )
            images = _merge_rows(
                _ordered_rows(old_conn, image_query), _ordered_rows(new_conn, image_query),
                key_len=4,
                render_key=lambda k: {"index": k[0], "scale": k[1], "name": k[2], "type": k[3]},
                render_value=lambda v: {
                    "bytes": len(v[0] or b""),
                    "sha256": sha256_bytes(v[0] or b""),
                } if v else None,
            )
            jsons = _merge_rows(
                _ordered_rows(old_conn, json_query), _ordered_rows(new_conn, json_query),
                key_len=2,
                render_key=lambda k: {"name": k[0], "language": k[1]},
                render_value=lambda v: v[0] if v else "",
                language_index=1,
            )

            for row in jsons["changes"]:
                if row["status"] != "changed":
                    continue
                try:
                    old_obj, new_obj = json.loads(row["old"]), json.loads(row["new"])
                except (TypeError, json.JSONDecodeError):
                    continue
                row["structured"] = structured_diff(old_obj, new_obj, limit=300)

            return {
                "type": "sonos-sclib-resource-db",
                "description": "Decrypted Sonos packed resource database",
                "schema": {
                    "tables": ["strings", "images", "jsons"],
                    "old_bytes": old_tmp.stat().st_size,
                    "new_bytes": new_tmp.stat().st_size,
                },
                "strings": strings,
                "images": images,
                "jsons": jsons,
            }
        finally:
            old_conn.close()
            new_conn.close()
    finally:
        old_tmp.unlink(missing_ok=True)
        new_tmp.unlink(missing_ok=True)


def _mapping_changes(old: dict[Any, Any], new: dict[Any, Any], *,
                     render_key=lambda key: key,
                     limit: int = MAX_RESOURCE_CHANGES) -> dict[str, Any]:
    counts = collections.Counter()
    changes = []
    for key in sorted(set(old) | set(new), key=lambda value: str(value)):
        if key not in old:
            status = "added"
        elif key not in new:
            status = "removed"
        elif old[key] == new[key]:
            status = "same"
        else:
            status = "changed"
        counts[status] += 1
        if status != "same" and len(changes) < limit:
            changes.append({
                "key": render_key(key),
                "status": status,
                "old": json_safe(old.get(key)) if key in old else None,
                "new": json_safe(new.get(key)) if key in new else None,
            })
    changed_total = counts["added"] + counts["removed"] + counts["changed"]
    return {
        "counts": dict(counts),
        "changes": changes,
        "truncated": changed_total > len(changes),
    }


def zip_diff(old: Path, new: Path) -> dict[str, Any]:
    def members(path: Path) -> dict[str, tuple[int, int]]:
        with zipfile.ZipFile(path) as archive:
            return {info.filename: (info.file_size, info.CRC) for info in archive.infolist()}
    return _mapping_changes(members(old), members(new), render_key=lambda k: k, limit=1000)


def semantic_diff(old: Path, new: Path, old_meta: dict[str, Any], new_meta: dict[str, Any]) -> dict[str, Any] | None:
    kind = old_meta["kind"] if old_meta["kind"] == new_meta["kind"] else None
    try:
        if kind == "sonos-resource-db":
            return sonos_resource_db_diff(old, new)
        if kind == "plist":
            limit = (
                MAX_COMPILED_UI_FIELD_DIFFS
                if old_meta.get("role") in {"compiled-ui", "localization"}
                or new_meta.get("role") in {"compiled-ui", "localization"}
                else MAX_FIELD_DIFFS
            )
            return {
                "type": "plist",
                **structured_diff(plist_value(old), plist_value(new), limit=limit),
            }
        if kind == "json":
            return {
                "type": "json",
                **structured_diff(json_value(old), json_value(new), limit=MAX_FIELD_DIFFS),
            }
        if kind == "strings-table":
            return strings_table_diff(old, new)
        if kind == "text":
            return {"type": "text", **text_diff(old, new)}
        if kind == "nibarchive":
            return nibarchive_diff(old, new)
        if kind == "mach-o":
            return {"type": "mach-o", **macho_diff(old, new)}
        if kind == "sqlite":
            return {"type": "sqlite", **sqlite_diff(old, new)}
        if kind == "zip":
            return {"type": "zip", **zip_diff(old, new)}
    except Exception as exc:
        return {"type": kind or "unknown", "error": f"{type(exc).__name__}: {exc}"}
    return None


def compact_inventory_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """Keep blob identity and useful type info without duplicating row context."""
    out = {
        key: meta[key]
        for key in ("bytes", "sha256", "kind", "target")
        if key in meta and meta[key] is not None
    }
    type_meta = meta.get("type")
    if isinstance(type_meta, dict):
        description = type_meta.get("description")
        if description:
            out["type"] = {"description": str(description)[:320]}
        for key in ("format", "width", "height"):
            if key in type_meta:
                out.setdefault("type", {})[key] = type_meta[key]
    return out


def summarize_roles(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = collections.Counter(row.get("role") or "other" for row in rows)
    return dict(sorted(counts.items()))


def build_diff(old_root: Path, new_root: Path, *, old_version: str, new_version: str,
               platform: str, family: str) -> dict[str, Any]:
    old_inv, new_inv = inventory(old_root), inventory(new_root)
    added_paths = sorted(set(new_inv) - set(old_inv))
    removed_paths = sorted(set(old_inv) - set(new_inv))
    changed_paths = sorted(
        path for path in set(old_inv) & set(new_inv)
        if old_inv[path].get("sha256") != new_inv[path].get("sha256")
        or old_inv[path].get("target") != new_inv[path].get("target")
    )
    unchanged = len(set(old_inv) & set(new_inv)) - len(changed_paths)

    added = [new_inv[path] for path in added_paths]
    removed = [old_inv[path] for path in removed_paths]
    changed = []
    semantic_count = 0
    highlights = {
        "translations": None,
        "images": None,
        "json_resources": None,
        "mach_o": {
            "files": 0,
            "dependencies_added": 0,
            "dependencies_removed": 0,
            "symbols_added": 0,
            "symbols_removed": 0,
        },
        "plist": {"files": 0, "fields_changed": 0},
        "localized_strings": {"files": 0, "keys_changed": 0},
        "text": {"files": 0},
        "compiled_ui": {"files": 0, "tokens_changed": 0, "classes_changed": 0},
    }

    for path in changed_paths:
        old_meta, new_meta = old_inv[path], new_inv[path]
        semantic = semantic_diff(old_root / path, new_root / path, old_meta, new_meta)
        row = {
            "path": path,
            "role": new_meta.get("role") or old_meta.get("role") or "other",
            "old": compact_inventory_meta(old_meta),
            "new": compact_inventory_meta(new_meta),
        }
        if semantic:
            semantic_count += 1
            row["semantic"] = semantic
            stype = semantic.get("type")
            if stype == "sonos-sclib-resource-db":
                highlights["translations"] = semantic["strings"]["counts"]
                highlights["images"] = semantic["images"]["counts"]
                highlights["json_resources"] = semantic["jsons"]["counts"]
            elif stype == "mach-o":
                highlights["mach_o"]["files"] += 1
                highlights["mach_o"]["dependencies_added"] += len(semantic.get("dependencies_added", []))
                highlights["mach_o"]["dependencies_removed"] += len(semantic.get("dependencies_removed", []))
                highlights["mach_o"]["symbols_added"] += len(semantic.get("symbols_added", []))
                highlights["mach_o"]["symbols_removed"] += len(semantic.get("symbols_removed", []))
            elif stype == "plist":
                highlights["plist"]["files"] += 1
                counts = semantic.get("counts", {})
                highlights["plist"]["fields_changed"] += (
                    counts.get("added", 0) + counts.get("removed", 0) + counts.get("changed", 0)
                )
            elif stype == "strings-table":
                highlights["localized_strings"]["files"] += 1
                counts = semantic.get("counts", {})
                highlights["localized_strings"]["keys_changed"] += (
                    counts.get("added", 0) + counts.get("removed", 0) + counts.get("changed", 0)
                )
            elif stype == "text":
                highlights["text"]["files"] += 1
            elif stype == "nibarchive":
                highlights["compiled_ui"]["files"] += 1
                for bucket, field in (("tokens", "tokens_changed"), ("classes", "classes_changed")):
                    counts = semantic.get(bucket, {}).get("counts", {})
                    highlights["compiled_ui"][field] += (
                        counts.get("added", 0) + counts.get("removed", 0) + counts.get("changed", 0)
                    )
        changed.append(row)

    return {
        "schema_version": 1,
        "kind": "smart-app-diff",
        "platform": platform,
        "family": family,
        "old_version": old_version,
        "new_version": new_version,
        "summary": {
            "old_files": len(old_inv),
            "new_files": len(new_inv),
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
            "unchanged": unchanged,
            "semantically_analyzed_changed_files": semantic_count,
            "added_roles": summarize_roles(added),
            "removed_roles": summarize_roles(removed),
            "changed_roles": summarize_roles(changed),
        },
        "highlights": highlights,
        "files": {"added": added, "removed": removed, "changed": changed},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-root", type=Path, required=True)
    parser.add_argument("--new-root", type=Path, required=True)
    parser.add_argument("--old-version", required=True)
    parser.add_argument("--new-version", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--family", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    payload = build_diff(
        args.old_root.resolve(), args.new_root.resolve(),
        old_version=args.old_version, new_version=args.new_version,
        platform=args.platform, family=args.family,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"smart diff {args.old_version} -> {args.new_version}: "
        f"+{payload['summary']['added']} -{payload['summary']['removed']} "
        f"~{payload['summary']['changed']} "
        f"semantic={payload['summary']['semantically_analyzed_changed_files']}"
    )


if __name__ == "__main__":
    main()
