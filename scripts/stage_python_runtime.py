#!/usr/bin/env python3
"""Stage a pinned embeddable CPython runtime + backend for Sythion (Synth).

Build output is written under src-tauri/resources so Tauri can bundle it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT
SRC_TAURI = APP_DIR / "src-tauri"
RESOURCES = SRC_TAURI / "resources"
RUNTIME_DIR = RESOURCES / "python-runtime"
BACKEND_DIR = RESOURCES / "backend"
LOCK_PATH = APP_DIR / "backend" / "requirements.lock.txt"
PACKAGING_REQ_PATH = APP_DIR / "backend" / "requirements-packaging.txt"
DOWNLOAD_DIR = APP_DIR / "build" / "staging" / "python-downloads"

# The payload policy lives in one place so the staging step and the release
# verification can never disagree about what may ship.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_release_payload import (  # noqa: E402
    payload_ignore_patterns,
    payload_copy_ignore,
    prune_tree,
)

PYTHON_VERSION = "3.13.16"
PYTHON_TAG = "3.13"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"


def fail(msg: str) -> None:
    raise SystemExit(f"[stage] ERROR: {msg}")


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return dest
    print(f"[stage] downloading {url}")
    with urllib.request.urlopen(url) as response, open(dest, "wb") as out:
        shutil.copyfileobj(response, out)
    return dest


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def stage_embedded_python() -> Path:
    if platform.system() != "Windows" or platform.machine().lower() not in {"amd64", "x86_64"}:
        fail("embedded staging requires Windows x64")

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    zip_name = f"python-{PYTHON_VERSION}-embed-amd64.zip"
    zip_path = DOWNLOAD_DIR / zip_name
    url = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{zip_name}"
    download(url, zip_path)

    if RUNTIME_DIR.exists():
        shutil.rmtree(RUNTIME_DIR)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[stage] extracting {zip_path} -> {RUNTIME_DIR}")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(RUNTIME_DIR)

    python_exe = RUNTIME_DIR / "python.exe"
    if not python_exe.is_file():
        fail("embedded python.exe missing after extraction")

    # Enable sitecustomize/site-packages in embeddable distribution.
    pth = RUNTIME_DIR / f"python{PYTHON_TAG.replace('.', '')}._pth"
    if not pth.exists():
        # fallback glob
        candidates = list(RUNTIME_DIR.glob("python*._pth"))
        if not candidates:
            fail("embeddable _pth file missing")
        pth = candidates[0]
    original = pth.read_text(encoding="utf-8")
    lines = [line for line in original.splitlines()]
    # ensure import site
    if "import site" not in [line.strip() for line in lines]:
        lines.append("import site")
    # ensure site-packages path
    sp_rel = "Lib\\site-packages"
    if sp_rel not in lines:
        lines.append(sp_rel)
    pth.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[stage] updated {pth.name}")

    # Embedded distributions ignore PYTHONPATH; ensure the process working
    # directory (where the bundled backend lives) is importable at runtime.
    sp_dir = RUNTIME_DIR / "Lib" / "site-packages"
    sp_dir.mkdir(parents=True, exist_ok=True)
    sc = sp_dir / "sitecustomize.py"
    sc.write_text(
        "import os, sys\n"
        "cwd = os.getcwd()\n"
        "if cwd not in sys.path:\n"
        "    sys.path.insert(0, cwd)\n",
        encoding="utf-8",
    )

    # bootstrap pip
    get_pip = DOWNLOAD_DIR / "get-pip.py"
    download(GET_PIP_URL, get_pip)
    subprocess.run([str(python_exe), str(get_pip), "--no-warn-script-location"], check=True)
    return python_exe


def read_lock_lines(path: Path) -> list[str]:
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def install_requirements(python_exe: Path, lock_path: Path | None, regenerate: bool) -> None:
    lock_path = lock_path or LOCK_PATH
    if regenerate:
        if not PACKAGING_REQ_PATH.exists():
            fail("packaging requirements file missing")
        print(f"[stage] installing dependencies from {PACKAGING_REQ_PATH}")
        subprocess.run(
            [str(python_exe), "-m", "pip", "install", "--no-user",
             "-r", str(PACKAGING_REQ_PATH)],
            check=True,
        )
        out = subprocess.run(
            [str(python_exe), "-m", "pip", "freeze"],
            check=True, capture_output=True, text=True,
        )
        lock_path.write_text(out.stdout, encoding="utf-8")
        print(f"[stage] regenerated dependency lock {lock_path}")
        return

    if not lock_path.exists():
        fail(f"dependency lock missing: {lock_path}")
    has_hashes = any("--hash=" in ln for ln in read_lock_lines(lock_path))
    cmd = [str(python_exe), "-m", "pip", "install", "--no-user"]
    if has_hashes:
        cmd.append("--require-hashes")
    cmd += ["-r", str(lock_path)]
    print(f"[stage] installing dependencies from lock {lock_path}")
    subprocess.run(cmd, check=True)

    out = subprocess.run(
        [str(python_exe), "-m", "pip", "freeze"],
        check=True, capture_output=True, text=True,
    )
    actual = sorted(line.strip() for line in out.stdout.splitlines() if line.strip())
    expected = sorted(read_lock_lines(lock_path))
    if actual != expected:
        print("Expected:")
        print("\n".join(expected))
        print("Actual:")
        print("\n".join(actual))
        fail("installed dependency inventory does not match the lock")
    print("[stage] dependency inventory verified against lock")


def stage_backend() -> None:
    if BACKEND_DIR.exists():
        shutil.rmtree(BACKEND_DIR)
    BACKEND_DIR.mkdir(parents=True, exist_ok=True)
    # copy backend package
    src_backend = APP_DIR / "backend"
    dst_backend = BACKEND_DIR
    # The payload policy decides what may ship. The same module verifies the
    # staged tree afterwards, so a prohibited artifact can neither be copied
    # in nor survive a staging run.
    shutil.copytree(
        src_backend,
        dst_backend,
        dirs_exist_ok=True,
        ignore=payload_copy_ignore(),
    )
    removed = prune_tree(dst_backend)
    if removed:
        print(f"[stage] removed {len(removed)} prohibited artifact(s) from the staged backend")
    print(f"[stage] backend staged at {dst_backend}")


def verify_payload() -> None:
    python_exe = RUNTIME_DIR / "python.exe"
    main_py = BACKEND_DIR / "main.py"
    if not python_exe.is_file():
        fail(f"missing runtime interpreter: {python_exe}")
    if not main_py.is_file():
        fail(f"missing backend entrypoint: {main_py}")
    # verify uvicorn import in embedded interpreter
    subprocess.run([str(python_exe), "-c", "import uvicorn, fastapi; print(uvicorn.__version__)"], check=True)
    # verify backend import path
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BACKEND_DIR.parent)
    subprocess.run(
        [str(python_exe), "-c", "import backend.main; print('backend import ok')"],
        cwd=str(BACKEND_DIR.parent),
        env=env,
        check=True,
    )
    print("[stage] payload verification passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python-version", default=PYTHON_VERSION)
    parser.add_argument("--lock", type=Path, default=None, help="requirements lock file")
    parser.add_argument("--regenerate-lock", action="store_true", help="Regenerate lock from requirements-packaging.txt")
    args = parser.parse_args()
    stage_embedded_python()
    install_requirements(RUNTIME_DIR / "python.exe", args.lock, args.regenerate_lock)
    stage_backend()
    verify_payload()

    manifest = {
        "python_version": PYTHON_VERSION,
        "runtime_dir": str(RUNTIME_DIR),
        "backend_dir": str(BACKEND_DIR),
        "python_sha256": sha256_file(RUNTIME_DIR / "python.exe"),
    }
    (RESOURCES / "runtime-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("[stage] DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
