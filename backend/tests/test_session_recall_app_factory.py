"""Reader coverage using the application's autoflush-disabled session factory."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base, ConversationExchange
from backend.services.session_recall import read_session_recall_candidates


def test_app_session_factory_no_autoflush_no_spill(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'app-factory.db'}")
    Base.metadata.create_all(engine)
    try:
        Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        with Session() as db:
            db.add(ConversationExchange(id="e1", session_id="session-a", user_text="U", assistant_text="R"))
            db.commit()
            pending = ConversationExchange(id="pending", session_id="session-a", user_text="pending", assistant_text="pending")
            db.add(pending)
            snapshot = read_session_recall_candidates(db, ["session-a"])
            assert [candidate.exchange_id for candidate in snapshot.candidates] == ["e1"]
            assert pending in db.new
    finally:
        engine.dispose()