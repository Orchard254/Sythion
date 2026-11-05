"""Durable composer inbound request receipt primitive (alias module)."""

try:
    from backend.runtime.composer_request_receipt import *  # noqa: F401,F403
except Exception:  # pragma: no cover - alternate invocation layout
    try:
        from runtime.composer_request_receipt import *  # noqa: F401,F403
    except Exception:
        from composer_request_receipt import *  # noqa: F401,F403