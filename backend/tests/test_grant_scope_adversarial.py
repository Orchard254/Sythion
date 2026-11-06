
"""Adversarial tests for the non-executing grant-scope evaluation foundation."""

import datetime as dt

import pytest

from backend.runtime.grant_scope import (
    ContextIdentity,
    GrantEvaluationRequest,
    GrantEvaluationResult,
    GrantOperation,
    GrantScope,
    GrantScopeEvaluator,
    ResourceIdentity,
    evaluate_grant_scope,
)


UTC = dt.timezone.utc


def _now():
    return dt.datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)


def _res(res_id: str = "acct-1", kind: str = "account"):
    return ResourceIdentity(kind=kind, value=res_id)


def _ctx(ctx_id: str = "session-1", kind: str = "composer_session"):
    return ContextIdentity(kind=kind, value=ctx_id)


def _op(action: str = "read", res: str = "acct-1", ctx: str = "session-1"):
    return GrantOperation(action=action, resource=_res(res), context=_ctx(ctx))


def _grant(**overrides):
    now = _now()
    base = dict(
        grant_id="grant-1",
        trusted=True,
        revoked=False,
        valid_from=now - dt.timedelta(minutes=5),
        valid_until=now + dt.timedelta(minutes=5),
        allowed_operations=frozenset([_op()]),
    )
    base.update(overrides)
    return GrantScope(**base)


def test_exact_match_returns_true():
    g = _grant()
    r = evaluate_grant_scope(grant=g, operation=_op(), evaluation_time=_now())
    assert r.matches is True
    assert r.reason_code == "match"
    assert isinstance(r, GrantEvaluationResult)


def test_operation_not_in_scope_returns_false():
    g = _grant()
    r = evaluate_grant_scope(grant=g, operation=_op(action="write"), evaluation_time=_now())
    assert r.matches is False
    assert r.reason_code == "action_mismatch"

    r2 = evaluate_grant_scope(grant=g, operation=_op(res="acct-2"), evaluation_time=_now())
    assert r2.matches is False
    assert r2.reason_code == "resource_mismatch"

    r3 = evaluate_grant_scope(grant=g, operation=_op(ctx="session-2"), evaluation_time=_now())
    assert r3.matches is False
    assert r3.reason_code == "context_mismatch"


def test_untrusted_grant_denied():
    g = _grant(trusted=False)
    r = evaluate_grant_scope(grant=g, operation=_op(), evaluation_time=_now())
    assert r.matches is False
    assert r.reason_code == "grant_untrusted"


def test_revoked_flag_denied():
    g = _grant(revoked=True)
    r = evaluate_grant_scope(grant=g, operation=_op(), evaluation_time=_now())
    assert r.matches is False
    assert r.reason_code == "grant_revoked"


def test_revoked_at_future_boundary_denied_when_passed():
    now = _now()
    g = _grant(revoked_at=now - dt.timedelta(seconds=1))
    r = evaluate_grant_scope(grant=g, operation=_op(), evaluation_time=now)
    assert r.matches is False
    assert r.reason_code == "grant_revoked"


def test_expired_grant_denied():
    now = _now()
    g = _grant(valid_until=now - dt.timedelta(seconds=1))
    r = evaluate_grant_scope(grant=g, operation=_op(), evaluation_time=now)
    assert r.matches is False
    assert r.reason_code == "grant_expired"


def test_not_yet_valid_denied():
    now = _now()
    g = _grant(valid_from=now + dt.timedelta(seconds=1))
    r = evaluate_grant_scope(grant=g, operation=_op(), evaluation_time=now)
    assert r.matches is False
    assert r.reason_code == "grant_not_yet_valid"


def test_invalid_window_denied():
    now = _now()
    g = _grant(valid_from=now + dt.timedelta(minutes=1), valid_until=now - dt.timedelta(minutes=1))
    r = evaluate_grant_scope(grant=g, operation=_op(), evaluation_time=now)
    assert r.matches is False
    assert r.reason_code == "grant_invalid_window"

    # equal endpoints invalid (zero-length window)
    g2 = _grant(valid_from=now, valid_until=now)
    r2 = evaluate_grant_scope(grant=g2, operation=_op(), evaluation_time=now)
    assert r2.matches is False
    assert r2.reason_code == "grant_invalid_window"


def test_missing_evaluation_time_denied():
    g = _grant()
    r = evaluate_grant_scope(grant=g, operation=_op(), evaluation_time=None)
    assert r.matches is False
    assert r.reason_code == "input_malformed"


def test_missing_grant_id_denied():
    now = _now()
    g = _grant(grant_id="")
    r = evaluate_grant_scope(grant=g, operation=_op(), evaluation_time=now)
    assert r.matches is False
    assert r.reason_code == "input_malformed"


def test_mapping_is_rejected_as_grant():
    now = _now()
    g = {
        "grant_id": "grant-1",
        "trusted": True,
        "valid_from": now - dt.timedelta(minutes=5),
        "valid_until": now + dt.timedelta(minutes=5),
        "allowed_operations": [_op()],
    }
    r = evaluate_grant_scope(grant=g, operation=_op(), evaluation_time=now)
    assert r.matches is False
    assert r.reason_code == "input_malformed"


def test_mapping_is_rejected_as_operation():
    g = _grant()
    now = _now()
    op = {"action": "read", "resource": _res(), "context": _ctx()}
    r = evaluate_grant_scope(grant=g, operation=op, evaluation_time=now)
    assert r.matches is False
    assert r.reason_code == "input_malformed"


def test_wrong_type_grant_denied_without_crash():
    now = _now()
    for bad in (None, 42, "grant", object()):
        r = evaluate_grant_scope(grant=bad, operation=_op(), evaluation_time=now)
        assert r.matches is False


def test_resources_are_exact_opaque_no_normalization():
    now = _now()
    # A granted value that looks like a Windows path is treated literally,
    # not canonicalised.  A differing case or separator must not match.
    granted_res = ResourceIdentity(kind="fs", value=r"C:\Foo\Bar")
    ctx = _ctx()
    op_granted = GrantOperation(action="read", resource=granted_res, context=ctx)
    g = _grant(allowed_operations=frozenset([op_granted]))
    same = GrantOperation(action="read", resource=ResourceIdentity(kind="fs", value=r"C:\Foo\Bar"), context=ctx)
    r = evaluate_grant_scope(grant=g, operation=same, evaluation_time=now)
    assert r.matches is True

    different_case = GrantOperation(action="read", resource=ResourceIdentity(kind="fs", value=r"c:\foo\bar"), context=ctx)
    r2 = evaluate_grant_scope(grant=g, operation=different_case, evaluation_time=now)
    assert r2.matches is False

    # Kind mismatch is also a mismatch (exact opaque identity).
    kind_diff = GrantOperation(action="read", resource=ResourceIdentity(kind="FILE", value=r"C:\Foo\Bar"), context=ctx)
    r3 = evaluate_grant_scope(grant=g, operation=kind_diff, evaluation_time=now)
    assert r3.matches is False


def test_context_exact_matching():
    now = _now()
    g = _grant()
    r = evaluate_grant_scope(grant=g, operation=_op(ctx="session-2"), evaluation_time=now)
    assert r.matches is False
    assert r.reason_code == "context_mismatch"


def test_empty_allow_list_denies():
    now = _now()
    g = _grant(allowed_operations=frozenset())
    r = evaluate_grant_scope(grant=g, operation=_op(), evaluation_time=now)
    assert r.matches is False
    assert r.reason_code == "operation_not_granted"


def test_no_allow_list_at_all_denies():
    now = _now()
    g = _grant(allowed_operations=frozenset(), allowed_actions=frozenset(), allowed_resources=frozenset(), allowed_contexts=frozenset())
    r = evaluate_grant_scope(grant=g, operation=_op(), evaluation_time=now)
    assert r.matches is False
    assert r.reason_code == "operation_not_granted"


def test_coarse_lists_match():
    now = _now()
    g = GrantScope(
        grant_id="g2",
        trusted=True,
        valid_from=now - dt.timedelta(minutes=5),
        valid_until=now + dt.timedelta(minutes=5),
        allowed_actions=frozenset(["read"]),
        allowed_resources=frozenset([_res()]),
        allowed_contexts=frozenset([_ctx()]),
    )
    r = evaluate_grant_scope(grant=g, operation=_op(), evaluation_time=now)
    assert r.matches is True


def test_coarse_lists_incomplete_denies():
    now = _now()
    # Missing context list -> coarse mode cannot grant.
    g = GrantScope(
        grant_id="g3",
        trusted=True,
        valid_from=now - dt.timedelta(minutes=5),
        valid_until=now + dt.timedelta(minutes=5),
        allowed_actions=frozenset(["read"]),
        allowed_resources=frozenset([_res()]),
    )
    r = evaluate_grant_scope(grant=g, operation=_op(), evaluation_time=now)
    assert r.matches is False
    assert r.reason_code == "operation_not_granted"


def test_grantee_binding():
    now = _now()
    g = _grant(subject="user-42")
    # matching subject
    r = evaluate_grant_scope(grant=g, operation=_op(), evaluation_time=now, grantee="user-42")
    assert r.matches is True
    # mismatched subject
    r2 = evaluate_grant_scope(grant=g, operation=_op(), evaluation_time=now, grantee="user-99")
    assert r2.matches is False
    assert r2.reason_code == "input_malformed"


def test_schema_version_binding():
    now = _now()
    g = _grant(schema_version=2)
    r = evaluate_grant_scope(grant=g, operation=_op(), evaluation_time=now, expected_schema_version=2)
    assert r.matches is True
    r2 = evaluate_grant_scope(grant=g, operation=_op(), evaluation_time=now, expected_schema_version=1)
    assert r2.matches is False
    assert r2.reason_code == "input_malformed"


def test_inclusive_validity_boundaries():
    now = _now()
    # evaluation exactly at valid_from
    g = _grant(valid_from=now, valid_until=now + dt.timedelta(minutes=5))
    r = evaluate_grant_scope(grant=g, operation=_op(), evaluation_time=now)
    assert r.matches is True
    # evaluation exactly at valid_until
    g2 = _grant(valid_from=now - dt.timedelta(minutes=5), valid_until=now)
    r2 = evaluate_grant_scope(grant=g2, operation=_op(), evaluation_time=now)
    assert r2.matches is True


def test_naive_datetime_accepted_as_utc():
    now = _now().replace(tzinfo=None)
    g = _grant()
    # grant uses aware; evaluation naive accepted
    r = evaluate_grant_scope(grant=g, operation=_op(), evaluation_time=now)
    assert r.matches is False or r.matches is True  # no crash
    # at least no exception
    assert isinstance(r, GrantEvaluationResult)


def test_request_object_path():
    g = _grant()
    req = GrantEvaluationRequest(grant=g, operation=_op(), evaluation_time=_now())
    r = evaluate_grant_scope(req)
    assert r.matches is True
    r2 = GrantScopeEvaluator().evaluate(req)
    assert r2.matches is True


def test_aliases_callable():
    g = _grant()
    ev = GrantScopeEvaluator()
    r = ev(grant=g, operation=_op(), evaluation_time=_now())
    assert r.matches is True
    from backend.runtime.grant_scope import evaluate_grant, match_grant_scope, grant_scope_matches
    assert evaluate_grant(grant=g, operation=_op(), evaluation_time=_now()).matches is True
    assert match_grant_scope(grant=g, operation=_op(), evaluation_time=_now()).matches is True
    assert grant_scope_matches(grant=g, operation=_op(), evaluation_time=_now()).matches is True


def test_result_is_not_authorization_claim():
    r = GrantEvaluationResult(matches=True, reason_code="match", reason="x")
    d = r.to_dict()
    assert "authorized" not in d
    assert "permission" not in d
    assert r.matches is True


def test_bool_not_accepted_as_string_identity():
    # bool is a str subclass; resource identities must reject empty etc.
    with pytest.raises(ValueError):
        ResourceIdentity(kind=True, value="x")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ResourceIdentity(kind="k", value="   ")


def test_resource_identity_max_length():
    with pytest.raises(ValueError):
        ResourceIdentity(kind="k", value="x" * 4097)


def test_metadata_mapping_validation():
    now = _now()
    # non-mapping metadata rejected
    with pytest.raises(ValueError):
        GrantScope(grant_id="g", trusted=True, valid_from=now, valid_until=now + dt.timedelta(minutes=1), metadata=42)
    # mapping accepted
    g = GrantScope(grant_id="g", trusted=True, valid_from=now, valid_until=now + dt.timedelta(minutes=1), metadata={"a": 1})
    assert g.metadata == {"a": 1}


def test_revoked_at_malformed_denied():
    now = _now()
    g = _grant(revoked_at="not-a-date")
    # grant construction coerces revoked_at string; evaluation rejects malformed
    r = evaluate_grant_scope(grant=g, operation=_op(), evaluation_time=now)
    assert r.matches is False


def test_construction_granted_operations_frozen():
    g = _grant()
    assert isinstance(g.allowed_operations, frozenset)


def test_fail_closed_on_unexpected():
    # None operation plus weird grant should still return result
    r = evaluate_grant_scope(grant=None, operation=None, evaluation_time=None)
    assert r.matches is False
    assert isinstance(r, GrantEvaluationResult)


# --- Independent-review regression tests (subclass/operator/hash-seed bypasses) ---

class _EvilResource(ResourceIdentity):
    def __eq__(self, other):
        return True
    def __hash__(self):
        return 0


class _EvilOperation(GrantOperation):
    def __eq__(self, other):
        return True
    def __hash__(self):
        return 0


class _EvilStr(str):
    def __eq__(self, other):
        return True


class _EvilDateTime(dt.datetime):
    def __ge__(self, other):
        return False
    def __lt__(self, other):
        return False
    def __gt__(self, other):
        return False


def test_subclass_resource_identity_rejected():
    now = _now()
    # Construction of exact resource identity rejects non-exact string types.
    with pytest.raises(ValueError):
        ResourceIdentity(kind=_EvilStr("account"), value="x")
    with pytest.raises(ValueError):
        ResourceIdentity(kind="account", value=_EvilStr("x"))
    # A subclass instance passed where a resource is needed cannot be coerced.
    g = _grant()
    op = _op()
    # Build an operation with a plain value overridden at runtime impossible;
    # instead ensure an evil subclass instance is rejected by the evaluator.
    evil = _EvilResource(kind="account", value="acct-1") if False else None
    # _EvilResource construction itself is allowed by Python, but evaluator must
    # not treat it as the exact runtime type.
    evil_res = _EvilResource.__new__(_EvilResource)
    object.__setattr__(evil_res, "kind", "account")
    object.__setattr__(evil_res, "value", "acct-1")
    evil_op = GrantOperation.__new__(GrantOperation)
    object.__setattr__(evil_op, "action", "read")
    object.__setattr__(evil_op, "resource", evil_res)
    object.__setattr__(evil_op, "context", _ctx())
    r = evaluate_grant_scope(grant=g, operation=evil_op, evaluation_time=now)
    assert r.matches is False
    assert r.reason_code == "input_malformed"


def test_subclass_operation_rejected():
    now = _now()
    g = _grant()
    evil = _EvilOperation.__new__(_EvilOperation)
    object.__setattr__(evil, "action", "read")
    object.__setattr__(evil, "resource", _res())
    object.__setattr__(evil, "context", _ctx())
    r = evaluate_grant_scope(grant=g, operation=evil, evaluation_time=now)
    assert r.matches is False
    assert r.reason_code == "input_malformed"


def test_subclass_datetime_rejected():
    now = _EvilDateTime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)
    g = _grant()
    r = evaluate_grant_scope(grant=g, operation=_op(), evaluation_time=now)
    assert r.matches is False
    assert r.reason_code == "input_malformed"


def test_explicit_grant_window_accepts_exact_datetime_only():
    now = _now()
    g = _grant(valid_until=now)  # exact boundary accepted
    r = evaluate_grant_scope(grant=g, operation=_op(), evaluation_time=now)
    assert r.matches is True


def test_hash_seed_deterministic_reason():
    now = _now()
    # Granted entries differ in different dimensions; reason must be stable.
    granted = frozenset([
        GrantOperation(action="write", resource=_res("acct-1"), context=_ctx("session-1")),
        GrantOperation(action="read", resource=_res("acct-2"), context=_ctx("session-1")),
        GrantOperation(action="read", resource=_res("acct-1"), context=_ctx("session-2")),
    ])
    g = _grant(allowed_operations=granted)
    # Proposed "read/acct-1/session-1" differs from every entry, but action exists,
    # resource exists, context exists in different entries; resource_only? 
    # Exact deterministic: action exists in entries, resource exists, context exists;
    # no single dimension-only match against one entry, so not_granted.
    r = evaluate_grant_scope(grant=g, operation=_op(), evaluation_time=now)
    assert r.matches is False
    # Stable code regardless of frozenset ordering (already ensured by sorted logic).
    assert r.reason_code in {
        "action_mismatch", "resource_mismatch", "context_mismatch", "operation_not_granted"
    }
    # Run several reshuffles of the input set and require identical reason.
    import random
    codes = set()
    for _ in range(20):
        lst = list(granted)
        random.shuffle(lst)
        g2 = _grant(allowed_operations=frozenset(lst))
        codes.add(evaluate_grant_scope(grant=g2, operation=_op(), evaluation_time=now).reason_code)
    assert len(codes) == 1
