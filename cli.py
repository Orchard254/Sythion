"""
JARVIS CLI - send a request and watch it complete.

Requires the backend running on http://127.0.0.1:8000.

Usage:
    python cli.py "what time is it"
    python cli.py "say hello"
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error

# Force UTF-8 output so model responses with emoji/symbols don't crash on
# Windows consoles that default to cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_URL = "http://127.0.0.1:8000/api/v1"
TERMINAL_STATES = {"completed", "failed", "cancelled", "interrupted"}
CONTROL_TOKEN = os.environ.get("JARVIS_CONTROL_TOKEN", "")
DEVELOPMENT_MODE = os.environ.get("JARVIS_DEVELOPMENT_MODE", "").lower() in {"1", "true", "yes"}


def _load_control_token():
    if CONTROL_TOKEN:
        return CONTROL_TOKEN
    # New Phase A location: the per-launch token file written by the launcher
    # (%LOCALAPPDATA%\JARVIS\token.json).
    try:
        import json as _json
        data_dir = os.environ.get("LOCALAPPDATA")
        if data_dir:
            token_path = os.path.join(data_dir, "JARVIS", "token.json")
            if os.path.exists(token_path):
                with open(token_path, encoding="utf-8") as f:
                    return _json.load(f).get("token", "")
    except Exception:
        pass
    # Legacy location kept for existing installations.
    try:
        with open(os.path.join(os.path.expanduser("~"), ".jarvis", "control_token"), encoding="utf-8") as f:
            token = f.read().strip()
        return token
    except Exception:
        return ""


def api(path, method="GET", data=None):
    url = BASE_URL + path
    req = urllib.request.Request(url, method=method)
    token = _load_control_token()
    if not token:
        raise SystemExit(
            "No control token found. Start JARVIS with start_jarvis.cmd, then run the CLI "
            "from a terminal where JARVIS_CONTROL_TOKEN is set (or read "
            "%LOCALAPPDATA%\\JARVIS\\token.json)."
        )
    req.add_header("X-Control-Token", token)
    req.add_header("Origin", "http://localhost:3000" if DEVELOPMENT_MODE else "tauri://localhost")
    if data is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(data).encode("utf-8")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {e.code}: {body}")


def watch_task(task_id):
    last_event_seq = 0
    while True:
        task = api(f"/tasks/{task_id}")
        state = task["state"]

        events = api(f"/tasks/{task_id}/events")["events"]
        for ev in events:
            if ev["sequence_num"] <= last_event_seq:
                continue
            last_event_seq = ev["sequence_num"]
            et = ev["event_type"]
            ed = ev["event_data"]
            if isinstance(ed, str):
                try:
                    ed = json.loads(ed)
                except (json.JSONDecodeError, TypeError):
                    ed = {}
            if et == "tool.executed":
                ok = ed.get("success")
                name = ed.get("tool_name", "?")
                output = ed.get("output")
                mark = "OK " if ok else "ERR"
                print(f"  [{mark}] tool: {name} -> {json.dumps(output)[:200]}")
            elif et == "user.replied":
                print(f"  [user] {ed.get('answer', '')}")
            elif et in ("task.completed", "task.failed"):
                print(f"  [model] {ed.get('summary') or ed.get('message') or ''}")

        if state in TERMINAL_STATES:
            result = task.get("final_result")
            if result:
                print()
                print("=" * 60)
                print(result)
                print("=" * 60)
            else:
                reason = task.get("terminal_reason") or "(no detail)"
                print()
                print(f"Task {state}: {reason}")
            return state

        time.sleep(2)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    request = " ".join(sys.argv[1:])
    try:
        health = api("/health")
    except Exception as e:
        raise SystemExit(
            f"Cannot reach backend at {BASE_URL}. Start it with:\n"
            f"  cd C:\\Ai\\jarvis && python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000\n"
            f"({e})"
        )
    print(f"Backend: {health.get('status')} ({health.get('model', '')})".rstrip())
    print(f"You: {request}")
    print()

    task = api("/tasks", method="POST", data={"original_request": request})
    task_id = task["id"]
    print(f"Task: {task_id} ({task['state']})")
    print("Working...")
    watch_task(task_id)


if __name__ == "__main__":
    main()
