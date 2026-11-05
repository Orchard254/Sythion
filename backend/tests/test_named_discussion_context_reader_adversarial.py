from datetime import datetime, timedelta

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

try:
    from backend.models.database import (
        Base, ConversationExchange, ConversationTaskAssociation, ConversationTurn,
        DiscussionContext, DiscussionContextMembership, Task,
    )
    from backend.services.discussion_contexts import read_session_discussion_contexts
except ImportError:
    from models.database import (
        Base, ConversationExchange, ConversationTaskAssociation, ConversationTurn,
        DiscussionContext, DiscussionContextMembership, Task,
    )
    from services.discussion_contexts import read_session_discussion_contexts


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'named-context-adversarial.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr("backend.core.session._EXPLICIT_SESSION_ID", "same-session", raising=False)
    try:
        monkeypatch.setattr("core.session._EXPLICIT_SESSION_ID", "same-session", raising=False)
    except Exception:
        pass
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


def _seed_context(db, suffix, session, *, membership=True, foreign_exchange=False,
                 exact_task=True, task_id=None, created_at=None):
    if created_at is None:
        created_at = datetime.utcnow()
    exchange_id = f"exchange-{suffix}"
    exchange_session = "other-session" if foreign_exchange else session
    db.add(ConversationExchange(id=exchange_id, session_id=exchange_session,
                               user_text=f"request {suffix}", assistant_text=f"reply {suffix}"))
    turn_user_id = 100000 + (uuid.uuid4().int % 900000)
    turn_assistant_id = turn_user_id + 1
    db.add_all([
        ConversationTurn(id=turn_user_id, session_id=exchange_session, role="user", summary=f"request {suffix}"),
        ConversationTurn(id=turn_assistant_id, session_id=exchange_session, role="assistant", summary=f"reply {suffix}"),
    ])
    context_id = f"ctx-{suffix}"
    db.add(DiscussionContext(id=context_id, session_id=session, name=f"Context {suffix}",
                           name_source="initial_request_excerpt", created_at=created_at))
    if membership:
        db.add(DiscussionContextMembership(context_id=context_id, exchange_id=exchange_id,
                                         user_turn_id=turn_user_id, assistant_turn_id=turn_assistant_id))
        if task_id is not None:
            if exact_task:
                db.add(ConversationTaskAssociation(exchange_id=exchange_id, task_id=task_id,
                                                 user_turn_id=turn_user_id,
                                                 assistant_turn_id=turn_assistant_id))
            else:
                db.add(ConversationTaskAssociation(exchange_id=exchange_id, task_id=task_id,
                                                 user_turn_id=turn_user_id + 999,
                                                 assistant_turn_id=turn_assistant_id))
    if task_id is not None and task_id not in [t.id for t in db.query(Task).all()]:
        db.add(Task(id=task_id, original_request=f"task request {suffix}", state="running", version=3))
    db.commit()
    return context_id


def test_orphan_context_without_exact_membership_is_excluded(db):
    session = "same-session"
    db.add(DiscussionContext(id="orphan-ctx", session_id=session, name="Orphan",
                           name_source="initial_request_excerpt", created_at=datetime.utcnow()))
    db.commit()
    rows = read_session_discussion_contexts(db, session, limit=20)
    assert all(row.context_id != "orphan-ctx" for row in rows)


def test_foreign_exchange_membership_is_excluded_before_limit(db):
    session = "same-session"
    _seed_context(db, "foreign", session, foreign_exchange=True)
    _seed_context(db, "valid", session)
    rows = read_session_discussion_contexts(db, session, limit=20)
    names = [row.name for row in rows]
    assert "Context valid" in names
    assert "Context foreign" not in names


def test_bogus_task_linkage_is_not_materialized(db):
    session = "same-session"
    _seed_context(db, "bogus-task", session, task_id="task-bogus", exact_task=False)
    rows = read_session_discussion_contexts(db, session, limit=20)
    assert len(rows) == 1
    assert rows[0].task_id is None
    assert rows[0].task_state is None
    assert rows[0].task_version is None


def test_valid_rows_are_not_hidden_by_filtered_foreign_memberships(db):
    session = "same-session"
    _seed_context(db, "foreign-a", session, foreign_exchange=True,
                 created_at=datetime.utcnow() - timedelta(minutes=5))
    _seed_context(db, "foreign-b", session, foreign_exchange=True,
                 created_at=datetime.utcnow() - timedelta(minutes=4))
    for i in range(20):
        _seed_context(db, f"valid-{i:02d}", session,
                     created_at=datetime.utcnow() - timedelta(seconds=i))
    rows = read_session_discussion_contexts(db, session, limit=20)
    assert len(rows) == 20
    assert all(row.name.startswith("Context valid-") for row in rows)
    assert not any("foreign" in row.name for row in rows)


def test_foreign_session_context_is_excluded(db):
    session = "same-session"
    _seed_context(db, "other-session", "other-session")
    rows = read_session_discussion_contexts(db, session, limit=20)
    assert all(row.session_id == session for row in rows)
    assert all(row.name != "Context other-session" for row in rows)
