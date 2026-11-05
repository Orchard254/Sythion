"""Compatibility alias for the opt-in indexing coverage seam."""

from backend.services.grounded_discussion_index_coverage import *  # noqa: F401,F403
from backend.services.grounded_discussion_index_coverage import (  # noqa: F401
    IndexCoverageError,
    IndexCoverageManager,
    IndexCoverageEntry,
    IndexCoverageResult,
    read_session_index_coverage,
)