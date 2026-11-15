"""Bounded discovery queues with durable retries and periodic refreshes."""
from datetime import datetime, timedelta, timezone


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


def select(items, records, *, key, limit, force=False, now=None):
    """Keep source priority for new leads and reserve capacity for oldest retries."""
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
    reserved = min(len(retries), max(1, limit // 4))
    chosen = retries[:reserved] + fresh[:limit - reserved]
    # Fill spare capacity without starving either queue when the other is empty.
    chosen.extend(retries[reserved:reserved + limit - len(chosen)])
    return chosen
