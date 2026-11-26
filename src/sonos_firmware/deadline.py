"""Cooperative four-hour wind-down, not an operation timeout.

The workflow owns the four-hour clock. Workers start no new work units once
it passes, but never cancel, shorten, or time out a unit already underway.
No clock is imposed outside a workflow that explicitly sets the timestamp.
"""
import os
import time


def remaining():
    """Seconds until the workflow begins winding down, or None without a clock."""
    value = os.environ.get("SONOS_WIND_DOWN_AT")
    return max(0.0, float(value) - time.time()) if value is not None else None


def winding_down():
    """Whether another work unit should be deferred until the next workflow."""
    left = remaining()
    return left is not None and left <= 0
