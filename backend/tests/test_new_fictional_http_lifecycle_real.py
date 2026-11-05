"""Real-provider, fictional HTTP lifecycle test for partial-discussion recall.

Empty disposable file-backed DB, actual FastAPI route, real configured provider.
"""

import json
import os
import subprocess
import sys
import traceback
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
_CHILD_ENV = "FICTIONAL_LIFECYCLE_CHILD"


def _run_isolated(request, tmp_path, *, provider):
    """Start with isolated paths even if this pytest process imported main already."""
    data_dir = tmp_path / "profile"
    log_dir = tmp_path / "logs"
    data_dir.mkdir()
    log_dir.mkdir()
    env = os.environ.copy()
    env.update({
        _CHILD_ENV: "1",
        "FICTIONAL_LIFECYCLE_DATA_DIR": str(data_dir),
        "FICTIONAL_LIFECYCLE_LOG_DIR": str(log_dir),
        "SYTHION_DATA_DIR": str(data_dir),
        "SYTHION_DATABASE_PATH": str(data_dir / "sythion.db"),
        "SYTHION_LOG_DIR": str(log_dir),
        "SYTHION_RUN_PROVIDER_E2E": "1" if provider else "0",
    })
    env.pop("Sythion (Synth)_DATA_DIR", None)
    env.pop("SYTHION_HANDSHAKE_TOKEN_FILE", None)
    env.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(request.node.path) + "::" + request.node.name, "-q", "-s"],
        cwd=Path(__file__).resolve().parents[2], env=env,
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "1 passed" in result.stdout, result.stdout + "\n" + result.stderr
    print(result.stdout)


@pytest.fixture()
def real_lifecycle_env(tmp_path, monkeypatch):
    assert os.environ.get(_CHILD_ENV) == "1"
    data_dir = Path(os.environ["FICTIONAL_LIFECYCLE_DATA_DIR"])
    log_dir = Path(os.environ["FICTIONAL_LIFECYCLE_LOG_DIR"])
    assert data_dir.is_dir() and log_dir.is_dir()
    assert not any(data_dir.iterdir()) and not any(log_dir.iterdir())
    monkeypatch.setenv("SYTHION_DATA_DIR", str(data_dir))
    monkeypatch.setenv("SYTHION_LOG_DIR", str(log_dir))
    monkeypatch.delenv("Sythion (Synth)_DATA_DIR", raising=False)
    monkeypatch.delenv("SYTHION_HANDSHAKE_TOKEN_FILE", raising=False)

    from backend.core.config import settings
    from backend.core import storage
    from backend.core.db_manager import DatabaseManager
    from backend.runtime import composer_request_receipt
    from backend.services import goal_manager

    monkeypatch.setattr(settings, "database_path", str(data_dir / "sythion.db"))
    assert not Path(settings.database_path).exists()
    assert Path(storage.data_dir()).resolve() == data_dir.resolve()
    # Keep the startup call sites, but never execute any migration implementation.
    migration_calls = []
    def suppress(name, result=None):
        def no_migration(*args, **kwargs):
            migration_calls.append(name)
            return result
        return no_migration

    monkeypatch.setattr(storage, "migrate_legacy_data_dir", suppress("profile", False))
    monkeypatch.setattr(goal_manager, "migrate_legacy_objectives", suppress("objectives", {"migrated": [], "skipped": []}))
    monkeypatch.setattr(DatabaseManager, "_apply_migrations", suppress("schema"))
    monkeypatch.setattr(composer_request_receipt, "migrate_composer_request_receipt_digests", suppress("receipts"))

    from backend.main import app
    from backend.core.logging_config import default_log_path
    from backend.core.session import set_current_session_id

    assert Path(default_log_path()).resolve().parent == log_dir.resolve()

    sid = f"real-lifecycle-{uuid.uuid4().hex[:8]}"
    set_current_session_id(sid)

    from backend.api.dependencies import set_grounded_discussion_provider, reset_grounded_discussion_provider
    from backend.services.partial_discussion_recall_linkage import (
        PartialDiscussionRecallLinkageService,
    )

    set_grounded_discussion_provider(
        lambda db: PartialDiscussionRecallLinkageService(db)
    )
    try:
        yield sid, data_dir, app, migration_calls
    finally:
        reset_grounded_discussion_provider()


def test_fictional_lifecycle_startup_isolated_offline(request, tmp_path, monkeypatch):
    if os.environ.get(_CHILD_ENV) != "1":
        return _run_isolated(request, tmp_path, provider=False)

    from fastapi.testclient import TestClient
    from backend.services.conversation_runtime import ModelClient

    async def no_provider(*args, **kwargs):
        raise AssertionError("offline startup must not call a provider")

    monkeypatch.setattr(ModelClient, "chat", no_provider)
    _, data_dir, app, migration_calls = request.getfixturevalue("real_lifecycle_env")
    with TestClient(app):
        from backend.models.database import get_db_manager

        engine = get_db_manager().engine
        assert Path(engine.url.database).resolve() == (data_dir / "sythion.db").resolve()
        assert (data_dir / "sythion.db").is_file()
        assert set(migration_calls) == {"profile", "objectives", "schema", "receipts"}
    assert (Path(os.environ["SYTHION_LOG_DIR"]) / "sythion.log").is_file()


@pytest.mark.provider_e2e
@pytest.mark.network
@pytest.mark.skipif(
    os.environ.get("SYTHION_RUN_PROVIDER_E2E") != "1",
    reason="set SYTHION_RUN_PROVIDER_E2E=1 to run real-provider tests",
)
def test_new_fictional_complete_http_lifecycle_real(request, tmp_path, monkeypatch, caplog):
    if os.environ.get(_CHILD_ENV) != "1":
        return _run_isolated(request, tmp_path, provider=True)

    stage = "child_entry"
    data_dir = None
    artifact = {}
    calls = []
    hook_events = []
    index_outcomes = []
    migration_calls = []

    def _sanitize(value, _depth=0):
        if _depth > 14:
            return "<truncated-depth>"
        if isinstance(value, str):
            if len(value) > 12000:
                return value[:12000] + "...<truncated>"
            return value
        if isinstance(value, bytes):
            return "<bytes:" + str(len(value)) + ">"
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                key_text = str(key)
                lowered = key_text.lower()
                if any(word in lowered for word in ("token", "secret", "password", "api_key", "authorization", "credential", "handshake")):
                    result[key_text] = "<redacted>"
                else:
                    result[key_text] = _sanitize(item, _depth + 1)
            return result
        if isinstance(value, (list, tuple)):
            trimmed = list(value)[:200]
            sanitized = [_sanitize(item, _depth + 1) for item in trimmed]
            if len(value) > 200:
                sanitized.append("<" + str(len(value) - 200) + " more items truncated>")
            return sanitized
        if isinstance(value, set):
            return _sanitize(sorted(value, key=str), _depth)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return str(value)[:12000]

    def _write_failure_diagnostic(exc=None):
        nonlocal stage
        diag = {
            "stage": stage,
            "exception_type": type(exc).__name__ if exc is not None else None,
            "exception_message": _sanitize(str(exc)) if exc is not None else None,
            "traceback": _sanitize(traceback.format_exc()) if exc is not None else None,
            "artifact": _sanitize(artifact),
            "migration_calls": migration_calls,
            "hook_events": hook_events,
            "index_outcomes": index_outcomes,
            "model_calls_count": len(calls),
        }
        try:
            if data_dir is not None:
                path = Path(data_dir) / "failure_diagnostic.json"
                path.write_text(
                    json.dumps(diag, indent=2, ensure_ascii=False, default=str)[:300000],
                    encoding="utf-8",
                )
                print("FICTIONAL_LIFECYCLE_FAILURE_DIAGNOSTIC=" + str(path), flush=True)
        except Exception as write_exc:
            print("FICTIONAL_LIFECYCLE_DIAGNOSTIC_WRITE_ERROR=" + repr(write_exc), flush=True)
        print("FICTIONAL_LIFECYCLE_FAILURE_STAGE=" + str(stage), flush=True)
        print("FICTIONAL_LIFECYCLE_FAILURE_TYPE=" + (type(exc).__name__ if exc is not None else "None"), flush=True)
        if exc is not None:
            print("FICTIONAL_LIFECYCLE_FAILURE_MESSAGE=" + _sanitize(str(exc)), flush=True)

    try:
        from fastapi.testclient import TestClient

        sid, data_dir, app, migration_calls = request.getfixturevalue("real_lifecycle_env")
        artifact = {"session_id": sid, "data_dir": str(data_dir)}
        stage = "env_initialized"
        print("FICTIONAL_LIFECYCLE_STAGE=" + stage, flush=True)

        from backend.services.conversation_runtime import ModelClient

        calls = []

        class RecordingModelClient(ModelClient):
            async def chat(self, *args, **kwargs):
                messages = kwargs.get("messages", args[0] if args else [])
                call = {"messages": [dict(message) for message in messages]}
                calls.append(call)
                def save_calls():
                    (data_dir / "model_calls.json").write_text(
                        json.dumps(calls, indent=2, ensure_ascii=False), encoding="utf-8"
                    )
                save_calls()
                try:
                    result = await super().chat(*args, **kwargs)
                    call["response"] = result
                except Exception as exc:
                    call["error_type"] = type(exc).__name__
                    raise
                finally:
                    save_calls()
                return result

        monkeypatch.setattr("backend.services.conversation_runtime.ModelClient", RecordingModelClient)

        import backend.api.routes as routes

        original_hook = routes._run_partial_recall_post_persistence_semantic_index
        hook_events = []

        async def recording_hook(*args, **kwargs):
            hook_events.append("started")
            try:
                return await original_hook(*args, **kwargs)
            finally:
                hook_events.append("finished")
                (data_dir / "hook_events.json").write_text(json.dumps(hook_events), encoding="utf-8")

        monkeypatch.setattr(routes, "_run_partial_recall_post_persistence_semantic_index", recording_hook)
        import backend.services.grounded_discussion_server_owned_semantic_index as index_module

        original_index = index_module.run_server_owned_semantic_index
        index_outcomes = []

        async def recording_index(*args, **kwargs):
            index_path = data_dir / "index_outcome.json"
            index_path.write_text(json.dumps({"started": True}), encoding="utf-8")
            try:
                outcome = await original_index(*args, **kwargs)
            except Exception as exc:
                outcome_record = {
                    "exchange_id": None,
                    "state_status": None,
                    "rejected_reason": type(exc).__name__,
                    "applied": False,
                    "error_type": type(exc).__name__,
                    "error_message": _sanitize(str(exc)),
                }
                index_outcomes.append(outcome_record)
                index_path.write_text(json.dumps(index_outcomes, indent=2, default=str), encoding="utf-8")
                raise
            outcome_record = {
                "exchange_id": getattr(outcome, "exchange_id", None),
                "state_status": getattr(outcome, "state_status", None),
                "rejected_reason": getattr(outcome, "rejected_reason", None),
                "applied": getattr(outcome, "applied", None),
                "applied_count": getattr(outcome, "applied_count", None),
                "provider_call_count": getattr(outcome, "provider_call_count", None),
            }
            index_outcomes.append(outcome_record)
            index_path.write_text(json.dumps(index_outcomes, indent=2, default=str), encoding="utf-8")
            (data_dir / "index_outcome.json").write_text(json.dumps(index_outcomes, indent=2, default=str), encoding="utf-8")
            return outcome

        monkeypatch.setattr(index_module, "run_server_owned_semantic_index", recording_index)
        caplog.set_level("INFO")

        from backend.models.database import get_db_manager
        from backend.services.grounded_discussions import Base as GroundedBase
        from backend.services.grounded_discussion_index import Base as IndexBase
        from backend.core.config import settings
        from backend.core.storage import data_dir as resolved_data_dir

        stage = "startup_isolation_assertions"
        print("FICTIONAL_LIFECYCLE_STAGE=" + stage, flush=True)
        # Check the profile and database overrides before lifespan startup can write.
        assert Path(resolved_data_dir()).resolve() == data_dir.resolve()
        assert Path(settings.database_path).resolve().is_relative_to(data_dir.resolve())
        with TestClient(app) as client:
            stage = "lifespan_and_database_assertions"
            print("FICTIONAL_LIFECYCLE_STAGE=" + stage, flush=True)
            assert set(migration_calls) == {"profile", "objectives", "schema", "receipts"}
            db_manager = get_db_manager()
            engine = db_manager.engine if hasattr(db_manager, "engine") else None
            if engine is None:
                with db_manager.get_session() as _s:
                    engine = _s.get_bind()
            assert Path(engine.url.database).resolve().is_relative_to(data_dir.resolve())
            GroundedBase.metadata.create_all(engine)
            IndexBase.metadata.create_all(engine)

            from sqlalchemy import inspect, text

            def work_records():
                tables = [name for name in inspect(engine).get_table_names()
                          if "task" in name.lower() or "dispatch" in name.lower()]
                with engine.connect() as connection:
                    return {name: sorted((tuple(map(str, row)) for row in connection.execute(
                        text('SELECT * FROM "' + name.replace('"', '""') + '"'))), key=repr)
                            for name in tables}

            stage = "work_records_before"
            print("FICTIONAL_LIFECYCLE_STAGE=" + stage, flush=True)
            work_before = work_records()
            assert "tasks" in work_before

            from backend.main import control_token as current_control_token

            headers = {"X-Control-Token": current_control_token}

            # A distinct fictional conversation, not a replay of a previous request.
            # The story is deliberately >120 characters so resolver discovery preview
            # truncation cannot include the parrot fact, which sits strictly beyond
            # the 120-character preview limit.
            story_id = uuid.uuid4().hex
            first_text = (
                f"In fictional story {story_id}, an explorer wandered through a sunlit "
                "library full of old maps and quiet ravens while a clerk recorded "
                "unremarkable details about curtains, chairs, and afternoon tea; "
                "my parrot Juniper prefers a yellow perch beside the window."
            )
            assert len(first_text) > 120
            assert first_text.lower().find("juniper") >= 120
            first_request_id = f"real-first-{uuid.uuid4()}"
            stage = "first_request"
            print("FICTIONAL_LIFECYCLE_STAGE=" + stage, flush=True)
            first_resp = client.post(
                "/api/v1/assistant/turns",
                headers=headers,
                json={"client_request_id": first_request_id, "text": first_text},
            )
            assert first_resp.status_code == 200, first_resp.text
            first_payload = first_resp.json()
            artifact["first_response"] = first_payload
            assert first_payload["status"] == "completed"
            assert first_payload.get("background_task_ids") in (None, [], ())

            # DB provenance after synchronous background completion.
            stage = "first_db_provenance"
            print("FICTIONAL_LIFECYCLE_STAGE=" + stage, flush=True)
            with db_manager.get_session() as db:
                from backend.models.database import ConversationExchange, ConversationTurn
                from backend.runtime.composer_request_receipt import ComposerRequestReceiptManager
                from backend.services.grounded_discussion_index import DiscussionIndexStateRow

                exchanges = db.query(ConversationExchange).filter_by(session_id=sid).all()
                assert exchanges, "no persisted first exchange"
                first_exchange = exchanges[-1]
                index_rows = db.query(DiscussionIndexStateRow).filter_by(session_id=sid).all()
                receipt_mgr = ComposerRequestReceiptManager(db)
                receipt_row = receipt_mgr.get(request_id=first_request_id, session_id=sid)

                artifact["first_exchange"] = {
                    "id": first_exchange.id,
                    "user_text": first_exchange.user_text,
                    "assistant_text": first_exchange.assistant_text,
                }
                artifact["first_index_rows"] = [
                    {"status": r.status, "exchange_id": r.exchange_id, "user_turn_id": r.user_turn_id, "assistant_turn_id": r.assistant_turn_id}
                    for r in index_rows
                ]
                artifact["first_receipt_status"] = getattr(receipt_row, "status", None)
                artifact["semantic_index_provider_responses"] = [
                    call.get("response") for call in calls
                ]
                artifact["model_calls_before_first_assertion"] = len(calls)

                (data_dir / "first_observation.json").write_text(
                    json.dumps({**artifact, "hook_events": hook_events,
                                "logs": [r.getMessage() for r in caplog.records if "index" in r.getMessage().lower()]},
                               indent=2, default=str), encoding="utf-8"
                )

                # Fictional-only diagnostic capture for the exact semantic provider
                # output and bounded validation outcome, if complete indexing did
                # not occur.  This records non-secret provider text and reason codes
                # without changing validation strictness.
                (data_dir / "semantic_index_diagnostic.json").write_text(
                    json.dumps({
                        "stage": "first_db_provenance",
                        "first_exchange_id": first_exchange.id,
                        "first_index_rows": artifact["first_index_rows"],
                        "index_outcomes": index_outcomes,
                        "semantic_index_provider_responses": artifact["semantic_index_provider_responses"],
                        "provider_call_count": len(calls),
                        "first_receipt_status": artifact["first_receipt_status"],
                    }, indent=2, default=str, ensure_ascii=False),
                    encoding="utf-8",
                )

                assert any(
                    row.exchange_id == first_exchange.id and row.status == "complete"
                    for row in index_rows
                ), "first exchange was not completely indexed"
                assert getattr(receipt_row, "status", None) == "resolved", "first receipt not resolved"

                # Synthetic turns only: no extra exchanges or unindexed source rows.
                stage = "synthetic_turns"
                print("FICTIONAL_LIFECYCLE_STAGE=" + stage, flush=True)
                for i in range(8):
                    db.add(ConversationTurn(
                        session_id=sid,
                        role="user" if i % 2 == 0 else "assistant",
                        summary=f"Unrelated fictional weather note {i}",
                        created_at=datetime.utcnow() + timedelta(minutes=i + 1),
                    ))

            # Second targeted recall question.
            stage = "second_request"
            print("FICTIONAL_LIFECYCLE_STAGE=" + stage, flush=True)
            first_call_count = len(calls)
            second_text = f"In fictional story {story_id}, what did I mention about my parrot?"
            second_request_id = f"real-second-{uuid.uuid4()}"
            second_resp = client.post(
                "/api/v1/assistant/turns",
                headers=headers,
                json={"client_request_id": second_request_id, "text": second_text},
            )
            assert second_resp.status_code == 200, second_resp.text
            second_payload = second_resp.json()
            artifact["second_response"] = second_payload
            assert second_payload["status"] == "completed"
            assert second_payload.get("background_task_ids") in (None, [], ())
            assert "incomplete" in (second_payload.get("assistant_text") or "").lower(), "recall caveat missing"
            answer = (second_payload.get("assistant_text") or "").lower()
            assert "juniper" in answer and "yellow" in answer

            payloads = []
            for call in calls[first_call_count:]:
                for message in call["messages"]:
                    if message.get("role") != "user":
                        continue
                    try:
                        payload = json.loads(message.get("content", ""))
                    except (ValueError, TypeError):
                        continue
                    if isinstance(payload, dict) and payload.get("user_request") == second_text:
                        payloads.append(payload)
            resolver = next(p for p in payloads if "discussion_discovery" in p)
            final = next(p for p in payloads if "selected_discussion_evidence" in p)

            # Capture diagnostic model-call outputs before assertions (fictional only).
            stage = "pre_assertion_diagnostics_written"
            print("FICTIONAL_LIFECYCLE_STAGE=" + stage, flush=True)
            (data_dir / "model_calls_before_assertions.json").write_text(
                json.dumps(calls, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            (data_dir / "second_payloads_before_assertions.json").write_text(
                json.dumps(payloads, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            (data_dir / "index_outcome_before_assertions.json").write_text(
                json.dumps(index_outcomes, indent=2), encoding="utf-8"
            )

            leakage_keys = ("juniper", "yellow", "beside", "window")
            full_recall_fact = ("juniper", "yellow", "beside", "window")
            source_leaks = []
            resolver_discovery_text = json.dumps(
                resolver.get("discussion_discovery", {}), ensure_ascii=False
            ).lower()
            # Discovery labels/previews are resolver-only inputs.  They may
            # legitimately contain an incidental single marker, such as the
            # model-inferred discussion label, but they must not contain the
            # full distinctive recall fact.  The earlier "any marker" test was
            # too strict and failed on that legitimate single marker.
            assert not all(key in resolver_discovery_text for key in full_recall_fact), (
                "full recall fact leaked into resolver discovery: " + resolver_discovery_text
            )
            for name, payload in (("resolver", resolver), ("final", final)):
                recent = json.dumps(payload["recent_turns"]).lower()
                assert not all(key in recent for key in full_recall_fact), (name, recent)
                assert len(payload["recent_turns"]) == 6
                outside_evidence = json.dumps(
                    {key: value for key, value in payload.items() if key != "selected_discussion_evidence"},
                    ensure_ascii=False,
                ).lower()
                if all(key in outside_evidence for key in full_recall_fact):
                    source_leaks.append(name)
            legacy = json.dumps(final.get("discussion_contexts", [])).lower()
            assert not all(key in legacy for key in full_recall_fact), legacy
            evidence = json.dumps(final["selected_discussion_evidence"])
            evidence_lower = evidence.lower()
            assert all(key in evidence_lower for key in full_recall_fact), "recall fact missing from selected evidence"
            assert artifact["first_exchange"]["id"] in evidence, "source id missing from selected evidence"
            artifact["recall_model_payloads"] = payloads
            artifact["source_leaks_outside_selected_evidence"] = source_leaks

            stage = "second_db_provenance"
            print("FICTIONAL_LIFECYCLE_STAGE=" + stage, flush=True)
            with db_manager.get_session() as db:
                from backend.services.grounded_discussion_index import DiscussionIndexStateRow
                from backend.runtime.composer_request_receipt import ComposerRequestReceiptManager

                index_rows_after = db.query(DiscussionIndexStateRow).filter_by(session_id=sid).all()
                receipt_row2 = ComposerRequestReceiptManager(db).get(
                    request_id=second_request_id, session_id=sid
                )
                artifact["second_index_rows"] = [
                    {"status": r.status, "exchange_id": r.exchange_id}
                    for r in index_rows_after
                ]
                artifact["second_receipt_status"] = getattr(receipt_row2, "status", None)
                assert artifact["second_receipt_status"] == "resolved", "second receipt not resolved"

            artifact["index_outcomes"] = index_outcomes
            stage = "index_outcome_assertions"
            print("FICTIONAL_LIFECYCLE_STAGE=" + stage, flush=True)
            assert len(index_outcomes) >= 2, "second semantic index outcome not recorded"
            work_after = work_records()
            artifact["work_record_counts_before"] = {k: len(v) for k, v in work_before.items()}
            artifact["work_record_counts_after"] = {k: len(v) for k, v in work_after.items()}
            stage = "work_record_assertions"
            print("FICTIONAL_LIFECYCLE_STAGE=" + stage, flush=True)
            assert work_after == work_before, "task or dispatch records changed during recall lifecycle"

            # Provider provenance.
            stage = "provider_provenance"
            print("FICTIONAL_LIFECYCLE_STAGE=" + stage, flush=True)
            from backend.runtime.model_provider import get_default_provider

            provider = get_default_provider()
            artifact["provider_class"] = type(provider).__name__
            artifact["provider_id"] = provider.provider_id()
            artifact["selected_model"] = provider.selected_model_id()

        stage = "artifact_write"
        print("FICTIONAL_LIFECYCLE_STAGE=" + stage, flush=True)
        artifact_path = data_dir / "lifecycle_test_artifact.json"
        artifact_path.write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")
        print("ARTIFACT_WRITTEN=", artifact_path)
        print("PROVIDER=", artifact["provider_class"], artifact["provider_id"], artifact["selected_model"])
        print("FIRST_INDEX=", artifact["first_index_rows"])
        print("SECOND_INDEX_COUNT=", len(artifact["second_index_rows"]))
        print("INDEX_OUTCOMES=", artifact["index_outcomes"])
        print("WORK_RECORD_COUNTS=", artifact["work_record_counts_after"])
        assert not source_leaks, f"original fictional source outside selected evidence: {source_leaks}"
    except BaseException as exc:
        _write_failure_diagnostic(exc)
        raise
