"""Sync provider -> authenticated HTTP discovery, without real credentials/network."""

import asyncio
import threading

import pytest
from fastapi import HTTPException

from backend.core import secrets
from backend.runtime import model_provider


MODELS = [{"name": "beam", "model": "beam"}, {"model": "other:exact-tag"}]


class SyncProvider:
    def __init__(self, models=MODELS, error=None):
        self.models = models
        self.error = error
        self.calls = 0

    def list_models(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.models


@pytest.fixture(autouse=True)
def memory_secrets(monkeypatch):
    monkeypatch.setattr(secrets, "_store", secrets.MemoryStore())


@pytest.fixture
def provider(monkeypatch):
    stub = SyncProvider()
    monkeypatch.setattr(model_provider, "get_default_provider", lambda: stub)
    return stub


def test_authenticated_sync_discovery_preserves_provider_payload(test_client, provider):
    response = test_client.get("/api/v1/models")
    assert response.status_code == 200
    assert response.json() == {"models": MODELS}
    assert provider.calls == 1


def test_empty_discovery_is_a_success(test_client, provider):
    provider.models = []
    response = test_client.get("/api/v1/models")
    assert response.status_code == 200
    assert response.json() == {"models": []}
    assert provider.calls == 1


def test_discovery_error_preserves_503_contract(test_client, provider):
    provider.error = RuntimeError("discovery unavailable")
    response = test_client.get("/api/v1/models")
    assert response.status_code == 503
    assert response.json()["detail"] == "Failed to fetch models: discovery unavailable"
    assert provider.calls == 1


@pytest.mark.parametrize("token", [None, "wrong-inert-token"])
def test_rejected_auth_never_invokes_provider(test_client, provider, token):
    test_client.headers.pop("X-Control-Token", None)
    headers = {} if token is None else {"X-Control-Token": token}
    response = test_client.get("/api/v1/models", headers=headers)
    assert response.status_code == 401
    assert provider.calls == 0


def test_sync_discovery_is_offloaded_and_loop_heartbeat_progresses(monkeypatch):
    from backend.api.routes import list_models

    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    observed = {}

    class BlockingProvider:
        def list_models(self):
            observed["thread"] = threading.get_ident()
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                observed["has_loop"] = False
            else:
                observed["has_loop"] = True
            started.set()
            try:
                # On broken synchronous execution this expires rather than hanging.
                observed["released"] = release.wait(timeout=2)
                return MODELS
            finally:
                finished.set()

    monkeypatch.setattr(model_provider, "get_default_provider", BlockingProvider)

    async def exercise():
        loop_thread = threading.get_ident()

        async def heartbeat():
            while not started.is_set():
                await asyncio.sleep(0)
            observed["heartbeat_while_waiting"] = not finished.is_set()
            release.set()

        pulse = asyncio.create_task(heartbeat())
        try:
            try:
                result = await asyncio.wait_for(list_models(), timeout=5)
            except HTTPException as error:
                result = error.status_code
            await asyncio.wait_for(pulse, timeout=3)
            assert observed["thread"] != loop_thread
            assert observed["has_loop"] is False
            assert observed["heartbeat_while_waiting"] is True
            assert observed["released"] is True
            assert result == {"models": MODELS}
        finally:
            release.set()
            pulse.cancel()
            await asyncio.gather(pulse, return_exceptions=True)

    asyncio.run(exercise())
