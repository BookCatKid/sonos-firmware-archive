"""Bounded discovery queues with durable retries and periodic refreshes."""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import math


def _now(now=None):
    return now or datetime.now(timezone.utc)


def due(record, now=None):
    if not record or not record.get('next_attempt_at'):
        return True
    try:
        timestamp = datetime.fromisoformat(record['next_attempt_at'].replace('Z', '+00:00'))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, AttributeError):
        return True
    return timestamp <= _now(now)


def checkpoint(prior=None, *, success=False, negative=False, success_hours=24, now=None):
    """Timing fields to merge into an observation after an actual attempt."""
    now = _now(now)
    failures = 0 if success else int((prior or {}).get('consecutive_failures', 0)) + 1
    hours = success_hours if success else 168 if negative else min(24, 2 ** min(failures - 1, 5))
    return {'checked_at': now.isoformat(), 'consecutive_failures': failures,
            'next_attempt_at': (now + timedelta(hours=hours)).isoformat()}


def error_checkpoint(error, prior=None, *, now=None):
    """Use a server's Retry-After when supplied; otherwise retain backoff."""
    now = _now(now)
    result = checkpoint(prior, now=now)
    value = (getattr(error, 'headers', None) or {}).get('Retry-After')
    if value is None:
        return result
    try:
        seconds = float(value)
        if not math.isfinite(seconds) or seconds < 0:
            return result
        after = now + timedelta(seconds=seconds)
    except ValueError:
        try:
            after = parsedate_to_datetime(value)
            if after.tzinfo is None:
                after = after.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError, OverflowError):
            return result
    except (TypeError, OverflowError):
        return result
    result['next_attempt_at'] = max(now, after).isoformat()
    return result


def select(items, records, *, key, limit, force=False, now=None, state=None, queue='default'):
    """Reserve retry capacity; persist alternating turns when only one slot fits.

    Workers pass their saved state and a stable queue name. Larger batches keep
    their retry reservation and source priority without needing rotation.
    """
    if limit <= 0:
        return []
    fresh, retries = [], []
    for item in items:
        prior = records.get(key(item))
        if not prior or not (prior.get('checked_at') or prior.get('next_attempt_at')):
            fresh.append(item)
        elif force or due(prior, now):
            retries.append(item)
    retries.sort(key=lambda item: records[key(item)].get('checked_at', ''))
    if limit == 1 and state is not None:
        rotation = state.setdefault('retry_rotation', {}).setdefault(queue, {})
        lane = rotation.get('next_lane', 'retry')
        if lane not in {'retry', 'fresh'}:
            raise ValueError('retry queue lane must be retry or fresh')
        use_retry = bool(retries) and (not fresh or lane == 'retry')
        chosen = (retries if use_retry else fresh)[:1]
        if chosen:
            rotation['next_lane'] = 'fresh' if use_retry else 'retry'
        return chosen
    reserved = min(len(retries), max(1, limit // 4))
    chosen = retries[:reserved] + fresh[:limit - reserved]
    # Fill spare capacity without starving either queue when the other is empty.
    chosen.extend(retries[reserved:reserved + limit - len(chosen)])
    return chosen
