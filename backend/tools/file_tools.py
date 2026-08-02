"""
File System Tools for JARVIS
Implements file operations: read, write, list, create, delete, move, copy
"""
import os
import shutil
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable, Awaitable
from tools.executor import BaseTool
from api.schemas import (
    ToolOutput,
    FileListInput,
    FileReadInput,
    FileCreateInput,
    FileWriteInput,
    FileMoveInput,
    FileCopyInput,
    FileDeleteInput,
)

# Upper bound on how much content the LLM may write in one call.
MAX_WRITE_BYTES = 2 * 1024 * 1024


def _read_back_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="strict")


def _file_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _bytes_match(a: Path, b: Path) -> bool:
    return _file_bytes(a) == _file_bytes(b)


async def _atomic_write_text(
    path: Path,
    content: str,
    append: bool,
    overwrite: bool,
    cancelled_check: Optional[Callable[[], Awaitable[bool]]] = None,
) -> str:
    """Write `content` to `path` atomically: temp file + os.replace.

    The target only ever appears as either the old content or the new content
    (never a partial write). When a `cancelled_check` is supplied it is awaited
    immediately before the final `os.replace` so a cancel that arrives during
    the (potentially large) write still prevents the swap. Returns the final
    content written so the caller can verify it read back identically.
    """
    if len(content.encode('utf-8')) > MAX_WRITE_BYTES:
        raise _WriteTooLarge(content)

    if append and path.exists():
        existing = path.read_text(encoding='utf-8', errors='replace')
        final_content = existing + content
        if len(final_content.encode('utf-8')) > MAX_WRITE_BYTES:
            raise _WriteTooLarge(final_content)
    elif path.exists() and not overwrite and not append:
        raise _ExistsError(str(path))
    else:
        final_content = content

    if path.exists() and path.is_dir():
        raise ValueError(f"Target is a directory: {path}")

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".jarvis-tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(final_content)
            f.flush()
            os.fsync(f.fileno())
        # Cancel may have arrived while the temp file was being written: the
        # original target is still untouched, so stop before the swap.
        if cancelled_check is not None:
            if await cancelled_check():
                raise _CancelledError()
        # Final irreversible swap. os.replace is atomic on the same volume.
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return final_content


class _WriteTooLarge(Exception):
    pass


class _ExistsError(Exception):
    def __init__(self, message):
        super().__init__(message)


class _CancelledError(Exception):
    pass


def _approved_roots() -> List[Path]:
    """Return the configured file roots, or safe defaults when unset.

    When JARVIS_FILE_ROOTS is empty we fall back to the user's home directory
    and the process working directory so file tools remain usable but still
    cannot reach outside the user's own data (e.g. C:\\Windows, other users).
    """
    try:
        from backend.core.config import settings
    except ImportError:
        from core.config import settings
    if settings.file_roots:
        return [Path(r).expanduser().resolve() for r in settings.file_roots]
    return [Path.home().resolve(), Path.cwd().resolve()]


def _contained(path: Path) -> bool:
    """Return True if `path` is inside one of the approved roots.

    Performs case-insensitive comparison (Windows) and follows symlinks so a
    link inside a root pointing outside is rejected.
    """
    try:
        resolved = path.expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    norm = os.path.normcase(str(resolved))
    for root in _approved_roots():
        root_norm = os.path.normcase(str(root))
        if norm == root_norm or norm.startswith(root_norm + os.sep):
            return True
    return False


def _safe_resolve(raw: str, allow_existing_missing: bool = False) -> Path:
    """Resolve a user-supplied path and enforce it stays within approved roots."""
    if not raw or not str(raw).strip():
        raise ValueError("Path must not be empty")
    # Check the literal (unresolved) target first: if it is itself a reparse
    # point we reject it before resolve() follows the link away.
    literal = Path(str(raw)).expanduser()
    if literal.exists() and _is_reparse_point(literal):
        raise _TargetRejected(
            "TARGET_REPARSE_POINT_REJECTED",
            f"Target is a symbolic link, junction, or reparse point and is rejected in v1: {literal}")
    path = literal.resolve()
    if not _contained(path):
        raise _TargetRejected(
            "TARGET_OUTSIDE_ROOTS",
            f"Target is outside approved roots: {path}")
    if not _canonical_contained(path):
        raise _TargetRejected(
            "TARGET_OUTSIDE_ROOTS",
            f"Canonical location is outside approved roots: {path}")
    return path


# v1 rejects wildcards, bulk selection, and implicit recursion in file targets.
_WILDCARD_CHARS = set("*?[")


def _path_target_error(raw: str) -> Optional[Dict[str, str]]:
    """Deterministic, side-effect-free validation of a file target path.

    Returns an error dict when the target is empty, contains wildcards, or
    escapes the approved roots; None when the target is acceptable. This gate
    runs before any OS call.
    """
    if not raw or not str(raw).strip():
        return {"code": "TARGET_REQUIRED", "message": "A file target path is required"}
    text = str(raw)
    if any(c in text for c in _WILDCARD_CHARS):
        return {"code": "TARGET_WILDCARD_REJECTED", "message": "Wildcards and bulk selection are not allowed in v1 file targets"}
    try:
        path = Path(text).expanduser().resolve()
    except (OSError, RuntimeError):
        return {"code": "TARGET_UNRESOLVABLE", "message": f"Cannot resolve target path: {text}"}
    if not _contained(path):
        return {"code": "TARGET_OUTSIDE_ROOTS", "message": f"Target is outside approved roots: {path}"}
    return None


# --- Canonical Windows handle rechecks --------------------------------------
# The string gate above is fast and deterministic; these rechecks open a real
# handle to the target (and to each approved root) and compare the final
# location the OS reports. This defeats short-name (8.3) aliases, case tricks,
# and reparse points that a string comparison could be fooled by, and rejects
# operating on symbolic links / junctions outright.

_CTYPES_READY = False
if os.name == "nt":
    try:
        import ctypes
        import ctypes.wintypes as wintypes

        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        _FILE_READ_ATTRIBUTES = 0x80
        _FILE_SHARE_READ = 0x1
        _FILE_SHARE_WRITE = 0x2
        _FILE_SHARE_DELETE = 0x4
        _OPEN_EXISTING = 3
        _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
        _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
        _FILE_ATTRIBUTE_REPARSE_POINT = 0x400
        _INVALID_HANDLE = ctypes.c_void_p(-1).value

        _kernel32.CreateFileW.restype = wintypes.HANDLE
        _kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        _kernel32.CloseHandle.restype = wintypes.BOOL
        _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        class _ByHandleFileInformation(ctypes.Structure):
            _fields_ = [
                ("dwFileAttributes", wintypes.DWORD),
                ("ftCreationTime", wintypes.FILETIME),
                ("ftLastAccessTime", wintypes.FILETIME),
                ("ftLastWriteTime", wintypes.FILETIME),
                ("dwVolumeSerialNumber", wintypes.DWORD),
                ("nFileSizeHigh", wintypes.DWORD),
                ("nFileSizeLow", wintypes.DWORD),
                ("nNumberOfLinks", wintypes.DWORD),
                ("nFileIndexHigh", wintypes.DWORD),
                ("nFileIndexLow", wintypes.DWORD),
            ]

        _kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
        _kernel32.GetFileInformationByHandle.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInformation),
        ]
        _kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        _kernel32.GetFinalPathNameByHandleW.argtypes = [
            wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
        ]

        def _open_no_follow(path: str):
            handle = _kernel32.CreateFileW(
                str(path), _FILE_READ_ATTRIBUTES,
                _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
                None, _OPEN_EXISTING,
                _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
                None)
            return None if handle == _INVALID_HANDLE else handle

        def _open_follow(path: str):
            handle = _kernel32.CreateFileW(
                str(path), _FILE_READ_ATTRIBUTES,
                _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
                None, _OPEN_EXISTING, _FILE_FLAG_BACKUP_SEMANTICS, None)
            return None if handle == _INVALID_HANDLE else handle

        def _file_attributes(handle) -> Optional[int]:
            info = _ByHandleFileInformation()
            if not _kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
                return None
            return int(info.dwFileAttributes)

        def _final_path_by_handle(handle) -> Optional[str]:
            buf = ctypes.create_unicode_buffer(32768)
            n = _kernel32.GetFinalPathNameByHandleW(handle, buf, 32768, 0)
            if n == 0 or n >= 32768:
                return None
            return buf.value

        _CTYPES_READY = True
    except Exception:
        _CTYPES_READY = False


def _normalize_final(final_path: str) -> str:
    """Strip the \\\\?\\ device prefix GetFinalPathNameByHandleW adds."""
    if final_path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + final_path[8:]
    if final_path.startswith("\\\\?\\"):
        return final_path[4:]
    return final_path


def _canonical_of_existing(path: Path) -> Optional[str]:
    """OS-reported final location of an existing file/directory."""
    if not _CTYPES_READY:
        return None
    handle = _open_follow(path)
    if not handle:
        return None
    try:
        final = _final_path_by_handle(handle)
        return _normalize_final(final) if final else None
    finally:
        _kernel32.CloseHandle(handle)


def _is_reparse_point(path: Path) -> bool:
    """True when the existing target itself is a link/junction/reparse point."""
    if not _CTYPES_READY:
        return False
    handle = _open_no_follow(path)
    if not handle:
        return False
    try:
        attrs = _file_attributes(handle)
        return bool(attrs and (attrs & _FILE_ATTRIBUTE_REPARSE_POINT))
    finally:
        _kernel32.CloseHandle(handle)


def _is_under_canonical(canonical: str, roots: List[str]) -> bool:
    c = canonical.rstrip("\\") or "\\"
    cl = c.lower()
    for root in roots:
        r = root.rstrip("\\") or "\\"
        if cl == r.lower() or cl.startswith(r.lower() + "\\"):
            return True
    return False


def _canonical_contained(path: Path) -> bool:
    """Defense-in-depth containment check using real OS handles.

    Compares the OS-reported final location of the nearest existing ancestor of
    `path` (or the target itself) against each approved root's final location,
    so short names, case, and reparse-point targets cannot bypass the string
    gate. Returns True on non-Windows where the string gate is authoritative.
    """
    if not _CTYPES_READY:
        return True
    root_finals = []
    for root in _approved_roots():
        final = _canonical_of_existing(root)
        if final:
            root_finals.append(final)
    if not root_finals:
        return False
    probe = path
    while not probe.exists():
        parent = probe.parent
        if parent == probe:
            return False
        probe = parent
    target = _canonical_of_existing(probe)
    if not target:
        return False
    return _is_under_canonical(target, root_finals)


class _TargetRejected(Exception):
    """Raised when an execution-time target recheck rejects a path."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class FileListTool(BaseTool):
    """List directory contents."""
    
    name = "file_list"
    description = "List files and directories in a given path"
    input_schema = FileListInput
    
    async def execute(self, input_data: FileListInput) -> ToolOutput:
        """Execute the file list tool."""
        try:
            path = _safe_resolve(input_data.path)
            
            if not path.exists():
                return ToolOutput(
                    success=False,
                    error={"code": "PATH_NOT_FOUND", "message": f"Path not found: {path}"},
                    duration_ms=0
                )
            
            if not path.is_dir():
                return ToolOutput(
                    success=False,
                    error={"code": "NOT_DIRECTORY", "message": f"Path is not a directory: {path}"},
                    duration_ms=0
                )
            
            items = []
            for item in path.iterdir():
                # Skip hidden files unless requested
                if not input_data.include_hidden and item.name.startswith('.'):
                    continue
                
                try:
                    stat_info = item.stat()
                    items.append({
                        "name": item.name,
                        "path": str(item),
                        "is_file": item.is_file(),
                        "is_dir": item.is_dir(),
                        "size": stat_info.st_size if item.is_file() else None,
                        "modified": stat_info.st_mtime
                    })
                except (PermissionError, OSError):
                    items.append({
                        "name": item.name,
                        "path": str(item),
                        "error": "Permission denied"
                    })
            
            # Sort: directories first, then files
            items.sort(key=lambda x: (not x.get('is_dir', False), x['name'].lower()))
            
            return ToolOutput(
                success=True,
                data={
                    "path": str(path),
                    "count": len(items),
                    "items": items
                },
                evidence={"source": "filesystem_list"}
            )
        except _TargetRejected as e:
            return ToolOutput(success=False, error={"code": e.code, "message": e.message}, duration_ms=0)
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "LIST_ERROR", "message": str(e)},
                duration_ms=0
            )
    
    async def verify(self, input_data: FileListInput, output: ToolOutput) -> bool:
        """Verify the tool execution result."""
        return output.success and "items" in (output.data or {})
    
    def get_policy_requirements(self) -> Dict[str, Any]:
        """Get policy requirements for this tool."""
        return {"risk_level": "none", "requires_confirmation": False}

    def validate_targets(self, input_data: FileListInput) -> Optional[Dict[str, str]]:
        return _path_target_error(input_data.path)


class FileReadTool(BaseTool):
    """Read file contents."""
    
    name = "file_read"
    description = "Read the contents of a file"
    input_schema = FileReadInput
    
    async def execute(self, input_data: FileReadInput) -> ToolOutput:
        """Execute the file read tool."""
        try:
            path = _safe_resolve(input_data.path)
            
            if not path.exists():
                return ToolOutput(
                    success=False,
                    error={"code": "FILE_NOT_FOUND", "message": f"File not found: {path}"},
                    duration_ms=0
                )
            
            if not path.is_file():
                return ToolOutput(
                    success=False,
                    error={"code": "NOT_FILE", "message": f"Path is not a file: {path}"},
                    duration_ms=0
                )
            
            # Check file size before reading
            file_size = path.stat().st_size
            if file_size > input_data.max_bytes:
                return ToolOutput(
                    success=False,
                    error={
                        "code": "FILE_TOO_LARGE",
                        "message": f"File size ({file_size} bytes) exceeds maximum allowed ({input_data.max_bytes} bytes)"
                    },
                    duration_ms=0
                )
            
            content = path.read_text(encoding='utf-8', errors='replace')
            
            return ToolOutput(
                success=True,
                data={
                    "path": str(path),
                    "content": content,
                    "size": len(content),
                    "bytes_read": file_size
                },
                evidence={"source": "filesystem_read"}
            )
        except _TargetRejected as e:
            return ToolOutput(success=False, error={"code": e.code, "message": e.message}, duration_ms=0)
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "READ_ERROR", "message": str(e)},
                duration_ms=0
            )
    
    async def verify(self, input_data: FileReadInput, output: ToolOutput) -> bool:
        """Verify the tool execution result."""
        return output.success and "content" in (output.data or {})
    
    def get_policy_requirements(self) -> Dict[str, Any]:
        """Get policy requirements for this tool."""
        return {"risk_level": "low", "requires_confirmation": False, "redact_sensitive": True}

    def validate_targets(self, input_data: FileReadInput) -> Optional[Dict[str, str]]:
        return _path_target_error(input_data.path)


class FileCreateTool(BaseTool):
    """Create a new file."""
    
    name = "file_create"
    description = "Create a new file with specified content"
    input_schema = FileCreateInput
    
    async def execute(self, input_data: FileCreateInput) -> ToolOutput:
        """Execute the file create tool."""
        try:
            path = _safe_resolve(input_data.path)

            if len(input_data.content.encode('utf-8')) > MAX_WRITE_BYTES:
                return ToolOutput(
                    success=False,
                    error={"code": "WRITE_TOO_LARGE", "message": f"Content exceeds max write size of {MAX_WRITE_BYTES} bytes"},
                    duration_ms=0
                )
            
            # Atomic write: temp file + replace. If the target exists and
            # overwrite is False, refuse (no clobber). A cancellation that
            # arrives mid-write stops before the final replace.
            try:
                final_content = await _atomic_write_text(
                    path,
                    input_data.content,
                    append=False,
                    overwrite=input_data.overwrite,
                    cancelled_check=self._is_cancelled,
                )
            except _ExistsError as e:
                return ToolOutput(
                    success=False,
                    error={"code": "FILE_EXISTS", "message": f"File already exists: {path}. Use overwrite=True to replace."},
                    duration_ms=0
                )
            except _CancelledError:
                return ToolOutput(
                    success=False,
                    error={"code": "CANCELLED", "message": "Task was cancelled before the file replace"},
                    duration_ms=0
                )
            
            # Fresh post-mutation verification: read the committed bytes back.
            try:
                written = _read_back_text(path)
                matches = written == final_content
            except (OSError, UnicodeDecodeError):
                matches = False

            return ToolOutput(
                success=True,
                data={
                    "path": str(path),
                    "bytes_written": len(final_content.encode('utf-8')),
                    "overwritten": path.exists() and input_data.overwrite,
                    "verified": matches,
                },
                evidence={"source": "filesystem_create", "verification": "passed" if matches else "failed"}
            )
        except _TargetRejected as e:
            return ToolOutput(success=False, error={"code": e.code, "message": e.message}, duration_ms=0)
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "CREATE_ERROR", "message": str(e)},
                duration_ms=0
            )
    
    async def verify(self, input_data: FileCreateInput, output: ToolOutput) -> bool:
        """Verify the tool execution result."""
        return output.success and "path" in (output.data or {})
    
    def get_policy_requirements(self) -> Dict[str, Any]:
        """Get policy requirements for this tool."""
        return {"risk_level": "medium", "requires_confirmation": True, "confirm_overwrite": True}

    def validate_targets(self, input_data: FileCreateInput) -> Optional[Dict[str, str]]:
        return _path_target_error(input_data.path)


class FileWriteTool(BaseTool):
    """Write content to a file."""
    
    name = "file_write"
    description = "Write or append content to a file"
    input_schema = FileWriteInput
    
    async def execute(self, input_data: FileWriteInput) -> ToolOutput:
        """Execute the file write tool."""
        try:
            path = _safe_resolve(input_data.path)
            
            if len(input_data.content.encode('utf-8')) > MAX_WRITE_BYTES:
                return ToolOutput(
                    success=False,
                    error={"code": "WRITE_TOO_LARGE", "message": f"Content exceeds max write size of {MAX_WRITE_BYTES} bytes"},
                    duration_ms=0
                )
            
            # Atomic write (temp file + replace); a cancellation arriving during
            # the write stops before the final swap so the old file survives.
            try:
                final_content = await _atomic_write_text(
                    path,
                    input_data.content,
                    append=input_data.append,
                    overwrite=True,
                    cancelled_check=self._is_cancelled,
                )
            except _CancelledError:
                return ToolOutput(
                    success=False,
                    error={"code": "CANCELLED", "message": "Task was cancelled before the file replace"},
                    duration_ms=0
                )
            except _WriteTooLarge:
                return ToolOutput(
                    success=False,
                    error={"code": "WRITE_TOO_LARGE", "message": f"Content exceeds max write size of {MAX_WRITE_BYTES} bytes"},
                    duration_ms=0
                )
            
            # Fresh post-mutation verification: read the committed bytes back.
            try:
                written = _read_back_text(path)
                matches = written == final_content
            except (OSError, UnicodeDecodeError):
                matches = False

            return ToolOutput(
                success=True,
                data={
                    "path": str(path),
                    "bytes_written": len(final_content.encode('utf-8')),
                    "appended": input_data.append,
                    "verified": matches,
                },
                evidence={"source": "filesystem_write", "verification": "passed" if matches else "failed"}
            )
        except _TargetRejected as e:
            return ToolOutput(success=False, error={"code": e.code, "message": e.message}, duration_ms=0)
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "WRITE_ERROR", "message": str(e)},
                duration_ms=0
            )
    
    async def verify(self, input_data: FileWriteInput, output: ToolOutput) -> bool:
        """Verify the tool execution result."""
        return output.success and "path" in (output.data or {})
    
    def get_policy_requirements(self) -> Dict[str, Any]:
        """Get policy requirements for this tool."""
        return {"risk_level": "medium", "requires_confirmation": True}

    def validate_targets(self, input_data: FileWriteInput) -> Optional[Dict[str, str]]:
        return _path_target_error(input_data.path)


class FileMoveTool(BaseTool):
    """Move a file to a new location."""
    
    name = "file_move"
    description = "Move a file from source to destination"
    input_schema = FileMoveInput
    
    async def execute(self, input_data: FileMoveInput) -> ToolOutput:
        """Execute the file move tool."""
        try:
            source = _safe_resolve(input_data.source_path)
            dest = _safe_resolve(input_data.destination_path)
            
            if not source.exists():
                return ToolOutput(
                    success=False,
                    error={"code": "SOURCE_NOT_FOUND", "message": f"Source file not found: {source}"},
                    duration_ms=0
                )
            
            if not source.is_file():
                return ToolOutput(
                    success=False,
                    error={"code": "SOURCE_NOT_FILE", "message": f"Source is not a file: {source}"},
                    duration_ms=0
                )
            
            # Check if destination exists
            if dest.exists() and not input_data.overwrite:
                return ToolOutput(
                    success=False,
                    error={"code": "DESTINATION_EXISTS", "message": f"Destination already exists: {dest}. Use overwrite=True to replace."},
                    duration_ms=0
                )
            
            # Create parent directories if needed
            dest.parent.mkdir(parents=True, exist_ok=True)
            
            # shutil.move uses os.rename on Windows, which raises FileExistsError
            # when the destination exists; honour overwrite explicitly.
            if input_data.overwrite and dest.exists():
                if dest.is_dir():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
            
            # Capture source bytes before the move for post-mutation verification.
            try:
                source_bytes = _file_bytes(source)
            except OSError:
                source_bytes = None

            # Move the file
            shutil.move(str(source), str(dest))
            
            # Fresh post-mutation verification: the destination exists and
            # carries the exact source bytes.
            verified = dest.exists() and dest.is_file()
            if verified and source_bytes is not None:
                try:
                    verified = _file_bytes(dest) == source_bytes
                except OSError:
                    verified = False

            return ToolOutput(
                success=True,
                data={
                    "source": str(source),
                    "destination": str(dest),
                    "overwritten": dest.exists() and input_data.overwrite,
                    "verified": verified,
                },
                evidence={"source": "filesystem_move", "verification": "passed" if verified else "failed"}
            )
        except _TargetRejected as e:
            return ToolOutput(success=False, error={"code": e.code, "message": e.message}, duration_ms=0)
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "MOVE_ERROR", "message": str(e)},
                duration_ms=0
            )
    
    async def verify(self, input_data: FileMoveInput, output: ToolOutput) -> bool:
        """Verify the tool execution result."""
        return output.success and "destination" in (output.data or {})
    
    def get_policy_requirements(self) -> Dict[str, Any]:
        """Get policy requirements for this tool."""
        return {"risk_level": "medium", "requires_confirmation": True}

    def validate_targets(self, input_data: FileMoveInput) -> Optional[Dict[str, str]]:
        return _path_target_error(input_data.source_path) or _path_target_error(input_data.destination_path)


class FileCopyTool(BaseTool):
    """Copy a file to a new location."""
    
    name = "file_copy"
    description = "Copy a file from source to destination"
    input_schema = FileCopyInput
    
    async def execute(self, input_data: FileCopyInput) -> ToolOutput:
        """Execute the file copy tool."""
        try:
            source = _safe_resolve(input_data.source_path)
            dest = _safe_resolve(input_data.destination_path)
            
            if not source.exists():
                return ToolOutput(
                    success=False,
                    error={"code": "SOURCE_NOT_FOUND", "message": f"Source file not found: {source}"},
                    duration_ms=0
                )
            
            if not source.is_file():
                return ToolOutput(
                    success=False,
                    error={"code": "SOURCE_NOT_FILE", "message": f"Source is not a file: {source}"},
                    duration_ms=0
                )
            
            # Check if destination exists
            if dest.exists() and not input_data.overwrite:
                return ToolOutput(
                    success=False,
                    error={"code": "DESTINATION_EXISTS", "message": f"Destination already exists: {dest}. Use overwrite=True to replace."},
                    duration_ms=0
                )
            
            # Create parent directories if needed
            dest.parent.mkdir(parents=True, exist_ok=True)
            
            # Copy the file
            shutil.copy2(str(source), str(dest))
            
            # Fresh post-mutation verification: the destination exists and
            # carries the exact source bytes.
            verified = dest.exists() and dest.is_file()
            if verified:
                try:
                    verified = _bytes_match(source, dest)
                except OSError:
                    verified = False

            return ToolOutput(
                success=True,
                data={
                    "source": str(source),
                    "destination": str(dest),
                    "overwritten": dest.exists() and input_data.overwrite,
                    "verified": verified,
                },
                evidence={"source": "filesystem_copy", "verification": "passed" if verified else "failed"}
            )
        except _TargetRejected as e:
            return ToolOutput(success=False, error={"code": e.code, "message": e.message}, duration_ms=0)
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "COPY_ERROR", "message": str(e)},
                duration_ms=0
            )
    
    async def verify(self, input_data: FileCopyInput, output: ToolOutput) -> bool:
        """Verify the tool execution result."""
        return output.success and "destination" in (output.data or {})
    
    def get_policy_requirements(self) -> Dict[str, Any]:
        """Get policy requirements for this tool."""
        return {"risk_level": "medium", "requires_confirmation": True}

    def validate_targets(self, input_data: FileCopyInput) -> Optional[Dict[str, str]]:
        return _path_target_error(input_data.source_path) or _path_target_error(input_data.destination_path)


class FileDeleteTool(BaseTool):
    """Delete a file."""
    
    name = "file_delete"
    description = "Delete a file (optionally using recycle bin)"
    input_schema = FileDeleteInput
    
    async def execute(self, input_data: FileDeleteInput) -> ToolOutput:
        """Execute the file delete tool."""
        try:
            path = _safe_resolve(input_data.path)
            
            if not path.exists():
                return ToolOutput(
                    success=False,
                    error={"code": "FILE_NOT_FOUND", "message": f"File not found: {path}"},
                    duration_ms=0
                )
            
            if not path.is_file():
                return ToolOutput(
                    success=False,
                    error={"code": "NOT_FILE", "message": f"Path is not a file: {path}"},
                    duration_ms=0
                )
            
            # On Windows, use recycle bin if requested
            if input_data.use_recycle_bin:
                try:
                    import send2trash
                except ImportError:
                    # Do NOT silently fall back to permanent deletion: the caller
                    # asked for recycle-bin semantics and expects recoverable deletes.
                    return ToolOutput(
                        success=False,
                        error={"code": "RECYCLE_BIN_UNAVAILABLE",
                               "message": "use_recycle_bin requested but send2trash is not installed; refusing to permanently delete."},
                        duration_ms=0
                    )
                send2trash.send2trash(str(path))
                method = "recycle_bin"
            else:
                path.unlink()
                method = "permanent_deletion"
            
            # Fresh post-mutation verification: the target no longer exists.
            verified = not path.exists()
            recoverable = method == "recycle_bin"

            return ToolOutput(
                success=True,
                data={
                    "path": str(path),
                    "deletion_method": method,
                    "verified": verified,
                    "recoverable": recoverable,
                },
                evidence={
                    "source": "filesystem_delete",
                    "verification": "passed" if verified else "failed",
                    "recoverable": recoverable,
                }
            )
        except _TargetRejected as e:
            return ToolOutput(success=False, error={"code": e.code, "message": e.message}, duration_ms=0)
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "DELETE_ERROR", "message": str(e)},
                duration_ms=0
            )
    
    async def verify(self, input_data: FileDeleteInput, output: ToolOutput) -> bool:
        """Verify the file was actually deleted."""
        if not output.success:
            return False
        data = output.data or {}
        path = data.get("path")
        if not path:
            return False
        return not Path(str(path)).exists()
    
    def get_policy_requirements(self) -> Dict[str, Any]:
        """Get policy requirements for this tool."""
        return {"risk_level": "high", "requires_confirmation": True, "destructive": True}

    def validate_targets(self, input_data: FileDeleteInput) -> Optional[Dict[str, str]]:
        return _path_target_error(input_data.path)


def create_file_tools() -> List[BaseTool]:
    """Create instances of all file tools."""
    return [
        FileListTool(),
        FileReadTool(),
        FileCreateTool(),
        FileWriteTool(),
        FileMoveTool(),
        FileCopyTool(),
        FileDeleteTool(),
    ]
