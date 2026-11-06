#!/usr/bin/env python3
"""
Check that every module the authority documents name exists on disk.

WHY THIS SCRIPT EXISTS
----------------------
Two documents in this repository named modules that were deleted years ago,
and nothing noticed. A document that names a module which does not exist is
a lie about the codebase, and it is the kind of lie nobody catches, because
reading a document never touches the filesystem. This script makes the claim
checkable: it resolves every repository-relative path the documents mention
and fails when one of them does not exist.

WHAT COUNTS AS A REFERENCE
--------------------------
1. A reference is written in CODE FONT. The script scans inline code spans
   (backticks) and fenced code blocks. It does not scan plain prose. That is
   not an arbitrary choice: ``README.md`` used to contain the
   prose shorthand "backend/acceptance", which is not a path to anything (the
   real one is ``backend/tests/acceptance``). A prose scanner false-alarms on
   it. A document references a module by writing it in code font.
2. A PATH-SHAPED TOKEN is classified, not assumed. A token is path-shaped if
   it contains a path separator or ends with a known source/document
   extension. Tokens that are not path-shaped (``TaskEvent``,
   ``ConversationTurn``, ``BaseTool``, ``Objective``, ``PlanStep``) are type
   names, not paths, and are ignored.
3. MARKDOWN LINK TARGETS are resolved with markdown semantics: relative to
   the document's own directory.

THE CLASSES
-----------
MODULE            ``backend/...`` ending in a source extension. Must exist as
                  a FILE, relative to the repository root.
DIRECTORY         ``backend/...`` with no extension. Must exist as a
                  DIRECTORY. A directory reference is legitimate and counts
                  as resolved.
DOCUMENT          any other path with a separator, or a document extension.
                  Resolved first relative to the DOCUMENT'S OWN DIRECTORY,
                  then relative to the REPOSITORY ROOT. Must exist.
MODULE BY NAME    a bare ``*.py`` filename. Searched for under ``backend/``:
                  exactly one match -> resolved; no match -> MISSING; several
                  matches -> AMBIGUOUS (reported, not fatal unless --strict).
                  This is the class that gives the script teeth on a document
                  that names a deleted module by its bare filename.
MARKDOWN LINK     ``[text](target)``. Relative to the document's directory.
                  ``http(s)://``, ``mailto:`` and ``#anchor`` targets are
                  skipped.

THE ALLOWLIST
-------------
``ALLOWED_ABSENT`` maps a path to the reason it is allowed to be absent. An
entry is reported as "allowed (absent by design: <reason>)" and is NOT
counted as missing; an entry whose path NOW EXISTS is reported as a stale
allowlist entry (a warning by default, fatal under --strict).

The mapping starts EMPTY, and it must stay that way unless a path is
genuinely absent by design. The way to make a missing path go away is to
CORRECT THE DOCUMENT, not to allowlist the lie.

EXIT CODES
----------
0  zero missing paths
1  at least one missing path
2  usage error
   (--strict also fails on ambiguous names and stale allowlist entries)
"""

import argparse
import json
import re
import sys
from pathlib import Path

# The documents that are checked by default: the four that name modules as
# modules. Other documents legitimately contain paths that were never built
# (a plan proposes them), which is exactly why the scope is explicit.
# All module-naming documents have been removed; there is nothing to check by
# default. Keep the script available for explicit `--docs` arguments.
DEFAULT_DOCS = ()

SOURCE_EXTENSIONS = frozenset(
    {
        ".py",
        ".pyi",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".rs",
        ".go",
        ".java",
        ".c",
        ".h",
        ".cpp",
        ".cs",
        ".sql",
        ".sh",
        ".ps1",
        ".bat",
        ".cmd",
    }
)

DOCUMENT_EXTENSIONS = frozenset(
    {
        ".md",
        ".markdown",
        ".rst",
        ".txt",
        ".ini",
        ".toml",
        ".cfg",
        ".json",
        ".yaml",
        ".yml",
        ".lock",
    }
)

# A path that is legitimately absent, and WHY. EMPTY ON PURPOSE: see the
# module docstring. Add an entry only for a path that is absent BY DESIGN.
ALLOWED_ABSENT: dict[str, str] = {}

_CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
_FENCE_RE = re.compile(r"^```.*$")
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_WHITESPACE_RE = re.compile(r"\s+")

# Characters that end a token but are not part of a path.
_TRIM_CHARS = "\"'`()[]{}<>,;:*!?"


class UsageError(Exception):
    """A problem with the invocation or with the files it points at."""


def is_path_shaped(token):
    """True when a token looks like a path rather than a name."""
    if "/" in token or "\\" in token:
        return True
    return token.lower().endswith(tuple(SOURCE_EXTENSIONS | DOCUMENT_EXTENSIONS))


def normalise(token):
    """The token as a forward-slash path, or None when it is not one."""
    token = token.strip().strip(_TRIM_CHARS)
    if not token:
        return None
    token = token.replace("\\", "/")
    if "=" in token:
        # An assignment ("PYTHONPATH=..."): the value is the path.
        token = token.split("=", 1)[1].strip()
        if not token:
            return None
        token = token.replace("\\", "/")
    if token.startswith("/") or re.match(r"^[A-Za-z]:", token):
        # Absolute: not a repository-relative reference.
        return None
    while token.startswith("./"):
        token = token[2:]
    if not token:
        return None
    return token


def code_references(text):
    """(line number, token) for every code-font reference in a document.

    Inline code spans and fenced code blocks are scanned; plain prose is not.
    """
    references = []
    in_fence = False
    fence_token = None
    for number, line in enumerate(text.splitlines(), 1):
        if _FENCE_RE.match(line.strip()):
            in_fence = not in_fence
            continue
        if in_fence:
            for token in _WHITESPACE_RE.split(line.strip()):
                if token:
                    references.append((number, token))
            continue
        for match in _CODE_SPAN_RE.finditer(line):
            for token in _WHITESPACE_RE.split(match.group(1)):
                if token:
                    references.append((number, token))
    return references


def link_targets(text):
    """(line number, target) for every markdown link in a document."""
    targets = []
    for number, line in enumerate(text.splitlines(), 1):
        for match in _LINK_RE.finditer(line):
            targets.append((number, match.group(1)))
    return targets


def skip_link_target(target):
    """True for a link target that is not a repository path."""
    lowered = target.lower()
    return (
        lowered.startswith(("http://", "https://", "mailto:", "tel:"))
        or target.startswith("#")
        or target.startswith("{")
    )


def classify(token, root, doc_dir):
    """Classify a token and resolve it. Returns a finding dict."""
    finding = {"token": token, "kind": None, "resolved": None, "detail": ""}
    normalised = normalise(token)
    if normalised is None:
        finding["kind"] = "skipped"
        finding["detail"] = "not a repository-relative path"
        return finding
    token = normalised
    extension = ("." + token.rsplit(".", 1)[1].lower()) if "." in token.rsplit("/", 1)[-1] else ""
    source_like = extension in SOURCE_EXTENSIONS
    document_like = extension in DOCUMENT_EXTENSIONS

    if not is_path_shaped(token):
        finding["kind"] = "skipped"
        finding["detail"] = "not a path-shaped token"
        return finding

    if token.startswith("backend/") or token == "backend":
        if source_like:
            finding["kind"] = "module"
            target = root / token
            finding["resolved"] = str(target)
            if not target.is_file():
                finding["detail"] = "no such module in the repository"
            return finding
        if not document_like:
            finding["kind"] = "directory"
            target = root / token
            finding["resolved"] = str(target)
            if not target.is_dir():
                finding["detail"] = "no such directory in the repository"
            return finding
        # A document under backend/ (a README, say) is a DOCUMENT.
        finding["kind"] = "document"
        for candidate in (doc_dir / token, root / token):
            if candidate.exists():
                finding["resolved"] = str(candidate)
                return finding
        finding["detail"] = "no such file (looked in {} and {})".format(
            doc_dir / token, root / token
        )
        return finding

    if "/" not in token and source_like:
        finding["kind"] = "module_by_name"
        matches = sorted(root.glob("backend/**/" + token))
        finding["resolved"] = ", ".join(str(m) for m in matches)
        if not matches:
            finding["detail"] = "no module with this name under backend/"
        elif len(matches) > 1:
            finding["detail"] = "ambiguous: {} modules have this name".format(
                len(matches)
            )
        return finding

    finding["kind"] = "document"
    candidates = [doc_dir / token, root / token]
    for candidate in candidates:
        if candidate.exists():
            finding["resolved"] = str(candidate)
            return finding
    finding["detail"] = "no such file (looked in {} and {})".format(
        doc_dir / token, root / token
    )
    return finding


def check_document(path, root):
    """Every finding for one document."""
    text = path.read_text(encoding="utf-8", errors="replace")
    doc_dir = path.resolve().parent
    findings = []
    seen = set()

    for number, token in code_references(text):
        key = (number, token)
        if key in seen:
            continue
        seen.add(key)
        finding = classify(token, root, doc_dir)
        finding["line"] = number
        finding["document"] = str(path)
        finding["source"] = "code"
        findings.append(finding)

    for number, target in link_targets(text):
        if skip_link_target(target):
            continue
        key = (number, target)
        if key in seen:
            continue
        seen.add(key)
        normalised = normalise(target)
        if normalised is None:
            continue
        resolved = (doc_dir / normalised)
        finding = {
            "token": target,
            "kind": "link",
            "line": number,
            "document": str(path),
            "source": "link",
            "resolved": str(resolved) if resolved.exists() else None,
            "detail": "" if resolved.exists() else "the link target does not exist",
        }
        findings.append(finding)

    return findings


def is_allowed(token, resolved):
    """The allowlist reason for a token, or None."""
    for candidate in (token, resolved):
        if candidate and candidate in ALLOWED_ABSENT:
            return ALLOWED_ABSENT[candidate]
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Check that every repository-relative path the given documents "
            "mention exists on disk."
        ),
        epilog=(
            "exit codes: 0 = zero missing paths; 1 = at least one missing path; "
            "2 = usage error. --strict also fails on ambiguous module names and "
            "on stale allowlist entries."
        ),
    )
    parser.add_argument(
        "--root",
        default=None,
        help="repository root (default: the parent of this script's directory)",
    )
    parser.add_argument(
        "--docs",
        nargs="+",
        default=None,
        help="the documents to check (default: the four module-naming documents)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the findings as JSON",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="also fail on ambiguous module names and stale allowlist entries",
    )
    args = parser.parse_args(argv)

    try:
        root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
        if not root.is_dir():
            raise UsageError("repository root not found: {}".format(root))
        names = args.docs if args.docs else list(DEFAULT_DOCS)
        documents = []
        for name in names:
            path = Path(name)
            if not path.is_absolute():
                from_cwd = Path.cwd() / path
                path = from_cwd if from_cwd.exists() else root / path
            if not path.is_file():
                raise UsageError("document not found: {}".format(path))
            documents.append(path)

        findings = []
        for path in documents:
            findings.extend(check_document(path, root))

        missing = []
        ambiguous = []
        allowed = []
        stale = []
        for finding in findings:
            if finding["kind"] == "skipped":
                continue
            reason = is_allowed(finding["token"], finding["resolved"])
            if finding["kind"] == "module_by_name" and finding["detail"].startswith("ambiguous"):
                ambiguous.append(finding)
                continue
            if finding["kind"] in ("module", "directory", "document", "module_by_name", "link"):
                if finding["detail"]:
                    if reason:
                        allowed.append((finding, reason))
                    else:
                        missing.append(finding)
                else:
                    if reason:
                        stale.append((finding, reason))
            elif reason:
                allowed.append((finding, reason))

        # An allowlist entry whose path now exists is a stale entry.
        for token, reason in ALLOWED_ABSENT.items():
            if (root / token).exists():
                stale.append(
                    (
                        {
                            "token": token,
                            "kind": "allowlist",
                            "line": 0,
                            "document": "<allowlist>",
                            "resolved": str(root / token),
                            "detail": "the allowlisted path exists again",
                        },
                        reason,
                    )
                )

        if args.json:
            print(
                json.dumps(
                    {
                        "root": str(root),
                        "documents": [str(p) for p in documents],
                        "missing": missing,
                        "ambiguous": ambiguous,
                        "allowed": [
                            {"finding": f, "reason": r} for f, r in allowed
                        ],
                        "stale_allowlist": [
                            {"finding": f, "reason": r} for f, r in stale
                        ],
                        "missing_paths": len(missing),
                    },
                    indent=2,
                )
            )
        else:
            for finding in missing:
                print(
                    "{}:{}: {} ({})".format(
                        finding["document"], finding["line"], finding["token"], finding["detail"]
                    )
                )
            for finding, reason in allowed:
                print(
                    "{}:{}: {} (allowed, absent by design: {})".format(
                        finding["document"], finding["line"], finding["token"], reason
                    )
                )
            for finding in ambiguous:
                print(
                    "{}:{}: {} (ambiguous: {})".format(
                        finding["document"], finding["line"], finding["token"], finding["detail"]
                    )
                )
            for finding, reason in stale:
                print(
                    "{}:{}: {} (stale allowlist entry: {})".format(
                        finding["document"], finding["line"], finding["token"], reason
                    )
                )
            print("missing paths: {}".format(len(missing)))
            if ambiguous:
                print("ambiguous module names: {}".format(len(ambiguous)))
            if stale:
                print("stale allowlist entries: {}".format(len(stale)))

        if missing:
            return 1
        if args.strict and (ambiguous or stale):
            return 1
        return 0
    except UsageError as exc:
        print("usage error: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
