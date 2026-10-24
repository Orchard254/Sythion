"""Regression guard for event-loop offload of voice-asset installation.

Pins that POST /api/v1/voice/assets/install does not execute the synchronous
STT installer on the FastAPI request event loop, that TTS status is computed on
the owning thread after the worker returns, and that install transactions are
serialized.  No network, secrets, user DB, or audio are used: the installer is
replaced with an inert blocking fake.
"""

import asyncio
import threading

import pytest
from fastapi import HTTPException

from backend.core import voice_assets


_FAKE_STATUS = {
    "stt_installed": True,
    "stt_model": "vosk-model-small-en-us-0.15",
    "stt_version": "0.15",
    "stt_license": "Apache-2.0",
    "stt_sha256_verified": True,
    "tts_provider": "sapi5",
    "tts_voice": "Microsoft Zira Desktop",
    "assets_dir": "C:/Temp/sythion-voice-assets",
    "downloads_on_import": False,
    "downloads_on_startup": False,
}


def test_voice_assets_install_is_offloaded_status_stays_on_owning_thread(monkeypatch):
    from backend.api.routes import voice_assets_install

    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    observed = {}
    status_thread = {}

    def blocking_install(*args, **kwargs):
        with voice_assets._STT_INSTALL_LOCK:
            observed["thread"] = threading.get_ident()
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                observed["has_loop"] = False
            else:
                observed["has_loop"] = True
            started.set()
            try:
                observed["released"] = release.wait(timeout=2)
                return None
            finally:
                finished.set()

    def fake_status():
        status_thread["thread"] = threading.get_ident()
        return dict(_FAKE_STATUS)

    monkeypatch.setattr(voice_assets, "install_stt_assets", blocking_install)
    monkeypatch.setattr(voice_assets, "asset_status", fake_status)

    async def exercise():
        loop_thread = threading.get_ident()

        async def heartbeat():
            while not started.is_set():
                await asyncio.sleep(0)
            observed["heartbeat_while_waiting"] = not finished.is_set()
            release.set()

        pulse = asyncio.create_task(heartbeat())
        try:
            result = await asyncio.wait_for(voice_assets_install(), timeout=5)
            await asyncio.wait_for(pulse, timeout=3)
            assert observed["thread"] != loop_thread
            assert observed["has_loop"] is False
            assert observed["heartbeat_while_waiting"] is True
            assert observed["released"] is True
            assert status_thread["thread"] == loop_thread
            assert result.stt_installed is True
            assert result.stt_model == "vosk-model-small-en-us-0.15"
            assert result.downloads_on_import is False
            assert result.downloads_on_startup is False
        finally:
            release.set()
            pulse.cancel()
            await asyncio.gather(pulse, return_exceptions=True)

    asyncio.run(exercise())


def test_voice_assets_install_maps_exception_to_502(monkeypatch):
    from backend.api.routes import voice_assets_install

    def failing_install(*args, **kwargs):
        raise RuntimeError("install boom")

    monkeypatch.setattr(voice_assets, "install_stt_assets", failing_install)
    monkeypatch.setattr(voice_assets, "asset_status", lambda: dict(_FAKE_STATUS))

    async def exercise():
        try:
            await voice_assets_install()
        except HTTPException as exc:
            assert exc.status_code == 502
            assert exc.detail == "Voice asset install failed: install boom"
        else:
            raise AssertionError("expected HTTPException")

    asyncio.run(exercise())


def test_voice_assets_install_serializes_helper(monkeypatch):
    from backend.api.routes import voice_assets_install

    lock = voice_assets._STT_INSTALL_LOCK
    first_started = threading.Event()
    first_release = threading.Event()
    first_finished = threading.Event()
    second_started = threading.Event()
    state = {"second_after_first": False}

    def blocking_install(*args, **kwargs):
        with lock:
            if not first_finished.is_set() and not first_started.is_set():
                first_started.set()
                first_release.wait(timeout=2)
                first_finished.set()
                return None
            state["second_after_first"] = first_finished.is_set()
            second_started.set()
            return None

    monkeypatch.setattr(voice_assets, "install_stt_assets", blocking_install)
    monkeypatch.setattr(voice_assets, "asset_status", lambda: dict(_FAKE_STATUS))

    async def exercise():
        first_task = asyncio.ensure_future(voice_assets_install())
        await asyncio.wait_for(_wait_event(first_started), timeout=2)
        second_task = asyncio.ensure_future(voice_assets_install())
        await asyncio.sleep(0.1)
        assert not second_started.is_set()
        first_release.set()
        await asyncio.wait_for(first_task, timeout=5)
        await asyncio.wait_for(second_task, timeout=5)
        assert state["second_after_first"] is True
        assert second_started.is_set()

    asyncio.run(exercise())


async def _wait_event(event: threading.Event):
    while not event.is_set():
        await asyncio.sleep(0)