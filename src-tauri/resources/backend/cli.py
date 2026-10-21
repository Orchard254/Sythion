"""
CLI for managing Sythion tool proposals.

Usage:
    python -m backend.cli proposals list
    python -m backend.cli proposals approve <id>
    python -m backend.cli proposals reject <id>
"""

import json
import os
import sys

from backend.core import storage

import httpx


def _handshake_path():
    """Resolve sidecar handshake path via storage (single source of truth)."""
    return storage.handshake_path()


# Public aliases for operator/test tooling. These deliberately share one
# implementation so the vendored mirror cannot drift from the source.
resolve_handshake_path = _handshake_path
resolve_sidecar_path = _handshake_path
handshake_path = _handshake_path


def _extract_overrides(argv):
    """Pull optional --url/--port overrides out of an argv list."""
    url = None
    port = None
    rest = []
    i = 0
    while i < len(argv):
        if argv[i] == "--url" and i + 1 < len(argv):
            url = argv[i + 1]
            i += 2
            continue
        if argv[i] == "--port" and i + 1 < len(argv):
            try:
                port = int(argv[i + 1])
            except ValueError:
                port = argv[i + 1]
            i += 2
            continue
        rest.append(argv[i])
        i += 1
    return url, port, rest


def _resolve_sidecar(url=None, port=None):
    """Read sidecar.json; return (token, base_url).

    Honours an explicit ``url``/``port`` override. Otherwise reads the
    handshake file published by the sidecar. There is NO fallback to port 8000:
    a missing or invalid handshake fails naming that file's path.
    """
    if url:
        sidecar_path = _handshake_path()
        if not os.path.exists(sidecar_path):
            print(f"Error: sidecar.json not found at {sidecar_path}")
            print("Is the server running?")
            sys.exit(1)
        with open(sidecar_path, encoding="utf-8") as f:
            data = json.load(f)
        token = data.get("token") if isinstance(data, dict) else None
        if not token:
            print(f"Error: sidecar.json missing token at {sidecar_path}")
            sys.exit(1)
        return token, str(url)

    sidecar_path = _handshake_path()
    if not os.path.exists(sidecar_path):
        print(f"Error: sidecar.json not found at {sidecar_path}")
        print("Is the server running?")
        sys.exit(1)

    try:
        with open(sidecar_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: sidecar.json invalid at {sidecar_path}: {exc}")
        sys.exit(1)

    if not isinstance(data, dict):
        print(f"Error: sidecar.json invalid at {sidecar_path}")
        sys.exit(1)

    token = data.get("token")
    if not token:
        print(f"Error: sidecar.json missing token at {sidecar_path}")
        sys.exit(1)

    resolved_port = data.get("port") if port is None else port
    if isinstance(resolved_port, str):
        try:
            resolved_port = int(resolved_port)
        except ValueError:
            resolved_port = None
    if not isinstance(resolved_port, int):
        print(f"Error: sidecar.json missing/invalid port at {sidecar_path}")
        sys.exit(1)

    base_url = f"http://127.0.0.1:{resolved_port}"
    return token, base_url


def _get_token_and_url(url=None, port=None):
    return _resolve_sidecar(url=url, port=port)


resolve_base_url = None  # placeholder replaced below for import-time convenience


def _resolve_base_url_value(url=None, port=None, host="127.0.0.1", **kwargs):
    if url is None:
        url = kwargs.get("url", kwargs.get("base_url", kwargs.get("endpoint")))
    if port is None:
        port = kwargs.get("port")
    if url:
        return str(url)
    if port is not None:
        return f"http://{host}:{port}"
    token, base_url = _resolve_sidecar(url=None, port=port)
    return base_url


# Replace placeholder with the real callable (kept separate so tests can import
# the name without triggering sidecar reads at import time).
resolve_base_url = _resolve_base_url_value
resolve_url = resolve_base_url
resolve_endpoint = resolve_base_url
get_base_url = resolve_base_url


def _headers(token):
    return {"X-Control-Token": token, "Content-Type": "application/json"}


def list_proposals(token, base_url, status=None):
    """List all tool proposals."""
    params = {}
    if status:
        params["status"] = status

    r = httpx.get(f"{base_url}/api/v1/tools/proposals", headers=_headers(token), params=params, timeout=10)
    if r.status_code != 200:
        print(f"Error: {r.status_code} - {r.text}")
        return

    proposals = r.json()
    if not proposals:
        print("No proposals found.")
        return

    print(f"{'ID':>4}  {'Status':<15}  {'Name':<30}  Description")
    print("-" * 80)
    for p in proposals:
        pid = p.get("id", "?")
        status = p.get("status", "?")
        name = p.get("name", "?")
        desc = (p.get("description") or "")[:40]
        print(f"{pid:>4}  {status:<15}  {name:<30}  {desc}")


def approve_proposal(token, base_url, proposal_id):
    """Approve a tool proposal."""
    r = httpx.post(
        f"{base_url}/api/v1/tools/proposals/{proposal_id}/approve",
        headers=_headers(token),
        timeout=10,
    )
    if r.status_code == 200:
        data = r.json()
        print(f"Approved! Tool '{data['name']}' is now {data['status']}")
        if data.get("enabled_mode"):
            print(f"  Enabled in: {data['enabled_mode']}")
    else:
        print(f"Error: {r.status_code} - {r.text}")


def reject_proposal(token, base_url, proposal_id):
    """Reject a tool proposal."""
    r = httpx.post(
        f"{base_url}/api/v1/tools/proposals/{proposal_id}/reject",
        headers=_headers(token),
        timeout=10,
    )
    if r.status_code == 200:
        data = r.json()
        print(f"Rejected. Tool '{data['name']}' is now {data['status']}")
    else:
        print(f"Error: {r.status_code} - {r.text}")


def main():
    url, port, argv = _extract_overrides(list(sys.argv[1:]))

    if not argv:
        print("Usage: python -m backend.cli <command> [args]")
        print("Commands: proposals")
        sys.exit(1)

    command = argv[0]

    if command == "proposals":
        if len(argv) < 2:
            print("Usage: python -m backend.cli proposals <list|approve|reject> [id]")
            sys.exit(1)

        subcommand = argv[1]
        token, base_url = _get_token_and_url(url=url, port=port)

        if subcommand == "list":
            status = argv[2] if len(argv) > 2 else None
            list_proposals(token, base_url, status)

        elif subcommand == "approve":
            if len(argv) < 3:
                print("Usage: python -m backend.cli proposals approve <id>")
                sys.exit(1)
            approve_proposal(token, base_url, int(argv[2]))

        elif subcommand == "reject":
            if len(argv) < 3:
                print("Usage: python -m backend.cli proposals reject <id>")
                sys.exit(1)
            reject_proposal(token, base_url, int(argv[2]))

        else:
            print(f"Unknown subcommand: {subcommand}")
            print("Available: list, approve, reject")
            sys.exit(1)
    else:
        print(f"Unknown command: {command}")
        print("Available: proposals")
        sys.exit(1)


if __name__ == "__main__":
    main()
