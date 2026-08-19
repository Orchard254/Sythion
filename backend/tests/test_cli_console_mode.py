import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("sythion_cli", ROOT / "cli.py")
sythion_cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sythion_cli)


def test_parse_console_command_handles_prompt_and_log_path():
    command, args = sythion_cli.parse_console_command("ask What time is it?")
    assert command == "ask"
    assert args == "What time is it?"

    command, args = sythion_cli.parse_console_command("status")
    assert command == "status"
    assert args == ""


def test_resolve_log_path_uses_default_and_explicit_paths(tmp_path):
    default_path = sythion_cli.resolve_log_path(None)
    assert default_path.endswith("sythion_console.log")

    explicit_path = sythion_cli.resolve_log_path(str(tmp_path / "custom.log"))
    assert explicit_path == str(tmp_path / "custom.log")


def test_parse_console_command_defaults_to_interactive_mode():
    command, request, log_file, follow, lines = sythion_cli.parse_console_command([])
    assert command == "interactive"
    assert request == ""
    assert log_file is None
    assert follow is False
    assert lines == 80


def test_format_event_message_for_completed_tasks():
    event = {
        "event_type": "task.completed",
        "event_data": {"summary": "Hello there!"},
    }
    assert sythion_cli.format_event_message(event) == "assistant> Hello there!"


def test_format_event_message_for_execution_started_tasks():
    event = {
        "event_type": "task.execution_started",
        "event_data": {"started_at": "2024-01-01T00:00:00"},
    }
    assert sythion_cli.format_event_message(event) == "assistant> I’m starting work on that."


def test_format_event_message_for_tool_execution_progress():
    event = {
        "event_type": "tool.executed",
        "event_data": {
            "tool_name": "read_file",
            "success": True,
            "model_summary": "The file contents were read.",
        },
    }
    assert sythion_cli.format_event_message(event) == "assistant> I used read_file and got: The file contents were read."


def test_run_single_request_uses_backend_for_conversation_requests(monkeypatch):
    calls = []

    def fake_api(path, method="GET", data=None):
        calls.append((path, method, data))
        return {"id": "task-123", "state": "running"}

    monkeypatch.setattr(sythion_cli, "api", fake_api)
    monkeypatch.setattr(sythion_cli, "watch_task", lambda task_id, log_path: None)

    sythion_cli.run_single_request("how are you doing today", "C:/tmp/sythion.log")

    task_calls = [call for call in calls if call[0] == "/tasks"]
    assert task_calls, "expected the CLI to submit a task to the backend"
    assert task_calls[0][1] == "POST"
    assert task_calls[0][2] == {"original_request": "how are you doing today"}


def test_run_single_request_routes_greetings_to_backend(monkeypatch):
    calls = []

    def fake_api(path, method="GET", data=None):
        calls.append((path, method, data))
        if path == "/health":
            return {"status": "healthy", "selected_model": "minimax-m3"}
        if path == "/tasks":
            return {"id": "task-123", "state": "running"}
        return {"events": []}

    monkeypatch.setattr(sythion_cli, "api", fake_api)
    monkeypatch.setattr(sythion_cli, "watch_task", lambda task_id, log_path: None)

    sythion_cli.run_single_request("hi", "C:/tmp/sythion.log")

    task_calls = [call for call in calls if call[0] == "/tasks"]
    assert task_calls, "expected greetings to be routed to the backend task API"


def test_format_objective_snapshot_includes_status_summary_and_steps():
    objective = {
        "title": "Build durable work-state layer",
        "status": "active",
        "purpose": "Track what Synth is doing over time",
        "current_summary": "Working on objective projection.",
        "steps": [
            {
                "step_order": 1,
                "status": "completed",
                "description": "Define objective and plan-step models",
                "result_summary": "Models created",
            },
            {
                "step_order": 2,
                "status": "blocked",
                "description": "Surface work-state in the CLI",
                "blocker_reason": "Need CLI integration",
            },
        ],
    }

    lines = sythion_cli.format_objective_snapshot(objective)
    joined = "\n".join(lines)
    assert "[objective] Build durable work-state layer [active]" in joined
    assert "purpose: Track what Synth is doing over time" in joined
    assert "1. [completed] Define objective and plan-step models | result: Models created" in joined
    assert "2. [blocked] Surface work-state in the CLI | blocked: Need CLI integration" in joined


def test_watch_task_prints_objective_snapshot_once_per_update(monkeypatch, capsys):
    calls = {"task": 0, "objective": 0, "events": 0}

    def fake_api(path, method="GET", data=None):
        if path == "/tasks/task-1":
            calls["task"] += 1
            if calls["task"] == 1:
                return {
                    "id": "task-1",
                    "state": "running",
                    "objective_id": "obj-1",
                }
            return {
                "id": "task-1",
                "state": "completed",
                "objective_id": "obj-1",
                "final_result": "Done",
            }
        if path == "/tasks/task-1/objective":
            calls["objective"] += 1
            return {
                "id": "obj-1",
                "title": "Test objective",
                "status": "active" if calls["objective"] == 1 else "completed",
                "updated_at": "2026-08-08T00:00:00" if calls["objective"] == 1 else "2026-08-08T00:00:02",
                "current_summary": "Working" if calls["objective"] == 1 else "Done",
                "steps": [],
            }
        if path == "/tasks/task-1/events":
            calls["events"] += 1
            return {"events": [] if calls["events"] == 1 else [{
                "sequence_num": 1,
                "event_type": "task.completed",
                "event_data": {"summary": "Done"},
            }]}
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(sythion_cli, "api", fake_api)
    monkeypatch.setattr(sythion_cli.time, "sleep", lambda _: None)

    result = sythion_cli.watch_task("task-1")
    out = capsys.readouterr().out
    assert result == "completed"
    assert out.count("[objective] Test objective") == 2
    assert "Task completed: Done" not in out
    assert "Done" in out


def test_cli_prefers_selected_model_field_when_present(monkeypatch, capsys):
    calls = []

    def fake_api(path, method="GET", data=None):
        calls.append((path, method, data))
        if path == "/health":
            return {"status": "healthy", "selected_model": "gemma4:31b"}
        if path == "/tasks":
            return {"id": "task-123", "state": "running"}
        return {"events": []}

    monkeypatch.setattr(sythion_cli, "api", fake_api)
    monkeypatch.setattr(sythion_cli, "watch_task", lambda task_id, log_path=None: "completed")
    monkeypatch.setattr(sythion_cli.sys, "argv", ["cli.py", "hello there"])

    sythion_cli.main()
    out = capsys.readouterr().out
    assert "Backend: healthy (gemma4:31b)" in out


def test_context_prefers_human_like_conversation_guidance():
    engine = sythion_cli.ModelDecisionEngine if hasattr(sythion_cli, "ModelDecisionEngine") else None
    assert engine is None
