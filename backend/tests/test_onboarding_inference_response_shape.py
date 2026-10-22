"""The onboarding inference gate must parse the provider's REAL response shape.

The first-run gate posts to ``/api/v1/onboarding/inference`` and refuses to
let the user continue unless the model answers.  Ollama Cloud answers in the
NATIVE ``/api/chat`` shape (``{"message": {"content": ...}}``).  The gate used
to read only the OpenAI-compatible and the legacy shapes, so a perfectly good
answer was reported as ``"Inference returned an empty response"`` (HTTP 502)
and a fresh install could not get past setup - the user saw nothing but
``"Request failed with status code 502"``.

Every body in this file is a CONSTANT recorded from the provider; the native
one is the verbatim body observed from the live API.  NO test calls the
provider, reads the credential vault or opens a socket: the provider is a
stub whose ``chat()`` returns the body under test, the database is an
in-memory SQLite session and the secret store is the in-memory one.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.main import app
from backend.models.database import Base, get_db
from backend.core.secrets import MemoryStore, set_secret_store_for_testing

# The NATIVE Ollama /api/chat body, verbatim, as observed from the live API:
#   {"model":"minimax-m3.1-flash","message":{"role":"assistant","content":"OK"},
#    "done":true,"done_reason":"stop","eval_count":2}
NATIVE_BODY = {
    "model": "minimax-m3.1-flash",
    "message": {"role": "assistant", "content": "OK"},
    "done": True,
    "done_reason": "stop",
    "eval_count": 2,
}

# The OpenAI-compatible chat shape.
OPENAI_BODY = {"choices": [{"message": {"role": "assistant", "content": "OK"}}]}

# The OpenAI-compatible legacy completions shape.
LEGACY_CHOICE_BODY = {"choices": [{"text": "OK"}]}

# The legacy Ollama /api/generate shape.
LEGACY_GENERATE_BODY = {"response": "OK"}


def _parse(response):
    """Parse ``response`` with the gate's own helper.

    The helper is imported INSIDE the function so that this module can be
    collected on a tree where it does not exist yet: the endpoint tests
    below then fail BEHAVIOURALLY (502 instead of 200) instead of the whole
    module failing to import.
    """
    from backend.api.routes import _extract_inference_text

    return _extract_inference_text(response)


class _Provider:
    """A provider stub whose ``chat()`` answers with the body under test."""

    def __init__(self, body):
        self.body = body

    def provider_id(self):
        return "ollama-cloud"

    def selected_model_id(self):
        return "m1"

    def list_models(self):
        return [{"name": "m1"}]

    def health(self):
        from backend.runtime.model_provider import ModelHealth

        return ModelHealth("ollama-cloud", ModelHealth.HEALTHY)

    async def chat(self, messages, max_tokens=4000, timeout=300.0):
        return self.body


def _client(monkeypatch, provider, selected="m1"):
    """A TestClient whose provider is the stub and whose database is in memory."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    import backend.core.config as core_config

    monkeypatch.setattr(core_config, "resolve_selected_model", lambda: selected)

    import backend.runtime.model_provider as model_provider

    monkeypatch.setattr(model_provider, "get_default_provider", lambda: provider)

    from backend.adapters import ollama_adapter

    monkeypatch.setattr(ollama_adapter, "get_adapter", lambda: None)

    app.dependency_overrides[get_db] = lambda: session

    import backend.main as backend_main

    previous_token = backend_main.control_token
    backend_main.control_token = "onboarding-shape-token"
    client = TestClient(
        app,
        headers={
            "Origin": "tauri://localhost",
            "X-Control-Token": "onboarding-shape-token",
        },
    )
    return client, previous_token, backend_main


def _post_inference(monkeypatch, body, model_id="m1", selected="m1"):
    """POST the inference gate with a provider that answers ``body``."""
    client, previous_token, backend_main = _client(
        monkeypatch, _Provider(body), selected
    )
    try:
        payload = {} if model_id is None else {"model_id": model_id}
        return client.post("/api/v1/onboarding/inference", json=payload)
    finally:
        backend_main.control_token = previous_token
        app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _in_memory_secret_store():
    """Never touch the real Windows Credential Manager.

    The route reads no secret, but the guard costs one line and removes the
    question: the in-memory store is installed for the duration of every
    test in this file and reset afterwards.
    """
    set_secret_store_for_testing(MemoryStore())
    yield
    set_secret_store_for_testing(None)


# --- the parser --------------------------------------------------------------


def test_the_native_provider_shape_is_parsed():
    """The live native body must yield the model's text."""
    assert _parse(NATIVE_BODY) == "OK"


def test_the_openai_compatible_shape_is_parsed():
    """The OpenAI-compatible chat shape must keep working."""
    assert _parse(OPENAI_BODY) == "OK"


def test_the_legacy_completion_shape_is_parsed():
    """The OpenAI-compatible legacy completions shape must keep working."""
    assert _parse(LEGACY_CHOICE_BODY) == "OK"


def test_the_legacy_generate_shape_is_parsed():
    """The legacy Ollama /api/generate shape must keep working."""
    assert _parse(LEGACY_GENERATE_BODY) == "OK"


def test_the_native_shape_is_preferred_over_the_others():
    """The ORDER is pinned, not just the membership: native wins."""
    body = {
        "message": {"role": "assistant", "content": "native"},
        "choices": [{"message": {"content": "chat"}, "text": "legacy-choice"}],
        "response": "generate",
    }
    assert _parse(body) == "native"


def test_a_shape_that_is_none_of_the_known_ones_yields_no_text():
    """An unknown body yields no text instead of raising."""
    for body in [{}, {"foo": "bar"}, [], "OK", None, 42]:
        assert _parse(body) == ""


def test_a_malformed_body_does_not_raise():
    """Every access is type-guarded, so a malformed body yields no text."""
    malformed = [
        {"message": "not-a-dict"},
        {"message": {"content": ["a"]}},
        {"choices": {}},
        {"choices": [None]},
        {"choices": [{"message": "x"}]},
        {"choices": [{"message": {"content": None}}]},
    ]
    for body in malformed:
        assert _parse(body) == ""


# --- the candidate rule ------------------------------------------------------

# The parser's rule, over the whole enumerable domain of candidate values: a
# candidate is taken only if its value is a ``str``.  These are the parser's
# FOUR branches, in the parser's own order (the ``_extract_inference_text``
# helper in backend/api/routes.py):
#   1. native Ollama /api/chat      -> response["message"]["content"]
#   2. OpenAI-compatible chat       -> response["choices"][0]["message"]["content"]
#   3. OpenAI-compatible legacy     -> response["choices"][0]["text"]
#   4. legacy Ollama /api/generate  -> response["response"]
_CANDIDATE_BRANCHES = (
    ("native", lambda candidate: {"message": {"content": candidate}}),
    (
        "openai-chat",
        lambda candidate: {"choices": [{"message": {"content": candidate}}]},
    ),
    ("legacy-choice", lambda candidate: {"choices": [{"text": candidate}]}),
    ("legacy-generate", lambda candidate: {"response": candidate}),
)

# The non-str candidate types: an int, a float, a bool, a dict and a list.
# None of them is the model's text, so the rule rejects every one of them.
_NON_STR_CANDIDATES = (7, 7.5, True, {"a": 1}, [1, 2])


def _candidate_cases():
    """Every case the candidate rule is pinned over.

    Five non-str candidate types in each of the four branches - 20 cases that
    must yield ``''`` - plus the four POSITIVE cases, a ``str`` candidate in
    each branch, that must still be returned.  The positive cases are what
    stops the rule from being satisfied by a helper that always answers ``''``.
    """
    cases = []
    for branch, build in _CANDIDATE_BRANCHES:
        for candidate in _NON_STR_CANDIDATES:
            cases.append(
                (
                    "%s / %s" % (branch, type(candidate).__name__),
                    build(candidate),
                    "",
                )
            )
        cases.append(("%s / str" % branch, build("OK"), "OK"))
    return cases


# The whole matrix is ONE parameter set on purpose: the rule is a single
# logical property and the round's count contract is one collected test, while
# every case inside the matrix still carries its own label, so a failure names
# the exact branch and candidate type that broke.
_CANDIDATE_CASES = _candidate_cases()


@pytest.mark.parametrize(
    "case",
    [_CANDIDATE_CASES],
    ids=["every-branch-and-every-candidate-type"],
)
def test_a_candidate_is_taken_only_if_its_value_is_a_str(case):
    """A non-str candidate is never the model's text - in ANY branch.

    The rule the parser applies is uniform: a candidate is taken only if its
    value is a ``str``.  Before this test the rule was pinned for the ``int``
    value only and for three of the four branches, so a change that widened
    ``_candidate`` to accept a float, a bool, a dict or a list - or that
    accepted an int in the fourth branch - would have passed the whole suite
    while the narrowing silently widened.

    The helper is imported INSIDE the test function, exactly as ``_parse``
    above does, so that a tree on which the helper does not exist yet fails
    this test BEHAVIOURALLY instead of turning the whole module into a
    collection error.
    """
    from backend.api.routes import _extract_inference_text

    for label, body, expected in case:
        assert _extract_inference_text(body) == expected, label


# --- the endpoint ------------------------------------------------------------


def test_the_endpoint_answers_200_for_the_native_shape(monkeypatch):
    """The live body must not be reported as an empty response any more."""
    res = _post_inference(monkeypatch, NATIVE_BODY)
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["response_preview"] == "OK"
    assert body["model"] == "m1"


def test_the_endpoint_answers_200_for_the_openai_compatible_shape(monkeypatch):
    """The OpenAI-compatible shape still answers 200."""
    res = _post_inference(monkeypatch, OPENAI_BODY)
    assert res.status_code == 200
    assert res.json()["response_preview"] == "OK"


def test_the_endpoint_answers_200_for_the_legacy_shape(monkeypatch):
    """Both legacy shapes still answer 200."""
    for body in (LEGACY_CHOICE_BODY, LEGACY_GENERATE_BODY):
        res = _post_inference(monkeypatch, body)
        assert res.status_code == 200
        assert res.json()["response_preview"] == "OK"


def test_the_endpoint_still_reports_502_for_a_genuinely_empty_answer(monkeypatch):
    """An answer with no text at all is still an empty response."""
    res = _post_inference(monkeypatch, {"done": True, "done_reason": "stop"})
    assert res.status_code == 502
    assert res.json()["detail"] == "Inference returned an empty response"


def test_the_endpoint_still_reports_502_for_an_unrecognised_shape(monkeypatch):
    """A body the gate does not understand is a 502, never a 500."""
    res = _post_inference(monkeypatch, {"foo": "bar"})
    assert res.status_code == 502
    assert res.status_code != 500
    assert res.json()["detail"] == "Inference returned an empty response"


def test_the_endpoint_reports_502_for_a_numeric_provider_answer(monkeypatch):
    """A number is not the model's text, so the gate answers 502.

    DELIBERATE change from the pre-fix parser: the old inline parse let any
    truthy value through, so a provider body of ``{"response": 7}`` answered
    **200** with a ``response_preview`` of ``"7"``.  The parser's rule - a
    candidate is taken only if its value is a ``str`` - rejects a number, so
    the same body is an empty response now.  Pinned here so the narrowing is
    a recorded decision and not a drift nobody noticed.

    The rule is uniform, so a non-str of ANY type (int, float, bool, dict,
    list) is an empty response.  The four bodies below cover ALL FOUR
    branches of the parser - the native ``/api/chat`` shape, the
    OpenAI-compatible chat shape, the OpenAI-compatible legacy completions
    shape and the legacy ``/api/generate`` shape - so no branch can widen
    unnoticed; the remaining non-str types are pinned at the helper level by
    ``test_a_candidate_is_taken_only_if_its_value_is_a_str``.
    """
    for body in (
        {"response": 7},
        {"message": {"content": 7}},
        {"choices": [{"message": {"content": 7}}]},
        {"choices": [{"text": 7}]},
    ):
        res = _post_inference(monkeypatch, body)
        assert res.status_code == 502
        assert res.status_code != 200
        assert res.status_code != 500
        assert res.json()["detail"] == "Inference returned an empty response"


def test_the_endpoint_contract_is_unchanged(monkeypatch):
    """The wire contract of the gate is exactly what it was."""
    res = _post_inference(monkeypatch, NATIVE_BODY, model_id=None, selected=None)
    assert res.status_code == 400
    assert res.json()["detail"] == "No model selected"

    res = _post_inference(monkeypatch, NATIVE_BODY, model_id="nope", selected="m1")
    assert res.status_code == 400
    assert res.json()["detail"] == "Model 'nope' is not available"

    res = _post_inference(monkeypatch, NATIVE_BODY)
    assert res.status_code == 200
    assert set(res.json().keys()) == {"success", "model", "response_preview"}
