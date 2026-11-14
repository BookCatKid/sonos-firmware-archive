"""Atomic JSON checkpoints for discovery and publication metadata."""
import json
import os
import tempfile
from pathlib import Path


def atomic_json(path, value, *, sort_keys=False):
    path = Path(path)
    contents = json.dumps(value, indent=2, sort_keys=sort_keys) + '\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=path.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(contents)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
