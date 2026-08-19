"""
Database models and schema definitions for Sythion (Synth).
"""

from sqlalchemy import Column, String, Integer, Float, Text, DateTime, ForeignKey, CheckConstraint, JSON
from sqlalchemy.orm import relationship, declarative_base, Session
from datetime import datetime
import os

Base = declarative_base()


# Database manager for dependency injection
_db_manager = None


def get_db() -> Session:
    """Get database session for dependency injection.
    
    This function is used by FastAPI's Depends() to provide database sessions.
    It requires the database manager to be initialized first.
    """
    if _db_manager is None:
        raise RuntimeError("Database not initialized. Call init_database first.")
    
    with _db_manager.get_session() as session:
        yield session


def set_db_manager(manager):
    """Set the database manager instance."""
    global _db_manager
    _db_manager = manager


def get_db_manager():
    """Get the database manager instance (used by background tasks)."""
    if _db_manager is None:
        raise RuntimeError("Database not initialized. Call init_database first.")
    return _db_manager


class Task(Base):
    """Current state projection for a task."""
    
    __tablename__ = "tasks"
    
    id = Column(String, primary_key=True)
    original_request = Column(Text, nullable=False)
    idempotency_key = Column(String, unique=True, nullable=True)
    normalized_goal = Column(Text)
    state = Column(String, nullable=False)
    version = Column(Integer, default=1)
    active_action_id = Column(String)
    selected_skill_version = Column(String)
    objective_id = Column(String, ForeignKey("objectives.id"), index=True, nullable=True)
    pending_question = Column(Text)
    pending_action = Column(Text)
    final_result = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    terminal_reason = Column(String)
    
    # Events relationship
    events = relationship("TaskEvent", back_populates="task", cascade="all, delete-orphan")
    objective = relationship("Objective", back_populates="tasks")
    
    __table_args__ = (
        CheckConstraint(
            "state IN ('created', 'running', 'waiting_for_user', 'completed', 'failed', 'cancelled', 'interrupted', 'paused')",
            name="check_task_state"
        ),
    )


class TaskEvent(Base):
    """Immutable audit log for task events."""
    
    __tablename__ = "task_events"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False)
    event_type = Column(String, nullable=False)
    event_data = Column(Text, nullable=False)
    sequence_num = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationship
    task = relationship("Task", back_populates="events")


class RunnerLease(Base):
    """Single instance enforcement lease."""
    
    __tablename__ = "runner_lease"
    
    id = Column(Integer, primary_key=True)
    owner_nonce = Column(String, nullable=False)
    process_id = Column(Integer, nullable=False)
    heartbeat = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    last_task_id = Column(String)  # Track which task was being executed
    
    __table_args__ = (
        CheckConstraint("id = 1", name="check_single_lease"),
    )


class Objective(Base):
    """Durable work objective above the task ledger."""

    __tablename__ = "objectives"

    id = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    purpose = Column(Text)
    status = Column(String, default="active", nullable=False, index=True)
    priority = Column(Integer, default=3, nullable=False)
    current_summary = Column(Text)
    last_task_id = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tasks = relationship("Task", back_populates="objective")
    steps = relationship(
        "PlanStep",
        back_populates="objective",
        cascade="all, delete-orphan",
        order_by="PlanStep.step_order",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'paused', 'completed', 'abandoned')",
            name="check_objective_status"
        ),
        CheckConstraint(
            "priority >= 1 AND priority <= 5",
            name="check_objective_priority"
        ),
    )


class PlanStep(Base):
    """Structured plan step linked to a durable objective."""

    __tablename__ = "plan_steps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    objective_id = Column(String, ForeignKey("objectives.id"), nullable=False, index=True)
    step_order = Column(Integer, nullable=False)
    description = Column(Text, nullable=False)
    status = Column(String, default="pending", nullable=False, index=True)
    linked_task_id = Column(String, ForeignKey("tasks.id"), nullable=True)
    result_summary = Column(Text)
    blocker_reason = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    objective = relationship("Objective", back_populates="steps")
    linked_task = relationship("Task")

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'in_progress', 'blocked', 'completed', 'failed', 'skipped')",
            name="check_plan_step_status"
        ),
    )


class Skill(Base):
    """Declarative skill definition."""
    
    __tablename__ = "skills"
    
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text)
    category = Column(String, default="custom")
    state = Column(String, default="enabled")
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Versions relationship
    versions = relationship("SkillVersion", back_populates="skill", cascade="all, delete-orphan")


class SkillVersion(Base):
    """Versioned skill definitions."""
    
    __tablename__ = "skill_versions"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    skill_id = Column(String, ForeignKey("skills.id"), nullable=False)
    version = Column(String, nullable=False)
    definition = Column(Text, nullable=False)
    definition_hash = Column(String)
    validation_result = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationship
    skill = relationship("Skill", back_populates="versions")


class ProposedSkill(Base):
    """Skills awaiting owner review or auto-enablement.

    Owner-submitted skills always require explicit review and enablement.
    Auto-generated proposals start as `proposed`; once their verified trace
    count reaches the configured threshold (default 5) they are re-validated
    and auto-enabled as live, versioned skills. Proposed skills are never
    executed.
    """

    __tablename__ = "proposed_skills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    skill_id = Column(String)  # requested live skill id (owner-submitted); None for auto-generated
    definition = Column(Text, nullable=False)
    trace_ids = Column(Text)  # JSON list of source task IDs (empty for owner-submitted)
    status = Column(String, default="proposed")  # proposed | enabled | rejected
    validation_errors = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    decided_at = Column(DateTime)


class ProposedTool(Base):
    """Model-authored tool proposals awaiting validation and owner approval.

    Every tool must pass validation, sandbox testing, and owner approval before
    it becomes a live, registered tool. Proposed tools are never executed
    directly — they are tested in an isolated sandbox first.
    """

    __tablename__ = "proposed_tools"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text, nullable=False)
    input_schema_json = Column(Text, nullable=False)  # Pydantic schema as JSON
    implementation = Column(Text, nullable=False)  # Python source code
    validation_errors = Column(Text)  # JSON list of validation errors
    sandbox_result = Column(Text)  # JSON sandbox test result
    status = Column(String, default="proposed")  # proposed | sandbox_passed | enabled | rejected
    enabled_mode = Column(String)  # guarded | autonomous (after N uses)
    successful_uses = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    decided_at = Column(DateTime)
    enabled_at = Column(DateTime)


class PurgeReceipt(Base):
    """Non-sensitive receipt for a completed-record purge.

    Receipts contain only identity and timing metadata (task ID, reason, purge
    time, event count). They never contain task content or request text.
    """

    __tablename__ = "purge_receipts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String, nullable=False)
    reason = Column(String)
    purged_at = Column(DateTime, default=datetime.utcnow)
    event_count = Column(Integer, default=0)


class SettingsMetadata(Base):
    """Non-secret settings storage."""
    
    __tablename__ = "settings_metadata"
    
    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MemoryEntry(Base):
    """Persistent real-time memory storage for user preferences, facts, and profile info."""

    __tablename__ = "memory_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    category = Column(String, default="user_preference", index=True)
    key = Column(String, nullable=False, unique=True, index=True)
    value = Column(Text, nullable=False)
    source_task_id = Column(String, nullable=True)
    access_count = Column(Integer, default=0)
    last_accessed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MemoryEdge(Base):
    """Directed relationship between two memory keys or facts (graph edge).

    A link says "source_key --relation--> target_key" (e.g.
    user_name --prefers--> preferred_editor). target_key may reference an
    existing memory_entries.key or be free text (a stated fact that has no
    stored entry of its own). No strict FK integrity is enforced so both
    endpoint styles are allowed; both columns are indexed for graph walks.

    `status` tracks the edge lifecycle like proposed_skills.status:
    "active" (live), "stale" (decayed below the review threshold; surfaced
    for owner review, never auto-deleted), or "removed" (owner-reviewed
    removal). Only the review endpoints move an edge to "removed"; decay
    itself never deletes. Existing DBs gain the column via the ALTER TABLE
    migration in db_manager.py.
    """

    __tablename__ = "memory_edges"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_key = Column(String, nullable=False, index=True)
    relation = Column(String, nullable=False)
    target_key = Column(String, nullable=False, index=True)
    weight = Column(Float, default=1.0)
    status = Column(String, default="active", index=True)
    source_task_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MemoryContextLog(Base):
    """Per-decision memory-context injection stats (validation dump source).

    One row per model context build recording how much persistent memory was
    available versus injected (relevant entries + one-hop linked neighbors +
    links surfaced). Written by model_engine._build_context and read by the
    validation dump report to summarize injection behavior over recent runs.
    Created by Base.metadata.create_all like the other tables.
    """

    __tablename__ = "memory_context_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String, nullable=False, index=True)
    available = Column(Integer, default=0)
    relevant = Column(Integer, default=0)
    connected = Column(Integer, default=0)
    links = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class ConversationTurn(Base):
    """Compressed summary of a completed task for session continuity (episodic memory).

    Each completed task produces two rows: one role='user' (the request) and
    one role='assistant' (the result).  This mirrors a natural conversation
    transcript while keeping token cost minimal (~25 tokens per turn).
    """

    __tablename__ = "conversation_turns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, nullable=False, index=True)
    task_id = Column(String, nullable=False)
    role = Column(String, default="user")  # "user" or "assistant"
    summary = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class ConversationState(Base):
    """Tracks conversational context for a session.

    Stores the current topic, recent topics, open threads, emotional context,
    and conversation stage. Updated after each task completion.
    """

    __tablename__ = "conversation_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, nullable=False, index=True, unique=True)
    active_topic = Column(String, nullable=True)
    previous_topics = Column(JSON, default=list)  # ["topic1", "topic2", ...]
    open_threads = Column(JSON, default=list)  # ["unresolved topic1", ...]
    emotional_context = Column(String, default="neutral")  # neutral|positive|negative|frustrated|excited|confused
    conversation_stage = Column(String, default="opening")  # opening|developing|deepening|closing
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
