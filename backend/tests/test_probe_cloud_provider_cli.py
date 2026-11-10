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
