"""
Regression tests for the runner lease contract.

Governing contract (single source of truth = backend/core/config.py):
- expiry / stale-takeover threshold: settings.lease_timeout_seconds
- heartbeat interval: settings.lease_heartbeat_seconds (< expiry)
- lease ownership records the real OS process id (os.getpid())
"""

import asyncio
import os
from datetime import datetime, timedelta

import pytest

from backend.core.config import settings
from backend.models.database import RunnerLease
from backend.services.task_runner import TaskRunner


def test_lease_records_real_process_pid(test_db):
    runner = TaskRunner(test_db)
    acquired = asyncio.run(runner.acquire_lease())
    assert acquired is True

    lease = test_db.query(RunnerLease).filter_by(id=1).first()
    assert lease is not None
    assert lease.process_id == os.getpid()

    asyncio.run(runner.release_lease())


def test_stale_takeover_also_records_real_pid(test_db):
    runner = TaskRunner(test_db)
    assert asyncio.run(runner.acquire_lease(task_id="t-old")) is True

    # Backdate expiry so the lease looks stale.
    lease = test_db.query(RunnerLease).filter_by(id=1).first()
    lease.expires_at = datetime.utcnow() - timedelta(seconds=1)
    test_db.commit()

    second = TaskRunner(test_db)
    assert asyncio.run(second.acquire_lease()) is True

    lease = test_db.query(RunnerLease).filter_by(id=1).first()
    assert lease.process_id == os.getpid()
    assert lease.owner_nonce != runner._lease_nonce

    asyncio.run(second.release_lease())


def test_renewal_extends_expiry_and_updates_task(test_db):
    runner = TaskRunner(test_db)
    assert asyncio.run(runner.acquire_lease(task_id="t-1")) is True

    lease = test_db.query(RunnerLease).filter_by(id=1).first()
    old_expiry = lease.expires_at

    import time
    time.sleep(0.01)
    assert asyncio.run(runner.renew_lease(task_id="t-1")) is True

    lease = test_db.query(RunnerLease).filter_by(id=1).first()
    assert lease.expires_at >= old_expiry
    expected = datetime.utcnow() + timedelta(seconds=settings.lease_timeout_seconds)
    assert (expected - lease.expires_at).total_seconds() < 5
    assert lease.last_task_id == "t-1"

    asyncio.run(runner.release_lease())


def test_release_clears_lease(test_db):
    runner = TaskRunner(test_db)
    assert asyncio.run(runner.acquire_lease()) is True
    asyncio.run(runner.release_lease())
    assert test_db.query(RunnerLease).filter_by(id=1).first() is None
    assert runner._lease_nonce is None


# --- Governing contract consistency ------------------------------------------


def test_heartbeat_interval_is_strictly_below_expiry():
    """The single-source-of-truth relationship between heartbeat and expiry."""
    assert settings.lease_heartbeat_seconds < settings.lease_timeout_seconds


def test_execution_loop_uses_configured_heartbeat():
    """The loop must read the interval from config, not a private constant."""
    import inspect
    from backend.services.task_execution_loop import TaskExecutionLoop

    source = inspect.getsource(TaskExecutionLoop._heartbeat_lease)
    assert "settings.lease_heartbeat_seconds" in source
    assert not any(
        name in inspect.getsource(TaskExecutionLoop)
        for name in ("LEASE_HEARTBEAT_SECONDS", "HEARTBEAT_INTERVAL")
    )


def test_invalid_lease_configuration_refused(monkeypatch):
    """Heartbeat >= expiry is a boot-time configuration error."""
    from backend.services.task_runner import ensure_lease_contract

    monkeypatch.setattr(settings, "lease_timeout_seconds", 10)
    monkeypatch.setattr(settings, "lease_heartbeat_seconds", 10)
    with pytest.raises(RuntimeError, match="strictly smaller"):
        ensure_lease_contract()