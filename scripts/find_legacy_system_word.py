#!/usr/bin/env python3
"""Recover an unknown 16-bit system word used by a legacy updater wrapper."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: E402

from sonos_firmware.legacy import (  # noqa: E402
    WRAPPER_BYTES,
    _legacy_random_material,
    recover_legacy_updater_key,
)
from sonos_firmware.mdp import key_recipient_id, write_private_key_exclusive  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("updater", type=Path)
    parser.add_argument("--model", type=int, required=True)
    parser.add_argument("--wrapper-offset", type=lambda value: int(value, 0), required=True)
    parser.add_argument("--expect-recipient")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    updater = args.updater.read_bytes()
    wrapper = updater[args.wrapper_offset : args.wrapper_offset + WRAPPER_BYTES]
    if len(wrapper) != WRAPPER_BYTES:
        raise ValueError("wrapper offset does not name a complete legacy wrapper")

    candidates: list[dict] = []
    candidate_keys = []
    for system_word in range(0x10000):
        aes_key, iv, _password = _legacy_random_material(args.model, system_word)
        decryptor = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).decryptor()
        first = decryptor.update(wrapper[:16])
        if not first.startswith(b"\x30\x82"):
            continue
        try:
            recovered = recover_legacy_updater_key(
                updater,
                args.model,
                system_word=system_word,
                wrapper_offset=args.wrapper_offset,
            )
        except (TypeError, ValueError):
            continue
        recipient = key_recipient_id(recovered.key)
        if args.expect_recipient and recipient != args.expect_recipient.lower():
            continue
        candidates.append(
            {
                "system_word": f"0x{system_word:04x}",
                "recipient_id": recipient,
                "wrapper_offset": args.wrapper_offset,
            }
        )
        candidate_keys.append(recovered.key)

    print(json.dumps({"candidates": candidates}, indent=2))
    if args.output:
        if len(candidates) > 1:
            raise ValueError("multiple candidates found; refusing ambiguous key output")
        if len(candidates) == 1:
            write_private_key_exclusive(args.output, candidate_keys[0])
    return 0 if len(candidates) == 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
