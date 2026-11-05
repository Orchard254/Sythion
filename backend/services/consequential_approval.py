"""Services-layer access to the bounded consequential approval foundation."""

try:
    from backend.runtime.consequential_approval import *  # noqa: F401,F403
except Exception:  # pragma: no cover - alternate invocation layout
    from runtime.consequential_approval import *  # type: ignore  # noqa: F401,F403