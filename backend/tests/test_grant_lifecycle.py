import datetime as dt

import pytest

from backend.runtime.grant_lifecycle import (
    Grant,
    GrantLifecycleManager,
    GrantValidityResult,
    create_grant,
    validate_grant,
    is_grant_valid,
    revoke_grant,
    expire_grant,
    STATUS_ACTIVE,
    STATUS_REVOKED,
    STATUS_EXPIRED,
    STATUS_ISSUED,
    REASON_MATCH,
    REASON_INPUT_MALFORMED,
    REASON_GRANT_REVOKED,
    REASON_GRANT_EXPIRED,
    REASON_GRANT_NOT_YET_VALID,
    REASON_GRANT_INVALID_WINDOW,
    REASON_GRANT_UNTRUSTED,
    REASON_MISSING_PROVENANCE,
    STATUS_PROPOSED,
)

UTC = dt.timezone.utc


def _now():
    return dt.datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)


def _grant(**overrides):
    now = _now()
    base = dict(
        grant_id="grant-1",
        trusted=True,
        provenance="independently-verified",
        valid_from=now - dt.timedelta(minutes=5),
        valid_until=now + dt.timedelta(minutes=5),
        status=STATUS_ACTIVE,
    )
    base.update(overrides)
    return Grant(**base)


def test_valid_grant_is_valid():
    g = _grant()
    now = _now()
    r = validate_grant(g, at=now)
    assert r.valid is True
    assert r.reason_code == REASON_MATCH
    assert isinstance(r, GrantValidityResult)


def test_missing_evaluation_time_denied():
    g = _grant()
    r = validate_grant(g, at=None)
    assert r.valid is False
    assert r.reason_code == REASON_INPUT_MALFORMED


def test_wrong_type_grant_denied():
    now = _now()
    r = validate_grant("not-a-grant", at=now)
    assert r.valid is False
    assert r.reason_code == REASON_INPUT_MALFORMED


def test_subclass_grant_rejected():
    now = _now()

    class Evil(Grant):
        pass

    # construction of subclass not exact type should be rejected by evaluator
    evil = Grant.__new__(Evil)
    for k, v in _grant().__dict__.items():
        object.__setattr__(evil, k, v)
    r = validate_grant(evil, at=now)
    assert r.valid is False
    assert r.reason_code == REASON_INPUT_MALFORMED


def test_untrusted_scope_denied():
    now = _now()
    g = _grant(trusted=False)
    r = validate_grant(g, at=now)
    assert r.valid is False
    assert r.reason_code == REASON_GRANT_UNTRUSTED


def test_missing_provenance_denied():
    now = _now()
    g = _grant(provenance=None)
    r = validate_grant(g, at=now)
    assert r.valid is False
    assert r.reason_code == REASON_MISSING_PROVENANCE


def test_revoked_flag_denied():
    now = _now()
    g = _grant(revoked=True)
    r = validate_grant(g, at=now)
    assert r.valid is False
    assert r.reason_code == REASON_GRANT_REVOKED


def test_revoked_at_denied_when_passed():
    now = _now()
    g = _grant(revoked_at=now - dt.timedelta(seconds=1))
    r = validate_grant(g, at=now)
    assert r.valid is False
    assert r.reason_code == REASON_GRANT_REVOKED


def test_expired_denied():
    now = _now()
    g = _grant(valid_until=now - dt.timedelta(seconds=1))
    r = validate_grant(g, at=now)
    assert r.valid is False
    assert r.reason_code == REASON_GRANT_EXPIRED


def test_not_yet_valid_denied():
    now = _now()
    g = _grant(valid_from=now + dt.timedelta(seconds=1))
    r = validate_grant(g, at=now)
    assert r.valid is False
    assert r.reason_code == REASON_GRANT_NOT_YET_VALID


def test_invalid_window_denied():
    now = _now()
    g = _grant(valid_from=now + dt.timedelta(minutes=1), valid_until=now - dt.timedelta(minutes=1))
    r = validate_grant(g, at=now)
    assert r.valid is False
    assert r.reason_code == REASON_GRANT_INVALID_WINDOW


def test_grantee_binding():
    now = _now()
    g = _grant(subject="user-42")
    assert validate_grant(g, at=now, grantee="user-42").valid is True
    r2 = validate_grant(g, at=now, grantee="user-99")
    assert r2.valid is False


def test_schema_version_binding():
    now = _now()
    g = _grant(schema_version=2)
    assert validate_grant(g, at=now, expected_schema_version=2).valid is True
    assert validate_grant(g, at=now, expected_schema_version=1).valid is False


def test_status_terminal_denied():
    now = _now()
    g = _grant(status=STATUS_REVOKED)
    r = validate_grant(g, at=now)
    assert r.valid is False
    assert r.reason_code == REASON_GRANT_REVOKED


def test_construction_malformed_raises():
    now = _now()
    with pytest.raises(ValueError):
        Grant(grant_id="", valid_from=now, valid_until=now + dt.timedelta(minutes=1), trusted=True)
    with pytest.raises(ValueError):
        Grant(grant_id="g", valid_from="bad", valid_until=now + dt.timedelta(minutes=1), trusted=True)


def test_scope_is_immutable_attribute():
    g = _grant()
    with pytest.raises(Exception):
        g.scope = g.scope  # frozen record must not allow reassignment


def test_revoke_transitions_and_increments_version():
    now = _now()
    g = _grant()
    assert g.version == 1
    g2 = revoke_grant(g, revoked_at=now, reason="user")
    assert g2.status == "revoked"
    assert g2.version == 2
    assert g2.revoked is True
    assert g2.revoked_reason == "user"
    # original unchanged
    assert g.version == 1
    assert g.status == STATUS_ACTIVE


def test_idempotent_same_status():
    now = _now()
    g = _grant()
    # transitioning to current status returns same object
    same = g.transition(STATUS_ACTIVE)
    assert same is g


def test_illegal_transition_raises():
    now = _now()
    g = _grant(status=STATUS_REVOKED)
    with pytest.raises(ValueError):
        g.transition(STATUS_ACTIVE)


def test_cas_manager_conflict():
    now = _now()
    m = GrantLifecycleManager(clock=lambda: now)
    g = m.create("g1", valid_from=now - dt.timedelta(minutes=1), valid_until=now + dt.timedelta(minutes=1), trusted=True, provenance="p", created_at=now)
    m.revoke(g, expected_version=1, revoked_at=now)
    with pytest.raises(Exception):
        m.revoke(g, expected_version=1, revoked_at=now)


def test_manager_create_duplicate_raises():
    now = _now()
    m = GrantLifecycleManager(clock=lambda: now)
    m.create("g1", valid_from=now - dt.timedelta(minutes=1), valid_until=now + dt.timedelta(minutes=1), trusted=True, provenance="p", created_at=now)
    with pytest.raises(ValueError):
        m.create("g1", valid_from=now - dt.timedelta(minutes=1), valid_until=now + dt.timedelta(minutes=1), trusted=True, provenance="p", created_at=now)


def test_manager_validate_unknown():
    now = _now()
    m = GrantLifecycleManager(clock=lambda: now)
    r = m.validate("missing", at=now)
    assert r.valid is False
    assert r.reason_code == "not_found"


def test_expiration_via_time():
    now = _now()
    g = _grant(valid_until=now - dt.timedelta(seconds=1))
    assert is_grant_valid(g, at=now) is False


def test_result_to_dict_has_no_authorization():
    r = GrantValidityResult(valid=True, reason_code="match", reason="x")
    d = r.to_dict()
    assert "authorized" not in d
    assert "permission" not in d


def test_non_authorizing_flat():
    # Result exposes authorized=False only; never True
    r = GrantValidityResult(valid=True, reason_code="match", reason="x")
    assert r.authorized is False


def test_manager_transition_and_version():
    now = _now()
    m = GrantLifecycleManager(clock=lambda: now)
    g = m.create("g1", valid_from=now - dt.timedelta(minutes=1), valid_until=now + dt.timedelta(minutes=1), trusted=True, provenance="p", created_at=now, status=STATUS_PROPOSED)
    g2 = m.transition(g, STATUS_ISSUED, expected_version=g.version, at=now)
    assert g2.status == STATUS_ISSUED
    assert g2.version == g.version + 1


def test_grant_to_dict_basic():
    g = _grant()
    d = g.to_dict()
    assert d["grant_id"] == "grant-1"
    assert d["status"] == STATUS_ACTIVE
    assert d.get("authorized") is None or d.get("authorized") is False


def test_result_matches_property():
    r = validate_grant(_grant(), at=_now())
    assert r.matches is True


def test_invalid_window_equal_endpoints():
    now = _now()
    g = _grant(valid_from=now, valid_until=now)
    r = validate_grant(g, at=now)
    assert r.valid is False
    assert r.reason_code == REASON_GRANT_INVALID_WINDOW


def test_status_proposed_cannot_be_used_unless_active_allowed():
    now = _now()
    g = _grant(status=STATUS_PROPOSED)
    r = validate_grant(g, at=now)
    assert r.valid is False
import threading as _threading


def test_nested_metadata_mutation_fails():
    g = _grant(metadata={"nested": {"x": [1, 2]}, "s": {3, 4}})
    # top-level mapping is read-only
    with pytest.raises(TypeError):
        g.metadata["new"] = 1
    # nested mapping is read-only
    with pytest.raises(TypeError):
        g.metadata["nested"]["y"] = 9
    # lists frozen to tuples (no append)
    assert not hasattr(g.metadata["nested"]["x"], "append")


def test_to_dict_metadata_is_deep_copy_not_alias():
    g = _grant(metadata={"nested": {"x": [1, 2]}})
    d = g.to_dict()
    d["metadata"]["nested"]["x"].append(3) if isinstance(d["metadata"]["nested"]["x"], list) else None
    d["metadata"]["top"] = "mutated"
    assert "top" not in g.metadata
    assert "top" not in g.to_dict()["metadata"]


def test_cyclic_metadata_rejected():
    container = {}
    container["self"] = container
    with pytest.raises(ValueError):
        _grant(metadata=container)


def test_unsupported_metadata_rejected():
    with pytest.raises(ValueError):
        _grant(metadata={"fn": lambda x: x})


def test_scope_divergence_normalized():
    from backend.runtime.grant_scope import GrantScope as GS
    now = _now()
    scope = GS(
        grant_id="grant-scope",
        trusted=True,
        valid_from=now - dt.timedelta(minutes=1),
        valid_until=now + dt.timedelta(minutes=1),
        allowed_operations=frozenset(),
        allowed_actions=frozenset(["read"]),
        allowed_resources=frozenset(),
        allowed_contexts=frozenset(),
    )
    # Pass divergent allow-lists; grant must follow scope's canonical immutable values
    g = Grant(
        grant_id="grant-scope",
        trusted=False,
        provenance="p",
        valid_from=now - dt.timedelta(minutes=1),
        valid_until=now + dt.timedelta(minutes=1),
        scope=scope,
        allowed_operations=frozenset(["evil"]),
        allowed_actions=frozenset(["evil"]),
    )
    assert g.trusted is True
    assert g.allowed_operations == scope.allowed_operations
    assert g.allowed_actions == scope.allowed_actions
    assert isinstance(g.allowed_operations, frozenset)
    assert isinstance(g.allowed_actions, frozenset)


def test_subclass_grant_rejected_by_validator():
    now = _now()

    class Evil(Grant):
        pass

    obj = Grant.__new__(Evil)
    base = _grant()
    for k, v in base.__dict__.items():
        object.__setattr__(obj, k, v)
    assert validate_grant(obj, at=now).valid is False


def test_concurrent_cas_exactly_one_wins():
    now = _now()
    m = GrantLifecycleManager(clock=lambda: now)
    g = m.create(
        "g1",
        valid_from=now - dt.timedelta(minutes=1),
        valid_until=now + dt.timedelta(minutes=1),
        trusted=True,
        provenance="p",
        created_at=now,
    )
    wins = []
    lock = _threading.Lock()

    def worker():
        try:
            m.revoke(g, expected_version=g.version, revoked_at=now)
            with lock:
                wins.append("win")
        except Exception:
            with lock:
                wins.append("lose")

    ts = [_threading.Thread(target=worker) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert wins.count("win") == 1
    assert wins.count("lose") == 7


def test_grant_metadata_deep_thaw_no_alias():
    g = _grant(metadata={"a": {"b": [1, 2]}})
    d = g.to_dict()
    d["metadata"]["a"]["b"].append(99)
    assert g.metadata["a"]["b"] == (1, 2)


def test_grant_allowlists_to_dict_are_copies():
    g = _grant()
    d = g.to_dict()
    d["allowed_actions"].append("mutated")
    assert "mutated" not in g.allowed_actions
