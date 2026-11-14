"""Catch accidental network use, including failures swallowed by source code.

This is a test assertion, not a sandbox for running untrusted code.
"""
import os
from pathlib import Path
import sys

_installed = False


def deny(operation):
    # Deliberately omit addresses, commands and URLs, which may hold credentials.
    with open(os.environ['SONOS_TEST_NETWORK_EVENTS'], 'a') as stream:
        stream.write(operation + '\n')
    raise RuntimeError('offline tests forbid ' + operation)


def install():
    global _installed
    if _installed:
        return
    _installed = True

    def audit(event, args):
        if event in {'socket.connect', 'socket.getaddrinfo', 'socket.sendto',
                     'socket.gethostbyname', 'socket.gethostbyaddr', 'socket.getnameinfo', 'os.system'}:
            deny(event)
        elif event == 'subprocess.Popen':
            executable, argv, _, environment = args
            environment = os.environ if environment is None else environment
            name = Path(os.fsdecode(executable)).name
            if name == 'git':
                if environment.get('GIT_ALLOW_PROTOCOL') != 'file':
                    deny('git without local-only transport')
            elif Path(os.fsdecode(executable)).resolve() == Path(sys.executable).resolve():
                if (environment.get('SONOS_TEST_NETWORK_EVENTS') != os.environ['SONOS_TEST_NETWORK_EVENTS']
                    or environment.get('PYTHONPATH') != os.environ.get('PYTHONPATH')
                    or any(flag in argv for flag in ('-S', '-I', '-E'))):
                    deny('Python child without offline guard')
            else:
                deny('unmocked external process')

    sys.addaudithook(audit)
    # libcurl makes connections in native code, outside Python's socket hooks.
    try:
        from curl_cffi.requests import Session
    except ImportError:
        pass
    else:
        def forbidden_request(*args, **kwargs):
            deny('curl_cffi request')
        Session.request = forbidden_request
