#!/usr/bin/env python3
"""Verify (and prune) the staged Sythion (Synth) release payload.

The distributable installer ships two payloads: the pinned Python runtime and
the backend application. This module is the single place that defines what
may go into them.

Why this exists
---------------
``scripts/stage_python_runtime.py`` copies the ``backend/`` source tree into
``src-tauri/resources/backend/`` and Tauri bundles that directory verbatim. A
wholesale copy therefore also ships whatever happens to be sitting in the
developer's working tree: a local ``backend/.env`` holding a real API key, the
development SQLite databases, a pytest report and the test suite.

The policy below keeps three classes of content out of a distributable:

* **Developer secrets** - local ``.env`` files and any recognised secret
  assignment with a value that is not a placeholder.
* **User / test state** - SQLite databases (the live profile database and the
  test fixtures) and test-run reports.
* **Build and dev-only directories** - ``__pycache__``, ``.pytest_cache`` and
  the duplicated test suite.

Secret values are never printed, logged or returned. A finding carries the
file path, the line number, the *name* of the offending key and a reason - the
value itself is discarded as soon as it has been classified.

Usage
-----
    python scripts/verify_release_payload.py                  # check the staged payload
    python scripts/verify_release_payload.py --prune          # remove prohibited files
    python scripts/verify_release_payload.py --json <root>    # machine readable

Exit code is 0 when the payload is clean and 1 when it is not, so the check can
gate a release build.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

#: Environment files that must never reach a distributable. These hold the
#: developer's own configuration, including credentials.
PROHIBITED_ENV_FILES = (".env",)

#: The only environment-shaped file that may ship, and only when every value
#: in it is a placeholder.
ALLOWED_ENV_FILES = (".env.example",)

#: Database files. The live profile database and the test fixtures are user or
#: test state, never payload.
PROHIBITED_DB_SUFFIXES = (".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".db3")

#: Test-run reports and coverage output.
PROHIBITED_STATE_FILES = ("pytest-report.xml", "coverage.xml", ".coverage")

#: Compiled Python and log output.
PROHIBITED_BUILD_SUFFIXES = (".pyc", ".pyo", ".log")

#: Private key material. A distributable never carries one.
PROHIBITED_KEY_SUFFIXES = (".pem", ".key", ".pfx", ".p12", ".jks", ".keystore")

#: Directories that only ever hold build or tool state.
PROHIBITED_STATE_DIRS = (
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
)

#: Directory names whose whole subtree is development-only. The backend ships
#: its own copy of the test suite under ``backend/tests``; the installed
#: application never imports it.
DEV_ONLY_DIR_NAMES = ("tests",)

#: Payload subtrees that hold the application's own code. The full policy
#: applies to them.
APP_PAYLOAD_DIRS = ("backend",)

#: Payload subtrees that hold vendored third-party code, pinned by
#: ``requirements.lock.txt`` and verified against the runtime's package
#: inventory. A package that ships a local environment file is a leak, but a
#: package that ships a ``tests`` directory or compiled modules is normal, so
#: only the environment-file rule applies here.
THIRD_PARTY_PAYLOAD_DIRS = ("python-runtime",)

#: Directory names whose subtree is third-party code. The pinned runtime is
#: hash-locked by ``requirements.lock.txt`` and is not the application's own
#: payload, so the secret-content scan skips it.
THIRD_PARTY_DIR_NAMES = ("site-packages",)

#: Recognised secret-bearing configuration keys.
_SECRET_KEY_PATTERN = re.compile(
    r"^(?:[A-Z0-9]+_)*(?:API[_-]?KEYS?|ACCESS[_-]?KEYS?|TOKEN|SECRET(S)?|PASSWORD|PASSWD|"
    r"CREDENTIALS?|PRIVATE[_-]?KEY|SESSION[_-]?KEY)$",
    re.IGNORECASE,
)

#: An assignment of a secret-bearing key to a value.
_SECRET_ASSIGNMENT = re.compile(
    r"^\s*(?:export\s+)?(?P<key>[A-Za-z0-9_.\-]+)\s*[:=]\s*(?P<value>.+?)\s*$"
)

#: An environment-style key: upper case, digits and underscores.
_ENV_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")

#: A PEM private key block.
_PEM_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")

#: Markers that make a value a placeholder rather than a usable credential.
_PLACEHOLDER_MARKERS = (
    "<",
    ">",
    "xxx",
    "changeme",
    "change-me",
    "change_me",
    "your-",
    "your_",
    "yourkey",
    "placeholder",
    "example",
    "replace",
    "todo",
    "dummy",
    "fake",
    "not-a-real",
    "notreal",
    "redacted",
    "***",
    "...",
)

#: Values that are obviously test or sample literals.
_TEST_LITERAL_PREFIXES = ("test-", "test_", "dummy", "fake", "sample", "notreal", "example-")

#: A name-like value: lower case letters, digits, separators. A name is not a
#: credential.
_NAME_LIKE = re.compile(r"^[a-z0-9_.\-]+$")

#: A value at least this long is treated as opaque enough to be a credential.
_OPAQUE_CREDENTIAL_LENGTH = 32

#: Minimum length for a shorter, mixed-case credential.
_MIN_CREDENTIAL_LENGTH = 16

#: Maximum size of a file the secret-content scan will read.
_MAX_SCANNED_BYTES = 2 * 1024 * 1024

#: File suffixes the secret-content scan considers as text.
_TEXT_SUFFIXES = (
    ".py",
    ".txt",
    ".json",
    ".toml",
    ".cfg",
    ".ini",
    ".yaml",
    ".yml",
    ".env",
    ".example",
    ".sh",
    ".cmd",
    ".ps1",
    ".bat",
    ".md",
    ".rst",
    ".lock",
    ".xml",
    ".properties",
    ".pem",
    ".key",
    ".crt",
)

#: Exact file names the secret-content scan considers as text.
_TEXT_NAMES = (".env", ".env.example", "Dockerfile", "Makefile")

#: File kinds whose values are configuration, not code. A secret assignment in
#: one of these is a finding regardless of the shape of the value.
_CONFIG_FILE_SUFFIXES = (
    ".env",
    ".example",
    ".ini",
    ".cfg",
    ".toml",
    ".yaml",
    ".yml",
    ".properties",
    ".json",
)

#: Exact file names that hold configuration.
_CONFIG_FILE_NAMES = (".env", ".env.example")

#: File kinds that hold code. A hardcoded credential in code has to be a
#: quoted literal behind an environment-style key, otherwise ordinary
#: statements such as ``token = headers.get("x-control-token")`` would be
#: reported.
_CODE_FILE_SUFFIXES = (".py", ".sh", ".cmd", ".ps1", ".bat")


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One packaging violation.

    ``key`` is the *name* of an offending configuration key. No secret value
    is ever stored, printed or returned.
    """

    path: str
    reason: str
    kind: str
    line: Optional[int] = None
    key: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "reason": self.reason,
            "kind": self.kind,
            "line": self.line,
            "key": self.key,
        }

    def describe(self) -> str:
        where = self.path if self.line is None else f"{self.path}:{self.line}"
        detail = f" ({self.key})" if self.key else ""
        return f"{where}: {self.reason}{detail}"


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------


def is_placeholder(value: str) -> bool:
    """Report whether a configuration value is a placeholder, not a credential."""
    text = value.strip().strip("\"'").strip()
    if not text:
        return True
    lowered = text.lower()
    if any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
        return True
    # A run of dots/dashes only ("...", "---") is a placeholder.
    if set(text) <= {"."}:
        return True
    return False


def looks_like_credential(value: str, *, code_file: bool) -> bool:
    """Report whether a configuration value is a usable credential.

    In a configuration file any non-placeholder value behind a secret-bearing
    key is treated as a credential: those files exist to hold configuration, so
    there is no code to confuse a value with.

    In code the rule is much stricter, because ordinary code assigns things
    like ``token = headers.get("x-control-token")``. Only an environment-style
    key holding a quoted literal can be a hardcoded credential, and a value
    that merely names something (``"ollama_api_key"``) or is an obvious test
    literal is not one.
    """
    text = value.strip()
    quoted = len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'"
    if quoted:
        text = text[1:-1]
    text = text.strip()

    if not text or is_placeholder(text):
        return False
    if not code_file:
        return True

    if not quoted:
        return False
    if "(" in text or ")" in text:
        return False
    lowered = text.lower()
    if lowered.startswith(_TEST_LITERAL_PREFIXES):
        return False
    if len(text) >= _OPAQUE_CREDENTIAL_LENGTH:
        return True
    if _NAME_LIKE.match(lowered):
        # A lower-case name-like token names a thing, it does not authenticate.
        return False
    return len(text) >= _MIN_CREDENTIAL_LENGTH


def secret_findings_in_text(path: str, text: str, *, code_file: bool = False) -> List[Finding]:
    """Return the secret assignments in ``text`` that carry a usable value.

    Only the key NAME is reported. The value is classified and discarded.
    """
    findings: List[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if _PEM_PRIVATE_KEY.search(line):
            findings.append(
                Finding(
                    path=path,
                    reason="PEM private key block in a distributable file",
                    kind="secret-material",
                    line=number,
                )
            )
            continue
        match = _SECRET_ASSIGNMENT.match(line)
        if not match:
            continue
        key = match.group("key")
        if not _SECRET_KEY_PATTERN.match(key):
            continue
        if code_file and not _ENV_KEY_PATTERN.match(key):
            # Only environment-style keys can hold a credential in code.
            continue
        value = match.group("value")
        if not looks_like_credential(value, code_file=code_file):
            continue
        findings.append(
            Finding(
                path=path,
                reason="secret assignment with a non-placeholder value",
                kind="secret-assignment",
                line=number,
                key=key,
            )
        )
    return findings


def prohibited_file_reason(name: str) -> Optional[str]:
    """Return why a file name may not ship, or None when it may."""
    if name in ALLOWED_ENV_FILES:
        return None
    if name in PROHIBITED_ENV_FILES:
        return "local environment file (may contain developer credentials)"
    if name.startswith(".env.") and name not in ALLOWED_ENV_FILES:
        return "local environment file (may contain developer credentials)"
    if name in PROHIBITED_STATE_FILES:
        return "test-run report / coverage state"
    for suffix in PROHIBITED_KEY_SUFFIXES:
        if name.endswith(suffix):
            return "private key material"
    for suffix in PROHIBITED_DB_SUFFIXES:
        if name.endswith(suffix):
            return "database file (user or test state)"
    for suffix in PROHIBITED_BUILD_SUFFIXES:
        if name.endswith(suffix):
            return "build output"
    return None


def _is_reparse_point(path) -> bool:
    """Report whether ``path`` is a symlink or Windows reparse point.

    Uses ``lstat`` so a junction/reparse point is never followed. A dangling
    reparse point still exposes lstat metadata and is treated as unsafe.

    A genuinely missing future output is NOT a reparse point. Any other
    metadata uncertainty fails closed.
    """
    try:
        st = Path(path).lstat()
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & 0x400)


def _safe_rmtree(path) -> bool:
    """Remove ``path`` without ever following a symlink or reparse point.

    Returns True only when the target is missing or has been removed. A
    failed or uncertain deletion returns False rather than claiming success.
    """
    path = Path(path)
    try:
        st = path.lstat()
    except FileNotFoundError:
        return True
    except (OSError, ValueError):
        return False
    if stat.S_ISLNK(st.st_mode) or (getattr(st, "st_file_attributes", 0) & 0x400):
        try:
            path.unlink()
            return True
        except OSError:
            return False
    if not stat.S_ISDIR(st.st_mode):
        try:
            path.unlink()
            return True
        except OSError:
            return False
    for child in list(path.iterdir()):
        if not _safe_rmtree(child):
            return False
    try:
        path.rmdir()
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def secret_material_file_reason(name: str) -> Optional[str]:
    """Filename-only classification of files that must never be read, hashed or
    copied under any circumstance.

    This is the SINGLE source of truth for "secret material" classification used
    by every consuming code path (the scanner, the staged-copy ignore callback,
    the prune pass and the backend-mirror comparison). No file content is ever
    consulted: the classification is based entirely on the file name.

    The accepted placeholder-only ``.env.example`` is deliberately NOT
    classified as secret material: it may ship, but only when all of its values
    are placeholders (checked separately by the content scan).
    """
    # Only the EXACT lowercase placeholder example is exempt; mixed-case
    # (or otherwise disguised) forms are treated as secret material.
    if name == ".env.example":
        return None

    normalized = name.lower()
    if normalized in {x.lower() for x in PROHIBITED_ENV_FILES}:
        return "local environment file (may contain developer credentials)"
    if normalized == ".env":
        return "local environment file (may contain developer credentials)"
    if normalized.startswith(".env."):
        return "local environment file (may contain developer credentials)"
    for suffix in PROHIBITED_KEY_SUFFIXES:
        if normalized.endswith(suffix.lower()):
            return "private key material"
    return None


def payload_copy_ignore():
    """Return a ``shutil.copytree`` ignore callable backed by the shared
    classifier.

    This is a drop-in ``ignore`` argument: ``shutil.copytree(..., ignore=payload_copy_ignore())``.
    It uses filename-only classification through :func:`secret_material_file_reason`
    together with the existing policy rules, so prohibited secret-material files
    are classified BEFORE they are ever copied.
    """

    def _ignore(directory, names):
        ignored = set()
        for name in names:
            # Filename-only classifier first.
            if secret_material_file_reason(name) is not None:
                ignored.add(name)
                continue
            if name in PROHIBITED_STATE_DIRS:
                ignored.add(name)
                continue
            if name in DEV_ONLY_DIR_NAMES:
                ignored.add(name)
                continue
            if name in PROHIBITED_STATE_FILES or name in PROHIBITED_ENV_FILES:
                ignored.add(name)
                continue
            if name.endswith(PROHIBITED_DB_SUFFIXES) or name.endswith(PROHIBITED_BUILD_SUFFIXES):
                ignored.add(name)
                continue
            if name.endswith(PROHIBITED_KEY_SUFFIXES):
                ignored.add(name)
                continue
            for suffix in PROHIBITED_KEY_SUFFIXES:
                if name.endswith(suffix):
                    ignored.add(name)
                    break
        return ignored

    return _ignore


def prohibited_dir_reason(name: str) -> Optional[str]:
    """Return why a directory may not ship, or None when it may."""
    if name in PROHIBITED_STATE_DIRS:
        return "build/tool state directory"
    if name in DEV_ONLY_DIR_NAMES:
        return "development-only directory (test suite)"
    return None


def _scope_of(parts: Sequence[str]) -> str:
    """Return the policy scope of a path inside the payload.

    ``"app"`` for the application's own code, ``"third-party"`` for the
    vendored runtime.
    """
    if not parts:
        return "app"
    if parts[0] in THIRD_PARTY_PAYLOAD_DIRS:
        return "third-party"
    return "app"


def _is_third_party(path: Path, root: Path) -> bool:
    """Report whether a path lies inside a vendored third-party tree."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    return any(part in THIRD_PARTY_DIR_NAMES for part in relative.parts)


def _looks_like_text(path: Path) -> bool:
    name = path.name
    if name in _TEXT_NAMES:
        return True
    return path.suffix.lower() in _TEXT_SUFFIXES


def _is_config_file(path: Path) -> bool:
    """Report whether a file holds configuration rather than code."""
    if path.name in _CONFIG_FILE_NAMES:
        return True
    return path.suffix.lower() in _CONFIG_FILE_SUFFIXES


def _is_code_file(path: Path) -> bool:
    """Report whether a file holds code, where a credential needs a literal."""
    return path.suffix.lower() in _CODE_FILE_SUFFIXES


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def scan_tree(root: Path, *, follow_third_party_names: bool = True) -> List[Finding]:
    """Scan a payload tree and return every packaging violation found.

    A prohibited directory is reported once and its contents are not reported
    individually: they are removed with it.

    The vendored runtime is scanned for local environment files only. Its
    package inventory is pinned by ``requirements.lock.txt`` and verified
    against the runtime itself, so the development-only and build-state rules
    do not apply to it.
    """
    root = Path(root)
    findings: List[Finding] = []
    # A reparse-point root must not be traversed: otherwise rglob follows the
    # link and reports/removes content in an unrelated external tree. Fail
    # closed with one finding so the CLI exits non-zero instead of printing OK.
    if _is_reparse_point(root):
        findings.append(
            Finding(
                path=".",
                reason="payload root is a symlink or reparse point",
                kind="reparse-point",
            )
        )
        return findings
    if not root.is_dir():
        return findings

    for path in sorted(root.rglob("*")):
        parts = path.relative_to(root).parts
        relative = path.relative_to(root).as_posix()
        scope = _scope_of(parts)

        # Never follow a symlink/reparse point that appeared in the staged tree:
        # classify and report it without touching its target. Skip its
        # descendants too (rglob may surface them when following links).
        if len(parts) > 1 and any(
            _is_reparse_point(root.joinpath(*parts[:i])) for i in range(1, len(parts))
        ):
            continue
        if _is_reparse_point(path):
            # Vendored third-party trees may legitimately contain links; never
            # read them, and do not make them a release gate there.
            if scope == "third-party":
                continue
            findings.append(
                Finding(path=relative, reason="symlink or reparse point in a distributable file",
                       kind="prohibited-file")
            )
            continue

        # Skip any descendant of a secret-material ancestor BEFORE reading,
        # hashing or reporting child violations.
        if len(parts) > 1 and any(
            secret_material_file_reason(part) is not None for part in parts[:-1]
        ):
            continue

        if path.is_dir():
            if scope == "third-party":
                # Vendored policy: only environment files are a leak signal.
                if secret_material_file_reason(path.name) is not None and \
                        path.name.lower().startswith(".env"):
                    findings.append(
                        Finding(
                            path=relative,
                            reason=secret_material_file_reason(path.name),
                            kind="prohibited-directory",
                        )
                    )
                continue
            sec_reason = secret_material_file_reason(path.name)
            if sec_reason is not None:
                findings.append(
                    Finding(path=relative, reason=sec_reason, kind="prohibited-directory")
                )
                continue
            reason = prohibited_dir_reason(path.name)
            if reason:
                findings.append(Finding(path=relative, reason=reason, kind="prohibited-directory"))
            continue

        # A file inside a prohibited directory is removed with that directory,
        # so it is not reported a second time.
        if scope != "third-party" and any(
            prohibited_dir_reason(part) for part in parts[:-1]
        ):
            continue

        # Classify secret/material files by NAME first, before ANY stat/read.
        # A prohibited secret-material file must never be opened for its
        # content; the violation is reported filename-only.
        sec_reason = secret_material_file_reason(path.name)
        if sec_reason is not None:
            if scope == "third-party" and not path.name.lower().startswith(".env"):
                # Preserve the accepted policy: only an environment file is a leak
                # signal in vendored code. Other secret-named files (e.g. CA
                # bundles) are not reported, but they are NEVER opened either.
                continue
            findings.append(
                Finding(path=relative, reason=sec_reason, kind="prohibited-file")
            )
            continue

        reason = prohibited_file_reason(path.name)
        if reason:
            if scope == "third-party" and not path.name.startswith(".env"):
                # Only an environment file is a leak signal in vendored code.
                reason = None
            else:
                findings.append(
                    Finding(path=relative, reason=reason, kind="prohibited-file")
                )
                continue

        if scope == "third-party":
            continue
        if not _looks_like_text(path):
            continue
        if not follow_third_party_names and _is_third_party(path, root):
            continue
        try:
            if path.stat().st_size > _MAX_SCANNED_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeError):
            continue
        findings.extend(
            secret_findings_in_text(relative, text, code_file=_is_code_file(path))
        )

    return findings


def prune_tree(root: Path) -> List[str]:
    """Remove every prohibited artifact from a payload tree.

    Returns the relative paths that were removed. Files that are merely
    suspicious (for example an ``.env.example`` holding a real value, or a
    local environment file inside vendored third-party code) are reported by
    :func:`scan_tree` instead of being deleted.
    """
    root = Path(root)
    removed: List[str] = []
    if _is_reparse_point(root):
        return removed
    if not root.is_dir():
        return removed

    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        parts = path.relative_to(root).parts
        relative = path.relative_to(root).as_posix()
        scope = _scope_of(parts)

        # Never descend through, or remove anything reached through, a reparse
        # point anywhere in the path. This protects external targets even when
        # a prohibited-looking entry sits beneath a junction/symlink.
        if len(parts) > 1 and any(
            _is_reparse_point(root.joinpath(*parts[:i])) for i in range(1, len(parts))
        ):
            continue
        if _is_reparse_point(path):
            continue

        if path.is_dir():
            if scope == "third-party":
                continue
            if prohibited_dir_reason(path.name):
                if _remove_tree(path):
                    removed.append(relative + "/")
            continue

        if scope != "third-party" and secret_material_file_reason(path.name) is not None:
            if _safe_rmtree(path):
                removed.append(relative)
            continue
        if prohibited_file_reason(path.name):
            if scope == "third-party":
                # Reported, never deleted: a vendored package may legitimately
                # ship a file with this name.
                continue
            if _safe_rmtree(path):
                removed.append(relative)
    return removed


def payload_ignore_patterns() -> List[str]:
    """Return the glob patterns a payload copy must skip.

    Used by ``shutil.copytree(ignore=...)`` so the prohibited content is never
    written in the first place. ``.env.example`` is deliberately NOT matched:
    the safe example is part of the payload.
    """
    patterns = set(PROHIBITED_STATE_DIRS)
    patterns.update(DEV_ONLY_DIR_NAMES)
    patterns.update(PROHIBITED_ENV_FILES)
    # Note: this flat pattern set is a legacy/compatibility convenience only.
    # It cannot express the exact-cased .env.* exclusion policy, so primary
    # copy protection is `payload_copy_ignore()` (used by stage_backend).
    patterns.update(PROHIBITED_STATE_FILES)
    for suffix in PROHIBITED_DB_SUFFIXES:
        patterns.add("*" + suffix)
    for suffix in PROHIBITED_BUILD_SUFFIXES:
        patterns.add("*" + suffix)
    for suffix in PROHIBITED_KEY_SUFFIXES:
        patterns.add("*" + suffix)
    return sorted(patterns)


def backend_mirror_findings(source: Path, mirror: Path) -> List[Finding]:
    """Compare the stageable backend files in both directions, without modifying them."""
    import os
    source, mirror = Path(source), Path(mirror)
    findings = []
    for root, label in ((source, "source"), (mirror, "staged")):
        if _is_reparse_point(root) or not root.is_dir():
            findings.append(Finding(path=str(root), reason=f"{label} backend directory missing or unsafe",
                                    kind="backend-missing"))
    if findings:
        return findings

    # Use the SAME copy-time ignore policy as ``payload_copy_ignore()`` so the
    # set of "stageable" files here cannot drift from what gets copied. The
    # flat glob patterns were a legacy approximation and disagreed with the
    # exact classifier on several filenames.
    ignore = payload_copy_ignore()

    def included(root: Path) -> set[Path]:
        files = set()
        for dirpath, dirs, names in os.walk(root, followlinks=False):
            # Prune directories the exact policy excludes - never descend. Also
            # never descend through (or report through) a symlink/reparse point,
            # since following it could reach outside the intended backend tree.
            ignored_dirs = ignore(dirpath, list(dirs))
            dirs[:] = [
                name for name in dirs
                if name not in ignored_dirs and not _is_reparse_point(Path(dirpath) / name)
            ]
            for name in names:
                if name in ignore(dirpath, [name]):
                    continue
                candidate = Path(dirpath) / name
                if _is_reparse_point(candidate):
                    continue
                try:
                    st = candidate.lstat()
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode):
                    continue
                relative = candidate.relative_to(root)
                # Defense in depth: the classifier above already excludes
                # secret-material names, but never let a misclassified path
                # slip through into a hash comparison.
                if any(secret_material_file_reason(part) is not None for part in relative.parts):
                    continue
                files.add(relative)
        return files

    source_files, mirror_files = included(source), included(mirror)
    for relative in sorted(source_files | mirror_files):
        path = "backend/" + relative.as_posix()
        if relative not in mirror_files:
            reason = "file missing from staged backend"
        elif relative not in source_files:
            reason = "extra file in staged backend"
        else:
            try:
                if (source / relative).read_bytes() == (mirror / relative).read_bytes():
                    continue
                reason = "staged backend differs from source"
            except OSError:
                reason = "unable to compare backend files"
        findings.append(Finding(path=path, reason=reason, kind="backend-drift"))
    return findings


def _remove_tree(path: Path) -> bool:
    # Never follow a symlink/reparse point. This also keeps prune_tree safe if a
    # prohibited entry was replaced by a reparse point after classification.
    return bool(_safe_rmtree(path))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def default_root() -> Path:
    """Return the staged resource directory of the desktop shell."""
    return Path(__file__).resolve().parents[1] / "src-tauri" / "resources"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify that the staged release payload contains no developer "
        "secrets, test databases or user runtime state."
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=None,
        help="payload root to scan (default: src-tauri/resources)",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="remove the prohibited artifacts instead of only reporting them",
    )
    parser.add_argument(
        "--include-third-party",
        action="store_true",
        help="also scan vendored third-party trees (site-packages) for secrets",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON findings")
    parser.add_argument(
        "--check-backend-mirror", action="store_true",
        help="also compare staged backend against this checkout's source backend",
    )
    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else default_root()
    if not root.is_dir():
        print(f"[verify] payload root not found: {root}", file=sys.stderr)
        return 2

    removed: List[str] = []
    if args.prune:
        removed = prune_tree(root)
        if removed and not args.json:
            print(f"[verify] removed {len(removed)} prohibited artifact(s):")
            for item in removed:
                print(f"    - {item}")

    findings = scan_tree(root, follow_third_party_names=args.include_third_party)
    if args.check_backend_mirror:
        findings.extend(backend_mirror_findings(
            Path(__file__).resolve().parents[1] / "backend", root / "backend"
        ))

    if args.json:
        print(
            json.dumps(
                {
                    "root": str(root),
                    "removed": removed,
                    "findings": [f.as_dict() for f in findings],
                    "clean": not findings,
                },
                indent=2,
            )
        )
        return 1 if findings else 0

    if findings:
        print(f"[verify] FAIL: {len(findings)} packaging violation(s) in {root}:")
        for finding in findings:
            print(f"    - {finding.describe()}")
        print("[verify] resolve these violations before building a distributable.")
        print("[verify] inspect and safely refresh the staged payload before retrying.")
        return 1

    print(f"[verify] OK: {root} contains no developer secrets, test databases or user state.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
