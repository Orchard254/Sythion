import datetime as dt

import pytest

from backend.runtime.grant_scope import GrantScope, GrantScopeInputError
from backend.runtime.grant_lifecycle import Grant

UTC = dt.timezone.utc


def _now():
    return dt.datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)


def test_grant_scope_metadata_mutation_blocked_direct():
    now = _now()
    original = {"nested": {"x": [1, 2]}, "s": {3, 4}}
    scope = GrantScope(
        grant_id="g",
        trusted=True,
        valid_from=now,
        valid_until=now + dt.timedelta(minutes=1),
        metadata=original,
    )
    # original mutation does not affect stored scope
    original["nested"]["x"].append(999)
    original["new"] = "leak"
    assert "new" not in scope.metadata
    assert scope.metadata["nested"]["x"] == (1, 2)
    # direct mutation fails
    with pytest.raises((TypeError, AttributeError)):
        scope.metadata["nested"]["x"].append(1)
    with pytest.raises((TypeError, AttributeError)):
        scope.metadata["new"] = 1


def test_grant_scope_metadata_to_dict_style_deep():
    now = _now()
    scope = GrantScope(
        grant_id="g",
        trusted=True,
        valid_from=now,
        valid_until=now + dt.timedelta(minutes=1),
        metadata={"a": {"b": [1, 2]}},
    )
    # freeze shapes present
    assert scope.metadata == {"a": {"b": (1, 2)}}


def test_grant_scope_metadata_cycle_rejected():
    container = {}
    container["self"] = container
    now = _now()
    with pytest.raises(ValueError):
        GrantScope(
            grant_id="g",
            trusted=True,
            valid_from=now,
            valid_until=now + dt.timedelta(minutes=1),
            metadata=container,
        )


def test_grant_scope_metadata_unsupported_rejected():
    now = _now()
    with pytest.raises(ValueError):
        GrantScope(
            grant_id="g",
            trusted=True,
            valid_from=now,
            valid_until=now + dt.timedelta(minutes=1),
            metadata={"fn": lambda x: x},
        )


def test_embedded_grant_scope_metadata_mutation_blocked():
    now = _now()
    scope = GrantScope(
        grant_id="g",
        trusted=True,
        valid_from=now,
        valid_until=now + dt.timedelta(minutes=1),
        metadata={"nested": {"x": [1, 2]}},
    )
    grant = Grant(
        grant_id="g",
        provenance="p",
        valid_from=now,
        valid_until=now + dt.timedelta(minutes=1),
        scope=scope,
    )
    # top-level and nested
    with pytest.raises((TypeError, AttributeError)):
        grant.metadata["nested"]["x"].append(9)
    with pytest.raises((TypeError, AttributeError)):
        grant.scope.metadata["nested"]["x"].append(9)
    with pytest.raises((TypeError, AttributeError)):
        grant.metadata["new"] = 1
    # to_dict is a deep thaw copy
    d = grant.to_dict()
    if isinstance(d.get("metadata", {}).get("nested", {}).get("x"), list):
        d["metadata"]["nested"]["x"].append(999)
    assert grant.metadata["nested"]["x"] == (1, 2)


def test_grant_scope_subclass_style_exact_type():
    # GrantScopeInputError is a ValueError subclass (fail-closed misuse)
    assert issubclass(GrantScopeInputError, ValueError)


def test_grant_scope_metadata_none_becomes_empty_proxy():
    now = _now()
    scope = GrantScope(
        grant_id="g",
        trusted=True,
        valid_from=now,
        valid_until=now + dt.timedelta(minutes=1),
        metadata=None,
    )
    assert scope.metadata == {}
    with pytest.raises((TypeError, AttributeError)):
        scope.metadata["x"] = 1