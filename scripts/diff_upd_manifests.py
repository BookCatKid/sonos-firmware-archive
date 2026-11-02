#!/usr/bin/env python3
"""Compare every same-model UPD section manifest in two firmware versions.

Usage: python3 scripts/diff_upd_manifests.py data/upd OLD_VERSION NEW_VERSION
This is a ciphertext/metadata comparison, not a decrypted code diff.
"""

import argparse
import json
from pathlib import Path


def load(directory: Path, version: str) -> dict:
    result = {}
    for path in directory.glob(f"{version}-1-*.json"):
        model = int(path.stem.removeprefix(f"{version}-1-"))
        result[model] = json.loads(path.read_text())
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("old_version")
    parser.add_argument("new_version")
    args = parser.parse_args()
    old, new = load(args.directory, args.old_version), load(args.directory, args.new_version)
    comparison = {
        "old_version": args.old_version, "new_version": args.new_version,
        "old_models": sorted(old), "new_models": sorted(new),
        "old_only": sorted(old.keys() - new.keys()),
        "new_only": sorted(new.keys() - old.keys()),
        "models": [],
    }
    for model in sorted(old.keys() & new.keys()):
        before, after = old[model], new[model]
        a, b = before["sections"], after["sections"]
        if len(a) != len(b) or any(x["section_type"] != y["section_type"] for x, y in zip(a, b)):
            raise ValueError(f"model {model}: section layouts differ; manual alignment required")
        comparison["models"].append({
            "model": model,
            "old_package": before["package_id"], "new_package": after["package_id"],
            "old_bytes": before["bytes"], "new_bytes": after["bytes"],
            "bytes_delta": after["bytes"] - before["bytes"],
            "sections": [{
                "index": x["index"], "section_type": x["section_type"], "name": x["name"],
                "encrypted": x["encrypted"],
                "old_payload_length": x["payload_length"],
                "new_payload_length": y["payload_length"],
                "payload_length_delta": y["payload_length"] - x["payload_length"],
                "sha256_same": x["sha256"] == y["sha256"],
                "recipient_same": x["recipient_id"] == y["recipient_id"],
            } for x, y in zip(a, b)],
        })
    print(json.dumps(comparison, indent=2))


if __name__ == "__main__":
    main()
