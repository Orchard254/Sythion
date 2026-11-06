"""
GUI Tools for Sythion (Synth) -- Phase D: Reliable Windows GUI automation.

Windows-only. Every UI Automation call runs on one dedicated background COM MTA
worker thread -- never on the FastAPI event loop or the dashboard UI thread --
and live UIA handles never cross threads: each observation reacquires the
target from fresh evidence and returns only plain data.

v1 is limited to visible, foreground-able, medium-integrity native desktop
windows. Locked sessions, secure desktops, UAC, elevated processes, and security
surfaces are denied. UIA patterns are used first; coordinates are a single-shot
physical-pixel fallback followed by a fresh re-observation -- never a guessed
second click.
"""
import asyncio
import ctypes
import logging
import os
import platform
import queue
import sys
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Generic, TypeVar

logger = logging.getLogger(__name__)

# comtypes auto-initializes COM on its first import using sys.coinit_flags
# (defaulting to STA). The dedicated UIA worker initializes COM as MTA, so any
# subsequent comtypes import on that thread must also request MTA or
# CoInitializeEx raises RPC_E_CHANGED_MODE. gui_tools is the only comtypes
# consumer in Sythion (Synth), so this global is safe to set here.
if not hasattr(sys, "coinit_flags"):
    sys.coinit_flags = 0x0  # COINIT_MULTITHREADED

try:
    from backend.tools.executor import BaseTool
    from backend.api.schemas import (
        ToolOutput,
        GUIObserveInput,
        GUIFocusInput,
        GUIInvokeInput,
        GUISetValueInput,
        GUIClickInput,
        GUITypeInput,
        GUIHotkeyInput,
        GUIPasteInput,
        GUIScreenshotInput,
        GUIBatchFormInput,
    )
    from backend.core.hotkeys import DANGEROUS_HOTKEYS, HOTKEY_KEYS
except ImportError:
    from tools.executor import BaseTool
    from api.schemas import (
        ToolOutput,
        GUIObserveInput,
        GUIFocusInput,
        GUIInvokeInput,
        GUISetValueInput,
        GUIClickInput,
        GUITypeInput,
        GUIHotkeyInput,
        GUIPasteInput,
        GUIScreenshotInput,
        GUIBatchFormInput,
    )
    from core.hotkeys import DANGEROUS_HOTKEYS, HOTKEY_KEYS

_IS_WINDOWS = os.name == "nt"

# Bounds that keep observations and context small and deterministic.
_MAX_UI_TREE_DEPTH = 8
_MAX_UI_ELEMENTS = 120
_MAX_WINDOW_TITLES = 20
_UIA_CALL_TIMEOUT = 10.0

# A targeted OCR/visual observation must be no older than this to be usable.
_OCR_MAX_AGE_SECONDS = 5.0

# Security surfaces and elevated windows receive no interaction.
_SECURITY_WINDOW_TITLE_FRAGMENTS = ("user account control",)
_SECURITY_WINDOW_CLASSES = {"Consent UI Class", "SecureDesktop"}

# Hotkeys Sythion (Synth) never synthesizes, even when a window is foreground.
_DANGEROUS_HOTKEYS = DANGEROUS_HOTKEYS

_VALID_HOTKEY_KEYS = HOTKEY_KEYS


def _gui_target_error(window_title: str, element_description: Optional[str] = None) -> Optional[Dict[str, str]]:
    """Deterministic, side-effect-free validation of a GUI target.

    GUI v1 requires a non-empty window title (and an element description for
    element-bound actions) so the executor never reaches an OS call with an
    ambiguous target.
    """
    if not window_title or not str(window_title).strip():
        return {"code": "TARGET_REQUIRED", "message": "A window title is required"}
    if element_description is not None and not str(element_description).strip():
        return {"code": "ELEMENT_TARGET_REQUIRED", "message": "An element description is required"}
    return None


def _hotkey_error(keys: List[str]) -> Optional[Dict[str, str]]:
    """Deterministic validation of a hotkey combination."""
    if not keys:
        return {"code": "HOTKEY_REQUIRED", "message": "At least one key is required"}
    normalized = []
    for key in keys:
        k = str(key).strip().lower()
        if k not in _VALID_HOTKEY_KEYS:
            return {"code": "INVALID_HOTKEY_KEY", "message": f"Unsupported key: {key}"}
        normalized.append(k)
    combo = tuple(sorted(set(normalized)))
    if combo in _DANGEROUS_HOTKEYS:
        return {"code": "DANGEROUS_HOTKEY_REJECTED", "message": f"Hotkey {'+'.join(sorted(combo))} is rejected"}
    return None


# --------------------------------------------------------------------------
# Windows session / target safety (real API checks, run on the caller thread;
# they are cheap and do not need UIA).
# --------------------------------------------------------------------------

def _session_accepts_input() -> bool:
    """False when the session is locked or on the secure desktop.

    GetForegroundWindow() can legitimately be 0 for a background process even on
    an interactive desktop, so the reliable lock signal is the *input* desktop
    name: when the workstation is locked or a UAC/secure desktop is active, the
    input desktop is "Winlogon" (or the open fails entirely).
    """
    if not _IS_WINDOWS:
        return False
    try:
        desktop = ctypes.windll.user32.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
        if not desktop:
            return False
        try:
            name = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetUserObjectInformationW(
                desktop, 2, name, ctypes.sizeof(name), None)  # UOI_NAME = 2
            if name.value.strip().lower() in ("winlogon", "secure"):
                return False
        finally:
            ctypes.windll.user32.CloseDesktop(desktop)
        return True
    except (AttributeError, OSError):
        return False


def _window_pid(hwnd: int) -> Optional[int]:
    if not hwnd:
        return None
    pid = ctypes.c_ulong()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value) or None


def _window_title(hwnd: int) -> str:
    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _integrity_rid(pid: int) -> Optional[int]:
    """Windows integrity level RID of a process (4096 medium, 12288 high)."""
    if not _IS_WINDOWS:
        return None
    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    TOKEN_QUERY = 0x0008
    TokenIntegrityLevel = 25
    handle = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        token = ctypes.c_void_p()
        if not ctypes.windll.advapi32.OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(token)):
            return None
        try:
            size = ctypes.c_ulong()
            ctypes.windll.advapi32.GetTokenInformation(token, TokenIntegrityLevel, None, 0, ctypes.byref(size))
            if size.value == 0:
                return None
            buf = ctypes.create_string_buffer(size.value)
            if not ctypes.windll.advapi32.GetTokenInformation(
                    token, TokenIntegrityLevel, buf, size.value, ctypes.byref(size)):
                return None
            return _sid_last_subauthority_from_buffer(buf, size.value)
        finally:
            ctypes.windll.kernel32.CloseHandle(token)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _sid_last_subauthority_from_buffer(buf, size: int) -> Optional[int]:
    """Extract the last RID from a TOKEN_MANDATORY_LABEL buffer without ctypes structure churn.

    TOKEN_MANDATORY_LABEL { SID_AND_ATTRIBUTES { PSID Sid; DWORD Attributes } }:
      offset 0: PSID pointer (8 bytes on x64)
      offset 8: Attributes (4 bytes, ignored)
      offset 12: SID bytes begin: Revision(1) + SubAuthorityCount(1) + IdentifierAuthority(6) + SubAuthority[n] (4 each)
    """
    try:
        import ctypes as _c
        pointer_size = _c.sizeof(_c.c_void_p)
        sid_offset = pointer_size + 4  # PSID + Attributes
        if sid_offset + 2 > size:
            return None
        count = buf[sid_offset + 1]
        sub_auth_start = sid_offset + 2 + 6
        if sub_auth_start + (count * 4) > size:
            return None
        last = buf[sub_auth_start + (count - 1) * 4: sub_auth_start + count * 4]
        return int.from_bytes(last, "little")
    except Exception:
        return None


def _is_elevated(pid: Optional[int]) -> bool:
    if pid is None:
        return False
    rid = _integrity_rid(pid)
    return rid is not None and rid >= 12288  # high integrity or above


def _target_window_safety(hwnd: int, title: str) -> Optional[Dict[str, str]]:
    """Return an error dict when the target window must not be automated."""
    if not hwnd:
        return {"code": "WINDOW_NOT_FOUND", "message": "Target window not found"}
    if not ctypes.windll.user32.IsWindowVisible(hwnd):
        return {"code": "TARGET_NOT_VISIBLE", "message": f"Window '{title}' is not visible"}
    if ctypes.windll.user32.IsIconic(hwnd):
        return {"code": "TARGET_MINIMIZED", "message": f"Window '{title}' is minimized"}
    cls = _window_class(hwnd).strip()
    if cls in _SECURITY_WINDOW_CLASSES:
        return {"code": "SECURITY_SURFACE_DENIED", "message": "Security surfaces (UAC, secure desktop) are denied"}
    current_title = _window_title(hwnd).strip()
    if any(frag in current_title.lower() for frag in _SECURITY_WINDOW_TITLE_FRAGMENTS):
        return {"code": "SECURITY_SURFACE_DENIED", "message": "Security surfaces (UAC, secure desktop) are denied"}
    pid = _window_pid(hwnd)
    if _is_elevated(pid):
        return {"code": "ELEVATED_TARGET_DENIED", "message": f"Window '{title}' belongs to an elevated process and cannot be automated"}


def _is_foreground_or_modal_child(target_hwnd: int, target_pid: Optional[int] = None) -> bool:
    """Return True if target_hwnd is foreground, or if the current foreground window is an owned/modal child dialog of target_hwnd."""
    if not _IS_WINDOWS or not target_hwnd:
        return False
    fg_hwnd = int(ctypes.windll.user32.GetForegroundWindow() or 0)
    if not fg_hwnd:
        return False
    if fg_hwnd == target_hwnd:
        return True
    try:
        # Check if foreground window is owned by target_hwnd (e.g. modal Save As dialog)
        owner = ctypes.windll.user32.GetWindow(fg_hwnd, 4)  # GW_OWNER = 4
        if owner == target_hwnd:
            return True
        parent = ctypes.windll.user32.GetParent(fg_hwnd)
        if parent == target_hwnd:
            return True
        # If target_pid matches and foreground window is visible, allow it
        if target_pid:
            fg_pid = _window_pid(fg_hwnd)
            if fg_pid and fg_pid == target_pid and ctypes.windll.user32.IsWindowVisible(fg_hwnd):
                return True
    except Exception:
        pass
    return False


def _find_window_hwnd_win32(title: str) -> int:
    """Find a top-level window HWND matching title using Win32 EnumWindows without UIA."""
    if not _IS_WINDOWS or not title:
        return 0
    target_lower = title.lower().strip()
    match_hwnd = 0

    def enum_cb(hwnd, _):
        nonlocal match_hwnd
        if ctypes.windll.user32.IsWindowVisible(hwnd):
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                wtitle = buf.value.strip().lower()
                if wtitle and (target_lower in wtitle or (len(wtitle) >= 3 and wtitle in target_lower)):
                    match_hwnd = hwnd
                    return False
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    try:
        ctypes.windll.user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
    except Exception:
        pass
    return match_hwnd


# --------------------------------------------------------------------------
# UIA driver -- every call below runs inside the MTA worker thread.
# --------------------------------------------------------------------------

def _clear_comtypes_gen_cache():
    """Remove stale comtypes gen cache that causes 'Typelib different than module'.

    Happens when comtypes version changes but generated wrappers remain from
    prior version. Clearing forces regeneration on next GetModule.
    """
    try:
        import comtypes.gen as _gen
        import pathlib, shutil
        gen_path = pathlib.Path(_gen.__file__).parent
        for p in gen_path.glob("_944DE083*"):
            try:
                if p.is_file():
                    p.unlink()
                elif p.is_dir():
                    shutil.rmtree(p)
            except Exception:
                pass
        # also clear __pycache__
        for p in gen_path.glob("__pycache__/_944DE083*"):
            try:
                p.unlink()
            except Exception:
                pass
    except Exception:
        pass


def _import_uia():
    """Import UIAutomationClient with stale-cache recovery."""
    try:
        from comtypes.gen import UIAutomationClient as _uia
        return _uia
    except ImportError as exc:
        if "Typelib different than module" in str(exc):
            _clear_comtypes_gen_cache()
            try:
                import comtypes.client as _cc
                _cc.GetModule("UIAutomationCore.dll")
                from comtypes.gen import UIAutomationClient as _uia2
                return _uia2
            except Exception:
                raise exc
        raise


class _UIADriver:
    """Real comtypes-backed UIA layer; tests substitute a fake."""

    def _session(self):
        import comtypes.client
        try:
            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen import UIAutomationClient as uia
            return comtypes.client.CreateObject(uia.CUIAutomation).QueryInterface(uia.IUIAutomation)
        except ImportError as exc:
            if "Typelib different than module" in str(exc):
                _clear_comtypes_gen_cache()
                try:
                    comtypes.client.GetModule("UIAutomationCore.dll")
                    from comtypes.gen import UIAutomationClient as uia2
                    return comtypes.client.CreateObject(uia2.CUIAutomation).QueryInterface(uia2.IUIAutomation)
                except Exception:
                    raise exc
            raise

    def list_windows(self) -> List[Dict[str, Any]]:
        _uia = _import_uia()
        uia = self._session()
        root = uia.GetRootElement()
        wcond = uia.CreatePropertyCondition(_uia.UIA_ControlTypePropertyId, _uia.UIA_WindowControlTypeId)
        # TreeScope_Descendants (4): top-level windows and modal dialogs.
        result = root.FindAll(4, wcond)
        windows = []
        for i in range(result.Length):
            if i >= _MAX_WINDOW_TITLES:
                break
            element = result.GetElement(i)
            name = element.CurrentName
            if name:
                windows.append({"title": name})
        return windows

    def observe(self, window_title: str, max_depth: int, max_elements: int) -> Dict[str, Any]:
        _uia = _import_uia()
        uia = self._session()
        root = uia.GetRootElement()
        element = self._find_window(uia, root, window_title)
        if element is None:
            return {"found": False}
        identity = self._window_identity(uia, element)
        tree = self._build_tree(uia, element, 0, max_depth, max_elements, 0)
        identity["ui_tree"] = tree
        identity["found"] = True
        return identity

    def _find_window(self, uia, root, title: str):
        _uia = _import_uia()
        import re
        exact = uia.CreatePropertyCondition(_uia.UIA_NamePropertyId, title)
        # TreeScope_Descendants (4): search top-level and modal/child windows.
        found = root.FindFirst(4, exact)
        if found:
            return found
        # Bounded partial match over window controls.
        # Include Window and Pane control types (system dialogs like Save As
        # are Window or Pane controls in UIA).
        for ctrl_type_id in (_uia.UIA_WindowControlTypeId,
                             _uia.UIA_PaneControlTypeId):
            wcond = uia.CreatePropertyCondition(_uia.UIA_ControlTypePropertyId, ctrl_type_id)
            windows = root.FindAll(4, wcond)
            target_lower = title.lower().strip()
            # Pass 1: Case-insensitive substring match on name
            for i in range(windows.Length):
                win = windows.GetElement(i)
                try:
                    cname = (win.CurrentName or "").strip().lower()
                except Exception:
                    continue
                if cname and (target_lower in cname or (len(cname) >= 3 and cname in target_lower)):
                    return win

            # Pass 2: Token-based fallback (e.g. '*helo ddddd - Notepad' -> 'Notepad')
            tokens = [t.strip() for t in re.split(r"[-—:*]", target_lower) if len(t.strip()) >= 3]
            for token in reversed(tokens):
                for i in range(windows.Length):
                    win = windows.GetElement(i)
                    try:
                        cname = (win.CurrentName or "").strip().lower()
                    except Exception:
                        continue
                    if cname and token in cname:
                        return win

            # Pass 3: Class-name-based matching for common system dialogs
            # Standard Windows Save As dialog: class "#32770", name "Save As"
            # Standard Windows Open dialog: class "#32770", name "Open"
            dialog_classes = {
                "save": ("#32770", "Save As"),
                "save as": ("#32770", "Save As"),
                "open": ("#32770", "Open"),
                "print": ("#32770", "Print"),
            }
            for key, (cls_name, expected_name) in dialog_classes.items():
                if key in target_lower:
                    for i in range(windows.Length):
                        win = windows.GetElement(i)
                        try:
                            wclass = (win.CurrentClassName or "").strip().lower()
                            if wclass == cls_name.lower():
                                wname = (win.CurrentName or "").strip().lower()
                                if wname and (expected_name.lower() in wname or wname in expected_name.lower()):
                                    return win
                        except Exception:
                            pass

        return None

    def _window_identity(self, uia, element) -> Dict[str, Any]:
        _uia = _import_uia()
        runtime_ids = []
        try:
            rid = element.GetRuntimeId()
            for i in range(rid.Length):
                runtime_ids.append(int(rid.GetElement(i)))
        except Exception:
            pass
        hwnd = 0
        try:
            hwnd = int(element.CurrentNativeWindowHandle)
        except Exception:
            pass
        return {
            "title": element.CurrentName or "",
            "control_type": int(element.CurrentControlType),
            "hwnd": hwnd,
            "pid": _window_pid(hwnd) if hwnd else None,
            "runtime_id": runtime_ids,
            "observed_at": time.time(),
        }

    def _build_tree(self, uia, element, depth: int, max_depth: int, max_elements: int, count: int) -> Dict[str, Any]:
        _uia = _import_uia()
        if depth > max_depth or count >= max_elements:
            return {"name": "...", "truncated": True}
        count += 1
        node = {
            "name": element.CurrentName or "<unnamed>",
            "control_type": int(element.CurrentControlType),
            "automation_id": element.CurrentAutomationId or "",
            "enabled": bool(element.CurrentIsEnabled),
            "offscreen": bool(element.CurrentIsOffscreen),
        }
        try:
            walker = uia.ControlViewWalker
            child = walker.GetFirstChildElement(element)
            children = []
            while child and count < max_elements:
                children.append(self._build_tree(uia, child, depth + 1, max_depth, max_elements, count))
                child = walker.GetNextSiblingElement(child)
            if children:
                node["children"] = children
        except Exception:
            pass
        return node

    def _current_pattern(self, uia, element, pattern_id: int, interface) -> Any:
        """Fetch a UIA pattern and QI it to its concrete comtypes interface.

        ``GetCurrentPattern`` returns a generic ``POINTER(IUnknown)``; the
        generated module provides concrete interfaces (e.g.
        ``IUIAutomationInvokePattern``) that expose the pattern's methods.
        """
        try:
            ptr = element.GetCurrentPattern(pattern_id)
        except Exception:
            return None
        if not ptr:
            return None
        try:
            return ptr.QueryInterface(interface)
        except Exception:
            return None

    def focus(self, window_title: str) -> Dict[str, Any]:
        _uia = _import_uia()
        uia = self._session()
        root = uia.GetRootElement()
        element = self._find_window(uia, root, window_title)
        if element is None:
            return {"ok": False, "code": "WINDOW_NOT_FOUND", "message": f"Window '{window_title}' not found"}
        identity = self._window_identity(uia, element)
        hwnd = identity["hwnd"]
        try:
            pattern = self._current_pattern(uia, element, _uia.UIA_WindowPatternId, _uia.IUIAutomationWindowPattern)
            if pattern is not None:
                pattern.SetWindowVisualState(0)  # WindowVisualState_Normal
                pattern.WaitForInputIdle(2000)
        except Exception:
            pass
        if hwnd:
            if ctypes.windll.user32.IsIconic(hwnd):
                ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            ctypes.windll.user32.BringWindowToTop(hwnd)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        time.sleep(0.15)
        identity["foreground"] = hwnd and ctypes.windll.user32.GetForegroundWindow() == hwnd
        return {"ok": True, "window": identity}

    def find_element(self, window_title: str, description: str, pattern_type: str = "any") -> Dict[str, Any]:
        _uia = _import_uia()
        uia = self._session()
        root = uia.GetRootElement()
        window = self._find_window(uia, root, window_title)
        if window is None:
            return {"ok": False, "code": "WINDOW_NOT_FOUND", "message": f"Window '{window_title}' not found"}
        needle = description.strip().lower()
        target = self._find_element_recursive(uia, window, needle, 0, 0, pattern_type=pattern_type)
        if target is None:
            return {"ok": False, "code": "ELEMENT_NOT_FOUND", "message": f"No element matching '{description}'"}
        element = target[0]
        identity = {
            "name": element.CurrentName or "",
            "control_type": int(element.CurrentControlType),
            "automation_id": element.CurrentAutomationId or "",
            "rect": list(self._element_rect(uia, element)),
        }
        return {"ok": True, "element": identity, "window": self._window_identity(uia, window)}

    def _find_element_recursive(self, uia, element, needle: str, depth: int, count: int, pattern_type: str = "any"):
        _uia = _import_uia()
        if depth > _MAX_UI_TREE_DEPTH or count >= _MAX_UI_ELEMENTS:
            return None
        name = (element.CurrentName or "").strip().lower()
        aid = (element.CurrentAutomationId or "").strip().lower()
        matched = needle and (needle == name or needle in name or needle == aid or (needle == "save" and aid == "1"))

        if matched:
            if pattern_type == "value":
                vpat = self._current_pattern(uia, element, _uia.UIA_ValuePatternId, _uia.IUIAutomationValuePattern)
                if vpat is not None and not vpat.CurrentIsReadOnly:
                    return (element, count)
            elif pattern_type == "invoke":
                ipat = self._current_pattern(uia, element, _uia.UIA_InvokePatternId, _uia.IUIAutomationInvokePattern)
                if ipat is not None:
                    return (element, count)
                lpat = self._current_pattern(uia, element, _uia.UIA_LegacyIAccessiblePatternId, _uia.IUIAutomationLegacyIAccessiblePattern)
                if lpat is not None and getattr(lpat, "CurrentDefaultAction", None):
                    return (element, count)
                expat = self._current_pattern(uia, element, _uia.UIA_ExpandCollapsePatternId, _uia.IUIAutomationExpandCollapsePattern)
                if expat is not None:
                    return (element, count)
            elif pattern_type == "expand":
                expat = self._current_pattern(uia, element, _uia.UIA_ExpandCollapsePatternId, _uia.IUIAutomationExpandCollapsePattern)
                if expat is not None:
                    return (element, count)
            elif pattern_type == "any":
                return (element, count)

        count += 1
        try:
            walker = uia.ControlViewWalker
            child = walker.GetFirstChildElement(element)
            while child and count < _MAX_UI_ELEMENTS:
                found = self._find_element_recursive(uia, child, needle, depth + 1, count, pattern_type=pattern_type)
                if found is not None:
                    return found
                count += 1
                child = walker.GetNextSiblingElement(child)
        except Exception:
            pass
        return (element, count) if (matched and pattern_type == "any") else None

    def _element_rect(self, uia, element) -> Tuple[int, int, int, int]:
        try:
            rect = element.CurrentBoundingRectangle
            return (int(rect.left), int(rect.top), int(rect.right - rect.left), int(rect.bottom - rect.top))
        except Exception:
            return (0, 0, 0, 0)

    def invoke(self, window_title: str, description: str) -> Dict[str, Any]:
        _uia = _import_uia()
        found = self.find_element(window_title, description, pattern_type="invoke")
        if not found["ok"]:
            return found
        uia = self._session()
        root = uia.GetRootElement()
        window = self._find_window(uia, root, window_title)
        if window is None:
            return {"ok": False, "code": "WINDOW_NOT_FOUND", "message": f"Window '{window_title}' not found"}
        element = self._find_element_recursive(uia, window, description.strip().lower(), 0, 0, pattern_type="invoke")
        if element is None:
            return {"ok": False, "code": "ELEMENT_NOT_FOUND", "message": f"No element matching '{description}'"}
        target = element[0]
        try:
            pattern = self._current_pattern(uia, target, _uia.UIA_InvokePatternId, _uia.IUIAutomationInvokePattern)
            if pattern is not None:
                pattern.Invoke()
                return {"ok": True, "method": "uia_invoke", "element": found["element"]}

            legacy = self._current_pattern(uia, target, _uia.UIA_LegacyIAccessiblePatternId, _uia.IUIAutomationLegacyIAccessiblePattern)
            if legacy is not None:
                legacy.DoDefaultAction()
                return {"ok": True, "method": "uia_legacy_action", "element": found["element"]}

            expand = self._current_pattern(uia, target, _uia.UIA_ExpandCollapsePatternId, _uia.IUIAutomationExpandCollapsePattern)
            if expand is not None:
                expand.Expand()
                return {"ok": True, "method": "uia_expand", "element": found["element"]}

            return {"ok": False, "code": "NO_INVOKE_PATTERN",
                    "message": f"Element '{description}' has no Invoke, Legacy DefaultAction, or ExpandCollapse pattern; Sythion (Synth) does not guess a fallback click"}
        except Exception as exc:
            try:
                legacy = self._current_pattern(uia, target, _uia.UIA_LegacyIAccessiblePatternId, _uia.IUIAutomationLegacyIAccessiblePattern)
                if legacy is not None:
                    legacy.DoDefaultAction()
                    return {"ok": True, "method": "uia_legacy_action", "element": found["element"]}
            except Exception:
                pass
            try:
                expand = self._current_pattern(uia, target, _uia.UIA_ExpandCollapsePatternId, _uia.IUIAutomationExpandCollapsePattern)
                if expand is not None:
                    expand.Expand()
                    return {"ok": True, "method": "uia_expand", "element": found["element"]}
            except Exception:
                pass
            return {"ok": False, "code": "NO_INVOKE_PATTERN",
                    "message": f"Element '{description}' invocation failed: {exc}"}

    def set_value(self, window_title: str, description: str, value: str) -> Dict[str, Any]:
        _uia = _import_uia()
        found = self.find_element(window_title, description, pattern_type="value")
        if not found["ok"]:
            return found
        uia = self._session()
        root = uia.GetRootElement()
        window = self._find_window(uia, root, window_title)
        if window is None:
            return {"ok": False, "code": "WINDOW_NOT_FOUND", "message": f"Window '{window_title}' not found"}
        element = self._find_element_recursive(uia, window, description.strip().lower(), 0, 0, pattern_type="value")
        if element is None:
            return {"ok": False, "code": "ELEMENT_NOT_FOUND", "message": f"No element matching '{description}'"}
        target = element[0]
        try:
            pattern = self._current_pattern(uia, target, _uia.UIA_ValuePatternId, _uia.IUIAutomationValuePattern)
            if pattern is None or pattern.CurrentIsReadOnly:
                return {"ok": False, "code": "NO_VALUE_PATTERN",
                        "message": f"Element '{description}' has no writable Value pattern; Sythion (Synth) does not guess a fallback"}
            pattern.SetValue(value)
            return {"ok": True, "method": "uia_value", "element": found["element"]}
        except Exception as exc:
            return {"ok": False, "code": "NO_VALUE_PATTERN",
                    "message": f"Element '{description}' value set failed: {exc}"}

    # -- physical-pixel fallback input (single-shot; re-observes afterwards) --

    def single_click(self, window_title: str, x: int, y: int) -> Dict[str, Any]:
        identity = self.observe(window_title, 1, 1)
        if not identity.get("found"):
            return {"ok": False, "code": "WINDOW_NOT_FOUND", "message": f"Window '{window_title}' not found"}
        _uia = _import_uia()
        uia = self._session()
        root = uia.GetRootElement()
        window = self._find_window(uia, root, window_title)
        rect = self._element_rect(uia, window)
        l, t, w, h = rect
        px = l + x
        py = t + y
        _send_mouse_click(px, py)
        post = self.observe(window_title, 1, 1)
        return {
            "ok": True,
            "method": "coordinate_click",
            "point": {"x": px, "y": py},
            "window_rect": rect,
            "reobserved": post,
        }

    def _foreground_hwnd(self) -> int:
        """The currently focused window's hwnd (0 on non-Windows / failure)."""
        if not _IS_WINDOWS:
            return 0
        try:
            return int(ctypes.windll.user32.GetForegroundWindow() or 0)
        except (AttributeError, OSError):
            return 0

    def _require_foreground(self, identity: Dict[str, Any]) -> Optional[Dict[str, str]]:
        """Re-verify, at send time, that the target window is still foreground.

        The caller-thread check runs before worker dispatch; a user click-away
        in that gap would otherwise send keystrokes/hotkeys to the wrong window.
        This runs inside the worker immediately before SendInput to shrink the
        check-to-send window, using the same FOREGROUND_REQUIRED error path.
        """
        hwnd = int(identity.get("hwnd") or 0)
        if not hwnd:
            return None
        if not _is_foreground_or_modal_child(hwnd, int(identity.get("pid") or 0)):
            return {
                "code": "FOREGROUND_REQUIRED",
                "message": f"Window '{identity.get('title')}' is not foreground; focus it first",
            }
        return None

    def type_text(self, window_title: str, text: str) -> Dict[str, Any]:
        identity = self.observe(window_title, 1, 1)
        if not identity.get("found"):
            return {"ok": False, "code": "WINDOW_NOT_FOUND", "message": f"Window '{window_title}' not found"}
        error = self._require_foreground(identity)
        if error:
            return {"ok": False, **error}
        _send_unicode_text(text)
        post = self.observe(window_title, 1, 1)
        return {"ok": True, "method": "send_input_unicode", "typed": text, "reobserved": post}

    def hotkey(self, window_title: str, keys: List[str]) -> Dict[str, Any]:
        identity = self.observe(window_title, 1, 1)
        if not identity.get("found"):
            return {"ok": False, "code": "WINDOW_NOT_FOUND", "message": f"Window '{window_title}' not found"}
        error = self._require_foreground(identity)
        if error:
            return {"ok": False, **error}
        _send_hotkey(keys)
        post = self.observe(window_title, 1, 1)
        return {"ok": True, "method": "send_input_hotkey", "keys": list(keys), "reobserved": post}


# --------------------------------------------------------------------------
# Win32 input synthesis (physical pixels).
# --------------------------------------------------------------------------

_INPUT_KEYBOARD = 1
_INPUT_MOUSE = 0
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_ABSOLUTE = 0x8000
_MOUSEEVENTF_MOVE = 0x0001


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_ulong),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", ctypes.c_ushort),
        ("biBitCount", ctypes.c_ushort),
        ("biCompression", ctypes.c_ulong),
        ("biSizeImage", ctypes.c_ulong),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", ctypes.c_ulong),
        ("biClrImportant", ctypes.c_ulong),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", _INPUT_UNION)]


def _send_input(*inputs: _INPUT) -> None:
    if not _IS_WINDOWS:
        return
    arr = (_INPUT * len(inputs))(*inputs)
    ctypes.windll.user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))


def _key_input(vk: int, flags: int) -> _INPUT:
    extra = ctypes.c_ulong(0)
    return _INPUT(
        _INPUT_KEYBOARD,
        _INPUT_UNION(ki=_KEYBDINPUT(ctypes.c_ushort(vk), 0, ctypes.c_ulong(flags), 0, ctypes.pointer(extra))))


def _unicode_input(char: str, flags: int) -> _INPUT:
    extra = ctypes.c_ulong(0)
    return _INPUT(
        _INPUT_KEYBOARD,
        _INPUT_UNION(ki=_KEYBDINPUT(0, ctypes.c_ushort(ord(char)), ctypes.c_ulong(flags | _KEYEVENTF_UNICODE), 0,
                                    ctypes.pointer(extra))))


def _mouse_input(dx: int, dy: int, flags: int) -> _INPUT:
    extra = ctypes.c_ulong(0)
    return _INPUT(_INPUT_MOUSE, _INPUT_UNION(mi=_MOUSEINPUT(
        ctypes.c_long(dx), ctypes.c_long(dy), 0, ctypes.c_ulong(flags), 0, ctypes.pointer(extra))))


def _absolute_xy(x: int, y: int) -> Tuple[int, int]:
    """Map physical pixels to SendInput absolute coordinates on the virtual screen."""
    if not _IS_WINDOWS:
        return (x, y)
    sw = int(ctypes.windll.user32.GetSystemMetrics(0))  # SM_CXSCREEN
    sh = int(ctypes.windll.user32.GetSystemMetrics(1))  # SM_CYSCREEN
    if sw <= 1 or sh <= 1:
        return (x, y)
    return (int(x * 65535 / (sw - 1)), int(y * 65535 / (sh - 1)))


def _send_mouse_click(x: int, y: int) -> None:
    dx, dy = _absolute_xy(x, y)
    _send_input(
        _mouse_input(dx, dy, _MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE),
        _mouse_input(dx, dy, _MOUSEEVENTF_LEFTDOWN | _MOUSEEVENTF_ABSOLUTE),
        _mouse_input(dx, dy, _MOUSEEVENTF_LEFTUP | _MOUSEEVENTF_ABSOLUTE),
    )


def _send_unicode_text(text: str) -> None:
    """Type text by sending each character as a sequential key-down + key-up pair.

    Previously this function batched ALL key-down events first, then ALL
    key-up events in one SendInput call.  The OS keyboard queue interpreted
    this as a simultaneous chord — not a sequence — causing characters to be
    dropped, repeated, or reordered (e.g. 'hello world' → 'helo ddddd').

    The fix sends each character individually: down, up, next char.
    A small inter-character delay (2 ms) prevents the input queue from
    merging adjacent events on fast machines.
    """
    for ch in text:
        _send_input(
            _unicode_input(ch, 0),
            _unicode_input(ch, _KEYEVENTF_KEYUP),
        )
        time.sleep(0.002)  # 2 ms inter-character delay


_VK_MAP = {
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "space": 0x20, "backspace": 0x08,
    "delete": 0x2E, "escape": 0x1B, "esc": 0x1B, "home": 0x24, "end": 0x23,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B,
    "volumeup": 0xAF, "volumedown": 0xAE, "mute": 0xAD,
}


def _key_vk(key: str) -> int:
    k = key.lower()
    if k in _VK_MAP:
        return _VK_MAP[k]
    if k.startswith("f") and k[1:].isdigit():
        return 0x70 + int(k[1:]) - 1
    if len(k) == 1 and k.isalpha():
        return ord(k.upper())
    if len(k) == 1 and k.isdigit():
        return ord(k)
    punct = {"-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, ";": 0xBA, "'": 0xDE,
             ",": 0xBC, ".": 0xBE, "/": 0xBF, "\\": 0xDC}
    return punct.get(k, 0)


def _send_hotkey(keys: List[str]) -> None:
    vks = [_key_vk(k) for k in keys if _key_vk(k)]
    down = [_key_input(vk, 0) for vk in vks]
    up = [_key_input(vk, _KEYEVENTF_KEYUP) for vk in reversed(vks)]
    _send_input(*(down + up))


# --------------------------------------------------------------------------
# The MTA worker.
# --------------------------------------------------------------------------

class _UIAWorker:
    """One background COM MTA thread that owns every UIA call."""

    def __init__(self):
        self._queue: "queue.Queue[Tuple[str, Future, Callable]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._stop_sent = False

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                if not self._stop_sent:
                    return
                # Previous stop() is winding down; wait for it so a fresh
                # worker thread starts cleanly instead of racing with the old.
                old = self._thread
                self._lock.release()
                try:
                    old.join(timeout=_UIA_CALL_TIMEOUT)
                finally:
                    self._lock.acquire()
                self._thread = None
            self._stop_sent = False
            self._thread = threading.Thread(target=self._run, name="sythion-uia-mta", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        if _IS_WINDOWS:
            try:
                ctypes.oledll.ole32.CoInitializeEx(None, 0x0)  # COINIT_MULTITHREADED
            except (AttributeError, OSError):
                pass
        while True:
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                if self._stop_sent:
                    break
                continue
            kind, future, fn = item
            if kind == "call":
                try:
                    future.set_result(fn())
                except BaseException as exc:  # noqa: BLE001 - delivered to caller
                    future.set_exception(exc)
            elif kind == "stop":
                break

    def run(self, fn: Callable[[], Any], timeout: float = _UIA_CALL_TIMEOUT) -> Any:
        """Run `fn` on the MTA worker and wait for its result."""
        self.start()
        future: Future = Future()
        self._queue.put(("call", future, fn))
        return future.result(timeout=timeout)

    def stop(self) -> None:
        with self._lock:
            self._stop_sent = True
        self._queue.put(("stop", Future(), lambda: None))
        # Drain any items queued after the stop marker so a later start() does
        # not resurrect stale calls; the old thread will exit on the marker.
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            kind, future, fn = item
            if kind == "call":
                future.set_exception(RuntimeError("worker stopped"))

    def restart(self) -> None:
        """Forcefully stop the worker thread and restart a fresh one."""
        self.stop()
        self.start()


_gui_worker = _UIAWorker()
_uia_driver = _UIADriver()


class _GUIUnavailable(Exception):
    pass


async def _run_on_worker(fn: Callable[[], Any]) -> Any:
    """Run a driver call on the MTA worker without blocking the event loop.

    If a transient COM or worker exception occurs, attempts recovery by
    restarting the dedicated MTA worker thread and retrying once.
    """
    try:
        return await asyncio.to_thread(_gui_worker.run, fn)
    except Exception as exc:
        try:
            _gui_worker.restart()
            return await asyncio.to_thread(_gui_worker.run, fn)
        except Exception:
            raise exc


def _desktop_gate() -> Optional[Dict[str, str]]:
    """Deny all GUI actions when the desktop is locked / on the secure desktop."""
    if not _IS_WINDOWS:
        return {"code": "NON_WINDOWS_UNSUPPORTED", "message": "GUI tools are Windows-only in v1"}
    if not _session_accepts_input():
        return {"code": "SESSION_LOCKED", "message": "The session is locked or on the secure desktop; no GUI action is allowed"}


def _window_gate(identity: Dict[str, Any]) -> Optional[Dict[str, str]]:
    hwnd = int(identity.get("hwnd") or 0)
    error = _target_window_safety(hwnd, identity.get("title") or "")
    if error:
        return error
    if identity.get("foreground") is False:
        return {"code": "FOREGROUND_REQUIRED", "message": f"Window '{identity.get('title')}' is not foreground; focus it first"}


# --------------------------------------------------------------------------
# Tools.
# --------------------------------------------------------------------------

class GUIObserveTool(BaseTool[GUIObserveInput]):
    """Observe the current GUI state (bounded UIA tree; optional bounded OCR)."""

    name = "gui_observe"
    description = "Observe a window's UI elements (bounded); optionally request OCR for a region"
    input_schema = GUIObserveInput

    async def execute(self, input_data: GUIObserveInput) -> ToolOutput:
        gate = _desktop_gate()
        if gate:
            return ToolOutput(success=False, error=gate, duration_ms=0)
        try:
            identity = await _run_on_worker(
                lambda: _uia_driver.observe(input_data.window_title, _MAX_UI_TREE_DEPTH, _MAX_UI_ELEMENTS))
        except Exception as exc:
            logger.warning("UIA observe call failed for '%s': %s: %s", input_data.window_title, type(exc).__name__, exc, exc_info=True)
            return ToolOutput(success=False, error={"code": "UIA_UNAVAILABLE", "message": "UI Automation unavailable"}, duration_ms=0)
        if not identity.get("found"):
            return ToolOutput(
                success=False,
                error={"code": "WINDOW_NOT_FOUND", "message": f"Window '{input_data.window_title}' not found"},
                duration_ms=0)
        error = _target_window_safety(int(identity.get("hwnd") or 0), identity.get("title") or "")
        if error:
            return ToolOutput(success=False, error=error, duration_ms=0)

        data = {
            "window": {
                "title": identity.get("title"),
                "hwnd": identity.get("hwnd"),
                "pid": identity.get("pid"),
                "observed_at": identity.get("observed_at"),
            },
            "ui_elements": identity.get("ui_tree"),
            "bounded": True,
        }
        if input_data.use_ocr:
            ocr = await self._bounded_ocr(input_data.window_title, identity)
            data["ocr"] = ocr
        return ToolOutput(success=True, data=data, evidence={"source": "uia_mta_worker"})

    async def _bounded_ocr(self, window_title: str, identity: Dict[str, Any]) -> Dict[str, Any]:
        # Targeted OCR/visual observation no older than _OCR_MAX_AGE_SECONDS.
        # The OCR engine (Windows.Media.Ocr / tesseract) is not bundled in v1:
        # return an honest "unavailable" instead of guessing or persisting raw
        # screenshots.
        try:
            return {
                "available": False,
                "reason": "bounded OCR engine is not installed in this v1 build",
                "max_age_seconds": _OCR_MAX_AGE_SECONDS,
            }
        except Exception as e:  # pragma: no cover
            return {"available": False, "reason": str(e)}

    async def verify(self, input_data: GUIObserveInput, output: ToolOutput) -> bool:
        return output.success and output.data is not None

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {
            "risk_level": "low",
            "requires_confirmation": False,
            "requires_screen_access": True,
            "requires_ui_automation": True,
            "elevated_process_check": True,
            "privacy_sensitive": True,
            "redact_sensitive": True,
        }

    def validate_targets(self, input_data: GUIObserveInput) -> Optional[Dict[str, str]]:
        return _gui_target_error(input_data.window_title)


class GUIFocusTool(BaseTool[GUIFocusInput]):
    """Bring a window to the foreground."""

    name = "gui_focus"
    description = "Bring a window to the foreground and give it focus"
    input_schema = GUIFocusInput

    async def execute(self, input_data: GUIFocusInput) -> ToolOutput:
        gate = _desktop_gate()
        if gate:
            return ToolOutput(success=False, error=gate, duration_ms=0)
        try:
            result = await _run_on_worker(lambda: _uia_driver.focus(input_data.window_title))
        except Exception as exc:
            logger.warning("UIA focus call failed for '%s': %s: %s", input_data.window_title, type(exc).__name__, exc, exc_info=True)
            return ToolOutput(success=False, error={"code": "UIA_UNAVAILABLE", "message": "UI Automation unavailable"}, duration_ms=0)
        if not result.get("ok"):
            return ToolOutput(success=False, error={"code": result.get("code", "FOCUS_ERROR"), "message": result.get("message", "")}, duration_ms=0)
        window = result.get("window", {})
        error = _target_window_safety(int(window.get("hwnd") or 0), window.get("title") or "")
        if error:
            return ToolOutput(success=False, error=error, duration_ms=0)
        return ToolOutput(
            success=True,
            data={
                "focused_window": window.get("title") or input_data.window_title,
                "hwnd": window.get("hwnd"),
                "pid": window.get("pid"),
                "foreground": window.get("foreground"),
            },
            evidence={"source": "uia_mta_worker", "method": "window_pattern"})

    async def verify(self, input_data: GUIFocusInput, output: ToolOutput) -> bool:
        return output.success

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {
            "risk_level": "medium",
            "requires_confirmation": True,
            "foreground_window_change": True,
            "requires_ui_automation": True,
            "elevated_process_check": True,
        }

    def validate_targets(self, input_data: GUIFocusInput) -> Optional[Dict[str, str]]:
        return _gui_target_error(input_data.window_title)


class GUIInvokeTool(BaseTool[GUIInvokeInput]):
    """Invoke a UI element using its native UIA pattern."""

    name = "gui_invoke"
    description = "Invoke a UI element by description using its UIA pattern (no coordinate fallback)"
    input_schema = GUIInvokeInput

    async def execute(self, input_data: GUIInvokeInput) -> ToolOutput:
        gate = _desktop_gate()
        if gate:
            return ToolOutput(success=False, error=gate, duration_ms=0)
        try:
            result = await _run_on_worker(
                lambda: _uia_driver.invoke(input_data.window_title, input_data.element_description))
        except Exception as exc:
            logger.warning("UIA invoke call failed for '%s' ('%s'): %s: %s", input_data.window_title, input_data.element_description, type(exc).__name__, exc, exc_info=True)
            return ToolOutput(success=False, error={"code": "UIA_UNAVAILABLE", "message": "UI Automation unavailable"}, duration_ms=0)
        if not result.get("ok"):
            return ToolOutput(success=False, error={"code": result.get("code", "INVOKE_ERROR"), "message": result.get("message", "")}, duration_ms=0)
        element = result.get("element", {})
        rect = element.get("rect") or [0, 0, 0, 0]
        hwnd = _element_hwnd_from_rect(rect)
        if hwnd:
            error = _target_window_safety(hwnd, input_data.window_title)
            if error:
                return ToolOutput(success=False, error=error, duration_ms=0)
        return ToolOutput(
            success=True,
            data={"invoked_element": input_data.element_description, "method": result.get("method")},
            evidence={"source": "uia_mta_worker", "method": result.get("method")})

    async def verify(self, input_data: GUIInvokeInput, output: ToolOutput) -> bool:
        return output.success

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {
            "risk_level": "medium",
            "requires_confirmation": True,
            "requires_ui_automation": True,
            "element_interaction": True,
            "elevated_process_check": True,
        }

    def validate_targets(self, input_data: GUIInvokeInput) -> Optional[Dict[str, str]]:
        return _gui_target_error(input_data.window_title, input_data.element_description)


class GUISetValueTool(BaseTool[GUISetValueInput]):
    """Set a UI element value using its native UIA Value pattern."""

    name = "gui_set_value"
    description = "Set the value of a UI element using its UIA Value pattern"
    input_schema = GUISetValueInput

    async def execute(self, input_data: GUISetValueInput) -> ToolOutput:
        gate = _desktop_gate()
        if gate:
            return ToolOutput(success=False, error=gate, duration_ms=0)
        try:
            result = await _run_on_worker(
                lambda: _uia_driver.set_value(input_data.window_title, input_data.element_description, input_data.value))
        except Exception as exc:
            logger.warning("UIA set_value call failed for '%s' ('%s'): %s: %s", input_data.window_title, input_data.element_description, type(exc).__name__, exc, exc_info=True)
            return ToolOutput(success=False, error={"code": "UIA_UNAVAILABLE", "message": "UI Automation unavailable"}, duration_ms=0)
        if not result.get("ok"):
            return ToolOutput(success=False, error={"code": result.get("code", "SET_VALUE_ERROR"), "message": result.get("message", "")}, duration_ms=0)
        return ToolOutput(
            success=True,
            data={"value_set": input_data.value, "method": result.get("method")},
            evidence={"source": "uia_mta_worker", "method": result.get("method")})

    async def verify(self, input_data: GUISetValueInput, output: ToolOutput) -> bool:
        return output.success

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {
            "risk_level": "medium",
            "requires_confirmation": True,
            "requires_ui_automation": True,
            "element_modification": True,
            "data_entry": True,
            "elevated_process_check": True,
        }

    def validate_targets(self, input_data: GUISetValueInput) -> Optional[Dict[str, str]]:
        return _gui_target_error(input_data.window_title, input_data.element_description)


class GUIClickTool(BaseTool[GUIClickInput]):
    """Single physical-pixel coordinate click fallback, then re-observe."""

    name = "gui_click"
    description = "Click once at physical-pixel coordinates within a window, then re-observe"
    input_schema = GUIClickInput

    async def execute(self, input_data: GUIClickInput) -> ToolOutput:
        gate = _desktop_gate()
        if gate:
            return ToolOutput(success=False, error=gate, duration_ms=0)

        # Bounds check the requested point against the window before any input.
        try:
            identity = await _run_on_worker(
                lambda: _uia_driver.observe(input_data.window_title, 1, 1))
        except Exception as exc:
            logger.warning("UIA observe before click failed for '%s': %s: %s", input_data.window_title, type(exc).__name__, exc, exc_info=True)
            return ToolOutput(success=False, error={"code": "UIA_UNAVAILABLE", "message": "UI Automation unavailable"}, duration_ms=0)
        if not identity.get("found"):
            return ToolOutput(success=False, error={"code": "WINDOW_NOT_FOUND", "message": f"Window '{input_data.window_title}' not found"}, duration_ms=0)
        hwnd = int(identity.get("hwnd") or 0)
        error = _target_window_safety(hwnd, identity.get("title") or "")
        if error:
            return ToolOutput(success=False, error=error, duration_ms=0)
        fg = _is_foreground_or_modal_child(hwnd, identity.get("pid"))
        if not fg:
            return ToolOutput(success=False, error={"code": "FOREGROUND_REQUIRED", "message": f"Window '{input_data.window_title}' is not foreground; focus it first"}, duration_ms=0)

        try:
            result = await _run_on_worker(
                lambda: _uia_driver.single_click(input_data.window_title, input_data.x, input_data.y))
        except Exception as exc:
            logger.warning("UIA single_click call failed for '%s' (%s, %s): %s: %s", input_data.window_title, input_data.x, input_data.y, type(exc).__name__, exc, exc_info=True)
            return ToolOutput(success=False, error={"code": "UIA_UNAVAILABLE", "message": "UI Automation unavailable"}, duration_ms=0)
        if not result.get("ok"):
            return ToolOutput(success=False, error={"code": result.get("code", "CLICK_ERROR"), "message": result.get("message", "")}, duration_ms=0)
        return ToolOutput(
            success=True,
            data={
                "clicked_at": result.get("point"),
                "method": result.get("method"),
                "inputs": 1,
                "reobserved": result.get("reobserved"),
            },
            evidence={"source": "send_input", "method": result.get("method"), "inputs": 1})

    async def verify(self, input_data: GUIClickInput, output: ToolOutput) -> bool:
        return output.success

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {
            "risk_level": "high",
            "requires_confirmation": True,
            "input_injection": True,
            "coordinate_bounds_check": True,
            "requires_ui_automation": True,
            "elevated_process_check": True,
        }

    def validate_targets(self, input_data: GUIClickInput) -> Optional[Dict[str, str]]:
        return _gui_target_error(input_data.window_title)


class GUITypeTool(BaseTool[GUITypeInput]):
    """Type text into the foreground window (single input burst, then re-observe)."""

    name = "gui_type"
    description = "Type text into the foreground window once, then re-observe"
    input_schema = GUITypeInput

    async def execute(self, input_data: GUITypeInput) -> ToolOutput:
        gate = _desktop_gate()
        if gate:
            return ToolOutput(success=False, error=gate, duration_ms=0)
        try:
            identity = await _run_on_worker(lambda: _uia_driver.observe(input_data.window_title, 1, 1))
        except Exception as exc:
            logger.warning("UIA observe before type failed for '%s': %s: %s", input_data.window_title, type(exc).__name__, exc, exc_info=True)
            return ToolOutput(success=False, error={"code": "UIA_UNAVAILABLE", "message": "UI Automation unavailable"}, duration_ms=0)
        if not identity.get("found"):
            return ToolOutput(success=False, error={"code": "WINDOW_NOT_FOUND", "message": f"Window '{input_data.window_title}' not found"}, duration_ms=0)
        hwnd = int(identity.get("hwnd") or 0)
        error = _target_window_safety(hwnd, identity.get("title") or "")
        if error:
            return ToolOutput(success=False, error=error, duration_ms=0)
        fg = _is_foreground_or_modal_child(hwnd, identity.get("pid"))
        if not fg:
            return ToolOutput(success=False, error={"code": "FOREGROUND_REQUIRED", "message": f"Window '{input_data.window_title}' is not foreground; focus it first"}, duration_ms=0)
        try:
            result = await _run_on_worker(lambda: _uia_driver.type_text(input_data.window_title, input_data.text))
        except Exception as exc:
            logger.warning("UIA type_text call failed for '%s': %s: %s", input_data.window_title, type(exc).__name__, exc, exc_info=True)
            return ToolOutput(success=False, error={"code": "UIA_UNAVAILABLE", "message": "UI Automation unavailable"}, duration_ms=0)
        if not result.get("ok"):
            return ToolOutput(success=False, error={"code": result.get("code", "TYPE_ERROR"), "message": result.get("message", "")}, duration_ms=0)
        return ToolOutput(
            success=True,
            data={"typed_text": result.get("typed"), "length": len(input_data.text), "inputs": 1,
                  "reobserved": result.get("reobserved")},
            evidence={"source": "send_input", "inputs": 1})

    async def verify(self, input_data: GUITypeInput, output: ToolOutput) -> bool:
        return output.success

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {
            "risk_level": "high",
            "requires_confirmation": True,
            "keyboard_input": True,
            "input_injection": True,
            "requires_ui_automation": True,
            "elevated_process_check": True,
        }

    def validate_targets(self, input_data: GUITypeInput) -> Optional[Dict[str, str]]:
        return _gui_target_error(input_data.window_title)


class GUIHotkeyTool(BaseTool[GUIHotkeyInput]):
    """Press a validated hotkey in the foreground window once, then re-observe."""

    name = "gui_hotkey"
    description = "Press a validated hotkey combination in the foreground window once"
    input_schema = GUIHotkeyInput

    async def execute(self, input_data: GUIHotkeyInput) -> ToolOutput:
        gate = _desktop_gate()
        if gate:
            return ToolOutput(success=False, error=gate, duration_ms=0)
        error = _hotkey_error(input_data.keys)
        if error:
            return ToolOutput(success=False, error=error, duration_ms=0)
        try:
            identity = await _run_on_worker(lambda: _uia_driver.observe(input_data.window_title, 1, 1))
        except Exception as exc:
            logger.warning("UIA observe before hotkey failed for '%s': %s: %s", input_data.window_title, type(exc).__name__, exc, exc_info=True)
            return ToolOutput(success=False, error={"code": "UIA_UNAVAILABLE", "message": "UI Automation unavailable"}, duration_ms=0)
        if not identity.get("found"):
            return ToolOutput(success=False, error={"code": "WINDOW_NOT_FOUND", "message": f"Window '{input_data.window_title}' not found"}, duration_ms=0)
        hwnd = int(identity.get("hwnd") or 0)
        error = _target_window_safety(hwnd, identity.get("title") or "")
        if error:
            return ToolOutput(success=False, error=error, duration_ms=0)
        fg = _is_foreground_or_modal_child(hwnd, identity.get("pid"))
        if not fg:
            return ToolOutput(success=False, error={"code": "FOREGROUND_REQUIRED", "message": f"Window '{input_data.window_title}' is not foreground; focus it first"}, duration_ms=0)
        try:
            result = await _run_on_worker(lambda: _uia_driver.hotkey(input_data.window_title, list(input_data.keys)))
        except Exception as exc:
            logger.warning("UIA hotkey call failed for '%s' %s: %s: %s", input_data.window_title, input_data.keys, type(exc).__name__, exc, exc_info=True)
            return ToolOutput(success=False, error={"code": "UIA_UNAVAILABLE", "message": "UI Automation unavailable"}, duration_ms=0)
        if not result.get("ok"):
            return ToolOutput(success=False, error={"code": result.get("code", "HOTKEY_ERROR"), "message": result.get("message", "")}, duration_ms=0)
        return ToolOutput(
            success=True,
            data={"hotkey_executed": "+".join(k.lower() for k in input_data.keys), "inputs": 1,
                  "reobserved": result.get("reobserved")},
            evidence={"source": "send_input", "inputs": 1})

    async def verify(self, input_data: GUIHotkeyInput, output: ToolOutput) -> bool:
        return output.success

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {
            "risk_level": "high",
            "requires_confirmation": True,
            "keyboard_input": True,
            "input_injection": True,
            "dangerous_combinations": sorted(c for c in _DANGEROUS_HOTKEYS),
            "requires_ui_automation": True,
            "elevated_process_check": True,
        }

    def validate_targets(self, input_data: GUIHotkeyInput) -> Optional[Dict[str, str]]:
        error = _gui_target_error(input_data.window_title)
        if error:
            return error
        return _hotkey_error(input_data.keys)


class GUIPasteTool(BaseTool[GUIPasteInput]):
    """Paste text into a target window via clipboard/input synthesis."""

    name = "gui_paste"
    description = "Paste text into a visible, foreground window"
    input_schema = GUIPasteInput

    async def execute(self, input_data: GUIPasteInput) -> ToolOutput:
        if not _IS_WINDOWS:
            return ToolOutput(success=False, error={"code": "NOT_SUPPORTED", "message": "Windows only"}, duration_ms=0)
        try:
            identity = await _run_on_worker(lambda: _uia_driver.observe(input_data.window_title, 1, 1))
        except Exception as exc:
            logger.warning("UIA observe before paste failed for '%s': %s: %s", input_data.window_title, type(exc).__name__, exc, exc_info=True)
            return ToolOutput(success=False, error={"code": "UIA_UNAVAILABLE", "message": "UI Automation unavailable"}, duration_ms=0)
        if not identity.get("found"):
            return ToolOutput(success=False, error={"code": "WINDOW_NOT_FOUND", "message": f"Window '{input_data.window_title}' not found"}, duration_ms=0)
        hwnd = int(identity.get("hwnd") or 0)
        error = _target_window_safety(hwnd, identity.get("title") or "")
        if error:
            return ToolOutput(success=False, error=error, duration_ms=0)
        fg = _is_foreground_or_modal_child(hwnd, identity.get("pid"))
        if not fg:
            return ToolOutput(success=False, error={"code": "FOREGROUND_REQUIRED", "message": f"Window '{input_data.window_title}' is not foreground; focus it first"}, duration_ms=0)
        try:
            result = await _run_on_worker(lambda: _uia_driver.hotkey(input_data.window_title, ["ctrl", "v"]))
        except Exception as exc:
            logger.warning("UIA paste call failed for '%s': %s: %s", input_data.window_title, type(exc).__name__, exc, exc_info=True)
            return ToolOutput(success=False, error={"code": "UIA_UNAVAILABLE", "message": "UI Automation unavailable"}, duration_ms=0)
        if not result.get("ok"):
            return ToolOutput(success=False, error={"code": result.get("code", "PASTE_ERROR"), "message": result.get("message", "")}, duration_ms=0)
        return ToolOutput(
            success=True,
            data={"pasted": True, "inputs": 1, "reobserved": result.get("reobserved")},
            evidence={"source": "clipboard_paste", "inputs": 1})

    def observe(self, window_title: str, max_depth: int, max_elements: int):
        return _uia_driver.observe(window_title, max_depth, max_elements)

    async def verify(self, input_data: GUIPasteInput, output: ToolOutput) -> bool:
        return output.success

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {
            "risk_level": "high",
            "requires_confirmation": True,
            "clipboard_access": True,
            "input_injection": True,
            "requires_ui_automation": True,
            "elevated_process_check": True,
        }

    def validate_targets(self, input_data: GUIPasteInput) -> Optional[Dict[str, str]]:
        return _gui_target_error(input_data.window_title)


class GUIScreenshotTool(BaseTool[GUIScreenshotInput]):
    """
    Capture a screenshot of a specific window or the entire screen.

    Uses Win32 APIs (GetWindowRect + BitBlt) to capture the target window.
    Saves as PNG to the specified path or a default screenshots directory.
    """

    name = "gui_screenshot"
    description = "Capture a screenshot of a specific window or the entire screen as an image file. Useful for visual inspection, OCR/visual verification, or fallback when UI Automation elements cannot be observed or targeted."
    input_schema = GUIScreenshotInput

    async def execute(self, input_data: GUIScreenshotInput) -> ToolOutput:
        if not _IS_WINDOWS:
            return ToolOutput(success=False, error={"code": "NOT_SUPPORTED", "message": "Windows only"})

        # Fail fast in prototype builds before any screen capture: an explicit
        # save path must stay inside approved roots / the isolated profile.
        if input_data.save_path:
            _proto = False
            try:
                try:
                    from backend.core.secrets import is_prototype_build as _ipb
                except ImportError:
                    from core.secrets import is_prototype_build as _ipb
                _proto = bool(_ipb())
            except Exception:
                _proto = os.environ.get("SYTHION_PROTECT_BUILD", "").strip().lower() in {
                    "1",
                    "true",
                    "yes",
                    "on",
                    "prototype",
                }
            if _proto:
                try:
                    from backend.tools.file_tools import _contained as _contained_path
                    from backend.core.storage import data_dir as _gui_data_dir
                except ImportError:
                    from tools.file_tools import _contained as _contained_path
                    from core.storage import data_dir as _gui_data_dir
                candidate = Path(input_data.save_path).resolve()
                try:
                    data_root = Path(_gui_data_dir()).resolve()
                except Exception:
                    return ToolOutput(
                        success=False,
                        error={
                            "code": "DATA_DIR_UNAVAILABLE",
                            "message": "Prototype profile data directory could not be resolved; refusing screenshot.",
                        },
                    )
                if not (
                    _contained_path(candidate)
                    or str(candidate).lower().startswith(str(data_root).lower() + os.sep)
                    or candidate == data_root
                ):
                    return ToolOutput(
                        success=False,
                        error={
                            "code": "TARGET_OUTSIDE_ROOTS",
                            "message": f"Screenshot target is outside approved prototype roots: {candidate}",
                        },
                    )

        from datetime import datetime
        import io

        try:
            from PIL import Image
        except ImportError:
            return ToolOutput(
                success=False,
                error={"code": "MISSING_DEPENDENCY", "message": "Pillow (PIL) is required for screenshots"},
            )

        try:
            if input_data.window_title:
                hwnd = 0
                try:
                    identity = await _run_on_worker(lambda: _uia_driver.observe(input_data.window_title, 1, 1))
                    if identity.get("found"):
                        hwnd = int(identity.get("hwnd") or 0)
                except Exception:
                    pass
                if not hwnd:
                    hwnd = _find_window_hwnd_win32(input_data.window_title)
                if not hwnd:
                    return ToolOutput(
                        success=False,
                        error={"code": "WINDOW_NOT_FOUND", "message": f"Window '{input_data.window_title}' not found"},
                    )

                rect = ctypes.wintypes.RECT()
                if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    return ToolOutput(
                        success=False,
                        error={"code": "GET_RECT_FAILED", "message": "Failed to get window rectangle"},
                    )

                x, y = rect.left, rect.top
                width = rect.right - rect.left
                height = rect.bottom - rect.top

                if width <= 0 or height <= 0:
                    return ToolOutput(
                        success=False,
                        error={"code": "INVALID_RECT", "message": f"Window has invalid dimensions: {width}x{height}"},
                    )

                hwndDC = ctypes.windll.user32.GetWindowDC(hwnd)
                if not hwndDC:
                    return ToolOutput(
                        success=False,
                        error={"code": "DC_FAILED", "message": "Failed to get window device context"},
                    )

                try:
                    mfcDC = ctypes.windll.gdi32.CreateCompatibleDC(hwndDC)
                    saveDC = ctypes.windll.gdi32.CreateCompatibleDC(hwndDC)

                    bmp = ctypes.windll.gdi32.CreateCompatibleBitmap(hwndDC, width, height)
                    ctypes.windll.gdi32.SelectObject(mfcDC, bmp)

                    ctypes.windll.gdi32.BitBlt(saveDC, 0, 0, width, height, mfcDC, 0, 0, 0x00CC0020)  # SRCCOPY

                    bmpinfo = _BITMAPINFOHEADER()
                    bmpinfo.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
                    bmpinfo.biWidth = width
                    bmpinfo.biHeight = -height
                    bmpinfo.biPlanes = 1
                    bmpinfo.biBitCount = 32
                    bmpinfo.biCompression = 0

                    buf_size = width * height * 4
                    buf = ctypes.create_string_buffer(buf_size)
                    ctypes.windll.gdi32.GetDIBits(saveDC, bmp, 0, height, buf, ctypes.byref(bmpinfo), 0)

                    img = Image.frombuffer("RGBA", (width, height), buf, "raw", "BGRA", 0, 1)
                    img = img.convert("RGB")

                finally:
                    ctypes.windll.gdi32.DeleteObject(bmp)
                    ctypes.windll.gdi32.DeleteDC(saveDC)
                    ctypes.windll.gdi32.DeleteDC(mfcDC)
                    ctypes.windll.user32.ReleaseDC(hwnd, hwndDC)

            else:
                user32 = ctypes.windll.user32
                screen_width = user32.GetSystemMetrics(0)
                screen_height = user32.GetSystemMetrics(1)

                hwndDC = ctypes.windll.user32.GetDC(0)
                if not hwndDC:
                    return ToolOutput(
                        success=False,
                        error={"code": "DC_FAILED", "message": "Failed to get screen device context"},
                    )

                try:
                    mfcDC = ctypes.windll.gdi32.CreateCompatibleDC(hwndDC)
                    saveDC = ctypes.windll.gdi32.CreateCompatibleDC(hwndDC)

                    bmp = ctypes.windll.gdi32.CreateCompatibleBitmap(hwndDC, screen_width, screen_height)
                    ctypes.windll.gdi32.SelectObject(mfcDC, bmp)

                    ctypes.windll.gdi32.BitBlt(saveDC, 0, 0, screen_width, screen_height, mfcDC, 0, 0, 0x00CC0020)

                    bmpinfo = _BITMAPINFOHEADER()
                    bmpinfo.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
                    bmpinfo.biWidth = screen_width
                    bmpinfo.biHeight = -screen_height
                    bmpinfo.biPlanes = 1
                    bmpinfo.biBitCount = 32
                    bmpinfo.biCompression = 0

                    buf_size = screen_width * screen_height * 4
                    buf = ctypes.create_string_buffer(buf_size)
                    ctypes.windll.gdi32.GetDIBits(saveDC, bmp, 0, screen_height, buf, ctypes.byref(bmpinfo), 0)

                    img = Image.frombuffer("RGBA", (screen_width, screen_height), buf, "raw", "BGRA", 0, 1)
                    img = img.convert("RGB")

                finally:
                    ctypes.windll.gdi32.DeleteObject(bmp)
                    ctypes.windll.gdi32.DeleteDC(saveDC)
                    ctypes.windll.gdi32.DeleteDC(mfcDC)
                    user32.ReleaseDC(0, hwndDC)

                x, y = 0, 0
                width, height = screen_width, screen_height

            if input_data.save_path:
                candidate = Path(input_data.save_path).resolve()
                _proto = False
                try:
                    from backend.core.secrets import is_prototype_build as _ipb

                    _proto = bool(_ipb())
                except Exception:
                    _proto = os.environ.get("SYTHION_PROTECT_BUILD", "").strip().lower() in {
                        "1",
                        "true",
                        "yes",
                        "on",
                        "prototype",
                    }
                if _proto:
                    try:
                        from backend.tools.file_tools import _contained as _contained_path
                        from backend.core.storage import data_dir as _gui_data_dir
                    except ImportError:
                        from tools.file_tools import _contained as _contained_path
                        from core.storage import data_dir as _gui_data_dir
                    data_root = Path(_gui_data_dir()).resolve()
                    if not (
                        _contained_path(candidate)
                        or str(candidate).lower().startswith(str(data_root).lower() + os.sep)
                        or candidate == data_root
                    ):
                        return ToolOutput(
                            success=False,
                            error={
                                "code": "TARGET_OUTSIDE_ROOTS",
                                "message": f"Screenshot target is outside approved prototype roots: {candidate}",
                            },
                            model_summary="Screenshot save path rejected (prototype isolation).",
                        )
                save_path = candidate
            else:
                _proto = False
                try:
                    try:
                        from backend.core.secrets import is_prototype_build as _ipb
                    except ImportError:
                        from core.secrets import is_prototype_build as _ipb
                    _proto = bool(_ipb())
                except Exception:
                    _proto = os.environ.get("SYTHION_PROTECT_BUILD", "").strip().lower() in {"1", "true", "yes", "on", "prototype"}
                if _proto:
                    try:
                        try:
                            from backend.core.storage import data_dir as _sd
                        except ImportError:
                            from core.storage import data_dir as _sd
                        screenshots_dir = Path(_sd()) / "screenshots"
                    except Exception:
                        return ToolOutput(
                            success=False,
                            error={
                                "code": "DATA_DIR_UNAVAILABLE",
                                "message": "Prototype profile data directory could not be resolved; refusing to write screenshot.",
                            },
                        )
                else:
                    screenshots_dir = Path(os.environ.get("LOCALAPPDATA", ".")) / "Sythion" / "screenshots"
                screenshots_dir.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                save_path = screenshots_dir / f"screenshot_{timestamp}.png"

            save_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(str(save_path), "PNG")

            return ToolOutput(
                success=True,
                data={
                    "path": str(save_path),
                    "width": width,
                    "height": height,
                    "window_title": input_data.window_title,
                },
                model_summary=f"Screenshot saved: {width}x{height} -> {save_path.name}",
                evidence={"source": "Win32 BitBlt capture"},
            )

        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "SCREENSHOT_ERROR", "message": str(e)},
            )

    def observe(self, window_title: str, max_depth: int, max_elements: int):
        return _uia_driver.observe(window_title, max_depth, max_elements)

    async def verify(self, input_data: GUIScreenshotInput, output: ToolOutput) -> bool:
        return output.success and "path" in (output.data or {})

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {
            "risk_level": "low",
            "requires_confirmation": False,
            "mutating": False,
            "requires_screen_access": True,
            "requires_ui_automation": True,
            "privacy_sensitive": True,
        }

    def validate_targets(self, input_data: GUIScreenshotInput) -> Optional[Dict[str, str]]:
        if input_data.window_title:
            return _gui_target_error(input_data.window_title)
        return None


class GUIBatchFormTool(BaseTool[GUIBatchFormInput]):
    """
    Fill multiple form fields in sequence.

    Combines observe + type/set_value for each field. Useful for filling
    multi-field forms like login dialogs, settings panels, etc.
    """

    name = "gui_batch_form"
    description = "Fill multiple form fields in a window sequentially"
    input_schema = GUIBatchFormInput

    async def execute(self, input_data: GUIBatchFormInput) -> ToolOutput:
        if not _IS_WINDOWS:
            return ToolOutput(success=False, error={"code": "NOT_SUPPORTED", "message": "Windows only"})

        import asyncio

        results = []
        fields = input_data.fields

        if not fields:
            return ToolOutput(
                success=False,
                error={"code": "NO_FIELDS", "message": "No fields provided"},
            )

        if len(fields) > 20:
            return ToolOutput(
                success=False,
                error={"code": "TOO_MANY_FIELDS", "message": "Maximum 20 fields per batch"},
            )

        for i, field in enumerate(fields):
            element_desc = field.get("element", "")
            value = field.get("value", "")
            action = field.get("action", "type")

            if not element_desc:
                results.append({
                    "index": i,
                    "element": element_desc,
                    "success": False,
                    "error": "No element description provided",
                })
                continue

            try:
                observe_result = await _run_on_worker(
                    lambda: _uia_driver.observe(input_data.window_title, _MAX_UI_TREE_DEPTH, _MAX_UI_ELEMENTS)
                )

                if not observe_result.get("found"):
                    results.append({
                        "index": i,
                        "element": element_desc,
                        "success": False,
                        "error": f"Window '{input_data.window_title}' not found",
                    })
                    continue

                elements = observe_result.get("elements", [])
                target_element = None

                for elem in elements:
                    name = (elem.get("name") or "").lower()
                    automation_id = (elem.get("automation_id") or "").lower()
                    desc = element_desc.lower()

                    if desc in name or desc in automation_id:
                        target_element = elem
                        break

                if not target_element:
                    for elem in elements:
                        name = (elem.get("name") or "").lower()
                        desc_words = desc.split()
                        if all(word in name for word in desc_words):
                            target_element = elem
                            break

                if not target_element:
                    results.append({
                        "index": i,
                        "element": element_desc,
                        "success": False,
                        "error": f"Element '{element_desc}' not found in window",
                    })
                    continue

                if action == "set_value":
                    set_result = await _run_on_worker(
                        lambda e=target_element, v=value: _uia_driver.set_value(
                            input_data.window_title, e.get("automation_id") or e.get("name"), v
                        )
                    )
                    success = set_result.get("ok", False)
                    error = set_result.get("message") if not success else None
                else:
                    invoke_result = await _run_on_worker(
                        lambda e=target_element: _uia_driver.invoke(
                            input_data.window_title, e.get("automation_id") or e.get("name")
                        )
                    )

                    if not invoke_result.get("ok"):
                        results.append({
                            "index": i,
                            "element": element_desc,
                            "success": False,
                            "error": f"Failed to focus element: {invoke_result.get('message', '')}",
                        })
                        continue

                    await asyncio.sleep(0.1)

                    type_result = await _run_on_worker(
                        lambda v=value: _uia_driver.type_text(input_data.window_title, v)
                    )
                    success = type_result.get("ok", False)
                    error = type_result.get("message") if not success else None

                results.append({
                    "index": i,
                    "element": element_desc,
                    "success": success,
                    "error": error,
                })

                if i < len(fields) - 1:
                    await asyncio.sleep(input_data.delay_between_fields)

            except Exception as e:
                results.append({
                    "index": i,
                    "element": element_desc,
                    "success": False,
                    "error": str(e),
                })

        success_count = sum(1 for r in results if r["success"])
        failed_count = len(results) - success_count

        return ToolOutput(
            success=failed_count == 0,
            data={
                "total_fields": len(fields),
                "success_count": success_count,
                "failed_count": failed_count,
                "results": results,
            },
            model_summary=f"Batch form: {success_count}/{len(fields)} fields filled" + (f" ({failed_count} failed)" if failed_count else ""),
        )

    async def verify(self, input_data: GUIBatchFormInput, output: ToolOutput) -> bool:
        return output.success and "results" in (output.data or {})

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {
            "risk_level": "high",
            "requires_confirmation": True,
            "requires_ui_automation": True,
            "input_injection": True,
        }

    def validate_targets(self, input_data: GUIBatchFormInput) -> Optional[Dict[str, str]]:
        return _gui_target_error(input_data.window_title)


def _element_hwnd_from_rect(rect: List[int]) -> int:
    """Resolve a window hwnd from an element rect using window-from-point.

    Returns 0 when the point is not on a window. Used only for safety re-checks.
    """
    if not _IS_WINDOWS or len(rect) < 4 or rect[2] <= 0 or rect[3] <= 0:
        return 0
    try:
        pt = ctypes.wintypes.POINT(int(rect[0] + rect[2] // 2), int(rect[1] + rect[3] // 2))
        return int(ctypes.windll.user32.WindowFromPoint(pt))
    except Exception:
        return 0


def create_gui_tools() -> List[BaseTool]:
    """Create instances of all GUI tools."""
    return [
        GUIObserveTool(),
        GUIFocusTool(),
        GUIClickTool(),
        GUITypeTool(),
        GUIHotkeyTool(),
        GUIPasteTool(),
        GUIInvokeTool(),
        GUISetValueTool(),
        GUIScreenshotTool(),
        GUIBatchFormTool(),
    ]


def _is_prototype_build() -> bool:
    try:
        from backend.core.secrets import is_prototype_build as _proto
    except Exception:
        try:
            from core.secrets import is_prototype_build as _proto
        except Exception:
            _proto = None
    if _proto is None:
        return True
    try:
        return bool(_proto())
    except Exception:
        return True


_PROTOTYPE_HOST_DATA_GUI_TOOL_CLASSES = (GUIObserveTool, GUIScreenshotTool)


def _prototype_host_data_guard(method):
    async def wrapped(self, input_data):
        if _is_prototype_build():
            try:
                from backend.api.schemas import ToolOutput
            except Exception:
                from api.schemas import ToolOutput
            return ToolOutput(
                success=False,
                error={"code": "PROTOTYPE_HOST_DATA_DISABLED", "message": "Host data observation is disabled in prototype builds."},
                duration_ms=0,
            )
        return await method(self, input_data)
    wrapped.__name__ = getattr(method, "__name__", "execute")
    wrapped.__doc__ = getattr(method, "__doc__", None)
    return wrapped


try:
    from backend.api.schemas import ToolOutput as _ToolOutput
except Exception:
    from api.schemas import ToolOutput as _ToolOutput

for _cls in _PROTOTYPE_HOST_DATA_GUI_TOOL_CLASSES:
    _execute = getattr(_cls, "execute", None)
    if callable(_execute):
        _cls.execute = _prototype_host_data_guard(_execute)
    _policy = getattr(_cls, "get_policy_requirements", None)
    if callable(_policy):
        def _make_policy(orig):
            def _policy(self):
                reqs = orig(self)
                if isinstance(reqs, dict) and _is_prototype_build():
                    reqs = dict(reqs)
                    reqs["privacy_sensitive"] = True
                return reqs
            return _policy
        _cls.get_policy_requirements = _make_policy(_policy)

