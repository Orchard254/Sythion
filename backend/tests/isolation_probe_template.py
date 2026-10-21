"""TEMPLATE of the nested isolation probe (item 020).

This file is NOT a test module.  Its name does not match pytest's
``test_*.py`` pattern, so the repository's own run never collects it and it
adds nothing to the collected count.  ``backend/tests/test_env_isolation.py``
copies it into a scratch tree under the name ``test_env_isolation_probe.py``,
where it IS collected - and where it is the only observer of the effective
settings from INSIDE a test, which is what makes it a measurement rather
than an assumption.

Every value the probe needs comes from the environment.  The variable names
are deliberately NOT ``SYTHION_*``-prefixed: the isolation under test deletes
every ``SYTHION_*`` variable from ``os.environ`` for the duration of a test,
so the probe's own plumbing has to survive that deletion.

``PROBE_FILE_SENTINEL``     the value the generated ``.env`` holds
``PROBE_ENV_SENTINEL``      the value the process environment holds
``PROBE_SCRATCH_ROOT``      the scratch tree every path has to stay inside
``PROBE_SIGNAL_PATH``       the file the single signal line is written to
``PROBE_NETWORK_ATTEMPTS``  the file a blocked outbound attempt is recorded in

The probe never RENDERS a sentinel value.  Every comparison is boolean and
every failure message names the property that broke, never the value, so a
sentinel can never reach a terminal or a JUnit XML.

The probe installs a no-network guard at import time: an outbound connection
attempt is recorded (as a COUNT, never as an address) and refused.
"""

import os
import socket


FILE_SENTINEL = os.environ.get("PROBE_FILE_SENTINEL", "")
ENV_SENTINEL = os.environ.get("PROBE_ENV_SENTINEL", "")
SCRATCH_ROOT = os.environ.get("PROBE_SCRATCH_ROOT", "")
SIGNAL_PATH = os.environ.get("PROBE_SIGNAL_PATH", "")
NETWORK_ATTEMPTS_PATH = os.environ.get("PROBE_NETWORK_ATTEMPTS", "")

# The number of blocked outbound connection attempts.  A COUNT only: an
# address could carry a value, a count cannot.
_attempts = {"count": 0}


def _record_blocked_attempt():
    """Record one blocked outbound connection attempt."""
    _attempts["count"] += 1
    try:
        with open(NETWORK_ATTEMPTS_PATH, "a", encoding="utf-8") as handle:
            handle.write("blocked outbound connection attempt\n")
    except OSError:
        # The record file is a diagnostic, never the subject of the check.
        pass


def _is_loopback(address):
    """True for a loopback address.

    The event loop's own self-pipe is a loopback socket pair, and it is not
    network I/O: refusing it would break the asyncio machinery the readiness
    file needs.  A loopback connection is therefore allowed through
    untouched, and only a connection that could leave the machine is
    refused and recorded.
    """
    if isinstance(address, (tuple, list)) and address:
        host = address[0]
        if isinstance(host, str):
            if host in ("", "localhost", "::1"):
                return True
            if host.startswith("127."):
                return True
    return False


def _install_the_no_network_guard():
    """Refuse every outbound connection and record the attempt."""
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guarded_connect(self, address):
        if _is_loopback(address):
            return real_connect(self, address)
        _record_blocked_attempt()
        raise RuntimeError("the isolation probe blocks outbound network connections")

    def guarded_connect_ex(self, address):
        if _is_loopback(address):
            return real_connect_ex(self, address)
        _record_blocked_attempt()
        raise RuntimeError("the isolation probe blocks outbound network connections")

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex


_install_the_no_network_guard()


def _write_signal(fields):
    """Write the ONE machine-readable line this probe produces."""
    line = (
        "SENTINEL-PROBE "
        "file_neutralised={file_neutralised} "
        "env_neutralised={env_neutralised} "
        "env_cleared={env_cleared} "
        "profile_isolated={profile_isolated} "
        "network_attempts={network_attempts}"
    ).format(**fields)
    with open(SIGNAL_PATH, "w", encoding="utf-8") as handle:
        handle.write(line + "\n")


def test_the_probe_observes_that_the_local_env_isolation_holds():
    """The isolation must neutralise the env FILE and the process ENVIRONMENT.

    The signal line is written BEFORE the assertions, so it is produced even
    when the probe fails - which is exactly what the negative control needs
    in order to be read at all.
    """
    from backend.core.config import settings
    from backend.core.storage import data_dir, database_path
    from backend.tests.conftest import _LOCAL_ENV_FIELDS

    file_neutralised = settings.ollama_api_key != FILE_SENTINEL
    env_neutralised = settings.selected_model != ENV_SENTINEL
    env_cleared = "SYTHION_SELECTED_MODEL" not in os.environ
    profile_isolated = (
        data_dir().startswith(SCRATCH_ROOT)
        and database_path().startswith(SCRATCH_ROOT)
    )
    network_attempts = _attempts["count"]

    _write_signal(
        {
            "file_neutralised": file_neutralised,
            "env_neutralised": env_neutralised,
            "env_cleared": env_cleared,
            "profile_isolated": profile_isolated,
            "network_attempts": network_attempts,
        }
    )

    # The sentinel sweep follows the isolation's OWN field list, so a field
    # added to that list later is covered here as well (criterion 4).
    sweep_clean = all(
        getattr(settings, name) != FILE_SENTINEL
        and getattr(settings, name) != ENV_SENTINEL
        for name in _LOCAL_ENV_FIELDS
    )

    assert file_neutralised is True, (
        "the generated .env sentinel reached the effective settings"
    )
    assert env_neutralised is True, (
        "the exported process-environment sentinel reached the effective settings"
    )
    assert env_cleared is True, (
        "a SYTHION_* process variable survived the isolation for the duration of the test"
    )
    assert profile_isolated is True, (
        "the profile directory or the database path resolved outside the scratch tree"
    )
    assert network_attempts == 0, (
        "the probe attempted an outbound network connection"
    )
    assert sweep_clean is True, (
        "a field of the isolation's own field list carries a sentinel value"
    )
