"""
File System Tools for JARVIS
Implements file operations: read, write, list, create, delete, move, copy
"""
import os
import shutil
from pathlib import Path
from typing import Dict, Any, List, Optional

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
    path = Path(str(raw)).expanduser().resolve()
    if not _contained(path):
        raise ValueError(f"Path is outside approved roots: {path}")
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
            
            # Check if file already exists
            if path.exists():
                if not input_data.overwrite:
                    return ToolOutput(
                        success=False,
                        error={"code": "FILE_EXISTS", "message": f"File already exists: {path}. Use overwrite=True to replace."},
                        duration_ms=0
                    )
                # If overwrite is True, we'll proceed to write
            
            # Create parent directories if they don't exist
            path.parent.mkdir(parents=True, exist_ok=True)
            
            # Write the file
            path.write_text(input_data.content, encoding='utf-8')
            
            return ToolOutput(
                success=True,
                data={
                    "path": str(path),
                    "bytes_written": len(input_data.content.encode('utf-8')),
                    "overwritten": path.exists() and input_data.overwrite
                },
                evidence={"source": "filesystem_create"}
            )
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
            
            # Create parent directories if they don't exist
            path.parent.mkdir(parents=True, exist_ok=True)
            
            mode = 'a' if input_data.append else 'w'
            with open(path, mode, encoding='utf-8') as f:
                f.write(input_data.content)
            
            return ToolOutput(
                success=True,
                data={
                    "path": str(path),
                    "bytes_written": len(input_data.content.encode('utf-8')),
                    "appended": input_data.append
                },
                evidence={"source": "filesystem_write"}
            )
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
            
            # Move the file
            shutil.move(str(source), str(dest))
            
            return ToolOutput(
                success=True,
                data={
                    "source": str(source),
                    "destination": str(dest),
                    "overwritten": dest.exists() and input_data.overwrite
                },
                evidence={"source": "filesystem_move"}
            )
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
            
            return ToolOutput(
                success=True,
                data={
                    "source": str(source),
                    "destination": str(dest),
                    "overwritten": dest.exists() and input_data.overwrite
                },
                evidence={"source": "filesystem_copy"}
            )
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
            
            return ToolOutput(
                success=True,
                data={
                    "path": str(path),
                    "deletion_method": method
                },
                evidence={"source": "filesystem_delete"}
            )
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
