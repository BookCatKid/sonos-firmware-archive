from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .catalog import filesystem_manifest, validate_catalog
from .discovery import download_all, parse_manifest, probe_all, write_json
from .extract import extract_components
from .legacy import recover_legacy_updater_key, write_recovery_evidence_exclusive
from .metadata import snapshot_update_metadata
from .mdp import (
    inspect_mdp,
    key_recipient_id,
    recipient_id_for_key_file,
    recover_amlogic_model_key,
    write_private_key_exclusive,
)
from .upd import parse_file


def _inspect(path: Path) -> int:
    data = path.read_bytes()
    result = {
        "file": path.name,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "sections": [section.to_dict() for section in parse_file(path)],
    }
    print(json.dumps(result, indent=2))
    return 0


def _verify(root: Path) -> int:
    errors = validate_catalog(root)
    if errors:
        for error in errors:
            print(f"error: {error}")
        return 1
    print("catalog: ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="sonos-fw")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="inspect a Sonos UPD container")
    inspect.add_argument("file", type=Path)
    verify = sub.add_parser("verify", help="validate the archive catalog")
    verify.add_argument("--root", type=Path, default=Path.cwd())
    manifest = sub.add_parser("fs-manifest", help="inventory an extracted rootfs")
    manifest.add_argument("directory", type=Path)
    manifest.add_argument("--output", type=Path)
    discover = sub.add_parser("discover", help="probe every artifact named by a Sonos UPM")
    discover.add_argument("manifest", type=Path)
    discover.add_argument("--output", type=Path, required=True)
    discover.add_argument("--workers", type=int, default=6)
    fetch = sub.add_parser("fetch", help="download available artifacts from discovery JSON")
    fetch.add_argument("discovery", type=Path)
    fetch.add_argument("--directory", type=Path, required=True)
    fetch.add_argument("--receipt", type=Path, required=True)
    fetch.add_argument("--workers", type=int, default=3)
    metadata = sub.add_parser("metadata", help="snapshot synthetic Sonos UPS metadata")
    metadata.add_argument("--raw", type=Path, required=True)
    metadata.add_argument("--receipt", type=Path, required=True)
    extract = sub.add_parser("extract", help="extract raw firmware components from a UPD")
    extract.add_argument("file", type=Path)
    extract.add_argument("--directory", type=Path, required=True)
    extract.add_argument("--private-key", type=Path)
    extract.add_argument("--legacy-model8", action="store_true")
    extract.add_argument("--receipt", type=Path)
    mdp_inspect = sub.add_parser("mdp-inspect", help="inspect an MDP3 or complete manufacturing-page dump")
    mdp_inspect.add_argument("file", type=Path)
    recover = sub.add_parser(
        "recover-amlogic-mdp-key",
        help="recover an RSA model key from the known Amlogic MDP3/OTP layout",
    )
    recover.add_argument("mdp", type=Path)
    recover.add_argument("--otp", type=Path, required=True)
    recover.add_argument("--output", type=Path, required=True)
    recover.add_argument("--expect-model", type=int)
    recover.add_argument("--expect-recipient")
    key_id = sub.add_parser("key-id", help="print a model private key's UPD recipient fingerprint")
    key_id.add_argument("private_key", type=Path)
    legacy = sub.add_parser(
        "recover-legacy-updater-key",
        help="recover a legacy RSA model key embedded in a plaintext updater",
    )
    legacy.add_argument("updater", type=Path)
    legacy.add_argument("--model", type=int, required=True)
    legacy.add_argument("--system-word", type=lambda value: int(value, 0), default=0x1996)
    legacy.add_argument("--byte-order", choices=("big", "little"), default="big")
    legacy.add_argument("--wrapper-offset", type=lambda value: int(value, 0))
    legacy.add_argument("--output", type=Path, required=True)
    legacy.add_argument("--expect-recipient")
    legacy.add_argument(
        "--evidence-directory",
        type=Path,
        help="securely retain the seed, wrapper, and decryptor intermediates",
    )
    args = parser.parse_args()

    if args.command == "inspect":
        return _inspect(args.file)
    if args.command == "verify":
        return _verify(args.root)
    if args.command == "discover":
        metadata, candidates = parse_manifest(args.manifest)
        results = probe_all(candidates, args.workers)
        write_json(args.output, {**metadata, "candidates": results})
        available = sum(item["available"] for item in results)
        print(f"discovered {len(results)} candidates; {available} available")
        return 0
    if args.command == "fetch":
        discovery = json.loads(args.discovery.read_text(encoding="utf-8"))
        results = download_all(discovery["candidates"], args.directory, args.workers)
        write_json(args.receipt, {"source": args.discovery.name, "artifacts": results})
        downloaded = sum(item["downloaded"] for item in results)
        print(f"downloaded {downloaded}/{len(results)} available artifacts")
        return 0 if downloaded == len(results) else 1
    if args.command == "metadata":
        result = snapshot_update_metadata(args.raw, args.receipt)
        print(
            f"snapshotted {len(result['records'])} metadata records "
            f"to {args.raw.name}; receipt {args.receipt.name}"
        )
        return 0
    if args.command == "extract":
        records = extract_components(
            args.file,
            args.directory,
            args.private_key,
            legacy_model8=args.legacy_model8,
        )
        result = {"source": args.file.name, "components": records}
        rendered = json.dumps(result, indent=2) + "\n"
        if args.receipt:
            args.receipt.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        print(f"extracted {len(records)} components from {args.file.name}")
        return 0
    if args.command == "mdp-inspect":
        result = {"file": args.file.name, **inspect_mdp(args.file.read_bytes())}
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "recover-amlogic-mdp-key":
        key = recover_amlogic_model_key(
            args.mdp.read_bytes(),
            args.otp.read_bytes(),
            expected_model=args.expect_model,
        )
        recipient = key_recipient_id(key)
        if args.expect_recipient:
            expected = args.expect_recipient.lower()
            if len(expected) != 40 or any(character not in "0123456789abcdef" for character in expected):
                raise ValueError("expected recipient must be a 40-character hexadecimal SHA-1 value")
            if recipient != expected:
                raise ValueError(f"recovered key recipient mismatch: got {recipient}, expected {expected}")
        write_private_key_exclusive(args.output, key)
        print(json.dumps({"output": str(args.output), "recipient_id": recipient}, indent=2))
        return 0
    if args.command == "key-id":
        print(recipient_id_for_key_file(args.private_key))
        return 0
    if args.command == "recover-legacy-updater-key":
        recovered = recover_legacy_updater_key(
            args.updater.read_bytes(),
            args.model,
            system_word=args.system_word,
            wrapper_offset=args.wrapper_offset,
            byte_order=args.byte_order,
        )
        recipient = key_recipient_id(recovered.key)
        if args.expect_recipient and recipient != args.expect_recipient.lower():
            raise ValueError(
                f"recovered key recipient mismatch: got {recipient}, expected {args.expect_recipient.lower()}"
            )
        write_private_key_exclusive(args.output, recovered.key)
        if args.evidence_directory:
            write_recovery_evidence_exclusive(args.evidence_directory, recovered)
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "recipient_id": recipient,
                    "wrapper_offset": recovered.wrapper_offset,
                    "byte_order": recovered.byte_order,
                },
                indent=2,
            )
        )
        return 0
    result = filesystem_manifest(args.directory)
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
