"""
Settings API tests for the live file-root grant path.

GET /api/v1/settings must report the same approved roots the file tools
enforce: environment defaults merged with roots granted at runtime via
PUT /api/v1/settings (no restart required).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.main import app
from backend.models.database import Base, get_db


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    import backend.main as backend_main

    previous_token = backend_main.control_token
    backend_main.control_token = "settings-test-token"
    try:
        yield TestClient(
            app,
            headers={
                "Origin": "tauri://localhost",
                "X-Control-Token": "settings-test-token",
            },
        )
    finally:
        backend_main.control_token = previous_token
        app.dependency_overrides.clear()


@pytest.fixture
def env_roots(monkeypatch):
    from backend.core.config import settings

    monkeypatch.setattr(settings, "file_roots", ["C:\\EnvRoot"])


def test_get_settings_reports_env_roots(client, db_session, env_roots):
    response = client.get("/api/v1/settings")
    assert response.status_code == 200
    assert "C:\\EnvRoot" in response.json()["file_roots"]


def test_granted_root_appears_immediately(client, db_session, env_roots):
    grant = client.put("/api/v1/settings", json={"file_roots": ["C:\\GrantedRoot"]})
    assert grant.status_code == 200

    response = client.get("/api/v1/settings")
    roots = response.json()["file_roots"]
    assert "C:\\EnvRoot" in roots
    assert "C:\\GrantedRoot" in roots


def test_revoked_root_disappears_immediately(client, db_session, env_roots):
    client.put("/api/v1/settings", json={"file_roots": ["C:\\GrantedRoot"]})
    client.put("/api/v1/settings", json={"file_roots": []})

    response = client.get("/api/v1/settings")
    assert "C:\\GrantedRoot" not in response.json()["file_roots"]


def test_invalid_root_spec_rejected(client, db_session, env_roots):
    response = client.put("/api/v1/settings", json={"file_roots": 42})
    assert response.status_code == 400


def test_root_sources_and_unrelated_update(client, db_session, env_roots, tmp_path):
    from backend.core.config import settings
    from backend.core.settings_store import get_setting
    from backend.tools.file_tools import _effective_roots

    environment = str(tmp_path / "environment")
    grant = str(tmp_path / "grant")
    settings.file_roots = [environment]
    body = client.get("/api/v1/settings").json()
    assert body["file_roots"] == [environment]
    assert body["environment_file_roots"] == [environment]
    assert body["persisted_file_roots"] == []
    assert body["effective_file_roots"] == [str(p) for p in _effective_roots([environment])]
    assert client.put("/api/v1/settings", json={"retention_days": 23}).status_code == 200
    assert get_setting(db_session, "file_roots") is None
    assert client.put("/api/v1/settings", json={"file_roots": [grant]}).status_code == 200
    body = client.get("/api/v1/settings").json()
    assert body["persisted_file_roots"] == [grant]
    assert body["file_roots"] == [environment, grant]
    assert body["effective_file_roots"] == [str(p) for p in _effective_roots([environment, grant])]
    assert client.put("/api/v1/settings", json={"file_roots": []}).status_code == 200
    assert client.get("/api/v1/settings").json()["file_roots"] == [environment]


def test_empty_roots_report_fallback(client, monkeypatch, tmp_path):
    from backend.core.config import settings

    monkeypatch.setattr(settings, "file_roots", [])
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    monkeypatch.chdir(tmp_path)
    body = client.get("/api/v1/settings").json()
    assert body["file_roots"] == []
    assert body["effective_file_roots"] == [str((tmp_path / "home").resolve()), str(tmp_path.resolve())]
    assert body["fallback_file_roots"] == body["effective_file_roots"]


def test_root_with_comma_round_trips(client, tmp_path):
    grant = str(tmp_path / "one,two")
    assert client.put("/api/v1/settings", json={"file_roots": [grant]}).status_code == 200
    assert client.get("/api/v1/settings").json()["persisted_file_roots"] == [grant]


def test_final_grant_requires_current_ack_and_enforces_transition(client, db_session, monkeypatch, tmp_path):
    import asyncio
    from contextlib import contextmanager
    from backend.api.schemas import FileReadInput
    from backend.core.config import settings
    from backend.models import database
    from backend.tools.file_tools import FileReadTool, _safe_resolve, _TargetRejected

    home = tmp_path / "home"
    grant = tmp_path / "grant"
    home.mkdir()
    grant.mkdir()
    (home / 'private.txt').write_text('isolated data')
    monkeypatch.setattr(settings, "file_roots", [])
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.chdir(tmp_path)
    @contextmanager
    def session():
        yield db_session
    monkeypatch.setattr(database, "get_db_manager", lambda: type("Manager", (), {"get_session": staticmethod(session)})())
    assert client.put('/api/v1/settings', json={'file_roots': [str(grant)]}).status_code == 200
    before = client.get('/api/v1/settings').json()
    ack = {'persisted_file_roots': before['persisted_file_roots'], 'fallback_file_roots': before['fallback_file_roots']}
    with pytest.raises(_TargetRejected):
        _safe_resolve(str(home / 'private.txt'))
    assert not asyncio.run(FileReadTool().execute(FileReadInput(path=str(home / 'private.txt')))).success
    response = client.put('/api/v1/settings', json={'file_roots': [], 'retention_days': 17})
    assert response.status_code == 400
    assert client.get('/api/v1/settings').json()['persisted_file_roots'] == [str(grant)]
    assert client.get('/api/v1/settings').json()['retention_days'] != 17
    with pytest.raises(_TargetRejected):
        _safe_resolve(str(home / 'private.txt'))
    for bad_ack in [True, {}, {**ack, 'fallback_file_roots': ['stale']}, {**ack, 'persisted_file_roots': ['stale']}]:
        assert client.put('/api/v1/settings', json={'file_roots': [], 'file_roots_fallback_acknowledgement': bad_ack}).status_code == 400
    response = client.put('/api/v1/settings', json={'file_roots': [], 'file_roots_fallback_acknowledgement': ack})
    assert response.status_code == 200
    assert _safe_resolve(str(home / 'private.txt')) == (home / 'private.txt').resolve()
    assert asyncio.run(FileReadTool().execute(FileReadInput(path=str(home / 'private.txt')))).success
    assert client.get('/api/v1/settings').json()['persisted_file_roots'] == []
    assert client.put('/api/v1/settings', json={'file_roots': [], 'file_roots_fallback_acknowledgement': ack}).status_code == 400
    assert client.put('/api/v1/settings', json={'file_roots': []}).status_code == 200


def test_stale_confirmation_rejected_after_root_or_fallback_change(client, monkeypatch, tmp_path):
    from backend.core.config import settings
    monkeypatch.setattr(settings, 'file_roots', [])
    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path / 'home')
    monkeypatch.chdir(tmp_path)
    assert client.put('/api/v1/settings', json={'file_roots': ['grant-a']}).status_code == 200
    body = client.get('/api/v1/settings').json()
    ack = {'persisted_file_roots': body['persisted_file_roots'], 'fallback_file_roots': body['fallback_file_roots']}
    assert client.put('/api/v1/settings', json={'file_roots': ['grant-b']}).status_code == 200
    assert client.put('/api/v1/settings', json={'file_roots': [], 'file_roots_fallback_acknowledgement': ack}).status_code == 400
    ack['persisted_file_roots'] = ['grant-b']
    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path / 'different-home')
    assert client.put('/api/v1/settings', json={'file_roots': [], 'file_roots_fallback_acknowledgement': ack}).status_code == 400
    monkeypatch.setattr(settings, 'file_roots', ['environment'])
    assert client.put('/api/v1/settings', json={'file_roots': [], 'file_roots_fallback_acknowledgement': ack}).status_code == 400
    assert client.put('/api/v1/settings', json={'file_roots': []}).status_code == 200
