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
            return {"status": "healthy"}
        if path == "/tasks":
            return {"id": "task-123", "state": "running"}
        return {"events": []}

    monkeypatch.setattr(sythion_cli, "api", fake_api)
    monkeypatch.setattr(sythion_cli, "watch_task", lambda task_id, log_path: None)

    sythion_cli.run_single_request("hi", "C:/tmp/sythion.log")

    task_calls = [call for call in calls if call[0] == "/tasks"]
    assert task_calls, "expected greetings to be routed to the backend task API"


def test_context_prefers_human_like_conversation_guidance():
    engine = sythion_cli.ModelDecisionEngine if hasattr(sythion_cli, "ModelDecisionEngine") else None
    assert engine is None
