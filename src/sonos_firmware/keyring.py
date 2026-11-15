"""Prepare recipient-verified keys without making optional recovery a prerequisite."""
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import UnsupportedAlgorithm

from .extract import key_recipient_id, load_private_key
from .mdp import write_private_key_exclusive


def recipient_models(ledger):
    """Keep every observed recipient, including missing groups and rotations."""
    result = {}
    groups = list(ledger.get('recovered_keys', {}).items()) + [
        (row['recipient_id'], row) for row in ledger.get('missing_keys', [])]
    for fingerprint, group in groups:
        models = set(group.get('package_models', []))
        if group.get('package_model') is not None:
            models.add(group['package_model'])
        models.update(row.get('package_model') for row in group.get('packages', group.get('coverage_packages', [])))
        for model in models:
            result.setdefault(model, set()).add(fingerprint)
    return result


def recipient_key_path(directory, model, recipient=None):
    if recipient and (len(recipient) != 40 or any(c not in '0123456789abcdef' for c in recipient)):
        raise ValueError('key recipient must be a SHA-1 fingerprint')
    canonical = directory / ('recipient-' + recipient + '.pem') if recipient else None
    return canonical if canonical and canonical.is_file() else directory / f'model{model}.pem'


def update_recovery_summary(ledger):
    origin = ledger.setdefault('key_origin', {})
    for field in list(origin):
        if field.startswith('local_status_'):
            del origin[field]
    recovered = ledger.get('recovered_keys', {})
    origin['summary'] = {'recovered_recipients': len(recovered),
                         'models': sorted(model for model in recipient_models({'recovered_keys': recovered}) if model is not None),
                         'missing_recipient_groups': len(ledger.get('missing_keys', []))}
    origin['recovery_methods'] = sorted({proof['method'] for group in recovered.values()
                                        for proof in group.get('recovery_provenance', [])})


def prepare_keys(directory, recipients, environment, recoverers):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    ready, unavailable = {}, []
    validated = {}
    targets = {model: {fingerprints} if isinstance(fingerprints, str) else set(fingerprints)
               for model, fingerprints in recipients.items()}
    try:
        bundle = json.loads(environment.get('SONOS_PRIVATE_KEYS') or '{}')
        if not isinstance(bundle, dict):
            raise ValueError('key bundle is not an object')
        bundle_error = False
    except (ValueError, TypeError):
        bundle, bundle_error = {}, True
    for model, expected in sorted(targets.items(), key=lambda item: (item[0] is None, item[0] or 0)):
        if not expected or any(len(fp) != 40 or any(c not in '0123456789abcdef' for c in fp) for fp in expected):
            raise ValueError('key recipients must be SHA-1 fingerprints')
        alias = directory / f'model{model}.pem'
        legacy_key = None
        if model is not None and (alias.exists() or alias.is_symlink()):
            try:
                if alias.is_symlink():
                    raise ValueError('key alias is a symlink')
                legacy_key = load_private_key(alias)
                if key_recipient_id(legacy_key) not in expected:
                    raise ValueError('unexpected key recipient')
            except (ValueError, TypeError, OSError, UnsupportedAlgorithm):
                alias.unlink(missing_ok=True)
                legacy_key = None
        if model is not None and len(expected) > 1:
            # A numeric alias cannot represent a rotated key set unambiguously.
            alias.unlink(missing_ok=True)
        for fingerprint in sorted(expected):
            destination = directory / ('recipient-' + fingerprint + '.pem')
            errors = ['configured key bundle could not be decoded'] if bundle_error else []
            key = None
            if destination.exists() or destination.is_symlink():
                try:
                    if destination.is_symlink():
                        raise ValueError('key path is a symlink')
                    key = load_private_key(destination)
                    if key_recipient_id(key) != fingerprint:
                        raise ValueError('unexpected key recipient')
                except (ValueError, TypeError, OSError, UnsupportedAlgorithm):
                    destination.unlink(missing_ok=True)
                    key = None
                    errors.append('prepared key could not be validated')
            if key is None and legacy_key and key_recipient_id(legacy_key) == fingerprint:
                key = legacy_key
            if key is None:
                for value in [bundle.get(fingerprint), environment.get(f'SONOS_MODEL_{model}_PRIVATE_KEY') if model is not None else None]:
                    if not value:
                        continue
                    try:
                        candidate = serialization.load_pem_private_key(value.encode(), password=None)
                        if key_recipient_id(candidate) != fingerprint:
                            raise ValueError('unexpected key recipient')
                        key = candidate
                        break
                    except (TypeError, ValueError, OSError, AttributeError, UnsupportedAlgorithm):
                        errors.append('configured key could not be validated')
            recover = recoverers.get((model, fingerprint)) or (recoverers.get(model) if len(expected) == 1 else None)
            if key is None and recover:
                try:
                    recovered = recover()
                    names = {destination.name, alias.name} if model is not None else {destination.name}
                    if recovered.resolve().parent != directory.resolve() or recovered.name not in names:
                        raise ValueError('recovery wrote to an unexpected key path')
                    key = load_private_key(recovered)
                    if key_recipient_id(key) != fingerprint:
                        key = None
                        raise ValueError('unexpected key recipient')
                    if model is not None and len(expected) > 1:
                        alias.unlink(missing_ok=True)
                except Exception as error:
                    key = None
                    errors.append(f'optional recovery failed ({type(error).__name__})')
                    destination.unlink(missing_ok=True)
                    if model is not None:
                        alias.unlink(missing_ok=True)
            if key is None:
                unavailable.append({'model': model, 'recipient_id': fingerprint,
                                    'reasons': errors or ['no configured or reproducible key']})
                continue
            if not destination.exists():
                write_private_key_exclusive(destination, key)
            destination.chmod(0o600)
            if model is not None and len(expected) == 1 and not alias.exists():
                write_private_key_exclusive(alias, key)
            if model is not None and alias.exists():
                alias.chmod(0o600)
            validated[fingerprint] = key
    # A later model can supply the same recipient key an earlier model lacked.
    # Finalize readiness across the full association set, including aliases.
    for model, expected in targets.items():
        prepared = sorted(expected & validated.keys())
        if not prepared:
            continue
        if model is not None:
            ready[str(model)] = prepared[0] if len(expected) == 1 else prepared
        alias = directory / f'model{model}.pem'
        if model is not None and len(expected) == 1 and not alias.exists():
            write_private_key_exclusive(alias, validated[prepared[0]])
    available = {fp: {'models': sorted(model for model, expected in targets.items() if model is not None and fp in expected)}
                 for fp in validated}
    unavailable = [row for row in unavailable if row['recipient_id'] not in validated]
    return {'schema_version': 1, 'ready': ready, 'ready_recipients': available, 'unavailable': unavailable}
