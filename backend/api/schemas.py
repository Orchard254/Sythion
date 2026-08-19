"""
Pydantic schemas for API request/response validation.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum

try:
    from backend.core.hotkeys import HOTKEY_KEYS_DESC
except ImportError:  # top-level `api.schemas` import path
    from core.hotkeys import HOTKEY_KEYS_DESC


class TaskState(str, Enum):
    """Valid task states."""
    CREATED = "created"
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    PAUSED = "paused"


# Task Schemas

class TaskCreate(BaseModel):
    """Schema for creating a new task."""
    original_request: str = Field(..., min_length=1, max_length=10000)
    idempotency_key: Optional[str] = None


class TaskReply(BaseModel):
    """Schema for replying to a waiting_for_user task."""
    answer: str = Field(..., min_length=1, max_length=5000)


class TaskResponse(BaseModel):
    """Schema for task response."""
    id: str
    original_request: str
    normalized_goal: Optional[str] = None
    state: TaskState
    version: int
    active_action_id: Optional[str] = None
    objective_id: Optional[str] = None
    pending_question: Optional[str] = None
    final_result: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    terminal_reason: Optional[str] = None

    model_config = {"from_attributes": True}


class TaskListResponse(BaseModel):
    """Schema for listing tasks."""
    tasks: List[TaskResponse]
    total: int


class ObjectiveStatus(str, Enum):
    """Valid durable objective states."""
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class PlanStepStatus(str, Enum):
    """Valid durable plan-step states."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class PlanStepCreate(BaseModel):
    """Schema for adding a plan step to an objective."""
    description: str = Field(..., min_length=1, max_length=2000)
    status: PlanStepStatus = PlanStepStatus.PENDING


class PlanStepUpdate(BaseModel):
    """Schema for updating an existing plan step."""
    description: Optional[str] = Field(default=None, min_length=1, max_length=2000)
    status: Optional[PlanStepStatus] = None
    result_summary: Optional[str] = Field(default=None, max_length=5000)
    blocker_reason: Optional[str] = Field(default=None, max_length=5000)


class PlanStepResponse(BaseModel):
    """Schema for a durable plan step."""
    id: int
    objective_id: str
    step_order: int
    description: str
    status: PlanStepStatus
    linked_task_id: Optional[str] = None
    result_summary: Optional[str] = None
    blocker_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ObjectiveCreate(BaseModel):
    """Schema for creating a durable objective."""
    title: str = Field(..., min_length=1, max_length=200)
    purpose: Optional[str] = Field(default=None, max_length=5000)
    priority: int = Field(default=3, ge=1, le=5)
    plan_steps: List[str] = Field(default_factory=list, max_length=20)
    completion_criteria: Optional[str] = Field(default=None, max_length=5000)
    parent_objective_id: Optional[str] = Field(default=None)


class ObjectiveUpdate(BaseModel):
    """Schema for updating a durable objective."""
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    purpose: Optional[str] = Field(default=None, max_length=5000)
    status: Optional[ObjectiveStatus] = None
    priority: Optional[int] = Field(default=None, ge=1, le=5)
    current_summary: Optional[str] = Field(default=None, max_length=5000)
    completion_criteria: Optional[str] = Field(default=None, max_length=5000)
    parent_objective_id: Optional[str] = Field(default=None)


class ObjectiveResponse(BaseModel):
    """Schema for a durable objective plus its plan steps."""
    id: str
    title: str
    purpose: Optional[str] = None
    status: ObjectiveStatus
    priority: int
    current_summary: Optional[str] = None
    last_task_id: Optional[str] = None
    completion_criteria: Optional[str] = None
    criteria_verified: bool = False
    parent_objective_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    steps: List[PlanStepResponse] = Field(default_factory=list)
    sub_objectives: List["ObjectiveResponse"] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class ObjectiveListResponse(BaseModel):
    """Schema for listing durable objectives."""
    objectives: List[ObjectiveResponse]
    total: int


# Event Schemas

class TaskEventResponse(BaseModel):
    """Schema for task event response."""
    id: int
    task_id: str
    event_type: str
    event_data: Dict[str, Any]
    sequence_num: int
    created_at: datetime

    model_config = {"from_attributes": True}


# Tool Execution Schemas

class ToolInput(BaseModel):
    """Base schema for tool input."""
    pass


class ToolOutput(BaseModel):
    """Schema for tool execution output."""
    model_config = {"protected_namespaces": ()}

    success: bool
    data: Optional[Dict[str, Any]] = None
    model_summary: Optional[str] = None
    error: Optional[Dict[str, Any]] = None
    evidence: Optional[Dict[str, Any]] = None
    duration_ms: int = 0


# System Read Tool Schemas

class SystemTimeInput(ToolInput):
    """Input for system time tool."""
    pass


class SystemBatteryInput(ToolInput):
    """Input for battery status tool."""
    pass


class SystemDiskInput(ToolInput):
    """Input for disk space tool."""
    path: Optional[str] = None


class SystemProcessesInput(ToolInput):
    """Input for process list tool."""
    limit: int = Field(default=50, ge=1, le=200)
    include_cpu: bool = Field(default=False, description="Include per-process CPU usage percentage")
    include_memory: bool = Field(default=False, description="Include per-process memory usage (MB)")
    sort_by: str = Field(default="name", description="Sort by: 'name', 'cpu', 'memory', 'pid'")
    include_children: bool = Field(default=False, description="Include child process count")


class SystemActiveWindowInput(ToolInput):
    """Input for active window tool."""
    pass


class SystemFileRootsInput(ToolInput):
    """Input for the file-roots introspection tool."""
    pass


class SystemCapabilitiesInput(ToolInput):
    """Input for the capability-introspection tool."""
    pass


class SystemVolumeInput(ToolInput):
    """Input for the volume control tool."""
    volume_percentage: Optional[float] = Field(default=None, ge=0, le=100, description="Set volume level (0-100). None to read only.")
    is_mute: Optional[bool] = Field(default=None, description="Set mute state (True/False). None to read only.")


class SystemPowerInput(ToolInput):
    """Input for the power/battery status tool."""
    pass


class SystemPowerControlInput(ToolInput):
    """Input for power control operations (lock, sleep, restart, shutdown)."""
    action: str = Field(
        ...,
        description="Power action to perform: 'lock', 'sleep', 'restart', or 'shutdown'"
    )
    delay_seconds: int = Field(
        default=0,
        ge=0,
        le=3600,
        description="Delay in seconds before executing the action (0 = immediate, max 1 hour)"
    )


class BrightnessControlInput(ToolInput):
    """Input for display brightness control."""
    action: str = Field(
        ...,
        description="Brightness action: 'get' to read current brightness, 'set' to change brightness"
    )
    brightness: int = Field(
        default=None,
        ge=0,
        le=100,
        description="Brightness level 0-100 (required when action='set')"
    )


class NetworkStatusInput(ToolInput):
    """Input for the network status tool."""
    pass


class WindowListInput(ToolInput):
    """Input for listing all open windows."""
    include_minimized: bool = False
    visible_only: bool = True


class BrowserTabsInput(ToolInput):
    """Input for discovering open browser tabs."""
    browser: Optional[str] = Field(
        default="all",
        description="Filter by browser name: 'chrome', 'edge', 'brave', 'firefox', or 'all'"
    )


class BrowserSwitchTabInput(ToolInput):
    """Input for switching to a specific browser tab by its ID."""
    tab_id: int = Field(..., description="The numeric tab ID from browser_tabs output to switch to")


class BrowserOpenTabInput(ToolInput):
    """Input for opening a new browser tab at a given URL."""
    url: str = Field(default="about:blank", description="The URL to open in the new tab")
    active: bool = Field(default=True, description="Whether to immediately focus the new tab")


class BrowserCloseTabInput(ToolInput):
    """Input for closing a specific browser tab by its ID."""
    tab_id: int = Field(..., description="The numeric tab ID from browser_tabs output to close")


class BrowserReadPageInput(ToolInput):
    """Input for reading the content, headings, and interactive elements of an open browser tab."""
    tab_id: Optional[int] = Field(
        default=None,
        description="Optional numeric tab ID from browser_tabs to read. If omitted, reads the currently active tab in Chrome."
    )


class BrowserInteractInput(ToolInput):
    """Input for interacting with elements on an open webpage."""
    action: str = Field(
        ...,
        description="The action to perform: 'click' (click an element), 'type' (type text into an input), 'press_key' (press a key like 'Enter'), or 'scroll' (scroll 'up' or 'down')"
    )
    selector: Optional[str] = Field(
        default=None,
        description="CSS selector (e.g. 'input[name=q]', '#search-button', 'button.submit') or element text/label to target"
    )
    text: Optional[str] = Field(
        default=None,
        description="Text to type when action is 'type'"
    )
    key: Optional[str] = Field(
        default=None,
        description="Key to press (e.g. 'Enter', 'Escape', 'Tab') when action is 'press_key' or after typing"
    )
    tab_id: Optional[int] = Field(
        default=None,
        description="Optional numeric tab ID to interact on; defaults to the currently active tab"
    )


class BrowserScreenshotInput(ToolInput):
    """Input for capturing a visual screenshot of an open browser tab."""
    tab_id: Optional[int] = Field(
        default=None,
        description="Optional numeric tab ID from browser_tabs to capture. If omitted, captures the currently active tab."
    )
    save_path: Optional[str] = Field(
        default=None,
        description="Optional file path to save the PNG image. If omitted, automatically saves to Sythion screenshots directory."
    )


class ToolProposeInput(ToolInput):


    """Input for proposing a new tool."""
    name: str = Field(..., min_length=1, max_length=64, description="Tool name (lowercase, underscores)")
    description: str = Field(..., min_length=1, max_length=500, description="What the tool does")
    input_schema: Dict[str, Any] = Field(..., description="Pydantic schema as JSON object")
    implementation: str = Field(..., min_length=1, description="Python source code defining a BaseTool subclass")


# File Tool Schemas

class FileListInput(ToolInput):
    """Input for listing directory contents."""
    path: str
    include_hidden: bool = False


class FileReadInput(ToolInput):
    """Input for reading a file."""
    path: str
    max_bytes: int = Field(default=1048576, ge=1, le=10485760)  # Default 1MB, max 10MB


class FileCreateInput(ToolInput):
    """Input for creating a file."""
    path: str
    content: str
    overwrite: bool = False


class FileWriteInput(ToolInput):
    """Input for writing to a file."""
    path: str
    content: str
    append: bool = False


class FileMoveInput(ToolInput):
    """Input for moving a file."""
    source_path: str
    destination_path: str
    overwrite: bool = False


class FileCopyInput(ToolInput):
    """Input for copying a file."""
    source_path: str
    destination_path: str
    overwrite: bool = False


class FileDeleteInput(ToolInput):
    """Input for deleting a file."""
    path: str
    use_recycle_bin: bool = True


class FileSearchInput(ToolInput):
    """Input for searching file contents."""
    path: str
    pattern: str
    max_results: int = Field(default=50, ge=1, le=500)
    include_hidden: bool = False
    file_extensions: Optional[List[str]] = None


# Application Tool Schemas

class AppListInput(ToolInput):
    """Input for listing installed applications."""
    search_term: Optional[str] = None


class AppLaunchInput(ToolInput):
    """Input for launching an application."""
    app_id: str
    new_instance: bool = Field(
        default=False,
        description="Open a genuinely new window/instance instead of reusing an existing open window (default: reuse existing if one is open)",
    )


class AppFocusInput(ToolInput):
    """Input for focusing an application window."""
    app_id: str


class AppCloseInput(ToolInput):
    """Input for closing an application."""
    app_id: str
    force: bool = False


# Package Tool Schemas

class PackageSearchInput(ToolInput):
    """Input for searching packages."""
    query: str
    limit: int = Field(default=10, ge=1, le=50)


class PackageListInput(ToolInput):
    """Input for listing installed packages."""
    search_term: Optional[str] = None


class PackageInstallInput(ToolInput):
    """Input for installing a package."""
    package_id: str
    source: Optional[str] = None


class PackageUninstallInput(ToolInput):
    """Input for uninstalling a package."""
    package_id: str


# GUI Tool Schemas

class GUIObserveInput(ToolInput):
    """Input for GUI observation."""
    window_title: Optional[str] = None
    element_description: Optional[str] = None
    use_ocr: bool = False


class GUIFocusInput(ToolInput):
    """Input for focusing a window."""
    window_title: str


class GUIInvokeInput(ToolInput):
    """Input for invoking a UI element."""
    window_title: str
    element_description: str


class GUISetValueInput(ToolInput):
    """Input for setting a UI element value."""
    window_title: str
    element_description: str
    value: str


class GUIClickInput(ToolInput):
    """Input for coordinate click."""
    window_title: str
    x: int
    y: int


class GUITypeInput(ToolInput):
    """Input for keyboard input."""
    window_title: str
    text: str


class GUIHotkeyInput(ToolInput):
    """Input for hotkey execution."""
    window_title: str = Field(description="Window title that must be foreground before the hotkey is pressed")
    keys: List[str] = Field(description=HOTKEY_KEYS_DESC)


class GUIPasteInput(ToolInput):
    """Input for clipboard paste execution."""
    window_title: str
    text: Optional[str] = None


class GUIScreenshotInput(ToolInput):
    """Input for GUI screenshot capture."""
    window_title: Optional[str] = Field(
        default=None,
        description="Window title to capture. If omitted, captures the currently active window."
    )
    save_path: Optional[str] = Field(
        default=None,
        description="File path to save the PNG image. If omitted, saves to Sythion screenshots directory."
    )


class ClipboardReadInput(ToolInput):
    """Input for reading clipboard contents."""
    pass


class FileHashInput(ToolInput):
    """Input for computing file hash."""
    path: str = Field(description="Path to the file to hash")
    algorithm: str = Field(
        default="sha256",
        description="Hash algorithm: 'md5', 'sha1', 'sha256', or 'sha512'"
    )


class GUIBatchFormInput(ToolInput):
    """Input for batch form filling."""
    window_title: str = Field(description="Window title that must be foreground")
    fields: List[Dict[str, str]] = Field(
        description="List of field operations. Each dict must have 'element' (description) and 'value' (text to enter). Optional: 'action' ('type' or 'set_value', default 'type')"
    )
    delay_between_fields: float = Field(
        default=0.5,
        ge=0.1,
        le=5.0,
        description="Delay in seconds between filling each field (0.1-5.0)"
    )


# Control Tool Schemas


# Model Decision Schemas

class ModelDecisionCompleteTask(BaseModel):
    """Schema for complete_task model decision."""
    summary: str
    warnings: Optional[List[str]] = None


class ModelDecisionFailTask(BaseModel):
    """Schema for fail_task model decision."""
    code: str
    user_message: str


class ModelDecisionRequestUserInput(BaseModel):
    """Schema for request_user_input model decision."""
    question: str
    required_fields: Optional[List[str]] = None


class ModelDecisionRunSkill(BaseModel):
    """Schema for run_skill model decision."""
    skill_id: str
    inputs: Dict[str, Any]


# Skills Schemas

class SkillSubmitRequest(BaseModel):
    """Owner submission of a validated, versioned declarative skill.

    Submissions always enter as proposals and require explicit review and
    enablement before they become live skills (auto-generated proposals may
    auto-enable after N similar verified traces; owner submissions never do).
    """
    skill_id: str = Field(..., min_length=1, max_length=128)
    definition: Dict[str, Any]


class SkillStateRequest(BaseModel):
    """Request to enable or disable a live skill."""
    state: str = Field(..., pattern="^(enabled|disabled)$")


class PurgeRequest(BaseModel):
    """Owner-triggered complete-record purge.

    Provide either a retention window (`days`) or an exact `task_id`.
    Receipts are non-sensitive; task content is never retained after purge.
    """
    days: Optional[int] = None
    task_id: Optional[str] = None
    reason: str = "owner_requested"


class PurgeReceiptResponse(BaseModel):
    """Non-sensitive purge receipt."""
    task_id: str
    reason: str
    purged_at: datetime
    event_count: int


class RetentionRunResponse(BaseModel):
    """Result of a retention/purge run."""
    receipts: List[PurgeReceiptResponse]
    purged_count: int
    purged_turns: int = 0


# Onboarding Schemas

class ModelSelectRequest(BaseModel):
    """Schema for selecting one exact authenticated Cloud model ID."""
    model_config = {"protected_namespaces": ()}

    model_id: str = Field(..., min_length=1, max_length=256)


class ModelSelectResponse(BaseModel):
    """Schema confirming the persisted model selection (non-secret metadata)."""
    selected_model: str
    persisted: bool


class OnboardingStatusResponse(BaseModel):
    """Schema for onboarding status (never contains secret values)."""
    api_key_configured: bool
    selected_model: str
    onboarded: bool


# Secret Schemas

class SecretWriteRequest(BaseModel):
    """Schema for storing a secret. Values are write-only and never returned."""
    value: str = Field(..., min_length=1, max_length=8192)


class SecretStatusResponse(BaseModel):
    """Schema for secret configuration status (never contains secret values)."""
    configured: bool
    name: str


class SecretsListResponse(BaseModel):
    """Schema listing which known secrets are configured (never their values)."""
    configured: List[str]


# Settings Schemas

class SettingsUpdate(BaseModel):
    """Schema for updating settings."""
    key: str
    value: str


class SettingsResponse(BaseModel):
    """Schema for settings response (non-secret values only)."""
    host: str
    port: int
    ollama_cloud_base_url: str
    selected_model: str
    file_roots: List[str]
    max_actions_per_task: int
    max_cloud_requests_per_task: int
    voice_enabled: bool
    voice_recording_enabled: bool = False
    voice_tts_voice: str = ""
    retention_days: int = 90


# Health Check Schemas

class HealthResponse(BaseModel):
    """Schema for health check response."""
    status: str
    version: str
    database_connected: bool
    cloud_configured: bool
    selected_model: str = ""


# Voice Schemas

class VoiceStartResponse(BaseModel):
    """Schema for voice session start response."""
    session_id: str
    status: str
    stt_available: bool = False
    recording_enabled: bool = False


class VoiceAudioInput(BaseModel):
    """Schema for voice audio frame."""
    session_id: str
    audio_data: str  # Base64 encoded PCM
    sample_rate: int = 16000
    channels: int = 1


class VoiceSegmentResponse(BaseModel):
    """Schema for a completed speech segment (editable transcript unit)."""
    segment_id: str
    text: str
    stop_detected: bool
    audio_deleted: bool
    stt_available: bool


class VoiceAudioResponse(BaseModel):
    """Schema for the audio-frame response."""
    session_id: str
    segments: List[VoiceSegmentResponse]
    is_speaking: bool
    stt_available: bool = False


class VoiceAssetsResponse(BaseModel):
    """Schema for pinned voice-asset status (never downloads)."""
    stt_installed: bool
    stt_model: str
    stt_version: str
    stt_license: str
    stt_sha256_verified: bool
    tts_provider: str
    tts_voice: str
    assets_dir: str
    downloads_on_import: bool
    downloads_on_startup: bool


# Error Response Schema

class ErrorResponse(BaseModel):
    """Schema for error responses."""
    error: str
    detail: Optional[str] = None
    code: Optional[str] = None


# Tool Execution Request/Response Schemas

class ToolExecutionRequest(BaseModel):
    """Schema for direct tool execution request."""
    tool_name: str
    params: Dict[str, Any] = {}


class ToolExecutionResponse(BaseModel):
    """Schema for tool execution response."""
    success: bool
    output: Optional[Any] = None
    error: Optional[str] = None
    evidence: Optional[str] = None


# Memory Tool Schemas

class MemoryStoreInput(ToolInput):
    """Input for storing or updating a persistent memory entry."""
    key: str
    value: str
    category: Optional[str] = "user_preference"


class MemoryRecallInput(ToolInput):
    """Input for searching or recalling stored memories."""
    query: Optional[str] = None
    category: Optional[str] = None


class MemoryForgetInput(ToolInput):
    """Input for deleting a persistent memory entry."""
    key: str


class MemoryLinkInput(ToolInput):
    """Input for creating or updating a directed memory link.

    Link endpoints are memory keys (or short free-text fact labels); the
    relation is lowercase snake_case describing how source relates to target.
    """

    source_key: str
    relation: str
    target_key: str
    weight: float = Field(default=1.0, gt=0)


class MemoryRecallRelatedInput(ToolInput):
    """Input for recalling a memory key plus its linked neighborhood.

    `hops` is clamped to 2 by the tool; larger values are accepted so the
    model can request a deeper walk without the schema rejecting it.
    """

    key: str
    hops: int = Field(default=1, ge=1)


class MemoryStatsInput(ToolInput):
    """Input for getting memory statistics."""
    pass


class MemoryExportInput(ToolInput):
    """Input for exporting memory entries."""
    category: Optional[str] = Field(
        default=None,
        description="Filter by category. If omitted, exports all categories."
    )
    format: str = Field(
        default="json",
        description="Export format: 'json' or 'markdown'"
    )
    include_edges: bool = Field(
        default=True,
        description="Whether to include memory edges (links) in the export"
    )


class ActivityRecallInput(ToolInput):
    """Input for recalling Sythion's own past activity within a time range.

    Query Sythion's activity history (task tool-call events and the
    conversation transcript) across tasks. The model is expected to resolve
    natural-language phrasing ("this morning", "8am", "yesterday") into a
    concrete `start_time` using its own knowledge of the current time; the
    tool does no date parsing.

    `exclude_task_ids` omits events from specified tasks (e.g. the current
    task) so the result only shows prior activity.

    `max_results` is loosely bounded (1-25) at the schema and hard-clamped by
    the tool, matching the memory_recall_related convention.
    """

    start_time: datetime
    end_time: Optional[datetime] = None
    tool_name: Optional[str] = None
    exclude_task_ids: Optional[List[str]] = None
    max_results: int = Field(default=10, ge=1, le=25)

    @field_validator("exclude_task_ids", mode="before")
    @classmethod
    def coerce_exclude_task_ids(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            if v.startswith("[") and v.endswith("]"):
                import json
                try:
                    parsed = json.loads(v)
                    if isinstance(parsed, list):
                        return [str(x) for x in parsed]
                except Exception:
                    pass
            if "," in v:
                return [x.strip() for x in v.split(",") if x.strip()]
            return [v]
        return v

