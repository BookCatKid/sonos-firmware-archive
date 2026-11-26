"""Load target declarations without discovering or acquiring any artifacts."""
import importlib.metadata
import json
from pathlib import Path

from .model import Target


def targets(root: Path):
    path = root / 'config/targets.json'
    data = json.loads(path.read_text())
    if data.get('schema_version') != 1:
        raise ValueError('unsupported archive target configuration')
    result = {}
    for item in data['targets']:
        target = Target.from_dict(item)
        if target.name in result:
            raise ValueError(f'duplicate archive target: {target.name}')
        result[target.name] = target
    return result


def providers():
    from .providers import BUILTINS
    result = dict(BUILTINS)
    for entry in importlib.metadata.entry_points(group='sonos_archive.providers'):
        if entry.name in result:
            raise ValueError(f'duplicate archive provider: {entry.name}')
        result[entry.name] = entry.load()
    return result
