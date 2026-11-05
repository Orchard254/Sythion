"""Standalone packaging secret-classification tests.

Loaded by path only (no application imports, no backend conftest). Synthetic
tmp trees + spies prove the narrow fix preserves:
* .env.example placeholder policy (allowed with placeholders, rejected with creds)
* secret-material files never opened/read/hashed/copied by filename-based rules
* scanner still reports secret paths (without reading them)
* copy-ignore excludes prohibited secret material (without blanket .env.example exclusion)
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import shutil
import sys

# Load the module strictly by path to avoid collection/import ancestors.
_MODULE_PATH = pathlib.Path(__file__).resolve().parent.parent / "verify_release_payload.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_release_payload_under_test", str(_MODULE_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Make the companion module available for any cross-imports (none today).
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


V = _load_module()


def _make_tree(root: pathlib.Path, mapping: dict) -> pathlib.Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in mapping.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if content is None:
            p.mkdir(parents=True, exist_ok=True)
        else:
            p.write_text(content, encoding="utf-8")
    return root


# ---- unit-level classification --------------------------------------------


def test_secret_material_classifier_basic():
    # Secret-classified by filename.
    assert V.secret_material_file_reason(".env") is not None
    assert V.secret_material_file_reason(".env.local") is not None
    assert V.secret_material_file_reason(".env.production") is not None
    assert V.secret_material_file_reason("id.pem") is not None
    assert V.secret_material_file_reason("server.key") is not None
    assert V.secret_material_file_reason("anything.pfx") is not None
    # Placeholder example is NOT classified as secret material.
    assert V.secret_material_file_reason(".env.example") is None
    # Mixed-case of the example name is denied (exact exemption only).
    assert V.secret_material_file_reason(".ENV.EXAMPLE") is not None
    assert V.secret_material_file_reason(".env.EXAMPLE") is not None
    assert V.secret_material_file_reason(".Env.example") is not None
    # Prefixed variants are not exempt.
    assert V.secret_material_file_reason(".env.examplex") is not None
    assert V.secret_material_file_reason(".env.examples") is not None
    assert V.secret_material_file_reason(".env.example.local") is not None
    assert V.secret_material_file_reason(".env.example-old") is not None
    # Ordinary files are not.
    assert V.secret_material_file_reason("main.py") is None
    assert V.secret_material_file_reason("README.md") is None
    # Mixed case prohibited forms are still classified.
    assert V.secret_material_file_reason(".ENV.LOCAL") is not None


def test_payload_ignore_does_not_blanket_exclude_example():
    import fnmatch

    patterns = V.payload_ignore_patterns()
    # Legacy flat patterns only know the exact ".env" name; env variants are NOT
    # enumerated here (primary protection is payload_copy_ignore()).
    assert any(fnmatch.fnmatch(".env", p) for p in patterns)
    assert not any(fnmatch.fnmatch(".env.local", p) for p in patterns)
    assert not any(fnmatch.fnmatch(".env.production", p) for p in patterns)
    # ... and the accepted placeholder example is NOT blanket-excluded.
    assert not any(fnmatch.fnmatch(".env.example", p) for p in patterns)


def test_payload_copy_ignore_callback():
    ignored = V.payload_copy_ignore()("/tmp", [
        ".env", ".env.local", ".env.example", "main.py", "cert.pem",
        "__pycache__", "pytest-report.xml", "a.log",
    ])
    assert ".env" in ignored
    assert ".env.local" in ignored
    assert "cert.pem" in ignored
    assert "__pycache__" in ignored
    assert "pytest-report.xml" in ignored
    assert "a.log" in ignored
    # Accepted example ships.
    assert ".env.example" not in ignored
    assert "main.py" not in ignored


# ---- copy/tree observation spies ------------------------------------------


def test_copy_exclusion_via_shutil_ignore_patterns(tmp_path):
    src = tmp_path / "src"
    _make_tree(src, {
        ".env": "OPENAI_API_KEY=sk-" + "a" * 40 + "\n",
        ".env.local": "DATABASE_URL=postgres://user:secret@host/db\n",
        ".env.example": "API_KEY=<YOUR_KEY>\n",
        "main.py": "print('hi')\n",
        "cert.pem": "-----BEGIN RSA PRIVATE KEY-----\n...\n",
    })
    dst = tmp_path / "dst"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*V.payload_ignore_patterns()))
    # Legacy flat patterns cover the exact ".env" name and key suffixes; env
    # variants are handled by payload_copy_ignore() instead.
    assert not (dst / ".env").exists()
    # .env.local is NOT covered by the legacy flat patterns (documented gap).
    assert (dst / ".env.local").is_file()
    assert not (dst / "cert.pem").exists()
    # Accepted example + code still copied.
    assert (dst / ".env.example").is_file()
    assert (dst / "main.py").is_file()


def test_copy_callback_excludes_material(tmp_path):
    src = tmp_path / "src"
    _make_tree(src, {
        ".env": "TOKEN=secret-12345678901234567890\n",
        ".env.example": "TOKEN=<your-token>\n",
        "service.pem": "key\n",
        "app.py": "print(1)\n",
    })
    dst = tmp_path / "dst"
    shutil.copytree(src, dst, ignore=V.payload_copy_ignore())
    assert not (dst / ".env").exists()
    assert not (dst / "service.pem").exists()
    assert (dst / ".env.example").exists()
    assert (dst / "app.py").exists()


# ---- scanner spies ----------------------------------------------------------


def _spy_read_text(monkeypatch, V, opened):
    orig = pathlib.Path.read_text

    def patched(self, *a, **k):
        opened.append(str(self))
        return orig(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_text", patched)


def _spy_read_bytes(monkeypatch, opened):
    orig = pathlib.Path.read_bytes

    def patched(self, *a, **k):
        opened.append(str(self))
        return orig(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_bytes", patched)


def test_scan_does_not_open_secret_material_and_reports_them(tmp_path, monkeypatch):
    secret_value = "sk-" + "B" * 40
    root = _make_tree(tmp_path / "payload", {
        ".env": "API_KEY=" + secret_value + "\n",
        ".env.local": "DATABASE_URL=postgres://u:p@h/d\n",
        "server.pem": "-----BEGIN RSA PRIVATE KEY-----\nsecret-but-not-read\n",
        "server.key": "private-key-material\n",
        "main.py": "normal code\n",
        ".env.example": "API_KEY=<YOUR_KEY>\n",
    })
    opened = []
    _spy_read_text(monkeypatch, V, opened)
    findings = V.scan_tree(root)
    rel_opened = sorted({os.path.relpath(p, str(root)).replace("\\", "/") for p in opened})

    # No secret-material file was opened for content.
    for prohibited in (".env", ".env.local", "server.pem", "server.key"):
        assert prohibited not in rel_opened, f"{prohibited} was opened: {rel_opened}"

    paths = [f.path for f in findings]
    kinds = [f.kind for f in findings]
    assert any(p.endswith(".env") or p.endswith(".env.local") for p in paths)
    assert any("server.pem" in p for p in paths)
    assert "prohibited-file" in kinds

    # The secret value must never leak via scanning output.
    joined = "\n".join(f.describe() for f in findings)
    assert secret_value not in joined

    # .env.example placeholder handled: scanned (present in opened) but no finding.
    assert ".env.example" in rel_opened  # allowed, content scan runs
    assert not any(p.endswith(".env.example") for p in paths)


def test_scan_rejects_credential_bearing_example(tmp_path, monkeypatch):
    cred = "Z" * 48
    root2 = tmp_path / "payload2"
    _make_tree(root2, {
        ".env.example": "SECRET_TOKEN=" + cred + "\n",
    })
    opened = []
    _spy_read_text(monkeypatch, V, opened)
    findings = V.scan_tree(root2)
    assert any(f.kind == "secret-assignment" for f in findings)
    # Key name reported, value discarded.
    found = [f for f in findings if f.kind == "secret-assignment"][0]
    assert found.key == "SECRET_TOKEN"
    desc = "\n".join(f.describe() for f in findings)
    assert cred not in desc
    # .env.example is allowed-to-ship but content-checked, so it must have been read.
    assert any(".env.example" in p for p in opened)


def test_mirror_does_not_hash_secret_classified_paths(tmp_path, monkeypatch):
    source = tmp_path / "source"
    mirror = tmp_path / "mirror"
    _make_tree(source, {
        "main.py": "print(1)\n",
        ".env": "API_KEY=" + "K" * 40 + "\n",
        ".env.local": "SECRET=x\n",
        "id.pem": "private\n",
        ".env.example": "API_KEY=<KEY>\n",
    })
    _make_tree(mirror, {
        "main.py": "print(1)\n",
        ".env.example": "API_KEY=<KEY>\n",
    })

    hashed = []
    _spy_read_bytes(monkeypatch, hashed)
    findings = V.backend_mirror_findings(source, mirror)
    hashed_rel = {os.path.relpath(p, str(source)) for p in hashed} | {os.path.relpath(p, str(mirror)) for p in hashed}

    # No secret-classified path hashed.
    for prohibited in (".env", ".env.local", "id.pem"):
        assert prohibited not in hashed_rel, f"{prohibited} was hashed: {hashed_rel}"

    # But .env.example IS hashed (placeholder-allowed drift check), so it must appear
    # among the read targets.
    assert any(".env.example" in p for p in hashed_rel)

    # Mirror drift finding for secret-classified paths? They are excluded, so no
    # "extra/missing" finding referencing them.
    joined = "\n".join(f.path for f in findings)
    assert ".env.local" not in joined
    assert "id.pem" not in joined


def test_prune_removes_without_reading(tmp_path, monkeypatch):
    root = _make_tree(tmp_path / "payload", {
        ".env": "x=" + "Q" * 40 + "\n",
        "cert.key": "key\n",
        "main.py": "print(1)\n",
    })
    opened = []
    _spy_read_text(monkeypatch, V, opened)
    removed = V.prune_tree(root)
    rel_removed = [r for r in removed]
    assert any(".env" in r for r in rel_removed)
    assert any("cert.key" in r for r in rel_removed)
    # Prohibited file must not have been opened before removal.
    for prohibited in (".env", "cert.key"):
        assert not any(prohibited in os.path.basename(p) for p in opened), (prohibited, opened)
    assert (root / "main.py").is_file()
    assert not (root / ".env").exists()


def test_scanner_reports_secret_paths_without_content(tmp_path, monkeypatch):
    secret = "DO_NOT_LEAK_" + "9" * 32
    root = _make_tree(tmp_path / "payload", {
        ".env": "API_KEY=" + secret + "\n",
        "a.pem": secret + "\n",
    })
    opened = []
    _spy_read_text(monkeypatch, V, opened)
    findings = V.scan_tree(root)
    assert len(findings) >= 2
    # Filenames reported...
    names = " ".join(os.path.basename(f.path) for f in findings)
    assert ".env" in names or any(f.path.endswith(".env") for f in findings)
    assert any("a.pem" in f.path for f in findings)
    # ...never opened...
    assert ".env" not in " ".join(opened)
    assert "a.pem" not in " ".join(opened)
    # ...secret value not present in output.
    desc = "\n".join(f.describe() for f in findings)
    assert secret not in desc


def test_cli_json_optional_positional_root_default(tmp_path, capsys):
    root = _make_tree(tmp_path / "payload", {
        "main.py": "print(1)\n",
        ".env": "API_KEY=" + "P" * 40 + "\n",
    })
    # Contaminated tree via positional root -> exits 1 and reports path only.
    rc = V.main([str(root), "--json"])
    assert rc == 1
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["clean"] is False
    assert any(".env" in f["path"] for f in data["findings"])
    assert "P" * 40 not in out
    # clean tree via positional root -> exit 0.
    clean = _make_tree(tmp_path / "clean", {"main.py": "print(1)\n"})
    rc2 = V.main([str(clean), "--json"])
    assert rc2 == 0


def test_cli_default_root_missing_returns_2(tmp_path, monkeypatch):
    # Change working dir behaviour? default_root depends on __file__, unchanged.
    # Point args root default by passing None-equivalent: no arg -> uses default_root.
    # To avoid hitting real tree, pass a nonexistent explicit positional root.
    rc = V.main([str(tmp_path / "nope"), "--json"])
    assert rc == 2


def test_third_party_secret_material_not_opened(tmp_path, monkeypatch):
    root = _make_tree(tmp_path / "runtime", {
        "python-runtime": None,
    })
    (root / "python-runtime" / "site-packages" / "somepkg").mkdir(parents=True, exist_ok=True)
    (root / "python-runtime" / "site-packages" / "somepkg" / "cacert.pem").write_text("-----BEGIN RSA PRIVATE KEY-----\nneverread\n", encoding="utf-8")
    (root / "python-runtime" / "site-packages" / "somepkg" / "config.txt").write_text("API_KEY=" + "Q" * 40 + "\n", encoding="utf-8")
    opened = []
    _spy_read_text(monkeypatch, V, opened)
    findings = V.scan_tree(root, follow_third_party_names=True)
    assert "cacert.pem" not in "".join(opened)
    assert all("cacert.pem" not in f.path for f in findings)


def test_backend_mirror_check_backend_mirror_interface(tmp_path):
    # Ensure --check-backend-mirror interface still works (smoke, exit maybe 0/1 but no crash).
    source = tmp_path / "source"
    mirror = tmp_path / "mirror"
    _make_tree(source, {"main.py": "print(1)\n"})
    _make_tree(mirror, {"main.py": "print(2)\n"})
    findings = V.backend_mirror_findings(source, mirror)
    assert findings and all(f.kind == "backend-drift" for f in findings)


def test_env_example_placeholder_ok_example(tmp_path, monkeypatch):
    root = _make_tree(tmp_path / "p", {
        ".env.example": "API_KEY=<YOUR_KEY>\nPASSWORD=changeme\nTOKEN=<token>\n",
    })
    opened = []
    _spy_read_text(monkeypatch, V, opened)
    findings = V.scan_tree(root)
    assert findings == []
    assert (root / ".env.example").is_file()


# ---- env-name variant and mixed-case regression ---------------------------


# Lowercase env-name variants that are case-distinct from .env.example even on
# a case-insensitive (Windows) filesystem.
_FS_ENV_VARIANTS = (
    ".env.examplex",
    ".env.examples",
    ".env.example.local",
    ".env.example-old",
)
# A representative mixed-case form tested in isolation (cannot coexist with
# .env.example on a case-insensitive filesystem).
_MIXED_CASE_ENV_VARIANT = ".ENV.EXAMPLE"


def test_copy_callback_excludes_env_variants_and_mixed_case(tmp_path):
    src = tmp_path / "src"
    _make_tree(src, {name: "TOKEN=variant-secret-" + "z" * 20 + "\\n" for name in _FS_ENV_VARIANTS})
    _make_tree(src, {
        ".env.example": "TOKEN=<your-token>\\n",
        "main.py": "print(1)\\n",
    })
    dst = tmp_path / "dst"
    shutil.copytree(src, dst, ignore=V.payload_copy_ignore())
    for name in _FS_ENV_VARIANTS:
        assert not (dst / name).exists(), f"{name} was copied"
    assert (dst / ".env.example").is_file()
    assert (dst / "main.py").is_file()


def test_copy_callback_excludes_secret_ancestor_directory(tmp_path):
    src = tmp_path / "src"
    _make_tree(src, {"main.py": "print(1)\\n"})
    secret_dir = src / ".env.local"
    secret_dir.mkdir(parents=True, exist_ok=True)
    (secret_dir / "ordinary.py").write_text("print('ordinarily shipped?')\\n", encoding="utf-8")
    dst = tmp_path / "dst"
    shutil.copytree(src, dst, ignore=V.payload_copy_ignore())
    assert not (dst / ".env.local").exists()
    assert not (dst / ".env.local" / "ordinary.py").exists()
    assert (dst / "main.py").is_file()


def test_scan_excludes_env_variants_before_open(tmp_path, monkeypatch):
    root = _make_tree(tmp_path / "payload", {
        **{name: "TOKEN=" + "V" * 30 + "\\n" for name in _FS_ENV_VARIANTS},
        ".env.example": "TOKEN=<YOUR_TOKEN>\\n",
        "main.py": "print(1)\\n",
    })
    opened = []
    _spy_read_text(monkeypatch, V, opened)
    findings = V.scan_tree(root)
    rel_opened = sorted({os.path.relpath(p, str(root)).replace("\\\\", "/") for p in opened})
    for name in _FS_ENV_VARIANTS:
        assert name not in rel_opened, f"{name} opened: {rel_opened}"
    reported = " ".join(f.path for f in findings)
    for name in _FS_ENV_VARIANTS:
        assert name in reported, f"{name} not reported: {reported}"
    # Exact placeholder example is still scanned and accepted.
    assert ".env.example" in rel_opened
    assert not any(f.path.endswith(".env.example") for f in findings)


def test_scan_skips_secret_ancestor_and_reports_ancestor(tmp_path, monkeypatch):
    root = _make_tree(tmp_path / "payload", {"main.py": "print(1)\\n"})
    secret_dir = root / ".env.local"
    secret_dir.mkdir(parents=True, exist_ok=True)
    (secret_dir / "ordinary.py").write_text("print('hidden ordinary')\\n", encoding="utf-8")
    opened = []
    _spy_read_text(monkeypatch, V, opened)
    findings = V.scan_tree(root)
    opened_bases = [os.path.basename(p) for p in opened]
    assert "ordinary.py" not in opened_bases, opened
    # Scanner reports the secret-material ancestor path.
    paths = [f.path for f in findings]
    assert any(p == ".env.local" or p.startswith(".env.local") for p in paths), paths
    assert (root / "main.py").is_file()


def test_mirror_does_not_hash_secret_material_or_variants(tmp_path, monkeypatch):
    source = tmp_path / "source"
    mirror = tmp_path / "mirror"
    _make_tree(source, {"main.py": "print(1)\\n", ".env.example": "API_KEY=<KEY>\\n"})
    _make_tree(mirror, {"main.py": "print(1)\\n", ".env.example": "API_KEY=<KEY>\\n"})
    # Add variants + secret ancestor with an ordinary file to the source only.
    for name in (".env.examplex", ".env.examples", ".env.example.local", ".env.example-old"):
        (source / name).write_text("TOKEN=x\\n", encoding="utf-8")
    secret_dir = source / ".env.local"
    secret_dir.mkdir(parents=True, exist_ok=True)
    (secret_dir / "ordinary.py").write_text("print('x')\\n", encoding="utf-8")

    hashed = []
    _spy_read_bytes(monkeypatch, hashed)
    findings = V.backend_mirror_findings(source, mirror)
    hashed_rel = {os.path.relpath(p, str(source)).replace("\\\\", "/") for p in hashed} | {
        os.path.relpath(p, str(mirror)).replace("\\\\", "/") for p in hashed
    }
    for forbidden in (".env.examplex", ".env.examples", ".env.example.local",
                      ".env.example-old", ".env.local", "ordinary.py"):
        assert forbidden not in hashed_rel, f"{forbidden} hashed: {hashed_rel}"
    assert any(".env.example" in p for p in hashed_rel)
    # No drift finding names the excluded material.
    joined = "\\n".join(f.path for f in findings)
    for forbidden in (".env.examplex", "ordinary.py"):
        assert forbidden not in joined


# ---- mixed-case representative (isolated; FS case-insensitive safe) ---------


def test_copy_callback_excludes_mixed_case_env_name(tmp_path):
    src = tmp_path / "src"
    _make_tree(src, {
        _MIXED_CASE_ENV_VARIANT: "TOKEN=mixed-case-secret-" + "z" * 20 + "\\n",
        "main.py": "print(1)\\n",
    })
    dst = tmp_path / "dst"
    shutil.copytree(src, dst, ignore=V.payload_copy_ignore())
    assert not (dst / _MIXED_CASE_ENV_VARIANT).exists()
    assert (dst / "main.py").is_file()


def test_scan_excludes_mixed_case_env_name_before_open(tmp_path, monkeypatch):
    root = _make_tree(tmp_path / "payload", {
        _MIXED_CASE_ENV_VARIANT: "TOKEN=" + "M" * 30 + "\\n",
        "main.py": "print(1)\\n",
    })
    opened = []
    _spy_read_text(monkeypatch, V, opened)
    findings = V.scan_tree(root)
    rel_opened = sorted({os.path.relpath(p, str(root)).replace("\\\\", "/") for p in opened})
    assert _MIXED_CASE_ENV_VARIANT not in rel_opened
    reported = " ".join(f.path for f in findings)
    assert _MIXED_CASE_ENV_VARIANT in reported
    # No placeholder policy bypass: exact mixed-case is still secret material,
    # so .env.example (absent here) is not involved.


# ---- stager wiring (source/AST only, never import/execute stager) ----------


def test_stager_wiring_payload_copy_ignore_ast():
    import ast

    stager_path = pathlib.Path(__file__).resolve().parent.parent / "stage_python_runtime.py"
    source = stager_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # verify_release_payload imports payload_copy_ignore ...
    imported = False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.endswith("verify_release_payload")
        ):
            if any(alias.name == "payload_copy_ignore" for alias in node.names):
                imported = True
    assert imported, "payload_copy_ignore not imported in stager source"

    # ... and stage_backend calls shutil.copytree(..., ignore=payload_copy_ignore()).
    stage_backend = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "stage_backend":
            stage_backend = node
    assert stage_backend is not None, "stage_backend function not found"

    wired = False
    for node in ast.walk(stage_backend):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "ignore":
                continue
            if (
                isinstance(kw.value, ast.Call)
                and isinstance(kw.value.func, ast.Name)
                and kw.value.func.id == "payload_copy_ignore"
            ):
                wired = True
    assert wired, "stage_backend does not pass ignore=payload_copy_ignore()"
    # Source-level corroboration (no execution).
    assert "ignore=payload_copy_ignore()" in source
