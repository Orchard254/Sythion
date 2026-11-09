"""Offline regression coverage for durable conversation background-task resolution."""

import uuid
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

try:
    from backend.models.database import Base, ConversationExchange, ConversationTaskAssociation, Task, ConversationTurn
    from backend.api.routes import list_conversations
except ImportError:  # pragma: no cover
    from models.database import Base, ConversationExchange, ConversationTaskAssociation, Task, ConversationTurn
    from api.routes import list_conversations


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.mark.asyncio
async def test_list_conversations_background_tasks_resolve_durable_tasks_and_omit_dangling_associations(db_session):
    now = datetime.utcnow()
    durable_exchange = ConversationExchange(
        id="exchange-durable",
        session_id="session-1",
        user_text="durable user",
        assistant_text="durable assistant",
        created_at=now,
    )
    dangling_exchange = ConversationExchange(
        id="exchange-dangling",
        session_id="session-1",
        user_text="dangling user",
        assistant_text="dangling assistant",
        created_at=now,
    )
    durable_task = Task(
        id="durable-task",
        original_request="durable work",
        state="completed",
        final_result="done",
        terminal_reason=None,
        created_at=now,
        updated_at=now,
    )
    db_session.add_all([durable_exchange, dangling_exchange, durable_task])
    db_session.commit()
    db_session.add_all([
        ConversationTaskAssociation(
            exchange_id=durable_exchange.id,
            task_id=durable_task.id,
            user_turn_id=1001,
            assistant_turn_id=1002,
        ),
        ConversationTaskAssociation(
            exchange_id=dangling_exchange.id,
            task_id="dangling-task",
            user_turn_id=2001,
            assistant_turn_id=2002,
        ),
    ])
    db_session.commit()

    result = await list_conversations(limit=50, cursor=None, db=db_session)
    by_id = {conversation["id"]: conversation for conversation in result["conversations"]}
    assert set(by_id) == {"exchange-durable", "exchange-dangling"}

    durable_result = by_id["exchange-durable"]
    assert durable_result["background_task_ids"] == ["durable-task"]
    assert len(durable_result["background_tasks"]) == 1
    assert durable_result["background_tasks"][0]["id"] == "durable-task"
    assert durable_result["background_tasks"][0]["state"] == "completed"

    dangling_result = by_id["exchange-dangling"]
    assert dangling_result["background_task_ids"] == ["dangling-task"]
    # A dangling association has no durable Task row, so background_tasks omits
    # it rather than fabricating task state.
    assert dangling_result["background_tasks"] == []