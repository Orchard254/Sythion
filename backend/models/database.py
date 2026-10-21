"""
Database models and schema definitions for Sythion (Synth).
"""

from sqlalchemy import (
    Column, String, Integer, Float, Text, DateTime, ForeignKey,
    CheckConstraint, Index, JSON, Boolean, text as _sql_text,
)
from sqlalchemy.orm import relationship, declarative_base, Session, backref
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
    version = Column(Integer, default=1, doc="Audit counter: incremented on every state change. Not used for optimistic concurrency control â€” the runner lease provides that guarantee.")
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


class TaskEventSequence(Base):
    """Per-task monotonic counter used to allocate gapless sequence numbers
    for ``TaskEvent`` rows under concurrent appends (item 008).

    ``next_seq`` is the value to assign to the *next* appended event; it is
    incremented atomically via an UPSERT so concurrent writers cannot allocate
    the same number. The row participates in the caller's transaction and
    rolls back with it, preserving strict transactional participation.
    """

    __tablename__ = "task_event_sequences"

    task_id = Column(String, primary_key=True)
    next_seq = Column(Integer, nullable=False)


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
    # Explicit completion criteria (verifiable conditions)
    completion_criteria = Column(Text)
    # Whether explicit completion_criteria has been verified
    criteria_verified = Column(Boolean, default=False, nullable=False)
    # Hierarchical objectives: parent objective for sub-work
    parent_objective_id = Column(String, ForeignKey("objectives.id"), nullable=True, index=True)
    # Phase 4 fairness: when this goal last received an execution slot.
    # Deterministic same-priority round-robin key (None = never served).
    last_served_at = Column(DateTime, nullable=True)
    # Phase 4.5 fairness counter: monotonic service count (round-robin key).
    times_served = Column(Integer, default=0, nullable=False)
    # Phase 4.5 fairness correction: persisted failure backoff.
    consecutive_failed_sessions = Column(Integer, default=0, nullable=False)
    backoff_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tasks = relationship("Task", back_populates="objective")
    steps = relationship(
        "PlanStep",
        back_populates="objective",
        cascade="all, delete-orphan",
        order_by="PlanStep.step_order",
    )
    sub_objectives = relationship(
        "Objective",
        backref=backref("parent_objective", remote_side=[id]),
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'paused', 'completed', 'failed', 'abandoned')",
            name="check_objective_status"
        ),
        CheckConstraint(
            "priority >= 1 AND priority <= 5",
            name="check_objective_priority"
        ),
    )


class GoalPlan(Base):
    """A versioned, durable plan for a goal (Phase 2 planning layer).

    Plans are immutable once superseded: replanning creates a NEW version and
    marks the previous one ``superseded``; history remains fully auditable.
    """

    __tablename__ = "goal_plans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    goal_id = Column(String, ForeignKey("objectives.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    status = Column(String, default="active", nullable=False)  # active | superseded | rejected
    source = Column(String, default="user")  # user | model | deterministic
    reason = Column(Text)  # why this plan exists (initial intent or replan context)
    created_at = Column(DateTime, default=datetime.utcnow)

    steps = relationship(
        "PlanStep",
        back_populates="plan",
        order_by="PlanStep.step_order",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'superseded', 'rejected')",
            name="check_goal_plan_status"
        ),
    )


class PlanStep(Base):
    """Structured plan step linked to a durable objective and (optionally) a
    versioned :class:`GoalPlan`.

    Phase 2 state machine (authoritative for plan-backed steps):
    pending â†’ ready â†’ running â†’ completed | failed; blocked â†” ready;
    any open state â†’ cancelled. Legacy free-form statuses
    (``in_progress``/``skipped``) remain readable for pre-plan rows.
    """

    __tablename__ = "plan_steps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    objective_id = Column(String, ForeignKey("objectives.id"), nullable=False, index=True)
    plan_id = Column(Integer, ForeignKey("goal_plans.id"), nullable=True, index=True)
    step_key = Column(String)  # stable identity within a plan (validated unique per plan)
    step_order = Column(Integer, nullable=False)
    description = Column(Text, nullable=False)
    status = Column(String, default="pending", nullable=False, index=True)
    priority = Column(Integer, default=3, nullable=False)
    depends_on = Column(Text)  # JSON list of step_keys this step depends on
    attempt_count = Column(Integer, default=0, nullable=False)
    linked_task_id = Column(String, ForeignKey("tasks.id"), nullable=True)
    evidence_task_id = Column(String, ForeignKey("tasks.id"), nullable=True)
    result_summary = Column(Text)
    failure_reason = Column(Text)
    blocker_reason = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    objective = relationship("Objective", back_populates="steps")
    plan = relationship("GoalPlan", back_populates="steps")
    linked_task = relationship("Task", foreign_keys=[linked_task_id])
    evidence_task = relationship("Task", foreign_keys=[evidence_task_id])

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'ready', 'running', 'in_progress', 'blocked', "
            "'completed', 'failed', 'cancelled', 'skipped')",
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
    """Model-authored tool proposals awaiting validation and owner review.

    Phase 0 protected runtime contract: proposals are validated with static
    analysis only and stored as INERT review artifacts. The implementation
    source is never compiled or executed in the core process, and approval
    records a review decision â€” it does not register a live tool. (The
    ``sandbox_result`` column name is legacy; it stores the static-check
    result JSON.) Executable enablement requires a future isolated boundary.
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


class MainAgent(Base):
    """Persistent identity of the Sythion main agent (Phase 4).

    Singleton row (id=1). The agent_id is minted once and survives restarts,
    model changes, and goal/task turnover. Agent state is a SEPARATE domain
    from Goal state and Task state.
    """

    __tablename__ = "main_agents"

    id = Column(Integer, primary_key=True)
    agent_id = Column(String, nullable=False, unique=True)
    name = Column(String, default="Sythion", nullable=False)
    state = Column(String, default="active", nullable=False, index=True)
    last_wake_at = Column(DateTime)
    last_wake_reason = Column(String)
    last_session_outcome = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "state IN ('active', 'paused', 'degraded', 'shutdown')",
            name="check_main_agent_state",
        ),
        CheckConstraint("id = 1", name="check_single_main_agent"),
    )


class AgentWake(Base):
    """Durable wake record â€” one bounded background execution attempt.

    Deduplication: ``wake_key`` is UNIQUE. A repeated event with the same key
    cannot create a second session (insert conflicts are treated as
    'already handled').
    """

    __tablename__ = "main_agent_wakes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(String, nullable=False, index=True)
    wake_key = Column(String, nullable=False, unique=True)
    trigger_type = Column(String, nullable=False)  # application.startup | task.completed | ...
    trigger_reference = Column(String)
    # Phase 4.5 durable state machine. The row is PERSISTED FIRST at
    # submission time (status=PENDING); the in-memory queue is only an
    # optimization. Authoritative states:
    #   PENDING -> RUNNING -> COMPLETED | SKIPPED | FAILED
    #   PENDING/RUNNING -> DEFERRED (rare; e.g. superseded by newer signal)
    status = Column(String, default="PENDING", nullable=False, index=True)
    coalesce_key = Column(String, nullable=False, index=True,
                          default="background_work")
    claimed_at = Column(DateTime)
    attempts = Column(Integer, default=0, nullable=False)
    merged_into = Column(Integer)  # wake id that absorbed this one (coalescing)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime)
    outcome = Column(String)  # human-readable summary (lowercase)
    steps_attempted = Column(Integer, default=0)
    steps_completed = Column(Integer, default=0)
    stop_reason = Column(String)


class AgentRecord(Base):
    """Phase 9 Agent abstraction — one row per logical agent.

    Hierarchy: MAIN (unique per profile) → SPECIALIST / TEMPORARY_SPECIALIST
    (bounded workers, parent_agent_id always points at the Main Agent).
    Specialists are never peers of the Main Agent; max_specialist_depth = 1.

    Lifecycle states live in backend/agents/state.py (explicit transition
    table). This is a SEPARATE domain from main_agents (identity/availability)
    and from Goal/Task state.
    """

    __tablename__ = "agents"

    id = Column(String, primary_key=True)          # uuid; MAIN uses agent_id
    type = Column(String, nullable=False, index=True)
    role = Column(String)                          # SpecialistRole value; None for MAIN
    state = Column(String, nullable=False, index=True)
    parent_agent_id = Column(String, nullable=True, index=True)
    definition_id = Column(String, nullable=True, index=True)
    goal_id = Column(String, nullable=True, index=True)   # parent goal/task scope
    config_json = Column(Text)                     # budgets, ttl, model requirements
    expires_at = Column(DateTime)                  # temporary specialists expire
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "type IN ('MAIN', 'SPECIALIST', 'TEMPORARY_SPECIALIST')",
            name="check_agent_type",
        ),
        CheckConstraint(
            "state IN ('CREATED','READY','RUNNING','WAITING','COMPLETED',"
            "'FAILED','CANCELLED','EXPIRED')",
            name="check_agent_lifecycle",
        ),
    )


class SpecialistDefinition(Base):
    """Reusable specialist profile (role template), separate from execution
    instances. The Main Agent instantiates temporary workers FROM definitions.
    """

    __tablename__ = "specialist_definitions"

    id = Column(String, primary_key=True)          # e.g. "analysis"
    role = Column(String, nullable=False, unique=True)
    name = Column(String, nullable=False)
    description = Column(Text)
    capabilities_json = Column(Text, nullable=False, default="[]")
    model_requirements_json = Column(Text, nullable=False, default="{}")
    enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Delegation(Base):
    """Durable specialist execution record and its lifecycle state machine.

    PROPOSED → VALIDATED → ASSIGNED → RUNNING → RESULT_READY → VERIFIED →
    INTEGRATED; failures RUNNING→FAILED/CANCELLED/EXPIRED (see
    backend/agents/state.py for the authoritative table). Raw model output can
    never reach INTEGRATED: only VERIFIED results are integrated by the Main
    Agent.
    """

    __tablename__ = "delegations"

    id = Column(String, primary_key=True)
    parent_agent_id = Column(String, nullable=False, index=True)
    specialist_agent_id = Column(String, nullable=True, index=True)
    definition_id = Column(String, nullable=True)
    specialist_role = Column(String, nullable=False)
    goal_id = Column(String, nullable=True, index=True)
    task_id = Column(String, nullable=True, index=True)   # parent runtime task
    specialist_task_id = Column(String, nullable=True, index=True)  # kernel execution record
    objective = Column(Text, nullable=False)
    status = Column(String, nullable=False, index=True)
    attempt = Column(Integer, default=0, nullable=False)
    constraints_json = Column(Text)
    expected_output = Column(Text)
    budget_json = Column(Text)
    context_packet_json = Column(Text)                    # bounded scoped context
    result_json = Column(Text)                            # structured SpecialistResult
    integration_outcome = Column(String)                  # ACCEPT/REJECT/REVISE/CONTINUE/ASK_USER
    failure_reason = Column(Text)
    # Phase 9.75: sha256[:16] of parent|role|objective while the delegation
    # is active; NULL once terminal. Backs the cross-process duplicate
    # idempotency boundary (partial unique index below).
    fingerprint = Column(String, nullable=True, index=True)
    # Phase 10: plan membership + dependency graph + per-delegation isolation
    plan_id = Column(String, nullable=True, index=True)
    depends_on_json = Column(Text)  # JSON list of delegation ids this delegation depends on
    priority = Column(Integer, default=3, nullable=False)
    required_result_from = Column(Text)  # JSON list of delegation ids whose VERIFIED result is required
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "status IN ('PROPOSED','VALIDATED','ASSIGNED','RUNNING',"
            "'RESULT_READY','VERIFIED','INTEGRATED','FAILED','CANCELLED','EXPIRED')",
            name="check_delegation_status",
        ),
        CheckConstraint(
            "integration_outcome IS NULL OR integration_outcome IN "
            "('ACCEPT','REJECT','REVISE','CONTINUE','ASK_USER')",
            name="check_integration_outcome",
        ),
        # Cross-process duplicate admission guard: at most ONE ACTIVE
        # delegation per fingerprint. The partial predicate releases the
        # slot when a row leaves the active states (including via UPDATE).
        Index(
            "uq_delegations_fingerprint_active",
            "fingerprint",
            unique=True,
            sqlite_where=_sql_text(
                "fingerprint IS NOT NULL AND status IN "
                "('PROPOSED','VALIDATED','ASSIGNED','RUNNING',"
                "'RESULT_READY','VERIFIED')"
            ),
        ),
    )


class DelegationPlan(Base):
    """Durable multi-specialist plan (Phase 10).

    Owns a set of specialist delegations and their dependency graph.
    Status: DRAFT→VALIDATED→ACTIVE→COMPLETED/FAILED/CANCELLED.
    Completion policy determines when the plan is done.
    """

    __tablename__ = "delegation_plans"

    id = Column(String, primary_key=True)
    parent_agent_id = Column(String, nullable=False, index=True)
    parent_goal_id = Column(String, nullable=True, index=True)
    parent_task_id = Column(String, nullable=True, index=True)
    status = Column(String, nullable=False, index=True, default="DRAFT")
    version = Column(Integer, default=1, nullable=False)
    objective = Column(Text, nullable=False)
    completion_policy = Column(String, default="ALL_REQUIRED", nullable=False)
    specialist_count = Column(Integer, default=0, nullable=False)
    parallelism_policy_json = Column(Text)
    budget_json = Column(Text)
    result_summary_json = Column(Text)
    failure_reason = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','VALIDATED','ACTIVE','COMPLETED','FAILED','CANCELLED')",
            name="check_delegation_plan_status",
        ),
        CheckConstraint(
            "completion_policy IN ('ALL_REQUIRED','MAJORITY','OPTIONAL_RESULTS')",
            name="check_completion_policy",
        ),
    )


class SpecialistExecutionSlot(Base):
    """Isolated execution slot for one specialist (Phase 10 parallelism).

    Profile/Orchestrator lease remains authoritative; each specialist slot
    is a child ownership record under the plan. Used to enforce
    max_parallel_specialists and to isolate cancellation/budget per specialist.
    """

    __tablename__ = "specialist_execution_slots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_id = Column(String, nullable=False, index=True)
    delegation_id = Column(String, nullable=False, index=True)
    owner_nonce = Column(String, nullable=False)
    acquired_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    status = Column(String, default="ACTIVE", nullable=False)  # ACTIVE/RELEASED/EXPIRED


class StrategyOutcome(Base):
    """Durable learning evidence (Phase 11).

    One row per verified execution outcome, traceable to real evidence via
    evidence_refs. Never influences Runtime policy directly; consumed only by
    StrategySelector as historical evidence.
    """

    __tablename__ = "strategy_outcomes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String, nullable=True, index=True)
    goal_id = Column(String, nullable=True, index=True)
    task_class = Column(String, nullable=False, index=True)
    strategy_type = Column(String, nullable=False, index=True)
    role = Column(String, nullable=True, index=True)
    model_id = Column(String, nullable=True)
    outcome = Column(String, nullable=False, index=True)
    verification = Column(String, nullable=True)
    user_feedback = Column(String, nullable=True)
    cost_json = Column(Text, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    attempts = Column(Integer, default=1, nullable=False)
    evidence_refs_json = Column(Text, nullable=True)
    evidence_fingerprint = Column(String, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    expires_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "outcome IN ('SUCCESS_VERIFIED','SUCCESS_PARTIAL','FAILED',"
            "'FAILED_VERIFICATION','CANCELLED','TIMED_OUT','USER_CORRECTED',"
            "'USER_REJECTED','UNAVAILABLE')",
            name="check_strategy_outcome_value",
        ),
        CheckConstraint(
            "strategy_type IN ('DIRECT_EXECUTION','DIRECT_ANSWER','DELEGATE_RESEARCH',"
            "'DELEGATE_ANALYSIS','DELEGATE_CODING','DELEGATE_REVIEW',"
            "'DELEGATE_PLANNING_SUPPORT','MULTI_SPECIALIST','ASK_CLARIFICATION')",
            name="check_strategy_type_value",
        ),
        Index(
            "uq_strategy_outcomes_fingerprint",
            "evidence_fingerprint",
            unique=True,
            sqlite_where=_sql_text("evidence_fingerprint IS NOT NULL"),
        ),
    )


class StrategyExperiment(Base):
    """Controlled experiment placeholder (Phase 11, bounded)."""

    __tablename__ = "strategy_experiments"

    id = Column(String, primary_key=True)
    strategy_a = Column(String, nullable=False)
    strategy_b = Column(String, nullable=False)
    task_class = Column(String, nullable=False)
    constraints_json = Column(Text)
    sample_limit = Column(Integer, default=10, nullable=False)
    risk_limit = Column(String, default="LOW", nullable=False)
    status = Column(String, default="DRAFT", nullable=False)
    outcome_json = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','ACTIVE','COMPLETED','CANCELLED')",
            name="check_strategy_experiment_status",
        ),
    )


# ---------------------------------------------------------------------------
# Phase 12 — Planning / Replanning learning (§4-5, §15-18)
# ---------------------------------------------------------------------------

class PlanningOutcome(Base):
    """Durable planning evidence (§4-5). Preserves raw task/plan evidence.

    One row per verified planning lifecycle (plan creation, replan, terminal goal).
    Never replaces StrategyOutcome — scoped to planning/replanning policy.
    """

    __tablename__ = "planning_outcomes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    goal_id = Column(String, nullable=True, index=True)
    plan_id = Column(Integer, nullable=True, index=True)
    planning_strategy = Column(String, nullable=False, index=True)
    task_class = Column(String, nullable=False, index=True)
    goal_type = Column(String, nullable=True)
    initial_plan_steps = Column(Integer, nullable=True)
    final_plan_steps = Column(Integer, nullable=True)
    retries = Column(Integer, default=0, nullable=False)
    replans = Column(Integer, default=0, nullable=False)
    verified_success = Column(Boolean, nullable=True)
    total_cost_json = Column(Text, nullable=True)
    total_latency_ms = Column(Integer, nullable=True)
    user_feedback = Column(String, nullable=True)
    evidence_refs_json = Column(Text, nullable=True)
    evidence_fingerprint = Column(String, nullable=True, index=True)
    # replan-specific
    original_plan_id = Column(Integer, nullable=True)
    new_plan_id = Column(Integer, nullable=True)
    failure_reason = Column(Text, nullable=True)
    replan_trigger = Column(String, nullable=True, index=True)
    # outcome classification mirrors StrategyOutcome but for planning
    outcome = Column(String, nullable=False, index=True, default="SUCCESS_PARTIAL")
    retry_vs_replan = Column(String, nullable=True)  # RETRY | REPLAN | null
    dependency_complexity = Column(Integer, nullable=True)
    risk_level = Column(String, nullable=True)
    provenance = Column(String, nullable=False, default="LEGACY", index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    expires_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "outcome IN ('SUCCESS_VERIFIED','SUCCESS_PARTIAL','FAILED',"
            "'FAILED_VERIFICATION','CANCELLED','TIMED_OUT','USER_CORRECTED',"
            "'USER_REJECTED','UNAVAILABLE')",
            name="check_planning_outcome_value",
        ),
        CheckConstraint(
            "planning_strategy IN ('DIRECT_PLAN','MINIMAL_PLAN','DECOMPOSE_PLAN',"
            "'DEPENDENCY_AWARE_PLAN','DELEGATED_PLAN','REPLAN_AFTER_FAILURE','REPLAN_BEFORE_RETRY')",
            name="check_planning_strategy_value",
        ),
        CheckConstraint(
            "provenance IN ('REAL_EXECUTION','SYNTHETIC_TEST','IMPORTED','SIMULATED','LEGACY')",
            name="check_planning_provenance_value",
        ),
        Index(
            "uq_planning_outcomes_fingerprint",
            "evidence_fingerprint",
            unique=True,
            sqlite_where=_sql_text("evidence_fingerprint IS NOT NULL"),
        ),
    )


class PlanningPolicy(Base):
    """Promotable planning policy (§17-18). Earned right to control production planning."""

    __tablename__ = "planning_policies"

    id = Column(String, primary_key=True)
    policy_key = Column(String, nullable=False, index=True)  # task_class:strategy
    planning_strategy = Column(String, nullable=False)
    task_class = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, index=True, default="OBSERVED")
    evidence_summary_json = Column(Text, nullable=True)
    previous_policy_json = Column(Text, nullable=True)
    promoted_at = Column(DateTime, nullable=True)
    rollback_reason = Column(Text, nullable=True)
    policy_version = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "status IN ('OBSERVED','SUPPORTED','SHADOW_VALIDATED','OWNER_APPROVED','ACTIVE','ROLLED_BACK')",
            name="check_planning_policy_status",
        ),
    )


class PlanningShadowDecision(Base):
    """Shadow vs actual planning policy comparison (§15)."""

    __tablename__ = "planning_shadow_decisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    goal_id = Column(String, nullable=True, index=True)
    task_class = Column(String, nullable=False, index=True)
    failure_context = Column(Text, nullable=True)
    actual_policy = Column(String, nullable=False)
    shadow_policy = Column(String, nullable=False)
    would_diverge = Column(Boolean, default=False, nullable=False)
    actual_outcome = Column(String, nullable=True)
    evidence_refs_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class PlanningCounterfactual(Base):
    """Counterfactual hypothesis — never a claimed fact (§16)."""

    __tablename__ = "planning_counterfactuals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    goal_id = Column(String, nullable=True, index=True)
    actual_action = Column(String, nullable=False)
    counterfactual_candidate = Column(String, nullable=False)
    evidence_refs_json = Column(Text, nullable=True)
    claimed_outcome = Column(String, nullable=True)  # always NULL — hypotheses have no outcome
    hypothesis = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class ModelEvaluation(Base):
    """Persisted result of one deterministic model evaluation run (Phase 6).

    Truthfulness: a row exists only where an actual evaluation ran. Absence
    of rows means the model is UNRATED for that task class — never inferred
    from name/provider metadata.
    """

    __tablename__ = "model_evaluations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(String, nullable=False, index=True)
    provider_id = Column(String, nullable=False)
    task_class = Column(String, nullable=False, index=True)
    evaluation_version = Column(String, nullable=False)
    score = Column(Float)          # 0.0..1.0; None when not measurable
    passed = Column(Boolean)       # None = inconclusive/unknown
    latency_ms = Column(Integer)
    usage_tokens = Column(Integer)
    input_ref = Column(String)     # deterministic test-case reference
    expected_summary = Column(String)
    actual_summary = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


class ModelSelection(Base):
    """Audit record of one model-selection decision (Phase 6)."""

    __tablename__ = "model_selections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    requirements_json = Column(Text, nullable=False)
    selected_model = Column(String)
    rejected_json = Column(Text)   # [{model, reason}]
    reason = Column(String)
    fallback_of = Column(String)   # previous failed model id, if any
    created_at = Column(DateTime, default=datetime.utcnow)


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
    """Persistent real-time memory storage for user preferences, facts, and profile info.

    Phase 7 typed/provenanced columns (additive; legacy rows backfilled):
    kind, scope(+scope_ref), source_type(+source_ref), confidence,
    verification_status, status lifecycle, superseded_by. ``category`` is
    retained as a legacy display field.
    """

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

    # --- Phase 7 typed memory columns ---
    kind = Column(String, default="UNKNOWN", nullable=False, index=True)
    scope = Column(String, default="USER", nullable=False, index=True)
    scope_ref = Column(String, nullable=True)
    source_type = Column(String, default="UNKNOWN", nullable=False)
    source_ref = Column(String, nullable=True)
    observed_at = Column(DateTime, default=datetime.utcnow)
    confidence = Column(Float)                     # None = not yet assessed
    verification_status = Column(String, default="UNVERIFIED", nullable=False)
    status = Column(String, default="ACTIVE", nullable=False, index=True)
    superseded_by = Column(Integer, nullable=True)


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
    
    Phase 13B: task_id is now nullable to support pure conversations that
    don't create tasks (CONVERSATION interaction type).
    """

    __tablename__ = "conversation_turns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, nullable=False, index=True)
    task_id = Column(String, nullable=True)  # Phase 13B: nullable for pure conversations
    role = Column(String, default="user")  # "user" or "assistant"
    summary = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class ConversationState(Base):
    """Tracks conversational context for a session.

    Stores the current topic, recent topics, and open threads.
    Updated after each task completion.
    """

    __tablename__ = "conversation_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, nullable=False, index=True, unique=True)
    active_topic = Column(String, nullable=True)
    previous_topics = Column(JSON, default=list)  # ["topic1", "topic2", ...]
    open_threads = Column(JSON, default=list)  # ["unresolved topic1", ...]
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

