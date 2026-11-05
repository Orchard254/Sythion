import os
import tempfile
import threading
import time

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.core.session import set_current_session_id
from backend.models.database import Base, ConversationExchange, ConversationTurn
from backend.services.grounded_discussions import Base as GroundedBase, GroundedDiscussionManager
from backend.services.grounded_discussion_index import (
    DiscussionIndexManager,
    INDEX_STATUS_COMPLETE,
)
from backend.services.grounded_discussion_indexed_discovery import (
    read_session_indexed_discussion_discovery,
)


def test_concurrent_writer_does_not_change_snapshot():
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{db}", future=True)
    eng2 = create_engine(f"sqlite:///{db}", future=True)
    with engine.begin() as c:
        c.execute(text("PRAGMA journal_mode=WAL"))
    Base.metadata.create_all(engine)
    GroundedBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    session = Session()
    sid = "session-concurrent"
    set_current_session_id(sid)
    uid, aid = 11, 12
    try:
        session.add(ConversationTurn(id=uid, session_id=sid, role="user", summary="u"))
        session.add(ConversationTurn(id=aid, session_id=sid, role="assistant", summary="a"))
        session.add(ConversationExchange(id="ex-1", session_id=sid, user_text="u", assistant_text="a"))
        session.commit()
        mgr = DiscussionIndexManager(session, db=session)
        row, _ = mgr.receive(sid, "ex-1", uid, aid, input_digest="digest", status=INDEX_STATUS_COMPLETE)
        mgr.transition(row.idempotency_key, INDEX_STATUS_COMPLETE, commit=True)
        gm = GroundedDiscussionManager(session)
        gm.create_discussion(
            session_id=sid,
            label="Approved Topic",
            label_provenance="human_stated",
            member_exchanges=[{"exchange_id": "ex-1", "user_turn_id": uid, "assistant_turn_id": aid}],
        )
        session.commit()
        def writer():
            time.sleep(0.2)
            s2 = sessionmaker(bind=eng2, autoflush=False, future=True)()
            try:
                s2.add(ConversationTurn(id=99, session_id=sid, role="user", summary="u2"))
                s2.add(ConversationTurn(id=100, session_id=sid, role="assistant", summary="a2"))
                s2.add(ConversationExchange(id="ex-2", session_id=sid, user_text="u2", assistant_text="a2"))
                s2.commit()
            finally:
                s2.close()

        th = threading.Thread(target=writer)
        th.start()
        result = read_session_indexed_discussion_discovery(
            session,
            sid,
            discovery_max_payload_bytes=100000,
            coverage_max_payload_bytes=100000,
        )
        th.join()
        assert result.available_count == 1
    finally:
        session.close()
        engine.dispose()
        eng2.dispose()
        for path in (db, db + "-wal", db + "-shm"):
            try:
                os.remove(path)
            except OSError:
                pass
