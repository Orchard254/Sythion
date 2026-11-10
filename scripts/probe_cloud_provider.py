"""Explicit, read-only cloud model compatibility probe. Never runs Sythion tools.

Usage from repo root:
  python -m scripts.probe_cloud_provider --provider openrouter --list --allow-network
  python -m scripts.probe_cloud_provider --provider openrouter \
    --model some/model:free --prove-tools --allow-network

Credential is entered using getpass; no keys in environment variables,
arguments, history, SQLite or logs. A vault-resident key can be opted into
with --use-vault; namespace isolation is enforced by backend.core.secrets.
No automatic retries, provider switching, or real task dispatch.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
from typing import Any, Dict, List

from backend.adapters.openai_compatible_adapter import (
    OpenAICompatibleCloudAdapter,
    CompatibleProviderError,
    PROVIDERS,
)


def visible_models(models: List[Dict[str, Any]], *, free_only: bool, limit: int = 20) -> List[Dict[str, Any]]:
    """Catalog metadata is advisory; listing NEVER marks a model compatible."""
    subset = [m for m in models if isinstance(m, dict) and isinstance(m.get("model"), str)]
    if free_only:
        subset = [m for m in subset if m["model"].endswith(":free")]
    subset.sort(key=lambda m: m["model"])
    return subset[:max(0, min(int(limit), 100))]


async def run_probe(args: argparse.Namespace, credential: str) -> int:
    adapter = OpenAICompatibleCloudAdapter(
        args.provider, args.model or "__discovery_only__", api_key=credential
    )
    try:
        if args.list:
            models = await adapter.list_models()
            selected = visible_models(models, free_only=args.free_only, limit=args.max_models)
            print("Catalog entries displayed: " + str(len(selected)))
            print("Metadata is NOT proof of tool calling or safe task execution.")
            for model in selected:
                possible = "tools" in model.get("supported_parameters", [])
                print(json.dumps({
                    "model": model["model"], "catalog_says_tools": possible
                }))
        if args.prove_tools:
            await adapter.prove_tool_capability()
            print("PROBE PASS: model returned required tool and exact arguments.")
            # The tool remains a model-only stub. No actual tool or task executes.
            action = {
                "type": "function",
                "function": {
                    "name": "complete_task",
                    "description": "Reply with a verified greeting",
                    "parameters": {
                        "type": "object",
                        "properties": {"result": {"type": "string"}},
                        "required": ["result"],
                    },
                },
            }
            result = await adapter.decide(
                [{"role": "user", "content": "Call complete_task with result OK."}],
                [action],
                max_tokens=90,
            )
            from backend.adapters.openai_compatible_adapter import _message, _tool_calls
            calls = _tool_calls(_message(result))
            if len(calls) != 1 or calls[0]["function"]["name"] != "complete_task":
                raise CompatibleProviderError("Completion tool decision not verified")
            raw = calls[0]["function"]["arguments"]
            payload = json.loads(raw) if isinstance(raw, str) else raw
            if payload != {"result": "OK"}:
                raise CompatibleProviderError("Completion tool arguments were incorrect")
            print("DECISION PASS: correct complete_task control tool format.")
            print("No local tool ran. No execution permissions were granted.")
        return 0
    except CompatibleProviderError as exc:
        print("NOT VERIFIED: " + str(exc), file=sys.stderr)
        return 2
    except (ValueError, TypeError):
        print("NOT VERIFIED: invalid model decision response", file=sys.stderr)
        return 2
    finally:
        await adapter.close()


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=sorted(PROVIDERS), required=True)
    parser.add_argument("--model", default="")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--prove-tools", action="store_true")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--use-vault", action="store_true")
    parser.add_argument("--free-only", action="store_true")
    parser.add_argument("--max-models", type=int, default=20)
    args = parser.parse_args(argv)

    if not (args.list or args.prove_tools):
        parser.error("Select --list or --prove-tools")
    if args.prove_tools and not args.model:
        parser.error("--model is mandatory for --prove-tools")
    if not args.allow_network:
        print("No provider call made. Add --allow-network to authorize model API requests.")
        return 2
    if args.use_vault:
        from backend.core.secrets import resolve_compatible_provider_key
        credential = resolve_compatible_provider_key(args.provider)
        if not credential:
            print("No key in the current build-scoped vault.", file=sys.stderr)
            return 2
    else:
        credential = getpass.getpass("API key for " + args.provider + " (hidden): ").strip()
        if not credential:
            print("No API key supplied.", file=sys.stderr)
            return 2
    return asyncio.run(run_probe(args, credential))


if __name__ == "__main__":
    raise SystemExit(main())
