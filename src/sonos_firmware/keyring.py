"""Prepare available firmware keys without making optional recovery a prerequisite."""
from pathlib import Path
from typing import Callable, Mapping

from cryptography.hazmat.primitives import serialization

from .extract import key_recipient_id, load_private_key
from .mdp import write_private_key_exclusive


def prepare_keys(directory: Path, recipients: Mapping[int, str], environment: Mapping[str, str],
                 recoverers: Mapping[int, Callable[[], Path]]) -> dict:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    ready = {}
    unavailable = []
    for model, expected in sorted(recipients.items()):
        destination = directory / f'model{model}.pem'
        errors = []
        value = environment.get(f'SONOS_MODEL_{model}_PRIVATE_KEY')
        if not destination.exists() and value:
            try:
                key = serialization.load_pem_private_key(value.encode(), password=None)
                if key_recipient_id(key) != expected:
                    raise ValueError('unexpected key recipient')
                write_private_key_exclusive(destination, key)
            except (TypeError, ValueError, OSError):
                errors.append('configured key could not be validated')
        if not destination.exists() and model in recoverers:
            try:
                recovered = recoverers[model]()
                if recovered.resolve() != destination.resolve():
                    raise ValueError('recovery wrote to an unexpected key path')
            except Exception as error:
                errors.append(f'optional recovery failed ({type(error).__name__})')
        if destination.exists():
            try:
                if key_recipient_id(load_private_key(destination)) != expected:
                    raise ValueError('unexpected key recipient')
                destination.chmod(0o600)
                ready[str(model)] = expected
                continue
            except (ValueError, TypeError, OSError):
                errors.append('prepared key could not be validated')
                destination.unlink(missing_ok=True)
        unavailable.append({'model': model, 'reasons': errors or ['no configured or reproducible key']})
    return {'schema_version': 1, 'ready': ready, 'unavailable': unavailable}
