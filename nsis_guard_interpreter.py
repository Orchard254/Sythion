"""Small in-process interpreter for the NSIS guard functions in sythion-installer-template.nsi."""

from __future__ import annotations

import ctypes
import os
import re
from typing import Dict, List


TEMPLATE = os.path.join(os.path.dirname(__file__), "src-tauri", "sythion-installer-template.nsi")

# NSIS string capacity (NSIS_MAX_STRLEN). Win32 path APIs must not be asked to
# write more than this many characters into an NSIS output variable.
NSIS_MAX_STRLEN = 1024

GUARD_FUNCTIONS = [
    "NormalizeSystemPath",
    "FinalizeResolvedSystemPath",
    "AddProfileRootForGuard",
    "PopulateProfileRootsForGuard",
    "IsInstallPathUnderOrContainingProfileRoot",
    "VerifyInstallDirNotProfileDataDir",
    "un.NormalizeSystemPath",
    "un.IsInstallPathUnderOrContainingProfileRoot",
    "un.VerifyInstallDirNotProfileDataDir",
    "RestorePreviousInstallLocation",
    "RegisteredInstallPathIsProfileRootUnsafe",
]


def read_template() -> str:
    with open(TEMPLATE, "r", encoding="utf-8") as f:
        return f.read()


def extract_function(source: str, name: str) -> List[str]:
    lines = source.splitlines()
    out: List[str] = []
    collecting = False
    header = re.compile(r"^\s*Function\s+" + re.escape(name) + r"\s*$")
    for line in lines:
        stripped = line.strip()
        if header.match(stripped):
            collecting = True
            continue
        if collecting and stripped == "FunctionEnd":
            break
        if collecting:
            if stripped == "" or stripped.startswith(";"):
                continue
            out.append(line.rstrip())
    if not out:
        raise KeyError("function not found: " + name)
    return out


def tokenize(line: str) -> List[str]:
    tokens: List[str] = []
    i = 0
    n = len(line)
    cur = ""
    in_d = False
    in_s = False
    while i < n:
        c = line[i]
        if c == '"' and not in_s:
            cur += c
            in_d = not in_d
            i += 1
        elif c == "'" and not in_d:
            cur += c
            in_s = not in_s
            i += 1
        elif c.isspace() and not in_d and not in_s:
            if cur:
                tokens.append(cur)
                cur = ""
            i += 1
        else:
            cur += c
            i += 1
    if cur:
        tokens.append(cur)
    return tokens


def unquote(tok: str) -> str:
    tok = tok.strip()
    if len(tok) >= 2 and ((tok[0] == '"' and tok[-1] == '"') or (tok[0] == "'" and tok[-1] == "'")):
        return tok[1:-1]
    return tok


class _Frame:
    pass


class NSISVM:
    def __init__(self, source: str | None = None) -> None:
        self.source = source or read_template()
        self.functions: Dict[str, List[str]] = {}
        for name in GUARD_FUNCTIONS:
            try:
                self.functions[name] = extract_function(self.source, name)
            except KeyError:
                pass
        self.nsis_max_strlen: int = NSIS_MAX_STRLEN
        self.silent = False
        self.silent_mode = False
        self.is_silent = False
        self.vars: Dict[str, str] = {"${NSIS_MAX_STRLEN}": str(NSIS_MAX_STRLEN)}
        self.messages: List[str] = []
        self.last_aborted = False

    def set_vars(self, **kw: str) -> None:
        for k, v in kw.items():
            self.vars[k] = v

    def resolve_value(self, token: str) -> str:
        token = unquote(token)
        return self._interpolate_ns_vars(token)

    def _interpolate_ns_vars(self, s: str) -> str:
        def _brace(m):
            key = "${" + m.group(1) + "}"
            if key in self.vars:
                return self.vars[key]
            if m.group(1) == "NSIS_MAX_STRLEN":
                return str(self.nsis_max_strlen)
            return ""
        s = re.sub(r"\$\{([A-Za-z0-9_]+)\}", _brace, s)
        return re.sub(r"\$[A-Za-z0-9_]+", lambda m: self.vars.get(m.group(0), ""), s)


    def resolve_int(self, token: str) -> int:
        v = self.resolve_value(token)
        if v == "" and "NSIS_MAX_STRLEN" in str(token):
            v = str(self.nsis_max_strlen)
        try:
            return int(v)
        except (ValueError, TypeError):
            try:
                return int(float(v))
            except (ValueError, TypeError):
                return 0

    def _resolve_jump(self, target: str, frame) -> int | None:
        target = unquote(target)
        if target == "" or target == "0":
            return None
        if target.lstrip("+-").isdigit():
            try:
                off = int(target)
            except ValueError:
                return None
            return max(0, min(len(frame.code), frame.ip + off))
        if target in frame.labels:
            return frame.labels[target]
        return None

    def execute_function(self, name: str) -> "NSISVM":
        code = self.functions[name]
        frame = _Frame()
        frame.vm = self
        frame.raw = code
        frame.code = [tokenize(x) for x in code]
        frame.labels = {}
        for idx, toks in enumerate(frame.code):
            if toks and toks[0].endswith(":"):
                frame.labels[toks[0][:-1]] = idx
        frame.ip = 0
        frame.returned = False
        frame.aborted = False
        self.last_aborted = False
        self._run_frame(frame)
        self.last_aborted = frame.aborted
        return self

    def _run_frame(self, frame) -> None:
        while not frame.returned and not frame.aborted and frame.ip < len(frame.code):
            toks = frame.code[frame.ip]
            if not toks:
                frame.ip += 1
                continue
            if toks[0].endswith(":"):
                frame.ip += 1
                continue
            ol = toks[0].lower()

            if ol == "return":
                frame.returned = True
                break
            if ol == "abort":
                frame.aborted = True
                break
            if ol == "goto":
                jump = self._resolve_jump(toks[1], frame)
                if jump is None:
                    frame.ip += 1
                else:
                    frame.ip = jump
                continue
            if ol == "call":
                fname = toks[1]
                candidate = fname
                if candidate not in self.functions:
                    for k in self.functions:
                        if k.lower() == fname.lower():
                            candidate = k
                            break
                if candidate in self.functions:
                    sub = _Frame()
                    sub.vm = self
                    sub.raw = self.functions[candidate]
                    sub.code = [tokenize(x) for x in sub.raw]
                    sub.labels = {}
                    for idx, t in enumerate(sub.code):
                        if t and t[0].endswith(":"):
                            sub.labels[t[0][:-1]] = idx
                    sub.ip = 0
                    sub.returned = False
                    sub.aborted = False
                    self._run_frame(sub)
                    if sub.aborted:
                        frame.aborted = True
                        break
                frame.ip += 1
                continue

            if ol.startswith("expandenvstrings"):
                if len(toks) >= 3:
                    dest = toks[1]
                    # NSIS ExpandEnvStrings honours %ENVVAR% using the installer
                    # process environment, then NSIS $variables are interpolated.
                    import os as _os

                    expanded = _os.path.expandvars(unquote(toks[2]))
                    self.vars[dest] = self._interpolate_ns_vars(expanded)
                frame.ip += 1
                continue

            if ol.startswith("strcp"):
                self._do_strcpy(frame, toks)
                frame.ip += 1
                continue
            if ol.startswith("strcm"):
                self._do_strcmp(frame, toks)
                continue
            if ol.startswith("strle"):
                self._do_strlen(frame, toks)
                frame.ip += 1
                continue
            if ol.startswith("intcm"):
                self._do_intcmp(frame, toks)
                continue
            if ol.startswith("msgbo"):
                self.messages.append("MessageBox: " + " ".join(toks))
                frame.ip += 1
                continue
            if ol == "ifsilent":
                is_silent = (
                    bool(self.silent)
                    or bool(self.silent_mode)
                    or bool(self.is_silent)
                    or self.vars.get("$Silent", "") == "1"
                    or self.vars.get("$SilentMode", "") == "1"
                )
                if is_silent:
                    jump = self._resolve_jump(toks[1], frame)
                    if jump is not None:
                        frame.ip = jump
                        continue
                frame.ip += 1
                continue
            if ol.startswith("system::"):
                self._handle_system_call(frame, toks)
                frame.ip += 1
                continue

            self.messages.append("UNKNOWN: " + " ".join(toks))
            frame.ip += 1
            continue

    def _do_strcpy(self, frame, toks) -> None:
        dest = toks[1]
        src = self.resolve_value(toks[2])
        if len(toks) < 4:
            self.vars[dest] = src
            return
        maxlen = self.resolve_int(toks[3])
        offset = 0
        if len(toks) >= 5:
            offset = self.resolve_int(toks[4])
        if offset < 0:
            offset = 0
        if maxlen == 0:
            self.vars[dest] = src[offset:]
        elif maxlen < 0:
            if offset == 0:
                self.vars[dest] = src[:maxlen]
            else:
                self.vars[dest] = src[offset:offset + abs(maxlen)]
        else:
            self.vars[dest] = src[offset:offset + maxlen]

    def _do_strcmp(self, frame, toks) -> None:
        s1 = self.resolve_value(toks[1])
        s2 = self.resolve_value(toks[2])
        if s1 == s2:
            if len(toks) > 3:
                jump = self._resolve_jump(toks[3], frame)
                if jump is not None:
                    frame.ip = jump
                    return
        else:
            if len(toks) > 4:
                jump = self._resolve_jump(toks[4], frame)
                if jump is not None:
                    frame.ip = jump
                    return
        frame.ip += 1

    def _do_strlen(self, frame, toks) -> None:
        if len(toks) < 3:
            return
        src = self.resolve_value(toks[2])
        self.vars[toks[1]] = str(len(src))

    def _do_intcmp(self, frame, toks) -> None:
        v1 = self.resolve_int(toks[1])
        v2 = self.resolve_int(toks[2])
        if v1 == v2:
            target = toks[3] if len(toks) > 3 else ""
        elif v1 < v2:
            target = toks[4] if len(toks) > 4 else ""
        else:
            target = toks[5] if len(toks) > 5 else ""
        jump = self._resolve_jump(target, frame)
        if jump is not None:
            frame.ip = jump
        else:
            frame.ip += 1

    def _buffer_size_from_call(self, joined: str, func_name: str) -> int:
        func_match = re.search(func_name + r"\\((.*)\\)", joined)
        inside = func_match.group(1) if func_match else joined
        matches = re.findall(r"\\bi\\s+([^\\s,)]+)", inside)
        if matches:
            size = self.resolve_int(matches[0])
            if size > 0:
                return size
        return self.nsis_max_strlen

    def _handle_system_call(self, frame, toks) -> None:
        joined = " ".join(toks)
        if "GetFullPathNameW" in joined:
            m = re.search(r'w\s+"([^"]*)"', joined)
            if m:
                src = self._interpolate_ns_vars(m.group(1))
            else:
                src = self.vars.get("$R8", "")
            size = self._buffer_size_from_call(joined, "GetFullPathNameW")
            if size <= 0 or size > self.nsis_max_strlen:
                # Never request more than the NSIS output capacity.
                size = self.nsis_max_strlen
            buf = ctypes.create_unicode_buffer(size)
            try:
                ret = ctypes.windll.kernel32.GetFullPathNameW(src, size, buf, None)
            except Exception:
                ret = 0
            self.vars["$0"] = buf.value
            self.vars["$1"] = str(ret)
            return
        if "CreateFileW" in joined:
            m = re.search(r'w\s+"([^"]*)"', joined)
            path = self._interpolate_ns_vars(m.group(1)) if m else ""
            try:
                h = ctypes.windll.kernel32.CreateFileW(path, 0, 3, None, 3, 0x02000000, None)
            except Exception:
                h = -1
            try:
                hi = int(h)
            except (ValueError, TypeError):
                hi = -1
            if hi == ctypes.c_void_p(-1).value:
                hi = -1
            self.vars["$0"] = str(hi)
            return
        if "GetFinalPathNameByHandleW" in joined:
            handle_str = self.vars.get("$0", "0")
            try:
                handle = int(handle_str)
            except (ValueError, TypeError):
                handle = 0
            size = self._buffer_size_from_call(joined, "GetFinalPathNameByHandleW")
            if size <= 0 or size > self.nsis_max_strlen:
                size = self.nsis_max_strlen
            buf = ctypes.create_unicode_buffer(size)
            try:
                ret = ctypes.windll.kernel32.GetFinalPathNameByHandleW(handle, buf, size, 0)
            except Exception:
                ret = 0
            if ret != 0:
                self.vars["$1"] = buf.value
            elif "$1" not in self.vars:
                self.vars["$1"] = ""
            self.vars["$2"] = str(ret)
            return

    def _interpolate_ns_vars(self, s: str) -> str:
        def _brace(m):
            key = "${" + m.group(1) + "}"
            if key in self.vars:
                return self.vars[key]
            if m.group(1) == "NSIS_MAX_STRLEN":
                return str(self.nsis_max_strlen)
            return ""
        s = re.sub(r"\$\{([A-Za-z0-9_]+)\}", _brace, s)
        return re.sub(r"\$[A-Za-z0-9_]+", lambda m: self.vars.get(m.group(0), ""), s)
