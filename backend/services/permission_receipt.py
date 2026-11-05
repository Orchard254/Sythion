"""Alias module for the durable permission decision receipt primitive."""
try:
    from backend.services.permission_decision_receipt import *  # noqa: F401,F403
except Exception:  # pragma: no cover
    from services.permission_decision_receipt import *  # type: ignore  # noqa: F401,F403
