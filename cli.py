"""
Sythion (Synth) CLI - send a request and watch it complete.

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
CONTROL_TOKEN = os.environ.get("SYTHION_CONTROL_TOKEN", "")
DEVELOPMENT_MODE = os.environ.get("SYTHION_DEVELOPMENT_MODE", "").lower() in {"1", "true", "yes"}


def _load_control_token(force_refresh=False):
    if not force_refresh:
        if CONTROL_TOKEN:
            return CONTROL_TOKEN
        cache = getattr(sys, "_cli_token_cache", None)
        if cache:
            return cache
    try:
        import json as _json
        data_dir = os.environ.get("LOCALAPPDATA")
        if data_dir:
            for folder in ("Sythion (Synth)", "Sythion"):
                for fname in ("sidecar.json", "token.json"):
                    token_path = os.path.join(data_dir, folder, fname)
                    if os.path.exists(token_path):
                        with open(token_path, encoding="utf-8") as f:
                            tdata = _json.load(f)
                            tok = tdata.get("token")
                            if tok:
                                sys._cli_token_cache = tok
                                return tok
    except Exception:
        pass
    # Legacy location kept for existing installations.
    try:
        with open(os.path.join(os.path.expanduser("~"), ".sythion", "control_token"), encoding="utf-8") as f:
            token = f.read().strip()
            if token:
                sys._cli_token_cache = token
                return token
    except Exception:
        pass
    return CONTROL_TOKEN or ""


def parse_console_command(cmd_input):
    if isinstance(cmd_input, str):
        parts = cmd_input.strip().split(maxsplit=1)
        if not parts:
            return "", ""
        cmd = parts[0]
        args = parts[1] if len(parts) > 1 else ""
        return cmd, args
    elif isinstance(cmd_input, list):
        if not cmd_input:
            return "interactive", "", None, False, 80
        return "interactive", " ".join(cmd_input), None, False, 80
    return "", ""


def resolve_log_path(path=None):
    if path:
        return path
    data_dir = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    return os.path.join(data_dir, "Sythion", "sythion_console.log")


def format_event_message(event):
    et = event.get("event_type")
    ed = event.get("event_data", {})
    if et == "task.completed":
        return f"assistant> {ed.get('summary', '')}"
    elif et == "task.execution_started":
        return "assistant> I’m starting work on that."
    elif et == "tool.executed":
        tname = ed.get("tool_name", "")
        msum = ed.get("model_summary", "")
        return f"assistant> I used {tname} and got: {msum}"
    return f"assistant> {ed}"


def run_single_request(request, log_path=None):
    res = api("/tasks", method="POST", data={"original_request": request})
    if isinstance(res, dict):
        if res.get("interaction_type") == "CONVERSATION" or str(res.get("id", "")).startswith("conv-"):
            content = res.get("response") or res.get("final_result") or ""
            print(f"\n{content}\n")
            return "completed"
        task_id = res.get("id")
        if task_id:
            return watch_task(task_id, log_path=log_path)
    return None


def api(path, method="GET", data=None, retry_on_401=True):
    url = BASE_URL + path
    token = _load_control_token()
    if not token:
        raise SystemExit(
            "No control token found. Start Sythion (Synth) with start_sythion.cmd, or set "
            "SYTHION_CONTROL_TOKEN in your terminal environment."
        )
    req = urllib.request.Request(url, method=method)
    req.add_header("X-Control-Token", token)
    req.add_header("Origin", "http://localhost:3000" if DEVELOPMENT_MODE else "tauri://localhost")
    if data is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(data).encode("utf-8")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401 and retry_on_401:
            fresh = _load_control_token(force_refresh=True)
            if fresh and fresh != token:
                return api(path, method=method, data=data, retry_on_401=False)
        body = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {e.code}: {body}")


def truncate_text(text, limit=120):
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def format_objective_snapshot(objective):
    if not objective:
        return []
    steps = objective.get("steps") or []
    lines = [
        f"  [objective] {objective.get('title', '(untitled)')} [{objective.get('status', 'unknown')}]",
    ]
    if objective.get("purpose"):
        lines.append(f"    purpose: {truncate_text(objective.get('purpose'), 140)}")
    if objective.get("current_summary"):
        lines.append(f"    now: {truncate_text(objective.get('current_summary'), 140)}")
    if steps:
        lines.append("    plan:")
        for step in steps[:5]:
            summary = truncate_text(step.get("description") or "", 100)
            status = step.get("status", "unknown")
            line = f"      {step.get('step_order', '?')}. [{status}] {summary}"
            if step.get("blocker_reason") and status == "blocked":
                line += f" | blocked: {truncate_text(step.get('blocker_reason'), 80)}"
            elif step.get("result_summary") and status in {"completed", "failed", "skipped"}:
                line += f" | result: {truncate_text(step.get('result_summary'), 80)}"
            lines.append(line)
    return lines


def print_objective_snapshot(objective):
    for line in format_objective_snapshot(objective):
        print(line)


def fetch_task_objective(task_id):
    return api(f"/tasks/{task_id}/objective")


def watch_task(task_id, log_path=None):
    if str(task_id).startswith("conv-"):
        return "completed"
    last_event_seq = 0
    last_question = None
    last_objective_signature = None
    while True:
        task = api(f"/tasks/{task_id}")
        state = task["state"]
        objective_id = task.get("objective_id")
        if objective_id:
            try:
                objective = fetch_task_objective(task_id)
                signature = (objective.get("id"), objective.get("updated_at"))
                if signature != last_objective_signature:
                    print_objective_snapshot(objective)
                    last_objective_signature = signature
            except Exception:
                pass

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
                if output is None and ed.get("error"):
                    output = ed.get("error")
                mark = "OK " if ok else "ERR"
                print(f"  [{mark}] tool: {name} -> {json.dumps(output)[:200]}")
            elif et == "user.replied":
                print(f"  [user] {ed.get('answer', '')}")
            elif et in ("task.waiting_for_user", "action.pending_approval"):
                q = ed.get("question") or f"Approve executing {ed.get('tool_name')}?"
                last_question = q
                print(f"  [question] {q}")
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
        elif state == "waiting_for_user":
            q = task.get("pending_question") or last_question or "User input required."
            print()
            print("=" * 60)
            print(f"QUESTION / INPUT REQUIRED:\n{q}")
            print("=" * 60)
            return state

        time.sleep(2)


def interactive_mode():
    """Run an interactive prompt (REPL) for Sythion."""
    print("=" * 60)
    print("            Sythion (Synth) Interactive CLI")
    print("=" * 60)

    try:
        health = api("/health")
        status = health.get("status", "unknown")
        model = health.get("selected_model") or health.get("model") or "(not configured)"
        print(f"Backend: {status} | Selected Model: {model}")
    except Exception as e:
        print(f"Backend Connection Warning: {e}")
        print("Ensure backend is running: python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000")

    print("\nType your request or command. Type 'help' for commands, 'exit' to quit.\n")

    while True:
        try:
            user_input = input("synth> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting Sythion CLI.")
            break

        if not user_input:
            continue

        cmd_name = user_input.lower()

        if cmd_name in ("exit", "quit", "q"):
            print("Goodbye!")
            break

        if cmd_name in ("help", "?"):
            print("\nAvailable Commands:")
            print("  help              Show this help message")
            print("  status            Show backend health & selected model")
            print("  tools             List all available backend tools")
            print("  stop              Send global stop signal to cancel running tasks")
            print("  tasks             List recent tasks")
            print("  objectives        List current durable objectives")
            print("  objective <id>    Show one objective by task id or objective id")
            print("  exit / quit       Exit interactive mode")
            print("\nAny other text is submitted as a task request to Sythion.\n")
            continue

        if cmd_name == "status":
            try:
                h = api("/health")
                print(f"  Status: {h.get('status')} | Model: {h.get('model')}")
            except Exception as e:
                print(f"  Error checking status: {e}")
            print()
            continue

        if cmd_name == "tools":
            try:
                tlist = api("/tools").get("tools", [])
                print(f"\nRegistered Tools ({len(tlist)}):")
                for t in tlist:
                    print(f"  - {t.get('name')}: {t.get('description')}")
                print()
            except Exception as e:
                print(f"  Error fetching tools: {e}")
            continue

        if cmd_name == "stop":
            try:
                res = api("/stop", method="POST")
                print(f"  Global stop sent: {res}")
            except Exception as e:
                print(f"  Error sending stop signal: {e}")
            print()
            continue

        if cmd_name == "tasks":
            try:
                tlist = api("/tasks").get("tasks", [])
                print(f"\nRecent Tasks ({len(tlist)}):")
                for t in tlist[:10]:
                    objective_ref = t.get("objective_id") or "no-objective"
                    print(
                        f"  - [{t.get('state')}] {t.get('id')}: {t.get('original_request', '')[:50]} "
                        f"(objective: {objective_ref})"
                    )
                print()
            except Exception as e:
                print(f"  Error listing tasks: {e}")
            continue

        if cmd_name == "objectives":
            try:
                objectives = api("/objectives").get("objectives", [])
                print(f"\nObjectives ({len(objectives)}):")
                for obj in objectives[:10]:
                    step_count = len(obj.get("steps") or [])
                    print(
                        f"  - [{obj.get('status')}] {obj.get('id')}: {obj.get('title', '')[:60]} "
                        f"(priority={obj.get('priority')}, steps={step_count})"
                    )
                print()
            except Exception as e:
                print(f"  Error listing objectives: {e}")
            continue

        if cmd_name.startswith("objective "):
            ident = user_input.split(maxsplit=1)[1].strip()
            if not ident:
                print("  Usage: objective <task-id-or-objective-id>\n")
                continue
            try:
                target = api(f"/tasks/{ident}")
                if target.get("objective_id"):
                    objective = fetch_task_objective(ident)
                else:
                    raise SystemExit
            except Exception:
                try:
                    objective = api(f"/objectives/{ident}")
                except Exception as e:
                    print(f"  Error fetching objective: {e}\n")
                    continue
            print()
            print_objective_snapshot(objective)
            print()
            continue

        # Submit user request as task / conversation
        try:
            task = api("/tasks", method="POST", data={"original_request": user_input})

            # Phase 13C: Conversation contract - direct response, no task polling
            if task.get("interaction_type") == "CONVERSATION" or str(task.get("id", "")).startswith("conv-"):
                content = task.get("response") or task.get("final_result") or ""
                print()
                print(content)
                print()
                continue

            task_id = task["id"]
            print(f"\nTask ID: {task_id} (State: {task['state']})")
            print("Executing...")
            result_state = watch_task(task_id)

            # If the task is waiting for user input, prompt and reply
            while result_state == "waiting_for_user":
                try:
                    reply = input("reply> ").strip()
                except (KeyboardInterrupt, EOFError):
                    print("\n[Cancelled reply]")
                    break
                if not reply:
                    continue
                try:
                    api(f"/tasks/{task_id}/reply", method="POST", data={"answer": reply})
                    print("Resuming...")
                    result_state = watch_task(task_id)
                except Exception as re:
                    print(f"  Error sending reply: {re}")
                    break

            print()
        except Exception as e:
            print(f"Error executing task: {e}\n")


def main():
    if len(sys.argv) < 2:
        interactive_mode()
        return

    request = " ".join(sys.argv[1:])
    if request.lower() in ("-i", "--interactive", "interactive"):
        interactive_mode()
        return

    try:
        health = api("/health")
    except Exception as e:
        raise SystemExit(
            f"Cannot reach backend at {BASE_URL}. Start it with:\n"
            f"  cd C:\\Ai\\sythion && python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000\n"
            f"({e})"
        )
    selected_model = health.get("selected_model") or health.get("model") or "not configured"
    print(f"Backend: {health.get('status')} ({selected_model})")
    print(f"You: {request}")
    print()

    task = api("/tasks", method="POST", data={"original_request": request})

    # Phase 13C: Conversation contract - direct response, no task polling
    if task.get("interaction_type") == "CONVERSATION" or str(task.get("id", "")).startswith("conv-"):
        content = task.get("response") or task.get("final_result") or ""
        print(content)
        return

    task_id = task["id"]
    print(f"Task: {task_id} ({task['state']})")
    print("Working...")
    watch_task(task_id)


if __name__ == "__main__":
    main()
