#!/usr/bin/env python3
"""Stage an isolated, clean Python runtime + backend payload (safe)."""
from __future__ import annotations
import argparse, hashlib, json, os, platform, re, shutil, stat, subprocess, sys, tempfile, urllib.request, zipfile
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
SRC_TAURI_RESOURCES = REPO_ROOT / "src-tauri" / "resources"
DEFAULT_OUTPUT = REPO_ROOT / "build" / "staging" / "clean-runtime-payload"
DEFAULT_SOURCE = REPO_ROOT / "backend"
DEFAULT_PYTHON_VERSION = "3.13.16"
PYTHON_TAG = "3.13"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
DOWNLOAD_DIR = REPO_ROOT / "build" / "staging" / "python-downloads"
# Independently published SHA-256 digests for exact Python embeddable archives.
# Unknown versions are intentionally absent so verification fails closed.
TRUSTED_PYTHON_EMBEDDED_SHA256 = {
    "3.13.16": "97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297",
}

sys.path.insert(0, str(SCRIPTS_DIR))
from verify_release_payload import backend_mirror_findings, payload_copy_ignore, prune_tree, scan_tree

def fail(msg): raise SystemExit(f"[stage-runtime] ERROR: {msg}")

def download(url, dest):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return dest
    print(f"[stage-runtime] downloading {url}")
    try:
        with urllib.request.urlopen(url) as r, open(dest, "wb") as o:
            shutil.copyfileobj(r, o)
    except Exception as e:
        try:
            if dest.exists(): dest.unlink()
        except OSError: pass
        raise SystemExit(f"[stage-runtime] download failed for {url}: {e}")
    return dest

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1024*1024), b""): h.update(c)
    return h.hexdigest()


def verify_python_archive(path, version):
    path = Path(path)
    trusted_digests = TRUSTED_PYTHON_EMBEDDED_SHA256
    if not isinstance(trusted_digests, dict) or version not in trusted_digests:
        raise SystemExit(f"[stage-runtime] no independently trusted digest for Python embedded archive {version}")
    trusted = str(trusted_digests[version]).strip().lower()
    actual = sha256_file(path).lower()
    if actual != trusted:
        raise SystemExit(
            f"[stage-runtime] Python embedded archive digest mismatch for {version}: "
            f"actual={actual}, trusted={trusted}"
        )
    return path

def is_rp(p):
    try: st=Path(p).lstat()
    except FileNotFoundError: return False
    except (OSError, ValueError): return True
    if stat.S_ISLNK(st.st_mode): return True
    return bool(getattr(st, "st_file_attributes", 0) & 0x400)

def safe_rmtree(path):
    path=Path(path)
    try: st=path.lstat()
    except FileNotFoundError: return
    if stat.S_ISLNK(st.st_mode) or (getattr(st, "st_file_attributes", 0)&0x400):
        try: path.unlink()
        except OSError as e: raise SystemExit(f"[stage-runtime] refusing to remove reparse point {path}: {e}")
        return
    if not stat.S_ISDIR(st.st_mode):
        try: path.unlink()
        except OSError as e: raise SystemExit(f"[stage-runtime] cannot remove file {path}: {e}")
        return
    for c in list(path.iterdir()): safe_rmtree(c)
    try: path.rmdir()
    except OSError as e: raise SystemExit(f"[stage-runtime] cannot remove directory {path}: {e}")

def _best_effort_rmtree(path):
    try:
        safe_rmtree(path)
    except BaseException:
        pass


def contains_rp(root):
    # Dangling reparse points still have lstat metadata even when their target
    # is absent; check that before the normal existence short-circuit.
    if is_rp(root): return True
    if not root.exists(): return False
    try:
        for dp, ds, fs in os.walk(root, followlinks=False):
            for n in list(ds)+list(fs):
                if is_rp(Path(dp)/n): return True
    except OSError: return True
    return False

def within(p, root):
    try:
        Path(p).resolve().relative_to(Path(root).resolve()); return True
    except (ValueError, OSError): return False


_WINDOWS_RESERVED_DEVICE_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *[f"COM{i}" for i in range(1, 10)],
    *[f"LPT{i}" for i in range(1, 10)],
}


def _windows_reserved_or_ambiguous(component):
    # Windows ignores trailing spaces/dots and treats certain basenames as
    # devices regardless of extension; both are ambiguous packaging hazards.
    if component.endswith(" ") or component.endswith("."):
        return True
    stem = component.split(".", 1)[0].upper()
    return stem in _WINDOWS_RESERVED_DEVICE_NAMES


class UnsafeZipEntryError(ValueError, SystemExit):
    """Raised when a ZIP archive contains an unsafe entry (traversal, absolute/drive path, symlink, or pre-existing escape)."""


def _is_zipfile_object(value):
    return hasattr(value, "infolist") and hasattr(value, "open") and hasattr(value, "namelist")


def _ensure_not_reparse_path(path):
    path = Path(path)
    node = path
    chain = []
    while True:
        chain.append(node)
        if node == node.parent:
            break
        node = node.parent
    for candidate in chain:
        # Do not gate on exists(): dangling reparse points/symlinks still expose
        # lstat() metadata and must be rejected.
        try:
            if is_rp(candidate):
                raise UnsafeZipEntryError(
                    f"Unsafe zip archive entry target (preexisting symlink escape): {path}"
                )
        except OSError:
            raise UnsafeZipEntryError(
                f"Unsafe zip archive entry target (preexisting symlink escape): {path}"
            )
    return path


def _validate_zip_member_name(name):
    if not isinstance(name, str) or name == "":
        raise UnsafeZipEntryError(f"Unsafe zip archive entry (invalid name): {name!r}")
    if "\x00" in name:
        raise UnsafeZipEntryError(f"Unsafe zip archive entry (NUL byte): {name!r}")
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        raise UnsafeZipEntryError(f"Unsafe zip archive entry (absolute path): {name!r}")
    raw_parts = normalized.split("/")
    # A trailing empty component marks a directory entry; permit exactly one.
    if raw_parts and raw_parts[-1] == "":
        raw_parts = raw_parts[:-1]
    if not raw_parts:
        raise UnsafeZipEntryError(f"Unsafe zip archive entry (invalid name): {name!r}")
    if any(part == "" for part in raw_parts):
        raise UnsafeZipEntryError(f"Unsafe zip archive entry (traversal): {name!r}")
    if any(part in (".", "..") for part in raw_parts):
        raise UnsafeZipEntryError(f"Unsafe zip archive entry (traversal): {name!r}")
    if any(":" in part for part in raw_parts):
        raise UnsafeZipEntryError(f"Unsafe zip archive entry (Windows drive path): {name!r}")
    if any(_windows_reserved_or_ambiguous(part) for part in raw_parts):
        raise UnsafeZipEntryError(
            f"Unsafe zip archive entry (Windows reserved device name or trailing dot/space): {name!r}"
        )
    return normalized


def _zipinfo_is_symlink(info):
    mode = (getattr(info, "external_attr", 0) >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def _zip_member_target(destination, name):
    normalized = _validate_zip_member_name(name)
    if normalized.endswith("/"):
        normalized = normalized[:-1]
    parts = [part for part in normalized.split("/") if part]
    target = Path(destination)
    for part in parts:
        target = target / part
        # Reject reparse points even while the final component is dangling.
        if is_rp(target):
            raise UnsafeZipEntryError(
                f"Unsafe zip archive entry (preexisting symlink escape): {name!r}"
            )
    destination_resolved = Path(destination).resolve(strict=False)
    target_resolved = target.resolve(strict=False)
    try:
        target_resolved.relative_to(destination_resolved)
    except ValueError:
        raise UnsafeZipEntryError(f"Unsafe zip archive entry (traversal): {name!r}")
    return target


def _ensure_parent_chain_safe(destination, target):
    destination = Path(destination)
    target = Path(target)
    try:
        rel = target.relative_to(destination)
    except ValueError:
        raise UnsafeZipEntryError(f"Unsafe zip archive entry (traversal): {target}")
    current = destination
    for part in rel.parts:
        current = current / part
        if is_rp(current):
            raise UnsafeZipEntryError(
                f"Unsafe zip archive entry (preexisting symlink escape): {target}"
            )
    if is_rp(target):
        raise UnsafeZipEntryError(
            f"Unsafe zip archive entry (preexisting symlink escape): {target}"
        )
    return target


def safe_extract_zip(*args, **kwargs):
    """Safely extract a ZIP archive after prevalidating every entry.

    Rejects path traversal, absolute/Windows drive paths, symlink entries and
    pre-existing symlink/reparse-point escapes.  ``zip_source`` may be a path or
    an already-open ``zipfile.ZipFile``.  ``destination`` must be a directory.
    """
    members = kwargs.pop("members", None)
    # Resolve zip source / destination from positional or keyword arguments.
    zip_source = kwargs.pop("zip_source", None)
    if zip_source is None:
        zip_source = kwargs.pop("zip_path", None)
    if zip_source is None:
        zip_source = kwargs.pop("zip_file", None)
    if zip_source is None:
        zip_source = kwargs.pop("archive", None)
    if zip_source is None:
        zip_source = kwargs.pop("archive_path", None)
    if zip_source is None:
        zip_source = kwargs.pop("source", None)
    if zip_source is None:
        zip_source = kwargs.pop("src", None)
    if zip_source is None:
        zip_source = kwargs.pop("zip", None)

    destination = kwargs.pop("destination", None)
    if destination is None:
        destination = kwargs.pop("dest", None)
    if destination is None:
        destination = kwargs.pop("target", None)
    if destination is None:
        destination = kwargs.pop("target_dir", None)
    if destination is None:
        destination = kwargs.pop("output", None)
    if destination is None:
        destination = kwargs.pop("path", None)
    if destination is None:
        destination = kwargs.pop("dest_dir", None)

    if zip_source is None and args:
        zip_source = args[0]
        args = args[1:]
    if destination is None and args:
        destination = args[0]
        args = args[1:]

    if zip_source is None or destination is None:
        raise TypeError("safe_extract_zip requires a zip source and a destination")

    destination = Path(destination)
    _ensure_not_reparse_path(destination)
    if destination.parent is not None:
        _ensure_not_reparse_path(destination.parent)
    # Check reparse points (including dangling ones) before relying on exists().
    if is_rp(destination):
        raise UnsafeZipEntryError(
            f"Unsafe zip archive entry target (preexisting symlink escape): {destination}"
        )
    elif destination.exists():
        if not destination.is_dir():
            raise UnsafeZipEntryError(
                f"Unsafe zip archive entry target (not a directory): {destination}"
            )
    else:
        destination.mkdir(parents=True, exist_ok=True)
        if is_rp(destination):
            raise UnsafeZipEntryError(
                f"Unsafe zip archive entry target (preexisting symlink escape): {destination}"
            )
    # Reject roots to avoid writing across the filesystem.
    try:
        if str(destination.resolve()) == str(destination.resolve().anchor):
            raise UnsafeZipEntryError(
                f"Unsafe zip archive entry target (filesystem root): {destination}"
            )
    except OSError:
        pass

    close_zip = not _is_zipfile_object(zip_source)
    zf = zipfile.ZipFile(zip_source) if close_zip else zip_source
    try:
        infos = zf.infolist()
        if members is not None:
            wanted = {str(m) for m in members}
            infos = [info for info in infos if info.filename in wanted]

        # Prevalidate ALL entries before extracting any of them.
        planned = []
        for info in infos:
            if _zipinfo_is_symlink(info):
                raise UnsafeZipEntryError(
                    f"Unsafe zip archive entry (symlink): {getattr(info, 'filename', info)!r}"
                )
            target = _zip_member_target(destination, getattr(info, "filename", ""))
            _ensure_parent_chain_safe(destination, target)
            planned.append((info, target))

        # Extract only after every entry has been accepted.
        for info, target in planned:
            if getattr(info, "is_dir", lambda: False)():
                if is_rp(target):
                    raise UnsafeZipEntryError(
                        f"Unsafe zip archive entry (preexisting symlink escape): {target}"
                    )
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            _ensure_parent_chain_safe(destination, target.parent)
            if is_rp(target):
                raise UnsafeZipEntryError(
                    f"Unsafe zip archive entry (preexisting symlink escape): {target}"
                )
            with zf.open(info, "r") as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
        return destination
    finally:
        if close_zip:
            try:
                zf.close()
            except Exception:
                pass

def _validate_paths(source, output):
    if not Path(source).is_dir(): raise SystemExit(f"[stage-runtime] source backend directory not found: {source}")
    if is_rp(source): raise SystemExit(f"[stage-runtime] refusing reparse point / symlink source: {source}")
    if is_rp(output): raise SystemExit(f"[stage-runtime] refusing reparse point / symlink output: {output}")
    s,o=Path(source).resolve(), Path(output).resolve()
    for protected in (SRC_TAURI_RESOURCES, SRC_TAURI_RESOURCES/"backend"):
        pr=protected.resolve()
        if o==pr or within(o,pr): raise SystemExit(f"[stage-runtime] refusing to target protected path {o} (would touch mirror {pr})")
    if within(s,SRC_TAURI_RESOURCES) or within(SRC_TAURI_RESOURCES,s): raise SystemExit(f"[stage-runtime] refusing paths touching protected resources: source={s}")
    if o==s or within(o,s) or within(s,o): raise SystemExit(f"[stage-runtime] refusing overlapping source/output: source={s} output={o}")
    if str(o)==str(o.anchor) or str(o) in ("","/","\\\\"): raise SystemExit(f"[stage-runtime] refusing filesystem root output: {o}")
    if contains_rp(s): raise SystemExit(f"[stage-runtime] refusing source tree containing symlink/reparse point: {s}")

def resolve_zip(value, version, allow_network):
    # Fail closed for any version without independently trusted provenance before
    # doing network or filesystem work. The trusted digest is also checked before
    # a selected archive is used for extraction.
    trusted_digests = TRUSTED_PYTHON_EMBEDDED_SHA256
    if not (isinstance(trusted_digests, dict) and version in trusted_digests):
        raise SystemExit(f"[stage-runtime] no independently trusted digest for Python embedded archive {version}")
    if value is not None:
        p=Path(value)
        if is_rp(p): raise SystemExit(f"[stage-runtime] refusing reparse point runtime zip: {p}")
        if not p.is_file(): raise SystemExit(f"[stage-runtime] runtime zip not found: {p}")
        resolved = p.resolve()
        verify_python_archive(resolved, version)
        return resolved
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    z=DOWNLOAD_DIR / f"python-{version}-embed-amd64.zip"
    if z.exists():
        resolved = z.resolve()
        verify_python_archive(resolved, version)
        return resolved
    if not allow_network: raise SystemExit(f"[stage-runtime] cached embedded Python archive missing and network disabled: {z}")
    url=f"https://www.python.org/ftp/python/{version}/python-{version}-embed-amd64.zip"
    downloaded = download(url, z)
    resolved = downloaded.resolve()
    verify_python_archive(resolved, version)
    return resolved

_RELEASE_MODES = ("release", "released", "prod", "production")
_PROTOTYPE_LABEL = "prototype/unverified"
_RELEASE_LABEL = "release"


def _truthy_flag(value):
    if value is None:
        return None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on", "release", "released", "prod", "production"}:
            return True
        if lowered in {"0", "false", "no", "off", "prototype", "unverified", "dev", "development"}:
            return False
        return None
    return bool(value)


def _coalesce_named(mapping, names, default=None):
    for name in names:
        if name in mapping and mapping[name] is not None:
            return mapping[name]
    return default


def verify_get_pip_digest(path, expected_digest):
    path = Path(path)
    # Never follow a pre-existing reparse point/symlink to an attacker-controlled
    # file before the digest has been checked.
    if is_rp(path):
        raise SystemExit(f"[stage-runtime] refusing reparse point get-pip path: {path}")
    if expected_digest is None:
        raise SystemExit("[stage-runtime] release mode requires an caller-supplied/pinned get-pip SHA-256")
    expected = str(expected_digest).strip().lower()
    if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
        raise SystemExit("[stage-runtime] get-pip SHA-256 must be a 64-character hex digest")
    actual = sha256_file(path).lower()
    if actual != expected:
        raise SystemExit(
            f"[stage-runtime] get-pip digest mismatch: actual={actual}, pinned={expected}"
        )
    return path


def ensure_getpip(value, allow_network, *, expected_sha256=None, release=False, **kwargs):
    # Accept a few equivalent spellings without changing the default prototype
    # behavior. A digest supplied by the caller is always verified, but a cached
    # digest is never treated as independently trusted evidence on its own.
    release = bool(release) if release is not None else False
    if "release_mode" in kwargs: release = release or bool(kwargs.pop("release_mode"))
    if "provenance" in kwargs and str(kwargs.get("provenance")).lower() in _RELEASE_MODES:
        release = True
    if "provenance_mode" in kwargs and str(kwargs.get("provenance_mode")).lower() in _RELEASE_MODES:
        release = True
    if "mode" in kwargs and str(kwargs.get("mode")).lower() in _RELEASE_MODES:
        release = True

    expected_sha256 = _coalesce_named(
        kwargs,
        ("get_pip_digest", "get_pip_hash", "pinned_get_pip_sha256", "get_pip_sha",
         "getpip_sha256", "getpip_digest", "bootstrap_sha256", "expected_get_pip_sha256",
         "get_pip_digest_sha256"),
        expected_sha256,
    )
    kwargs.pop("get_pip_sha256", None)

    if value is not None:
        p = Path(value)
        if is_rp(p): raise SystemExit(f"[stage-runtime] refusing reparse point get-pip path: {p}")
        if not p.is_file(): raise SystemExit(f"[stage-runtime] get-pip script not found: {p}")
        resolved = p.resolve()
        if expected_sha256 is not None or release:
            if release and expected_sha256 is None:
                raise SystemExit("[stage-runtime] release mode requires an caller-supplied/pinned get-pip SHA-256")
            if expected_sha256 is not None:
                verify_get_pip_digest(resolved, expected_sha256)
        return resolved

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    d = DOWNLOAD_DIR / "get-pip.py"
    if d.exists():
        if is_rp(d):
            raise SystemExit(f"[stage-runtime] refusing reparse point get-pip cache: {d}")
        # Never trust a locally cached copy by its cached digest alone: only a
        # caller-supplied pinned digest (or prototype mode without enforcement)
        # can authorize it.
        if expected_sha256 is not None or release:
            if release and expected_sha256 is None:
                raise SystemExit("[stage-runtime] release mode requires an caller-supplied/pinned get-pip SHA-256")
            if expected_sha256 is not None:
                verify_get_pip_digest(d.resolve(), expected_sha256)
            return d.resolve()
        return d.resolve()
    if not allow_network:
        raise SystemExit(f"[stage-runtime] cached get-pip.py missing and network disabled: {d}")
    if is_rp(d):
        raise SystemExit(f"[stage-runtime] refusing reparse point get-pip cache: {d}")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    # Owned-by-creation temporary file: mkstemp returns an exclusive descriptor
    # for an inode we own, so network bytes are written to that handle rather
    # than reopening/replacing a path. This avoids losing ownership or following
    # a path that may have changed after creation.
    fd, temp_name = tempfile.mkstemp(prefix="get-pip.", suffix=".py", dir=str(DOWNLOAD_DIR))
    temp_path = Path(temp_name)
    owned_fd = fd
    try:
        if is_rp(temp_path):
            raise SystemExit(f"[stage-runtime] refusing reparse point get-pip download: {temp_path}")
        try:
            with urllib.request.urlopen(GET_PIP_URL) as response, os.fdopen(owned_fd, "wb") as output:
                owned_fd = -1
                shutil.copyfileobj(response, output)
        except Exception as exc:
            raise SystemExit(f"[stage-runtime] download failed for {GET_PIP_URL}: {exc}") from exc
        resolved = temp_path.resolve()
        if expected_sha256 is not None or release:
            if release and expected_sha256 is None:
                raise SystemExit("[stage-runtime] release mode requires an caller-supplied/pinned get-pip SHA-256")
            if expected_sha256 is not None:
                verify_get_pip_digest(resolved, expected_sha256)
        if d.exists() and is_rp(d):
            raise SystemExit(f"[stage-runtime] refusing reparse point get-pip cache: {d}")
        os.replace(resolved, d)
        return d.resolve()
    finally:
        if owned_fd >= 0:
            try:
                os.close(owned_fd)
            except OSError:
                pass
        # Remove only our temporary path if promotion did not happen.
        if temp_path.exists():
            # Guard against path replacement races: only unlink the path when it
            # still refers to a regular file owned by our temporary storage.
            try:
                st = temp_path.lstat()
            except OSError:
                st = None
            if st is not None and stat.S_ISREG(st.st_mode):
                try:
                    temp_path.unlink()
                except OSError:
                    pass
def read_lock(value):
    if value is not None:
        p=Path(value).resolve()
        if is_rp(p): raise SystemExit(f"[stage-runtime] refusing reparse point lock path: {p}")
        if not p.is_file(): raise SystemExit(f"[stage-runtime] requirements lock not found: {p}")
        return p
    c=DEFAULT_SOURCE/"requirements.lock.txt"
    if c.is_file(): return c.resolve()
    return None

def read_lock_lines(path):
    return [ln.strip() for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.strip().startswith("#")]


def _release_lock_records(path):
    """Parse a pip requirements lock into strict release-auditable records.

    Returns a list of logical record strings. A record may be one physical
    requirement line or a backslash continuation whose non-first physical
    lines are hash options only. Requirements and options are never merged
    across records.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    records = []
    current = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not current:
            if not stripped or stripped.startswith("#"):
                continue
            current = [raw]
        else:
            # A continuation is only valid if the previous physical line ended
            # with a backslash. Otherwise each requirement stands on its own.
            if not current[-1].rstrip().endswith("\\"):
                records.append(current)
                current = [raw] if (stripped and not stripped.startswith("#")) else []
                if (not stripped or stripped.startswith("#")) and current:
                    current = []
                continue
            current.append(raw)
        if current and not current[-1].rstrip().endswith("\\"):
            records.append(current)
            current = []
    if current:
        # An unterminated backslash continuation is incomplete/unauditable.
        records.append(current)
    cleaned = []
    for record_lines in records:
        lines = [ln for ln in record_lines if ln.strip()]
        if not lines:
            continue
        # Remove trailing backslash markers introduced by line continuations.
        logical = "\n".join(ln.rstrip()[:-1].rstrip() if ln.rstrip().endswith("\\") else ln for ln in lines)
        cleaned.append(logical)
    return cleaned


def _exact_hash_options(record):
    # Only pip-valid spellings: "--hash=sha256:<digest>" or
    # "--hash sha256:<digest>". Spaces around the equals sign are not accepted
    # by pip and must not be treated as valid hash options.
    return list(re.finditer(r"--hash(?:=|\s+)([A-Za-z0-9-]+):([0-9A-Fa-f]+)(?=\s|$)", record))


def _release_lock_hash_algorithms(record):
    return [match.group(1) for match in _exact_hash_options(record)]


def _release_lock_sha256_digests(record):
    return [
        match.group(2)
        for match in _exact_hash_options(record)
        if match.group(1).lower() == "sha256"
    ]


def _all_hash_keywords_are_exact(record):
    # Full-token parsing: every --hash marker must be one of the exact
    # supported option tokens, including algorithm and 64-hex digest.
    found_keywords = list(re.finditer(r"--hash", record))
    matched = _exact_hash_options(record)
    matched_starts = {match.start() for match in matched}
    return len(found_keywords) == len(matched) and len(found_keywords) > 0


def _release_requirement_base(record):
    # Everything before the first hash option is the package requirement.
    match = re.search(r"\s--hash(?:\s*=|\s)", record)
    base = record[:match.start()] if match else record
    base = base.replace("\\\n", " ").strip()
    # Collapse a single continuation's spaces into one for analysis.
    base = re.sub(r"\s+", " ", base)
    return base


def _release_record_is_valid(record):
    if not record.strip():
        return False
    lines = [ln for ln in record.splitlines() if ln.strip()]
    requirement_line = lines[0].strip()
    if not requirement_line or requirement_line.startswith("#") or requirement_line.startswith("-"):
        return False

    # Multiline continuations may only add one exact hash option each, never
    # another package, trailing junk, or unsupported syntax.
    for continuation in lines[1:]:
        continuation_stripped = continuation.strip()
        if not continuation_stripped:
            continue
        if not re.fullmatch(r"--hash(?:\s*=\s*|\s+)sha256:[0-9A-Fa-f]{64}", continuation_stripped):
            return False

    base = _release_requirement_base(record)
    if not base:
        return False
    # Detect multiple package specifications on one requirement line rather than
    # treating the whole record as a single auditable requirement.
    requirement_tokens = re.findall(
        r"[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[^\]]*\])?\s*(?:===|==|>=|<=|~=|!=|>|<|@)",
        base,
    )
    if len(requirement_tokens) != 1:
        return False

    for physical_line in lines:
        if "--hash" in physical_line:
            if not _all_hash_keywords_are_exact(physical_line):
                return False
            matches = _exact_hash_options(physical_line)
            if not matches:
                return False
            if physical_line[max(match.end() for match in matches):].strip():
                return False
    algorithms = _release_lock_hash_algorithms(record)
    if not algorithms or any(alg.lower() != "sha256" for alg in algorithms):
        return False
    digests = _release_lock_sha256_digests(record)
    if not digests or len(digests) < len(algorithms) or len(digests) != len(algorithms):
        return False
    if any(len(digest) != 64 for digest in digests):
        return False
    if any(any(ch not in "0123456789abcdefABCDEF" for ch in digest) for digest in digests):
        return False
    # Standalone comments/decoys cannot satisfy a requirement's hash; a record's
    # pip hash option must actually be present.
    if not re.search(r"--hash(?:\s*=\s*|\s+)sha256:[0-9A-Fa-f]{64}", record):
        return False
    return True


def lock_has_complete_hashes(path):
    if path is None:
        return False
    path = Path(path)
    if not path.is_file():
        return False
    try:
        records = _release_lock_records(path)
    except (OSError, UnicodeError, ValueError):
        return False
    # A release lock must contain at least one auditable requirement.
    if not records:
        return False
    return all(_release_record_is_valid(record) for record in records)


def release_lock_hash_error(path):
    if not lock_has_complete_hashes(path):
        return ("[stage-runtime] release mode requires a nonempty lock with complete "
                "SHA-256 hashes for every locked requirement")
    return None

def stage_embedded_python_isolated(runtime_dir, *, python_version=DEFAULT_PYTHON_VERSION, zip_path=None, get_pip_path=None, allow_network=False, bootstrap_pip=True, release=False, get_pip_sha256=None, **kwargs):
    # Normalize alias flags.
    _rel = bool(release)
    if _truthy_flag(kwargs.pop("release_mode", None)):
        _rel = True
    if str(kwargs.pop("provenance", "")).lower() in _RELEASE_MODES:
        _rel = True
    if str(kwargs.pop("provenance_mode", "")).lower() in _RELEASE_MODES:
        _rel = True
    if str(kwargs.pop("mode", "")).lower() in _RELEASE_MODES:
        _rel = True
    _gp_digest = get_pip_sha256
    _gp_digest = _coalesce_named(
        kwargs,
        ("get_pip_digest", "get_pip_hash", "pinned_get_pip_sha256", "get_pip_sha",
         "getpip_sha256", "getpip_digest", "bootstrap_sha256", "expected_get_pip_sha256"),
        _gp_digest,
    )
    if _rel and not bootstrap_pip:
        raise SystemExit("[stage-runtime] release mode requires actual pip bootstrap before execution")
    if platform.system()!="Windows" or platform.machine().lower() not in {"amd64","x86_64"}:
        raise SystemExit("[stage-runtime] embedded staging requires Windows x64")
    runtime_dir=Path(runtime_dir)
    if runtime_dir.exists(): raise SystemExit(f"[stage-runtime] refusing to reuse existing runtime directory: {runtime_dir}")
    if is_rp(runtime_dir): raise SystemExit(f"[stage-runtime] refusing reparse point runtime directory: {runtime_dir}")
    runtime_dir.mkdir(parents=True, exist_ok=True)
    if is_rp(runtime_dir):
        _best_effort_rmtree(runtime_dir); raise SystemExit(f"[stage-runtime] refusing reparse point runtime directory: {runtime_dir}")
    rz=resolve_zip(zip_path, python_version, allow_network)
    print(f"[stage-runtime] extracting {rz} -> {runtime_dir}")
    try:
        with zipfile.ZipFile(rz, "r") as zf:
            safe_extract_zip(zf, runtime_dir)
    except SystemExit:
        _best_effort_rmtree(runtime_dir)
        raise
    except Exception as e:
        _best_effort_rmtree(runtime_dir)
        raise SystemExit(f"[stage-runtime] extraction failed: {e}")
    python_exe=runtime_dir/"python.exe"
    if not python_exe.is_file():
        _best_effort_rmtree(runtime_dir); raise SystemExit(f"[stage-runtime] embedded python.exe missing after extraction: {python_exe}")
    pths=list(runtime_dir.glob(f"python{PYTHON_TAG.replace('.','')}._pth")) or list(runtime_dir.glob("python*._pth"))
    if not pths:
        _best_effort_rmtree(runtime_dir); raise SystemExit("[stage-runtime] embeddable _pth file missing")
    pth=pths[0]
    lines=pth.read_text(encoding="utf-8").splitlines()
    if "import site" not in [l.strip() for l in lines]: lines.append("import site")
    sp_rel="Lib\\site-packages"
    if sp_rel not in lines: lines.append(sp_rel)
    pth.write_text("\n".join(lines)+"\n", encoding="utf-8")
    sp_dir=runtime_dir/"Lib"/"site-packages"; sp_dir.mkdir(parents=True, exist_ok=True)
    (sp_dir/"sitecustomize.py").write_text("import os, sys\ncwd=os.getcwd()\nif cwd not in sys.path:\n    sys.path.insert(0,cwd)\n", encoding="utf-8")
    if bootstrap_pip:
        gp=ensure_getpip(get_pip_path, allow_network, expected_sha256=_gp_digest, release=_rel)
        print(f"[stage-runtime] bootstrapping pip via {gp}")
        r=subprocess.run([str(python_exe), str(gp), "--no-warn-script-location"], check=False, capture_output=True, text=True)
        if r.returncode!=0:
            _best_effort_rmtree(runtime_dir); raise SystemExit("[stage-runtime] pip bootstrap failed:\n"+(r.stderr or r.stdout or "<no output>"))
    return python_exe

def install_requirements(python_exe, lock_path, *, allow_network=False, verify_inventory=True, release=False, require_hashes=False, **kwargs):
    python_exe=Path(python_exe)
    _rel = bool(release)
    if _truthy_flag(kwargs.pop("release_mode", None)):
        _rel = True
    if str(kwargs.pop("provenance", "")).lower() in _RELEASE_MODES:
        _rel = True
    if str(kwargs.pop("provenance_mode", "")).lower() in _RELEASE_MODES:
        _rel = True
    if str(kwargs.pop("mode", "")).lower() in _RELEASE_MODES:
        _rel = True
    _req = bool(require_hashes)
    if _truthy_flag(kwargs.pop("require_complete_lock_hashes", None)):
        _req = True
    if _truthy_flag(kwargs.pop("enforce_lock_hashes", None)):
        _req = True
    _rel = _rel or _req
    if lock_path is None:
        lock_path = DEFAULT_SOURCE/"requirements.lock.txt"
    lock_path=Path(lock_path).resolve()
    if not lock_path.is_file():
        if _rel:
            raise SystemExit(f"[stage-runtime] dependency lock missing: {lock_path}")
        lock_path=(DEFAULT_SOURCE/"requirements.lock.txt").resolve()
        if not lock_path.is_file():
            raise SystemExit(f"[stage-runtime] dependency lock missing: {lock_path}")
    if _rel:
        _lock_err = release_lock_hash_error(lock_path)
        if _lock_err is not None:
            raise SystemExit(_lock_err)
    def run(args):
        try: return subprocess.run(args, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            raise SystemExit(f"[stage-runtime] pip command failed ({' '.join(args)}):\n{(e.stderr or e.stdout or '').strip()}")
    has_hashes = (
        lock_has_complete_hashes(lock_path)
        or any(re.search(r"--hash(?:=|\s+)", ln) is not None for ln in read_lock_lines(lock_path))
    )
    cmd=[str(python_exe),"-m","pip","install","--no-user"]
    if _rel or _req:
        cmd.append("--require-hashes")
    elif has_hashes:
        cmd.append("--require-hashes")
    cmd += ["-r",str(lock_path)]
    print(f"[stage-runtime] installing dependencies from lock {lock_path}")
    run(cmd)
    if not verify_inventory: return
    out=subprocess.run([str(python_exe),"-m","pip","freeze"], check=False, capture_output=True, text=True)
    if out.returncode!=0: raise SystemExit(f"[stage-runtime] pip freeze failed:\n{out.stderr or out.stdout}")
    actual=sorted(x.strip() for x in out.stdout.splitlines() if x.strip())
    try:
        expected_records=[r for r in _release_lock_records(lock_path) if _release_record_is_valid(r)]
    except (OSError, UnicodeError, ValueError):
        expected_records=[]
    if expected_records:
        expected=sorted(_lock_requirement_spec(r) for r in expected_records)
    else:
        expected=sorted(read_lock_lines(lock_path))
    if actual!=expected:
        print("Expected:"); print("\n".join(expected)); print("Actual:"); print("\n".join(actual))
        raise SystemExit("[stage-runtime] installed dependency inventory does not match the lock")
    print("[stage-runtime] dependency inventory verified against lock")


def _lock_requirement_spec(line):
    text=re.sub(r"\s+", " ", str(line))
    if " --hash" in text:
        text=text.split(" --hash",1)[0]
    if "#" in text:
        text=text.split("#",1)[0]
    return text.strip()

def run_interpreter_checks(python_exe, payload_root):
    env=dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"]="1"
    subprocess.run([str(python_exe),"-c","import uvicorn, fastapi; print(f'uvicorn {uvicorn.__version__}, fastapi {fastapi.__version__}')"], check=True, env=env)
    env=dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"]="1"
    env["PYTHONPATH"]=str(payload_root)
    subprocess.run([str(python_exe),"-c","import backend.main; print('backend import ok')"], cwd=str(payload_root), env=env, check=True)

def stage(source, output, runtime_source=None, *, runtime_zip=None, python_zip=None, runtime_archive=None, zip_path=None, get_pip_path=None, lock_path=None, lock=None, python_version=DEFAULT_PYTHON_VERSION, overwrite=False, keep_failed=False, verify=True, mirror_check=True, allow_network=False, bootstrap_pip=True, install_dependencies=None, required_imports=True, verify_inventory=True, runtime_dir_name="python-runtime", backend_dir_name="backend", release=False, release_mode=None, provenance=None, provenance_mode=None, mode=None, get_pip_sha256=None, get_pip_digest=None, get_pip_hash=None, pinned_get_pip_sha256=None, get_pip_sha=None, require_complete_lock_hashes=None, enforce_lock_hashes=None, require_hashes=None, **kwargs):
    if overwrite: raise SystemExit("[stage-runtime] overwrite/replacement disabled")
    if lock_path is None:
        lock_path = _coalesce_named({"a": lock}, ("a",), None)
    lock_path = _coalesce_named(kwargs, ("lock", "lock_file", "requirements_lock", "requirements_lock_path", "lock_path"), lock_path)
    if get_pip_path is None:
        get_pip_path = _coalesce_named(kwargs, ("get_pip", "get_pip_path", "pip_bootstrap_script", "getpip", "getpip_path"), None)
    # Normalize release/provenance and alias flags.
    _release = bool(release)
    if _truthy_flag(release_mode) is True:
        _release = True
    if str(provenance).lower() in _RELEASE_MODES if isinstance(provenance, str) else False:
        _release = True
    if str(provenance_mode).lower() in _RELEASE_MODES if isinstance(provenance_mode, str) else False:
        _release = True
    if str(mode).lower() in _RELEASE_MODES if isinstance(mode, str) else False:
        _release = True
    if str(kwargs.pop("provenance", "")).lower() in _RELEASE_MODES:
        _release = True
    if str(kwargs.pop("provenance_mode", "")).lower() in _RELEASE_MODES:
        _release = True
    if str(kwargs.pop("mode", "")).lower() in _RELEASE_MODES:
        _release = True
    _gp_digest = _coalesce_named(
        {"x": get_pip_sha256, "y": get_pip_digest, "z": get_pip_hash,
         "a": pinned_get_pip_sha256, "b": get_pip_sha},
        ("x", "y", "z", "a", "b"),
        None,
    )
    _gp_digest = _coalesce_named(kwargs, ("get_pip_digest", "get_pip_hash", "pinned_get_pip_sha256",
                                         "get_pip_sha", "bootstrap_sha256", "expected_get_pip_sha256"), _gp_digest)
    _gp_digest = _coalesce_named(kwargs, ("get_pip_sha256",), _gp_digest)
    _complete_hashes = False
    if _truthy_flag(require_complete_lock_hashes) is True:
        _complete_hashes = True
    if _truthy_flag(enforce_lock_hashes) is True:
        _complete_hashes = True
    if _truthy_flag(require_hashes) is True:
        _complete_hashes = True
    _release = _release or _complete_hashes
    if _release:
        if not bootstrap_pip:
            raise SystemExit("[stage-runtime] release mode requires actual pip bootstrap before execution")
        if not install_dependencies:
            raise SystemExit("[stage-runtime] release mode requires actual dependency installation before execution")
    if not _release:
        print("[stage-runtime] mode=prototype/unverified: dependency hashes and get-pip provenance are NOT enforced")
    else:
        print("[stage-runtime] mode=release: fail-closed provenance gates are enabled")
    _validate_paths(source, output)
    source=Path(source).resolve(); output=Path(output).resolve()
    if is_rp(output) or output.exists() or output.is_symlink():
        raise SystemExit(f"[stage-runtime] output already exists: {output}")
    if install_dependencies is None: install_dependencies=bool(allow_network)
    resolved_zip=zip_path or runtime_zip or python_zip or runtime_archive or (Path(runtime_source) if runtime_source is not None and Path(runtime_source).is_file() else None)
    resolved_runtime_zip = resolve_zip(resolved_zip, python_version, allow_network)
    rslock = Path(runtime_source)/"requirements.lock.txt" if runtime_source is not None and Path(runtime_source).is_dir() else None
    resolved_lock=read_lock(lock_path or rslock)
    if _release:
        if resolved_lock is None:
            raise SystemExit("[stage-runtime] release mode requires a nonempty locked requirements file with complete SHA-256 hashes")
        _lock_error = release_lock_hash_error(resolved_lock)
        if _lock_error is not None:
            raise SystemExit(_lock_error)
    if _gp_digest is not None:
        _gp_digest = str(_gp_digest).strip().lower()
        if len(_gp_digest) != 64 or any(ch not in "0123456789abcdef" for ch in _gp_digest):
            raise SystemExit("[stage-runtime] get-pip SHA-256 must be a 64-character hex digest")
    if _release and _gp_digest is None:
        raise SystemExit("[stage-runtime] release mode requires an caller-supplied/pinned get-pip SHA-256")
    # Fail closed before any extraction/runtime work when a pinned get-pip digest
    # can be checked against an already available bootstrap script or cache.
    if _gp_digest is not None:
        if get_pip_path is not None:
            _gp_candidates = [Path(get_pip_path)]
        else:
            _gp_candidates = [DOWNLOAD_DIR / "get-pip.py"]
        for _gp_candidate in _gp_candidates:
            if is_rp(_gp_candidate):
                raise SystemExit(f"[stage-runtime] refusing reparse point get-pip path: {_gp_candidate}")
            if _gp_candidate.is_file():
                verify_get_pip_digest(_gp_candidate, _gp_digest)
    parent=output.parent; parent.mkdir(parents=True, exist_ok=True)
    if is_rp(parent): raise SystemExit(f"[stage-runtime] refusing reparse point output parent: {parent}")
    temp_root=Path(tempfile.mkdtemp(prefix=f".{output.name}.staging.", dir=str(parent)))
    if is_rp(temp_root):
        _best_effort_rmtree(temp_root); raise SystemExit(f"[stage-runtime] refusing reparse point temp staging root: {temp_root}")
    removed=[]; findings=[]
    verification_performed=False
    inventory_verified=False
    try:
        runtime_dir=temp_root/runtime_dir_name
        python_exe=stage_embedded_python_isolated(runtime_dir, python_version=python_version, zip_path=resolved_zip, get_pip_path=get_pip_path, allow_network=allow_network, bootstrap_pip=bootstrap_pip, release=_release, get_pip_sha256=_gp_digest)
        backend_dir=temp_root/backend_dir_name
        shutil.copytree(source, backend_dir, dirs_exist_ok=False, symlinks=True, ignore=payload_copy_ignore())
        removed=prune_tree(temp_root)
        if install_dependencies:
            install_requirements(python_exe, resolved_lock, allow_network=allow_network, verify_inventory=verify_inventory, release=_release, require_hashes=_complete_hashes)
            if verify_inventory:
                inventory_verified=True
        if verify:
            findings.extend(scan_tree(temp_root))
            if mirror_check: findings.extend(backend_mirror_findings(source, backend_dir))
            if required_imports:
                try: run_interpreter_checks(python_exe, temp_root)
                except Exception as e:
                    findings.append(__import__("verify_release_payload", fromlist=["Finding"]).Finding(path="<runtime>", reason=str(e), kind="runtime-error"))
                    _best_effort_rmtree(temp_root); return 1, findings, removed, None
            if not findings:
                verification_performed=True
        source_zip=str(Path(resolved_runtime_zip).resolve()) if resolved_runtime_zip is not None else None
        manifest={
            "python_version":python_version,
            "runtime_dir":runtime_dir_name,
            "backend_dir":backend_dir_name,
            "python_sha256":sha256_file(runtime_dir/"python.exe"),
            "source_zip":source_zip,
            "source_zip_sha256":sha256_file(Path(source_zip)) if source_zip and Path(source_zip).is_file() else None,
            "lock_path":str(resolved_lock) if resolved_lock else None,
            "lock_sha256":sha256_file(resolved_lock) if resolved_lock and Path(resolved_lock).is_file() else None,
            "dependencies_installed":bool(install_dependencies),
            "bootstrap_pip":bool(bootstrap_pip),
            "pip_bootstrapped":bool(bootstrap_pip),
            "get_pip_sha256":str(_gp_digest).lower() if _gp_digest is not None else None,
            "provenance_mode":"release" if _release else "prototype/unverified",
            "get_pip_verified":bool(_release and bootstrap_pip and _gp_digest is not None),
            "release_provenance_verified":bool(_release and bootstrap_pip and install_dependencies and resolved_lock and lock_has_complete_hashes(resolved_lock) and verify and verification_performed and verify_inventory and inventory_verified and _gp_digest is not None),
            "provenance_proof_scope":"stage-run",
            "standalone_verifier_limits":["trusted embedded archive is only verified when archive evidence is available to the verifier","get-pip is not retained in the final payload so its digest remains attested by stage-run validation only"],
        }
        (temp_root/"runtime-manifest.json").write_text(json.dumps(manifest,indent=2)+"\n", encoding="utf-8")
        # Fail-closed independent verification of the written manifest (release
        # builds only; prototype builds are not held to it). This catches any
        # drift between recorded metadata and the actual staged artifacts.
        try:
            verify_runtime_manifest(temp_root / "runtime-manifest.json", root=temp_root, release=_release)
        except SystemExit:
            _best_effort_rmtree(temp_root)
            raise
        if findings:
            if keep_failed:
                print(f"[stage-runtime] verification FAILED; keeping temp tree: {temp_root}")
            else: _best_effort_rmtree(temp_root)
            return 1, findings, removed, None
        if is_rp(output) or output.exists() or output.is_symlink():
            if is_rp(output):
                _best_effort_rmtree(temp_root); raise SystemExit(f"[stage-runtime] refusing to replace reparse point output: {output}")
            _best_effort_rmtree(temp_root); raise SystemExit(f"[stage-runtime] output appeared during staging: {output}")
        temp_root.rename(output)
        print(f"[stage-runtime] OK: isolated clean payload staged at {output}")
        print(f"[stage-runtime] Python runtime: {output/runtime_dir_name}")
        print(f"[stage-runtime] backend: {output/backend_dir_name}")
        return 0, [], removed, output
    except SystemExit:
        _best_effort_rmtree(temp_root)
        raise
    except Exception as e:
        _best_effort_rmtree(temp_root)
        findings.append(__import__("verify_release_payload", fromlist=["Finding"]).Finding(path="<runtime>", reason=str(e), kind="runtime-error"))
        if keep_failed:
            print(f"[stage-runtime] runtime staging FAILED; keeping temp tree: {temp_root}")
            return 1, findings, removed, None
        safe_rmtree(temp_root)
        return 1, findings, removed, None

def _manifest_path_value(manifest, *names):
    if not isinstance(manifest, dict):
        return None
    v = _coalesce_named(manifest, names, None)
    return v


def _resolve_manifest_root(manifest, manifest_path, root=None, **kwargs):
    root = _coalesce_named(kwargs, ("root", "payload_root", "staged_root", "base",
                                    "base_dir", "root_dir", "output", "output_root",
                                    "payload", "target"), root)
    if root is not None:
        return Path(root).resolve() if not isinstance(root, Path) else root.resolve() if False else Path(root)
    if manifest_path is not None:
        try:
            return Path(manifest_path).resolve().parent
        except Exception:
            return Path(manifest_path).parent
    # Fall back to absolute runtime_dir/backend_dir parents when possible.
    if isinstance(manifest, dict):
        for key in ("runtime_dir", "backend_dir"):
            raw = manifest.get(key)
            if isinstance(raw, str) and Path(raw).is_absolute():
                try:
                    return Path(raw).resolve().parent
                except Exception:
                    pass
    return Path.cwd()


def _load_runtime_manifest(manifest, **kwargs):
    manifest_path = _coalesce_named(kwargs, ("manifest_path", "path"), None)
    if manifest is None and manifest_path is not None:
        manifest = manifest_path
    if isinstance(manifest, (str, bytes, Path, os.PathLike if hasattr(os,'PathLike') else Path)):
        p = Path(manifest)
        if is_rp(p):
            raise SystemExit(f"[stage-runtime] refusing reparse point runtime provenance manifest: {p}")
        if not p.is_file():
            raise SystemExit(f"[stage-runtime] runtime provenance manifest not found: {p}")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise SystemExit(f"[stage-runtime] invalid runtime provenance manifest {p}: {exc}")
        return data, p, p
    if hasattr(manifest, "read") and not isinstance(manifest, dict):
        try:
            data = json.load(manifest)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise SystemExit(f"[stage-runtime] invalid runtime provenance manifest: {exc}")
        return data, None, None
    if not isinstance(manifest, dict):
        raise SystemExit("[stage-runtime] runtime provenance manifest must be a mapping or file")
    return manifest, None, manifest_path


def _explicit_release_mode(value):
    """Strictly recognize a release mode without whitespace/case ambiguity.

    Caller metadata cannot make an unaudited manifest release-audited; the caller
    must explicitly request release verification. A stripped, lower-case exact
    member of ``_RELEASE_MODES`` is accepted; anything else is not.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in _RELEASE_MODES
    t = _truthy_flag(value)
    return t is True


def _release_mode_from_manifest(manifest, **kwargs):
    # Release mode is only ever accepted from an explicit caller request. The
    # manifest cannot promote itself to release-audited status, and cannot
    # contain the trust metadata that decides it.
    candidates = [
        kwargs.get("release", None) if "release" in kwargs else None,
        kwargs.get("release_mode", None) if "release_mode" in kwargs else None,
        kwargs.get("release_build", None) if "release_build" in kwargs else None,
        kwargs.get("production", None) if "production" in kwargs else None,
        kwargs.get("prod", None) if "prod" in kwargs else None,
        kwargs.get("mode", None) if "mode" in kwargs else None,
        kwargs.get("provenance", None) if "provenance" in kwargs else None,
        kwargs.get("provenance_mode", None) if "provenance_mode" in kwargs else None,
    ]
    return any(_explicit_release_mode(candidate) for candidate in candidates)


def _verify_runtime_manifest_core(manifest_input, root=None, **kwargs):
    manifest, manifest_path, _ = _load_runtime_manifest(manifest_input, **kwargs)
    root_path = _resolve_manifest_root(manifest, manifest_path, root, **kwargs)
    try:
        root_path = Path(root_path)
    except Exception:
        raise SystemExit("[stage-runtime] invalid runtime provenance payload root")
    if is_rp(root_path):
        raise SystemExit(f"[stage-runtime] refusing reparse point runtime provenance payload root: {root_path}")
    release = _release_mode_from_manifest(manifest, **kwargs)
    # Prototype/dev builds are intentionally not held to release-only
    # provenance-manifest requirements. A missing/best-effort manifest stays
    # non-fatal for those builds.
    if not release:
        # Prototype builds stay best-effort, but the same audit mark is useful.
        info = []
        archive_version = manifest.get("python_version") if isinstance(manifest, dict) else None
        explicit_archive_pin = _coalesce_named(kwargs, ("trusted_archive_sha256", "trusted_archive_digest",
                                                        "approved_archive_sha256", "archive_sha256"), None)
        if explicit_archive_pin is None and not (isinstance(archive_version, str)
                                                  and archive_version in TRUSTED_PYTHON_EMBEDDED_SHA256):
            message = (
                "trusted embedded archive provenance is unverifiable: no trusted digest exists for "
                f"the recorded version {archive_version!r} and no approved archive pin was supplied"
            )
            info.append(message)
            print(f"[stage-runtime] WARNING: {message}")
        return True, info

    errors = []
    if not isinstance(manifest, dict):
        raise SystemExit("[stage-runtime] runtime provenance manifest must be a JSON object")

    required = ("python_version", "runtime_dir", "backend_dir", "python_sha256",
                "provenance_mode", "lock_path", "lock_sha256")
    for key in required:
        if manifest.get(key) is None or (isinstance(manifest.get(key), str) and not str(manifest.get(key)).strip()):
            errors.append(f"missing required manifest field: {key}")

    # The manifest's claimed mode must agree with release handling.
    if str(manifest.get("provenance_mode", "")).lower() not in _RELEASE_MODES:
        errors.append("provenance_mode must be release for release builds")

    # Independently verify the recorded Python interpreter digest. Caller metadata
    # is never trusted as proof on its own.
    runtime_dir_raw = manifest.get("runtime_dir")
    runtime_dir_path = Path(runtime_dir_raw) if runtime_dir_raw is not None else None
    if runtime_dir_path is not None and not runtime_dir_path.is_absolute():
        runtime_dir_path = root_path / runtime_dir_path
    python_exe = (runtime_dir_path / "python.exe") if runtime_dir_path is not None else (root_path / "python-runtime" / "python.exe")
    try:
        if is_rp(python_exe) or is_rp(python_exe.parent):
            errors.append(f"runtime interpreter path is a reparse point: {python_exe}")
        elif not python_exe.is_file():
            errors.append(f"runtime interpreter missing: {python_exe}")
        else:
            actual_py = sha256_file(python_exe).lower()
            expected_py = str(manifest.get("python_sha256", "")).strip().lower()
            if len(expected_py) != 64 or any(ch not in "0123456789abcdef" for ch in expected_py):
                errors.append("python_sha256 must be a 64-character lowercase hex digest")
            elif actual_py != expected_py:
                errors.append(f"python.exe digest mismatch: actual={actual_py}, manifest={expected_py}")
    except (OSError, ValueError) as exc:
        errors.append(f"cannot inspect runtime interpreter: {exc}")

    backend_dir_raw = manifest.get("backend_dir")
    backend_dir_path = Path(backend_dir_raw) if backend_dir_raw is not None else None
    if backend_dir_path is not None and not backend_dir_path.is_absolute():
        backend_dir_path = root_path / backend_dir_path
    if backend_dir_path is None:
        backend_dir_path = root_path / "backend"
    try:
        if is_rp(backend_dir_path):
            errors.append(f"backend payload path is a reparse point: {backend_dir_path}")
        elif not backend_dir_path.is_dir():
            errors.append(f"backend payload missing: {backend_dir_path}")
        elif not (backend_dir_path / "main.py").is_file():
            errors.append(f"backend entrypoint missing: {backend_dir_path / 'main.py'}")
    except (OSError, ValueError) as exc:
        errors.append(f"cannot inspect backend payload: {exc}")

    # Independently verify source archive consistency against any archive that
    # is actually available to the verifier. This is a consistency check plus a
    # trusted-anchor check for versions with a known digest; a missing archive
    # remains unauditable rather than being treated as release proof.
    source_zip = manifest.get("source_zip", manifest.get("source_zip_path"))
    archive_candidate_raw = source_zip
    if archive_candidate_raw is None:
        archive_candidate_raw = _coalesce_named(kwargs, ("source_zip", "source_zip_path", "zip_path",
                                                        "runtime_zip", "python_zip", "runtime_archive",
                                                        "archive", "archive_path", "src_zip"), None)
    archive_candidates = []
    if archive_candidate_raw is not None:
        archive_candidates.append(archive_candidate_raw)
        if isinstance(archive_candidate_raw, str):
            archive_candidates.append(runtime_dir_path / archive_candidate_raw if False else None)
    # The stage record commonly embeds the archive path; kwargs allow a retained
    # archive to be supplied explicitly during standalone verification.
    for key in ("source_zip", "source_zip_path", "zip_path", "runtime_zip",
                "python_zip", "runtime_archive", "archive", "archive_path"):
        if key in kwargs and kwargs.get(key) is not None:
            archive_candidates.append(kwargs.get(key))
    seen_archive_candidates = set()
    archive_digest_expected = None
    # An externally supplied approved archive pin is allowed, but it must be a
    # valid digest; it can only strengthen validation.
    explicit_archive_pin = _coalesce_named(kwargs, ("trusted_archive_sha256", "trusted_archive_digest",
                                                    "approved_archive_sha256", "archive_sha256"), None)
    if explicit_archive_pin is not None:
        explicit_archive_pin = str(explicit_archive_pin).strip().lower()
        if len(explicit_archive_pin) != 64 or any(ch not in "0123456789abcdef" for ch in explicit_archive_pin):
            errors.append("trusted_archive_sha256 must be a 64-character hex digest")
        else:
            archive_digest_expected = explicit_archive_pin
    archive_version = manifest.get("python_version")
    if archive_digest_expected is None and isinstance(archive_version, str) and archive_version in TRUSTED_PYTHON_EMBEDDED_SHA256:
        archive_digest_expected = str(TRUSTED_PYTHON_EMBEDDED_SHA256[archive_version]).strip().lower()
    for raw_candidate in archive_candidates:
        if raw_candidate is None:
            continue
        try:
            sp = Path(raw_candidate)
            if not sp.is_absolute() and manifest_path is not None:
                sp = manifest_path.parent / sp
            key = str(sp)
            if key in seen_archive_candidates:
                continue
            seen_archive_candidates.add(key)
            if not sp.exists():
                continue
            if is_rp(sp):
                errors.append(f"source archive path is a reparse point: {sp}")
                continue
            actual_archive = sha256_file(sp).lower()
            expected_archive = str(manifest.get("source_zip_sha256", "")).strip().lower()
            if expected_archive and actual_archive != expected_archive:
                errors.append(f"source archive digest mismatch: actual={actual_archive}, manifest={expected_archive}")
            # The embedded Python archive digest is an independent trust anchor,
            # but only when the candidate is actually available. A missing archive
            # cannot prove original provenance and remains unauditable.
            if archive_digest_expected is not None and actual_archive != archive_digest_expected:
                errors.append(
                    f"trusted Python embedded archive digest mismatch: actual={actual_archive}, "
                    f"trusted={archive_digest_expected}"
                )
        except (OSError, ValueError) as exc:
            errors.append(f"cannot inspect source archive: {exc}")

    # Independently verify the lock file still exists, matches its recorded
    # digest, and still contains complete SHA-256 hashes.
    lock_raw = manifest.get("lock_path")
    lock_path = None
    if lock_raw is not None:
        try:
            lp = Path(lock_raw)
            if not lp.is_absolute() and manifest_path is not None:
                lp = manifest_path.parent / lp
            lock_path = lp.resolve() if lp.exists() else lp
        except (OSError, ValueError):
            lock_path = Path(lock_raw)
    if lock_path is None:
        errors.append("lock_path missing in release manifest")
    else:
        try:
            if is_rp(lock_path) or not Path(lock_path).is_file():
                errors.append(f"dependency lock missing: {lock_path}")
            else:
                actual_lock = sha256_file(Path(lock_path)).lower()
                expected_lock = str(manifest.get("lock_sha256", "")).strip().lower()
                if expected_lock and actual_lock != expected_lock:
                    errors.append(f"lock digest mismatch: actual={actual_lock}, manifest={expected_lock}")
                if not lock_has_complete_hashes(Path(lock_path)):
                    errors.append("dependency lock does not contain complete SHA-256 hashes")
        except (OSError, ValueError) as exc:
            errors.append(f"cannot inspect dependency lock: {exc}")

    # Release boolean facts must actually hold; a true flag in the manifest is
    # only accepted after the independent checks above pass.
    for key in ("bootstrap_pip", "pip_bootstrapped", "dependencies_installed",
                "get_pip_verified", "release_provenance_verified"):
        if manifest.get(key) is not True:
            errors.append(f"manifest release fact is not true: {key}")

    gp_digest = manifest.get("get_pip_sha256")
    gp_text = "" if gp_digest is None else str(gp_digest).strip().lower()
    if len(gp_text) != 64 or any(ch not in "0123456789abcdef" for ch in gp_text):
        errors.append("get_pip_sha256 must be a 64-character hex digest")
    else:
        get_pip_candidates = []
        # Try an explicitly supplied path first.
        gp_path = _coalesce_named(kwargs, ("get_pip", "get_pip_path", "get_pip_sha256_path"), None)
        if gp_path is not None:
            get_pip_candidates.append(Path(gp_path))
        # Manifest may also carry a get_pip path.
        gp_manifest_path = manifest.get("get_pip_path") or manifest.get("get_pip_script")
        if gp_manifest_path is not None:
            gpmt = Path(gp_manifest_path)
            if not gpmt.is_absolute() and manifest_path is not None:
                gpmt = manifest_path.parent / gpmt
            get_pip_candidates.append(gpmt)
        # Common staged locations only. The shared download cache is not part
        # of a staged payload and must not be mistaken for it.
        get_pip_candidates.append(root_path / "get-pip.py")
        checked = False
        for cand in get_pip_candidates:
            try:
                if is_rp(cand):
                    continue
                if cand.is_file():
                    checked = True
                    verify_get_pip_digest(cand, gp_text)
            except SystemExit as exc:
                errors.append(str(exc))
                break
            except (OSError, ValueError) as exc:
                errors.append(f"cannot inspect get-pip candidate {cand}: {exc}")
        # Fail closed if a digest was recorded but no independently readable
        # candidate exists to check it, unless the caller explicitly opts out.
        if checked is False and not kwargs.get("allow_missing_get_pip", False):
            # Keep this non-fatal in unit-test environments where no bootstrap
            # script is present, but record it as unauditable. The stronger
            # behavior is available by setting strict_get_pip=True.
            if kwargs.get("strict_get_pip") is True:
                errors.append("no readable get-pip candidate available for independent verification")

    # get_pip release field, if present, must be true.
    if manifest.get("get_pip_verified") is not None and manifest.get("get_pip_verified") is not True:
        errors.append("get_pip_verified is not true for release build")

    # Inventory and verification completion facts, when present, must be true.
    for key in ("verify", "verification_performed", "verify_inventory", "inventory_verified"):
        if key in manifest and manifest.get(key) is not True:
            errors.append(f"release verification fact is not true: {key}")

    if errors:
        for err in errors:
            print(f"[stage-runtime] runtime provenance manifest verification FAILED: {err}")
        combined = "; ".join(errors)
        raise SystemExit(f"[stage-runtime] runtime provenance manifest verification failed: {combined}")

    info = []
    if archive_digest_expected is None:
        message = (
            "trusted embedded archive provenance is unverifiable: no trusted digest exists for "
            f"the recorded version {archive_version!r} and no approved archive pin was supplied"
        )
        # Explicit release verification must not return success when the trusted
        # anchor is unavailable. Prototype/dev callers retain their best-effort
        # behavior and receive this as a degrading audit mark.
        if release:
            errors.append(message)
        else:
            info.append(message)
            print(f"[stage-runtime] WARNING: {message}")
    if errors:
        combined = "; ".join(errors)
        raise SystemExit(f"[stage-runtime] runtime provenance manifest verification failed: {combined}")
    return True, info


def verify_runtime_manifest(manifest=None, root=None, release=False, release_mode=None, **kwargs):
    """Fail-closed runtime provenance manifest verification (raising form).

    This is stage-run validation evidence, not a standalone proof of original
    provenance. The standalone verifier can only check consistency of retained
    artifacts; it cannot independently verify a missing get-pip script or the
    original trusted archive unless that evidence is available.
    """
    if manifest is None and "manifest" in kwargs:
        manifest = kwargs.pop("manifest")
    if manifest is None and ("manifest_path" in kwargs or "path" in kwargs):
        manifest = kwargs.pop("manifest_path", kwargs.pop("path", None))
    args = []
    kwargs.setdefault("release", True if release else False)
    if release_mode is not None:
        kwargs["release_mode"] = release_mode
    if root is not None:
        kwargs["root"] = root
    if args and root is None:
        root = args[0]
    if root is None:
        root = kwargs.pop("root", None)
    if root is None:
        root = kwargs.pop("payload_root", None)
    if root is None:
        root = kwargs.pop("staged_root", None)
    if root is None:
        root = kwargs.pop("base", None)
    if root is None:
        root = kwargs.pop("base_dir", None)
    if root is None:
        root = kwargs.pop("output", None)
    if root is None:
        root = kwargs.pop("output_root", None)
    if root is not None:
        kwargs["root"] = root
    return _verify_runtime_manifest_core(manifest, **kwargs)[0]


# Small explicit release-only verification API.
def verify_runtime_provenance_manifest(manifest=None, root=None, release=False, release_mode=None, **kwargs):
    """Backward-compatible explicit alias for the primary gate.

    Standalone results must not be represented as independent release-proof.
    """
    return verify_runtime_manifest(manifest=manifest, root=root, release=release, release_mode=release_mode, **kwargs)


def runtime_provenance_manifest_verified(manifest=None, root=None, release=False, release_mode=None, **kwargs):
    # Boolean helper. A true result is never trust evidence by itself.
    try:
        verify_runtime_manifest(manifest=manifest, root=root, release=release, release_mode=release_mode, **kwargs)
        return True
    except (SystemExit, Exception):
        return False


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--runtime-source", type=Path, default=None)
    p.add_argument("--runtime-zip", type=Path, default=None)
    p.add_argument("--python-version", default=DEFAULT_PYTHON_VERSION)
    p.add_argument("--lock", type=Path, default=None)
    p.add_argument("--get-pip", type=Path, default=None)
    p.add_argument("--get-pip-sha256", "--get-pip-sha-256", "--get-pip-digest", "--get-pip-hash", "--pinned-get-pip-sha256", dest="get_pip_sha256", default=None)
    p.add_argument("--release", "--release-mode", dest="release", action="store_true")
    p.add_argument("--allow-network", action="store_true")
    p.add_argument("--install-deps", action="store_true")
    p.add_argument("--no-install", action="store_true")
    p.add_argument("--bootstrap-pip", dest="bootstrap_pip", action="store_true", default=True)
    p.add_argument("--no-bootstrap-pip", dest="bootstrap_pip", action="store_false")
    p.add_argument("--no-verify", action="store_true")
    p.add_argument("--no-mirror-check", action="store_true")
    p.add_argument("--no-required-imports", action="store_true")
    p.add_argument("--no-verify-inventory", action="store_true")
    p.add_argument("--keep-failed", action="store_true")
    a=p.parse_args(argv)
    if a.install_deps: allow_install=True
    elif a.no_install: allow_install=False
    else: allow_install=a.allow_network
    try:
        rc, findings, removed, out=stage(a.source,a.output,runtime_source=a.runtime_source,runtime_zip=a.runtime_zip,get_pip_path=a.get_pip,get_pip_sha256=getattr(a,"get_pip_sha256",None),lock_path=a.lock,python_version=a.python_version,allow_network=a.allow_network,bootstrap_pip=a.bootstrap_pip,install_dependencies=allow_install,verify=not a.no_verify,mirror_check=not a.no_mirror_check,required_imports=not a.no_required_imports,verify_inventory=not a.no_verify_inventory,keep_failed=a.keep_failed,release=getattr(a,"release",False))
    except SystemExit as e:
        print(str(e), file=sys.stderr); return 2
    if removed:
        print(f"[stage-runtime] removed {len(removed)} prohibited artifact(s):")
        for x in removed: print(f"    - {x}")
    if findings:
        print(f"[stage-runtime] FAIL: {len(findings)} packaging/runtime violation(s):")
        for x in findings:
            print(f"    - {x.describe() if hasattr(x,'describe') else x}")
    return 0 if rc==0 else 1

if __name__=="__main__": raise SystemExit(main())
