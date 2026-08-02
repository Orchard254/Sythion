"""
JARVIS end-to-end smoke test.

Hits a running backend at http://127.0.0.1:8000 and verifies the full
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

BASE_URL = "http://127.0.0.1:8000/api/v1"
TERMINAL_STATES = {"completed", "failed", "cancelled", "interrupted"}
POLL_INTERVAL = 2.0
TIMEOUT_SECONDS = 120

PASS = 0
FAIL = 1


def load_control_token():
    token = os.environ.get("JARVIS_CONTROL_TOKEN", "")
    if token:
        return token
    # New Phase A location: the per-launch token file written by the launcher
    # (%LOCALAPPDATA%\JARVIS\token.json).
    try:
        data_dir = os.environ.get("LOCALAPPDATA")
        if data_dir:
            token_path = os.path.join(data_dir, "JARVIS", "token.json")
            if os.path.exists(token_path):
                with open(token_path, encoding="utf-8") as f:
                    return json.load(f).get("token", "")
    except Exception:
        pass
    # Legacy location kept for existing installations.
    try:
        with open(os.path.join(os.path.expanduser("~"), ".jarvis", "control_token"), encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def api(path, method="GET", data=None, timeout=30):
    url = BASE_URL + path
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
    args = parser.parse_args()

    results = []

    try:
        health = api("/health")
    except Exception as e:
        print(f"[FAIL] backend unreachable at {BASE_URL}: {e}")
        print("Start it with: cd C:\\Ai\\jarvis && start_jarvis.cmd")
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
            done = wait_for_terminal(task["id"])
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
            done = wait_for_terminal(task["id"])
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
