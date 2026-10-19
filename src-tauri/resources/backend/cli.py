"""
CLI for managing Sythion tool proposals.

Usage:
    python -m backend.cli proposals list
    python -m backend.cli proposals approve <id>
    python -m backend.cli proposals reject <id>
"""

import json
import sys
import os

import httpx


def _get_token_and_url():
    """Read the control token from sidecar.json and build the base URL.

    NOTE: This path is intentionally duplicated from ``storage.handshake_path()``
    because cli.py is a standalone script that runs outside the backend context
    (no PYTHONPATH, no backend imports). If ``storage.py`` changes the path,
    this must be updated to match.
    """
    sidecar_path = os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Sythion (Synth)",
        "sidecar.json",
    )
    if not os.path.exists(sidecar_path):
        print(f"Error: sidecar.json not found at {sidecar_path}")
        print("Is the server running?")
        sys.exit(1)

    with open(sidecar_path) as f:
        data = json.load(f)

    token = data["token"]
    port = data.get("port", 8000)
    base_url = f"http://127.0.0.1:{port}"
    return token, base_url


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
    if len(sys.argv) < 2:
        print("Usage: python -m backend.cli <command> [args]")
        print("Commands: proposals")
        sys.exit(1)

    command = sys.argv[1]

    if command == "proposals":
        if len(sys.argv) < 3:
            print("Usage: python -m backend.cli proposals <list|approve|reject> [id]")
            sys.exit(1)

        subcommand = sys.argv[2]
        token, base_url = _get_token_and_url()

        if subcommand == "list":
            status = sys.argv[3] if len(sys.argv) > 3 else None
            list_proposals(token, base_url, status)

        elif subcommand == "approve":
            if len(sys.argv) < 4:
                print("Usage: python -m backend.cli proposals approve <id>")
                sys.exit(1)
            approve_proposal(token, base_url, int(sys.argv[3]))

        elif subcommand == "reject":
            if len(sys.argv) < 4:
                print("Usage: python -m backend.cli proposals reject <id>")
                sys.exit(1)
            reject_proposal(token, base_url, int(sys.argv[3]))

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
