"""Cooperative instruction append: never interrupt or replay admitted work.

A worker opens a window before inference and atomically claims it before any
decision (including completion, a skill, or a multi-tool batch). Revisions and
claims serialize through SQLite's writer transaction. A revision or queued
instruction accepted first fences old inference; an admitted response/batch is
not interrupted or replayed by a later instruction.
"""

from dataclasses import dataclass
from datetime import datetime
import uuid

from backend.models.database import Task, TaskDecisionWindow


class RevisionUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class DecisionSnapshot:
    revision: int
    original_request: str
    normalized_goal: str | None


class TaskInstructionRevision:
    ACCEPTED = (
        "Your instruction was appended to the same task. Its next decision will "
        "use the updated instructions; an older unstarted decision cannot run. "
        "No task was restarted or duplicated, and no action permission was granted."
    )
    BUSY = (
        "I could not apply that change: this task is not at a cooperative safe point. "
        "An already-admitted action may still be in flight and its effect may be "
        "unknown. I have not interrupted, replayed, or duplicated it, and your "
        "instruction has not been appended."
    )
    CHANGED = (
        "That task changed or stopped before I could apply your instruction. "
        "Nothing was appended, restarted, or duplicated. Please review its current status."
    )

    def __init__(self, runner):
        self.runner = runner
        self.db = runner.db

    def _window(self, task_id):
        return self.db.query(TaskDecisionWindow).filter_by(task_id=task_id).populate_existing().first()

    def _task(self, task_id):
        return self.db.query(Task).filter_by(id=task_id).populate_existing().one()

    def open(self, task_id):
        """Worker-only: snapshot instructions and advertise an admission window."""
        try:
            self.runner.task_ownership._ensure_transaction()
            if not self.runner.verify_task_ownership(task_id):
                raise RevisionUnavailable(self.CHANGED)
            task = self._task(task_id)
            if task.state != "running":
                self.db.rollback()
                return None
            # Permission/uncertain-action evidence is never consumed by revision.
            owner = self.runner.task_ownership.get(task_id)
            blocked = bool(task.pending_action or task.active_action_id
                           or owner.active_action_id or owner.uncertain_action_ids)
            row = self._window(task_id)
            if row is None:
                row = TaskDecisionWindow(task_id=task_id, revision=0)
                self.db.add(row)
            row.owner_nonce = self.runner.task_ownership_nonce
            # Leave existing recovery/evidence gates in control of unresolved
            # work, but never advertise it as eligible for an instruction edit.
            row.phase = "blocked" if blocked else "thinking"
            snapshot = DecisionSnapshot(row.revision, task.original_request, task.normalized_goal)
            self.db.commit()
            return snapshot
        except BaseException:
            self.db.rollback()
            raise

    def claim(self, task_id, revision):
        """Admit exactly one current response; old responses are discarded."""
        try:
            self.runner.task_ownership._ensure_transaction()
            task = self._task(task_id)
            row = self._window(task_id)
            if (not self.runner.verify_task_ownership(task_id) or task.state != "running"
                    or row is None or row.owner_nonce != self.runner.task_ownership_nonce
                    or row.phase not in {"thinking", "blocked"} or row.revision != revision):
                self.db.rollback()
                return False
            # Receipt enqueue and response admission share the SQLite writer
            # boundary. A queued instruction must be drained before admitting
            # *any* stale response (completion and entire tool batches included).
            from backend.runtime.followup_instruction_ledger import FollowupInstructionLedgerManager
            if FollowupInstructionLedgerManager(self.db).pending(task_id=task_id):
                self.db.rollback()
                return False
            row.phase = "executing"
            self.db.commit()
            return True
        except BaseException:
            self.db.rollback()
            raise

    async def append(self, task_id, expected_version, instruction, runtime, exchange_id, source_exchange_id, commit=True):
        """Persist exact human text, task revision, audit, and exchange together.

        No lease stealing, worker scheduling, permission consumption, cancellation,
        or external actions. A non-cooperating/older worker has no window: refused.

        ``commit`` controls whether this revision commits the caller's transaction.
        Use ``commit=False`` to compose it atomically with another durable write
        (for example, marking a reviewed follow-up ledger request applied) in the
        same transaction. The caller is then responsible for commit/rollback.
        """
        try:
            self.runner.task_ownership._ensure_transaction()
            if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 1:
                raise RevisionUnavailable(self.CHANGED)
            association = runtime.get_task_association(source_exchange_id)
            if association is None or association.task_id != task_id:
                raise RevisionUnavailable(self.CHANGED)
            task = self._task(task_id)
            if task.state != "running" or task.version != expected_version:
                raise RevisionUnavailable(self.CHANGED)
            owner = self.runner.task_ownership.get(task_id)
            row = self._window(task_id)
            if (owner is None or owner.status != "ACTIVE" or owner.expires_at <= datetime.utcnow()
                    or task.pending_action or task.active_action_id or owner.active_action_id
                    or owner.uncertain_action_ids or row is None or row.phase != "thinking"
                    or row.owner_nonce != owner.owner_nonce):
                raise RevisionUnavailable(self.BUSY)
            if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 12000:
                raise RevisionUnavailable(self.CHANGED)
            goal = (task.normalized_goal or task.original_request) + "\n[User follow-up]\n" + instruction
            if len(goal) > 24000:
                raise RevisionUnavailable("The accumulated instructions are too long; nothing was appended or restarted.")
            from backend.services.discussion_contexts import ensure_exchange_context
            context_id = ensure_exchange_context(
                self.db, runtime.session_id, source_exchange_id,
                association.user_turn_id, association.assistant_turn_id,
            )
            identity = await runtime._persist_conversation(
                instruction, self.ACCEPTED, exchange_id, commit=False, context_id=context_id,
            )
            task.normalized_goal = goal
            task.version += 1
            task.updated_at = datetime.utcnow()
            row.revision += 1
            self.runner._append_event(task_id, "task.instruction_appended", {
                "instruction": instruction, "revision": row.revision,
                "expected_task_version": expected_version,
                "source_exchange_id": source_exchange_id,
                "exchange_id": identity.exchange_id, "turn_ids": list(identity.turn_ids),
            })
            if commit:
                self.db.commit()
            else:
                self.db.flush()
            return identity
        except BaseException:
            self.db.rollback()
            raise

    async def _resolve_request_row(self, request_id, task_id=None):
        from backend.runtime.followup_instruction_ledger import FollowupInstructionLedgerManager
        ledger = FollowupInstructionLedgerManager(db=self.db, runner=self.runner)
        row = ledger.get(request_id=request_id)
        if row is None:
            raise RevisionUnavailable(self.CHANGED)
        effective_task_id = task_id or row.task_id or row.source_task_id
        if effective_task_id is None:
            raise RevisionUnavailable(self.CHANGED)
        return ledger, row, effective_task_id

    async def apply_followup_request(self, *args, **kwargs):
        """Atomically apply one reviewed follow-up ledger request at a safe point.

        The follow-up becomes applied only after the task goal/window/event
        revision commits in the same transaction. Ownership, permission,
        uncertain-action, and admitted-action gates are rechecked; concurrent
        requests serialize through the caller's writer transaction.
        """
        # Lock before *any* receipt/status/identity reads, including positional
        # lookup. Rechecking only the task version cannot fence a withdrawn or
        # already-applied receipt read before another transaction committed.
        commit = kwargs.get("commit", True)
        try:
            if kwargs.get("runner", self.runner).db is not self.db:
                raise RevisionUnavailable(self.CHANGED)
            self.runner.task_ownership._ensure_transaction()
            result = await self._apply_followup_request_locked(*args, **{**kwargs, "commit": False})
            if commit:
                self.db.commit()
            return result
        except BaseException:
            self.db.rollback()
            raise

    async def _apply_followup_request_locked(self, *args, **kwargs):
        # Normalize a wide range of call shapes within the writer transaction.
        request_id = kwargs.get("request_id", kwargs.get("request"))
        task_id = kwargs.get("task_id", kwargs.get("task"))
        runner = kwargs.get("runner", self.runner)
        runtime = kwargs.get("runtime")
        instruction = kwargs.get("instruction")
        expected_version = kwargs.get("expected_version", kwargs.get("expected_task_version"))
        exchange_id = kwargs.get("exchange_id")
        source_exchange_id = kwargs.get("source_exchange_id")
        commit = kwargs.get("commit", True)

        positional = [a for a in args if a is not None]
        if request_id is None and positional:
            # Support either apply(task_id, request_id) or apply(request_id, task_id).
            from backend.runtime.followup_instruction_ledger import FollowupInstructionLedgerManager
            ledger_probe = FollowupInstructionLedgerManager(db=self.db, runner=runner)
            first_row = ledger_probe.get(request_id=positional[0])
            if first_row is not None:
                request_id = positional[0]
                if task_id is None and len(positional) > 1:
                    task_id = positional[1]
            elif len(positional) > 1:
                second_row = ledger_probe.get(request_id=positional[1])
                if second_row is not None:
                    task_id = positional[0]
                    request_id = positional[1]
            else:
                request_id = positional[0]
                if task_id is None and len(positional) > 1:
                    task_id = positional[1]

        if not isinstance(request_id, str) or not request_id.strip():
            raise RevisionUnavailable(self.CHANGED)
        request_id = request_id.strip()

        from backend.runtime.followup_instruction_ledger import (
            FollowupInstructionLedgerManager, STATUS_PENDING, STATUS_APPLIED,
            STATUS_NOT_APPLIED, PENDING_STATUSES,
        )
        from backend.services.conversation_runtime import ConversationRuntime

        ledger = FollowupInstructionLedgerManager(db=self.db, runner=runner)
        row = ledger.get(request_id=request_id)
        if row is None:
            raise RevisionUnavailable(self.CHANGED)

        # Exact immutable task identity: follow-ups never retarget across tasks.
        source_task_id = row.source_task_id
        if not isinstance(source_task_id, str) or not source_task_id.strip():
            raise RevisionUnavailable(self.CHANGED)
        if row.task_id is not None and row.task_id != source_task_id:
            raise RevisionUnavailable(self.CHANGED)
        if isinstance(task_id, str):
            if task_id != source_task_id:
                raise RevisionUnavailable(self.CHANGED)
        elif task_id is not None:
            raise RevisionUnavailable(self.CHANGED)

        effective_task_id = source_task_id

        if runtime is None:
            runtime = ConversationRuntime(self.db)

        # Exact session and instruction identity: caller cannot substitute text.
        if (runtime.db is not self.db or not isinstance(row.session_id, str)
                or not row.session_id.strip() or runtime.session_id != row.session_id):
            raise RevisionUnavailable(self.CHANGED)
        if instruction is not None and instruction != row.instruction:
            raise RevisionUnavailable(self.CHANGED)
        revision_instruction = row.instruction

        declared_source_exchange_id = row.source_exchange_id or row.exchange_id
        if not isinstance(declared_source_exchange_id, str) or not declared_source_exchange_id.strip():
            raise RevisionUnavailable(self.CHANGED)
        if source_exchange_id is not None:
            if declared_source_exchange_id is not None and source_exchange_id != declared_source_exchange_id:
                raise RevisionUnavailable(self.CHANGED)
        else:
            source_exchange_id = declared_source_exchange_id

        if exchange_id is None or exchange_id == source_exchange_id:
            exchange_id = "followup-ledger-%s" % request_id

        if not isinstance(revision_instruction, str) or not revision_instruction.strip():
            raise RevisionUnavailable(self.CHANGED)
        if not isinstance(source_exchange_id, str) or not source_exchange_id.strip():
            raise RevisionUnavailable(self.CHANGED)

        # Validate identity even on the idempotent path. Exchange IDs are not
        # deduplication keys: the exact durable request is, under this lock.
        association = runtime.get_task_association(source_exchange_id)
        if association is None or association.task_id != effective_task_id:
            raise RevisionUnavailable(self.CHANGED)
        if row.status == STATUS_APPLIED:
            return row
        if row.status not in PENDING_STATUSES:
            raise RevisionUnavailable(self.CHANGED)
        pending = ledger.pending(task_id=effective_task_id)
        if not pending or pending[0].request_id != request_id:
            raise RevisionUnavailable(self.CHANGED)

        # Live task must be the exact same immutable task identity.
        try:
            task = self.db.query(Task).filter_by(id=effective_task_id).populate_existing().one()
        except Exception:
            raise RevisionUnavailable(self.CHANGED)
        if task.state != "running":
            raise RevisionUnavailable(self.CHANGED)
        if expected_version is not None:
            if (isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 1
                    or expected_version != task.version):
                raise RevisionUnavailable(self.CHANGED)
        else:
            expected_version = task.version

        try:
            self.runner.task_ownership._ensure_transaction()
            # Gate safety before touching revision state.
            window = self._window(effective_task_id)
            owner = self.runner.task_ownership.get(effective_task_id)
            if (owner is None or owner.status != "ACTIVE"
                    or owner.expires_at <= datetime.utcnow()
                    or task.pending_action or task.active_action_id or owner.active_action_id
                    or owner.uncertain_action_ids or window is None or window.phase != "thinking"
                    or window.owner_nonce != owner.owner_nonce):
                raise RevisionUnavailable(self.BUSY)

            identity = await self.append(
                effective_task_id, expected_version, revision_instruction, runtime,
                exchange_id, source_exchange_id, commit=False,
            )
            window = self._window(effective_task_id)
            applied_revision = int(window.revision) if window is not None else None
            applied_version = self.db.query(Task).filter_by(id=effective_task_id).populate_existing().one().version

            from backend.runtime.followup_instruction_ledger import FollowupInstructionLedgerRow
            ledger_row = (
                self.db.query(FollowupInstructionLedgerRow)
                .filter_by(request_id=request_id)
                .populate_existing()
                .one()
            )
            ledger_row.status = STATUS_APPLIED
            ledger_row.updated_at = datetime.utcnow()
            ledger_row.applied_at = datetime.utcnow()
            ledger_row.applied_task_version = applied_version
            ledger_row.applied_instruction_revision = applied_revision
            ledger_row.terminal_at = datetime.utcnow()
            if kwargs.get("reason") is not None:
                ledger_row.terminal_reason = str(kwargs.get("reason"))
            self.db.flush()

            if commit:
                self.db.commit()
            else:
                self.db.flush()
            return ledger.get(request_id=request_id)
        except BaseException:
            self.db.rollback()
            raise

    # Compatibility aliases for reviewed follow-up application.
    apply = apply_followup_request
    apply_followup = apply_followup_request
    apply_followups = apply_followup_request
    apply_pending_followup = apply_followup_request
    async def apply_pending_followups(self, *args, **kwargs):
        from backend.runtime.followup_instruction_ledger import FollowupInstructionLedgerManager, FollowupInstructionRequest
        request_id = kwargs.get("request_id")
        task_id = kwargs.get("task_id", kwargs.get("task"))
        runner = kwargs.get("runner", self.runner)
        runtime = kwargs.get("runtime")
        commit = kwargs.get("commit", True)
        positional = [a for a in args if a is not None]
        if task_id is None and positional:
            task_id = positional[0]
        if task_id is None:
            # infer from first pending? require
            raise RevisionUnavailable(self.CHANGED)
        ledger = FollowupInstructionLedgerManager(db=self.db, runner=runner)
        records = ledger.pending(task_id=task_id)
        applied = []
        from backend.services.conversation_runtime import ConversationRuntime
        if runtime is None:
            runtime = ConversationRuntime(self.db)
        for record in records:
            try:
                out = await self.apply_followup_request(
                    task_id=task_id,
                    request_id=record.request_id,
                    runner=runner,
                    runtime=runtime,
                    instruction=record.instruction,
                    exchange_id=record.exchange_id,
                    source_exchange_id=record.source_exchange_id,
                    expected_version=kwargs.get("expected_version"),
                    commit=commit,
                )
                applied.append(out)
            except Exception:
                break
        return applied
    apply_ledger_request = apply_followup_request
    apply_reviewed_followup = apply_followup_request
    apply_at_safe_point = apply_followup_request
    apply_instruction = apply_followup_request
    apply_followup_instruction = apply_followup_request
    atomic_apply = apply_followup_request
    apply_transactional = apply_followup_request
    apply_reviewed = apply_followup_request
