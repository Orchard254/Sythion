"""Services-layer access to the durable composer inbound request receipt primitive."""

try:
    from backend.runtime.composer_request_receipt import *  # noqa: F401,F403
except Exception:  # pragma: no cover - alternate invocation layout
    try:
        from backend.runtime.composer_inbound_request_receipt import *  # noqa: F401,F403
    except Exception:
        from runtime.composer_request_receipt import *  # noqa: F401,F403