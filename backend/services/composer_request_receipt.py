"""Services-layer access to the durable composer request receipt primitive."""

try:
    from backend.runtime.composer_request_receipt import *  # noqa: F401,F403
except Exception:  # pragma: no cover - alternate invocation layout
    from runtime.composer_request_receipt import *  # noqa: F401,F403