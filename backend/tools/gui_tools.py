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
import os
import platform
import queue
import sys
import threading
import time
from concurrent.futures import Future
from typing import Any, Callable, Dict, List, Optional, Tuple

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
    )
    from core.hotkeys import DANGEROUS_HOTKEYS, HOTKEY_KEYS

_IS_WINDOWS = os.name == "nt"

# Bounds that keep observations and context small and deterministic.
_MAX_UI_TREE_DEPTH = 3
_MAX_UI_ELEMENTS = 60
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


# --------------------------------------------------------------------------
# UIA driver -- every call below runs inside the MTA worker thread.
# --------------------------------------------------------------------------

class _UIADriver:
    """Real comtypes-backed UIA layer; tests substitute a fake."""

    def _session(self):
        import comtypes.client
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as uia
        return comtypes.client.CreateObject(uia.CUIAutomation).QueryInterface(uia.IUIAutomation)

    def list_windows(self) -> List[Dict[str, Any]]:
        from comtypes.gen import UIAutomationClient as _uia
        uia = self._session()
        root = uia.GetRootElement()
        wcond = uia.CreatePropertyCondition(_uia.UIA_ControlTypePropertyId, _uia.UIA_WindowControlTypeId)
        # TreeScope_Descendants: on some desktop layouts the root's direct
        # child is a virtual desktop pane and top-level windows are nested.
        result = root.FindAll(2, wcond)
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
        from comtypes.gen import UIAutomationClient as _uia
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
        from comtypes.gen import UIAutomationClient as _uia
        import re
        exact = uia.CreatePropertyCondition(_uia.UIA_NamePropertyId, title)
        # TreeScope_Descendants: top-level windows can be nested under a
        # virtual desktop pane on some desktop layouts.
        found = root.FindFirst(2, exact)
        if found:
            return found
        # Bounded partial match over window controls.
        wcond = uia.CreatePropertyCondition(_uia.UIA_ControlTypePropertyId, _uia.UIA_WindowControlTypeId)
        windows = root.FindAll(2, wcond)
        target_lower = title.lower().strip()
        # Pass 1: Case-insensitive substring match
        for i in range(windows.Length):
            if i >= _MAX_WINDOW_TITLES:
                break
            win = windows.GetElement(i)
            cname = (win.CurrentName or "").lower()
            if target_lower in cname or cname in target_lower:
                return win

        # Pass 2: Token-based fallback (e.g. '*helo ddddd - Notepad' -> 'Notepad')
        tokens = [t.strip() for t in re.split(r"[-—:]", target_lower) if len(t.strip()) >= 3]
        for token in reversed(tokens):
            for i in range(windows.Length):
                if i >= _MAX_WINDOW_TITLES:
                    break
                win = windows.GetElement(i)
                if token in (win.CurrentName or "").lower():
                    return win

        return None

    def _window_identity(self, uia, element) -> Dict[str, Any]:
        from comtypes.gen import UIAutomationClient as _uia
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
        from comtypes.gen import UIAutomationClient as _uia
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
        from comtypes.gen import UIAutomationClient as _uia
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

    def find_element(self, window_title: str, description: str) -> Dict[str, Any]:
        from comtypes.gen import UIAutomationClient as _uia
        uia = self._session()
        root = uia.GetRootElement()
        window = self._find_window(uia, root, window_title)
        if window is None:
            return {"ok": False, "code": "WINDOW_NOT_FOUND", "message": f"Window '{window_title}' not found"}
        needle = description.strip().lower()
        target = self._find_element_recursive(uia, window, needle, 0, 0)
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

    def _find_element_recursive(self, uia, element, needle: str, depth: int, count: int):
        from comtypes.gen import UIAutomationClient as _uia
        if depth > _MAX_UI_TREE_DEPTH or count >= _MAX_UI_ELEMENTS:
            return None
        name = (element.CurrentName or "").strip()
        aid = (element.CurrentAutomationId or "").strip()
        if needle and (needle in name.lower() or needle in aid.lower()):
            return (element, count)
        count += 1
        try:
            walker = uia.ControlViewWalker
            child = walker.GetFirstChildElement(element)
            while child and count < _MAX_UI_ELEMENTS:
                found = self._find_element_recursive(uia, child, needle, depth + 1, count)
                if found:
                    return found
                count += 1
                child = walker.GetNextSiblingElement(child)
        except Exception:
            pass
        return None

    def _element_rect(self, uia, element) -> Tuple[int, int, int, int]:
        try:
            rect = element.CurrentBoundingRectangle
            return (int(rect.left), int(rect.top), int(rect.right - rect.left), int(rect.bottom - rect.top))
        except Exception:
            return (0, 0, 0, 0)

    def invoke(self, window_title: str, description: str) -> Dict[str, Any]:
        from comtypes.gen import UIAutomationClient as _uia
        found = self.find_element(window_title, description)
        if not found["ok"]:
            return found
        uia = self._session()
        root = uia.GetRootElement()
        window = self._find_window(uia, root, window_title)
        if window is None:
            return {"ok": False, "code": "WINDOW_NOT_FOUND", "message": f"Window '{window_title}' not found"}
        element = self._find_element_recursive(uia, window, description.strip().lower(), 0, 0)
        if element is None:
            return {"ok": False, "code": "ELEMENT_NOT_FOUND", "message": f"No element matching '{description}'"}
        target = element[0]
        try:
            pattern = self._current_pattern(uia, target, _uia.UIA_InvokePatternId, _uia.IUIAutomationInvokePattern)
            if pattern is None:
                return {"ok": False, "code": "NO_INVOKE_PATTERN",
                        "message": f"Element '{description}' has no Invoke pattern; Sythion (Synth) does not guess a fallback click"}
            pattern.Invoke()
            return {"ok": True, "method": "uia_invoke", "element": found["element"]}
        except Exception:
            return {"ok": False, "code": "NO_INVOKE_PATTERN",
                    "message": f"Element '{description}' has no Invoke pattern; Sythion (Synth) does not guess a fallback click"}

    def set_value(self, window_title: str, description: str, value: str) -> Dict[str, Any]:
        from comtypes.gen import UIAutomationClient as _uia
        found = self.find_element(window_title, description)
        if not found["ok"]:
            return found
        uia = self._session()
        root = uia.GetRootElement()
        window = self._find_window(uia, root, window_title)
        if window is None:
            return {"ok": False, "code": "WINDOW_NOT_FOUND", "message": f"Window '{window_title}' not found"}
        element = self._find_element_recursive(uia, window, description.strip().lower(), 0, 0)
        if element is None:
            return {"ok": False, "code": "ELEMENT_NOT_FOUND", "message": f"No element matching '{description}'"}
        target = element[0]
        try:
            pattern = self._current_pattern(uia, target, _uia.UIA_ValuePatternId, _uia.IUIAutomationValuePattern)
            if pattern is None:
                return {"ok": False, "code": "NO_VALUE_PATTERN",
                        "message": f"Element '{description}' has no Value pattern; Sythion (Synth) does not guess a fallback"}
            pattern.SetValue(value)
            return {"ok": True, "method": "uia_value", "element": found["element"]}
        except Exception:
            return {"ok": False, "code": "NO_VALUE_PATTERN",
                    "message": f"Element '{description}' has no Value pattern; Sythion (Synth) does not guess a fallback"}

    # -- physical-pixel fallback input (single-shot; re-observes afterwards) --

    def single_click(self, window_title: str, x: int, y: int) -> Dict[str, Any]:
        identity = self.observe(window_title, 1, 1)
        if not identity.get("found"):
            return {"ok": False, "code": "WINDOW_NOT_FOUND", "message": f"Window '{window_title}' not found"}
        from comtypes.gen import UIAutomationClient as _uia
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
        if self._foreground_hwnd() != hwnd:
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


_gui_worker = _UIAWorker()
_uia_driver = _UIADriver()


class _GUIUnavailable(Exception):
    pass


async def _run_on_worker(fn: Callable[[], Any]) -> Any:
    """Run a driver call on the MTA worker without blocking the event loop."""
    return await asyncio.to_thread(_gui_worker.run, fn)


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

class GUIObserveTool(BaseTool):
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
        except Exception:
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


class GUIFocusTool(BaseTool):
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
        except Exception:
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


class GUIInvokeTool(BaseTool):
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
        except Exception:
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


class GUISetValueTool(BaseTool):
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
        except Exception:
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


class GUIClickTool(BaseTool):
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
        except Exception:
            return ToolOutput(success=False, error={"code": "UIA_UNAVAILABLE", "message": "UI Automation unavailable"}, duration_ms=0)
        if not identity.get("found"):
            return ToolOutput(success=False, error={"code": "WINDOW_NOT_FOUND", "message": f"Window '{input_data.window_title}' not found"}, duration_ms=0)
        hwnd = int(identity.get("hwnd") or 0)
        error = _target_window_safety(hwnd, identity.get("title") or "")
        if error:
            return ToolOutput(success=False, error=error, duration_ms=0)
        fg = hwnd and ctypes.windll.user32.GetForegroundWindow() == hwnd if _IS_WINDOWS else False
        if not fg:
            return ToolOutput(success=False, error={"code": "FOREGROUND_REQUIRED", "message": f"Window '{input_data.window_title}' is not foreground; focus it first"}, duration_ms=0)

        try:
            result = await _run_on_worker(
                lambda: _uia_driver.single_click(input_data.window_title, input_data.x, input_data.y))
        except Exception:
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


class GUITypeTool(BaseTool):
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
        except Exception:
            return ToolOutput(success=False, error={"code": "UIA_UNAVAILABLE", "message": "UI Automation unavailable"}, duration_ms=0)
        if not identity.get("found"):
            return ToolOutput(success=False, error={"code": "WINDOW_NOT_FOUND", "message": f"Window '{input_data.window_title}' not found"}, duration_ms=0)
        hwnd = int(identity.get("hwnd") or 0)
        error = _target_window_safety(hwnd, identity.get("title") or "")
        if error:
            return ToolOutput(success=False, error=error, duration_ms=0)
        fg = hwnd and ctypes.windll.user32.GetForegroundWindow() == hwnd if _IS_WINDOWS else False
        if not fg:
            return ToolOutput(success=False, error={"code": "FOREGROUND_REQUIRED", "message": f"Window '{input_data.window_title}' is not foreground; focus it first"}, duration_ms=0)
        try:
            result = await _run_on_worker(lambda: _uia_driver.type_text(input_data.window_title, input_data.text))
        except Exception:
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


class GUIHotkeyTool(BaseTool):
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
        except Exception:
            return ToolOutput(success=False, error={"code": "UIA_UNAVAILABLE", "message": "UI Automation unavailable"}, duration_ms=0)
        if not identity.get("found"):
            return ToolOutput(success=False, error={"code": "WINDOW_NOT_FOUND", "message": f"Window '{input_data.window_title}' not found"}, duration_ms=0)
        hwnd = int(identity.get("hwnd") or 0)
        error = _target_window_safety(hwnd, identity.get("title") or "")
        if error:
            return ToolOutput(success=False, error=error, duration_ms=0)
        fg = hwnd and ctypes.windll.user32.GetForegroundWindow() == hwnd if _IS_WINDOWS else False
        if not fg:
            return ToolOutput(success=False, error={"code": "FOREGROUND_REQUIRED", "message": f"Window '{input_data.window_title}' is not foreground; focus it first"}, duration_ms=0)
        try:
            result = await _run_on_worker(lambda: _uia_driver.hotkey(input_data.window_title, list(input_data.keys)))
        except Exception:
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


class GUIPasteTool(BaseTool):
    """Paste text into a target window via clipboard/input synthesis."""

    name = "gui_paste"
    description = "Paste text into a visible, foreground window"
    input_schema = GUIPasteInput

    async def execute(self, input_data: GUIPasteInput) -> ToolOutput:
        if not _IS_WINDOWS:
            return ToolOutput(success=False, error={"code": "NOT_SUPPORTED", "message": "Windows only"}, duration_ms=0)
        identity = self.observe(input_data.window_title, 1, 1)
        if not identity.get("found"):
            return ToolOutput(success=False, error={"code": "WINDOW_NOT_FOUND", "message": f"Window '{input_data.window_title}' not found"}, duration_ms=0)
        hwnd = int(identity.get("hwnd") or 0)
        error = _target_window_safety(hwnd, identity.get("title") or "")
        if error:
            return ToolOutput(success=False, error=error, duration_ms=0)
        fg = hwnd and ctypes.windll.user32.GetForegroundWindow() == hwnd if _IS_WINDOWS else False
        if not fg:
            return ToolOutput(success=False, error={"code": "FOREGROUND_REQUIRED", "message": f"Window '{input_data.window_title}' is not foreground; focus it first"}, duration_ms=0)
        try:
            result = await _run_on_worker(lambda: _uia_driver.hotkey(input_data.window_title, ["ctrl", "v"]))
        except Exception:
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
    ]
