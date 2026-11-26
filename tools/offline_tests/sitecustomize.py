"""Inherit the offline test guard in child Python interpreters."""
import os

if os.environ.get('SONOS_TEST_NETWORK_EVENTS'):
    from network_guard import install
    install()
