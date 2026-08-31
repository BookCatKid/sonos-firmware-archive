#!/usr/bin/env python3
"""Build the file:// compatible catalog payload for the static browser."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
catalog = json.loads((ROOT / "data/catalog.json").read_text(encoding="utf-8"))
(ROOT / "site/catalog.js").write_text(
    "window.SONOS_FIRMWARE_CATALOG=" + json.dumps(catalog, separators=(",", ":")) + ";\n",
    encoding="utf-8",
)
