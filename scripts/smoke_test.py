"""
Sythion (Synth) end-to-end smoke test.

Hits a running backend and verifies the full
task lifecycle: create -> run -> complete with a non-empty final_result.

Usage:
    python scripts/smoke_test.py                # run the standard checks
    python scripts/smoke_test.py --skip-model   # skip the live-model round trip
    python scripts/smoke_test.py --all          # also run the system_time tool check
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE_URL = None

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
try:
    from backend.core import storage as _storage
except Exception:  # pragma: no cover - standalone fallback
    _storage = None


def _manual_data_dir():
    """fallback only — storage.data_dir() wins.

    Cross-ref: backend/core/storage.py:data_dir(), resolve_data_dir(),
    handshake_path(), default_data_dir(). This is used only when
    backend.core.storage is unavailable; callers prefer
    storage.handshake_path().
    """
    canonical = os.environ.get("SYTHION_DATA_DIR", "").strip()
    legacy = os.environ.get("Sythion (Synth)_DATA_DIR", "").strip()
    if canonical and legacy:
        if os.path.normcase(os.path.normpath(os.path.abspath(canonical))) != os.path.normcase(
            os.path.normpath(os.path.abspath(legacy))
        ):
            raise ValueError("conflicting profile directories")
        return os.path.normpath(os.path.abspath(canonical))
    if canonical:
        return os.path.normpath(os.path.abspath(canonical))
    if legacy:
        return os.path.normpath(os.path.abspath(legacy))
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.normpath(os.path.abspath(os.path.join(local, "Sythion (Synth)")))
    import tempfile

    return os.path.normpath(os.path.abspath(os.path.join(tempfile.gettempdir(), "sythion-data")))


def resolve_handshake_path():
    if _storage is not None:
        try:
            return _storage.handshake_path()
        except Exception:
            pass
    return os.path.join(_manual_data_dir(), "sidecar.json")


resolve_sidecar_path = resolve_handshake_path
handshake_path = resolve_handshake_path


def _read_sidecar(url=None, port=None):
    if url:
        path = resolve_handshake_path()
        if not os.path.exists(path):
            raise FileNotFoundError(f"sidecar.json not found at {path}")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not data.get("token"):
            raise ValueError(f"sidecar.json invalid at {path}")
        return data.get("token"), str(url), path

    path = resolve_handshake_path()
    if not os.path.exists(path):
        raise FileNotFoundError(f"sidecar.json not found at {path}")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not data.get("token"):
        raise ValueError(f"sidecar.json invalid at {path}")

    resolved_port = data.get("port") if port is None else port
    if isinstance(resolved_port, str):
        try:
            resolved_port = int(resolved_port)
        except ValueError:
            resolved_port = None
    if not isinstance(resolved_port, int):
        raise ValueError(f"sidecar.json missing/invalid port at {path}")

    return data.get("token"), f"http://127.0.0.1:{resolved_port}", path


def resolve_base_url(url=None, port=None, host="127.0.0.1", **kwargs):
    if url is None:
        url = kwargs.get("url", kwargs.get("base_url", kwargs.get("endpoint")))
    if port is None:
        port = kwargs.get("port")
    if url:
        return str(url)
    if port is not None:
        return f"http://{host}:{port}"
    token, base_url, path = _read_sidecar(None, port)
    return base_url


resolve_url = resolve_base_url
resolve_endpoint = resolve_base_url


def get_base_url(url=None, port=None, host="127.0.0.1", **kwargs):
    global BASE_URL
    if url is None and port is None and "port" not in kwargs and BASE_URL is not None:
        return BASE_URL
    resolved = resolve_base_url(url=url, port=port, host=host, **kwargs)
    api_url = resolved + "/api/v1"
    BASE_URL = api_url
    return api_url


TERMINAL_STATES = {"completed", "failed", "cancelled", "interrupted"}

try:
    if BASE_URL is None:
        BASE_URL = get_base_url()
except Exception:
    BASE_URL = None
POLL_INTERVAL = 2.0
TIMEOUT_SECONDS = 120

PASS = 0
FAIL = 1


def load_control_token():
    token = os.environ.get("SYTHION_CONTROL_TOKEN", "")
    if token:
        return token
    # Legacy env var fallback for older local workflows.
    token = os.environ.get("Sythion (Synth)_CONTROL_TOKEN", "")
    if token:
        return token
    # New Phase A location: the per-launch token file written by the launcher
    # (%LOCALAPPDATA%\Sythion (Synth)\token.json).
    try:
        data_dir = os.environ.get("LOCALAPPDATA")
        if data_dir:
            token_path = os.path.join(data_dir, "Sythion (Synth)", "token.json")
            if os.path.exists(token_path):
                with open(token_path, encoding="utf-8") as f:
                    return json.load(f).get("token", "")
    except Exception:
        pass
    # Legacy location kept for existing installations.
    try:
        with open(os.path.join(os.path.expanduser("~"), ".sythion", "control_token"), encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def api(path, method="GET", data=None, timeout=30):
    url = get_base_url() + path
    req = urllib.request.Request(url, method=method)
    token = load_control_token()
    if token:
        req.add_header("X-Control-Token", token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(data).encode("utf-8")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check(name, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" - {detail}" if detail else ""))
    return condition


def wait_for_terminal(task_id):
    start = time.monotonic()
    while time.monotonic() - start < TIMEOUT_SECONDS:
        task = api(f"/tasks/{task_id}")
        if task["state"] in TERMINAL_STATES:
            return task
        time.sleep(POLL_INTERVAL)
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-model", action="store_true",
                        help="skip the live-model task round trip")
    parser.add_argument("--all", action="store_true",
                        help="also run the system_time tool check")
    parser.add_argument("--url", default=None, help="explicit backend base URL")
    parser.add_argument("--port", type=int, default=None, help="explicit backend port")
    args = parser.parse_args()

    get_base_url(args.url, args.port)

    results = []

    try:
        health = api("/health")
    except Exception as e:
        print(f"[FAIL] backend unreachable at {get_base_url()}: {e}")
        print("Start it with: cd C:\\Ai\\sythion && start_sythion.cmd")
        return FAIL

    results.append(check("backend healthy", health.get("status") == "healthy",
                         json.dumps(health)))
    results.append(check("db connected", bool(health.get("database_connected"))))
    results.append(check("model configured", bool(health.get("cloud_configured")),
                         health.get("model", "")))

    if not args.skip_model:
        print()
        print("--- live model task ---")
        try:
            task = api("/tasks", method="POST", data={"original_request": "Reply with the single word OK."})
        except Exception as e:
            print(f"[FAIL] could not create task: {e}")
            results.append(False)
        else:
            print(f"task {task['id'][:8]} created (state={task['state']})")
            done = (
                task
                if task.get("state") in TERMINAL_STATES
                else wait_for_terminal(task["id"])
            )
            if done is None:
                print("[FAIL] task did not reach a terminal state in time")
                results.append(False)
            else:
                ok = done["state"] == "completed"
                results.append(check("task completed", ok, done["state"]))
                result_ok = ok and bool(done.get("final_result"))
                results.append(check("final_result present", result_ok,
                                     (done.get("final_result") or "")[:120]))

    if args.all:
        print()
        print("--- system_time tool ---")
        try:
            task = api("/tasks", method="POST", data={"original_request": "What time is it?"})
        except Exception as e:
            print(f"[FAIL] could not create task: {e}")
            results.append(False)
        else:
            print(f"task {task['id'][:8]} created (state={task['state']})")
            done = (
                task
                if task.get("state") in TERMINAL_STATES
                else wait_for_terminal(task["id"])
            )
            if done is None:
                print("[FAIL] task did not reach a terminal state in time")
                results.append(False)
            else:
                ok = done["state"] == "completed" and bool(done.get("final_result"))
                results.append(check("time task completed with result", ok,
                                     (done.get("final_result") or "")[:120]))

    print()
    passed = sum(1 for r in results if r)
    print(f"{passed}/{len(results)} checks passed")
    return PASS if passed == len(results) else FAIL


if __name__ == "__main__":
    sys.exit(main())
