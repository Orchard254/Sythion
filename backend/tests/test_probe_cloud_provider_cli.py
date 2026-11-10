"""CLI is inert until explicit network authorization. No real API calls."""
import argparse
import pytest

from scripts import probe_cloud_provider as probe


def test_require_explicit_network_authorization(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("credentials or network were accessed")
    monkeypatch.setattr(probe.getpass, "getpass", forbidden)
    assert probe.main(["--provider", "openrouter", "--list"]) == 2
    assert "No provider call made" in capsys.readouterr().out


def test_probe_requires_model_even_with_network_requested(monkeypatch):
    monkeypatch.setattr(probe.getpass, "getpass", lambda *x: "inert")
    with pytest.raises(SystemExit):
        probe.main(["--provider", "openrouter", "--prove-tools", "--allow-network"])


def test_catalog_metadata_is_advisory_and_free_only():
    models = [
        {"model": "z/something:free", "supported_parameters": []},
        {"model": "a/tools:free", "supported_parameters": ["tools"]},
        {"model": "b/paid", "supported_parameters": ["tools"]},
        {"model": ""}, "garbage",
    ]
    assert [m["model"] for m in probe.visible_models(models, free_only=True)] == [
        "a/tools:free", "z/something:free"
    ]
    assert len(probe.visible_models(models, free_only=False, limit=2)) == 2


@pytest.mark.asyncio
async def test_probe_emits_no_task_or_tool_when_capability_fails(monkeypatch):
    attempted = []
    class Incompatible:
        def __init__(self, *args, **kwargs):
            pass
        async def prove_tool_capability(self):
            attempted.append("capability")
            raise probe.CompatibleProviderError("not compatible")
        async def decide(self, *args, **kwargs):
            attempted.append("decision")
        async def close(self):
            pass
    monkeypatch.setattr(probe, "OpenAICompatibleCloudAdapter", Incompatible)
    args = argparse.Namespace(provider="openrouter", model="unit-free", list=False, prove_tools=True, free_only=False, max_models=10)
    assert await probe.run_probe(args, "inert") == 2
    assert attempted == ["capability"]


@pytest.mark.asyncio
async def test_verify_key_mode_performs_only_authentication(monkeypatch, capsys):
    requests = []
    class KeyOnly:
        def __init__(self, provider, model, api_key):
            assert provider == "openrouter"
            assert api_key == "synthetic-key"
        async def verify_key(self):
            requests.append("key")
        async def list_models(self):
            requests.append("models")
        async def prove_tool_capability(self):
            requests.append("probe")
        async def close(self):
            pass
    monkeypatch.setattr(probe, "OpenAICompatibleCloudAdapter", KeyOnly)
    args = argparse.Namespace(provider="openrouter", model="", list=False,
                              verify_key=True, prove_tools=False,
                              free_only=False, max_models=20)
    assert await probe.run_probe(args, "synthetic-key") == 0
    assert requests == ["key"]
    output = capsys.readouterr().out
    assert "AUTH PASS" in output
    assert "synthetic-key" not in output


def test_verify_key_requires_explicit_network_permission(monkeypatch, capsys):
    monkeypatch.setattr(probe.getpass, "getpass",
                        lambda *args: (_ for _ in ()).throw(AssertionError("asked for key")))
    assert probe.main(["--provider", "openrouter", "--verify-key"]) == 2
    assert "No provider call made" in capsys.readouterr().out


def test_verify_key_rejects_provider_without_documented_auth_endpoint():
    with pytest.raises(SystemExit):
        probe.main(["--provider", "groq", "--verify-key", "--allow-network"])


@pytest.mark.asyncio
async def test_429_adds_non_retrying_free_quota_guidance(monkeypatch, capsys):
    requests = []
    class Limited:
        def __init__(self, *args, **kwargs):
            pass
        async def prove_tool_capability(self):
            requests.append("probe")
            raise probe.CompatibleProviderError("Model capability probe failed (provider HTTP 429)")
        async def close(self):
            requests.append("close")
    monkeypatch.setattr(probe, "OpenAICompatibleCloudAdapter", Limited)
    args = argparse.Namespace(provider="openrouter", model="free", list=False,
                              verify_key=False, prove_tools=True,
                              free_only=False, max_models=20)
    assert await probe.run_probe(args, "inert") == 2
    output = capsys.readouterr().err
    assert "Rate limited" in output
    assert "does NOT prove" in output
    assert "inert" not in output
    assert requests == ["probe", "close"]
