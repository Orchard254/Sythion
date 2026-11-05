"""Services-layer access to the durable permission decision receipt primitive."""

try:
    from backend.runtime.permission_decision_receipt import *  # noqa: F401,F403
except Exception:  # pragma: no cover - alternate invocation layout
    from runtime.permission_decision_receipt import *  # type: ignore  # noqa: F401,F403
