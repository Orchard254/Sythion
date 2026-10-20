"""Release payload packaging policy.

The distributable installer ships the staged ``src-tauri/resources`` tree
verbatim. These tests pin the policy that keeps developer secrets, test
databases and user runtime state out of it, and the rules that decide what a
usable credential looks like.

The verifier never prints, logs or returns a secret value: a finding carries
the file path, the line number and the *name* of the offending key.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "verify_release_payload.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_release_payload", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


verify = _load_module()


# ---------------------------------------------------------------------------
# Prohibited file names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        ".env.local",
        ".env.production",
        "sythion.db",
        "sythion.db-wal",
        "sythion.db-shm",
        "test_voice.db",
        "profile.sqlite3",
        "pytest-report.xml",
        "coverage.xml",
        ".coverage",
        "sidecar.out.log",
    ],
)
def test_prohibited_files_are_rejected(name):
    assert verify.prohibited_file_reason(name) is not None


@pytest.mark.parametrize("name", [".env.example", "main.py", "requirements.lock.txt", "app.log4"])
def test_allowed_files_are_not_rejected(name):
    assert verify.prohibited_file_reason(name) is None


@pytest.mark.parametrize("name", ["__pycache__", ".pytest_cache", ".mypy_cache", "tests"])
def test_prohibited_directories_are_rejected(name):
    assert verify.prohibited_dir_reason(name) is not None


@pytest.mark.parametrize("name", ["core", "api", "services", "migrations"])
def test_allowed_directories_are_not_rejected(name):
    assert verify.prohibited_dir_reason(name) is None


# ---------------------------------------------------------------------------
# Secret classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "<your-key-here>",
        "your-ollama-cloud-api-key-here",
        "replace-with-a-model-id",
        "changeme",
        "CHANGEME",
        "placeholder",
        "example-value",
        "xxx",
        "...",
        "***",
        "redacted",
    ],
)
def test_placeholder_values_are_not_credentials(value):
    assert verify.is_placeholder(value) is True
    assert verify.looks_like_credential(value, code_file=False) is False


def test_a_real_looking_key_in_a_configuration_file_is_a_credential():
    value = "0f7c1d2e3b4a5968778695a4b3c2d1e0f"  # noqa: S105 - a shape, not a secret
    assert verify.looks_like_credential(value, code_file=False) is True


def test_a_name_is_not_a_credential():
    # `OLLAMA_API_KEY = "ollama_api_key"` names a secret, it does not hold one.
    assert verify.looks_like_credential('"ollama_api_key"', code_file=True) is False


def test_a_code_expression_is_not_a_credential():
    assert verify.looks_like_credential('headers.get("x-control-token")', code_file=True) is False


def test_a_test_literal_is_not_a_credential():
    assert verify.looks_like_credential('"test-control-token"', code_file=True) is False


def test_an_unquoted_value_is_not_a_credential_in_code():
    # In code only a quoted literal can be a hardcoded credential.
    assert verify.looks_like_credential("some-value", code_file=True) is False


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_scan_finds_a_local_environment_file(tmp_path):
    _write(tmp_path / "backend" / ".env", "SYTHION_OLLAMA_API_KEY=abc\n")
    findings = verify.scan_tree(tmp_path)
    assert any(f.kind == "prohibited-file" and f.path == "backend/.env" for f in findings)


def test_scan_finds_a_database(tmp_path):
    _write(tmp_path / "backend" / "test_voice.db", "binary")
    findings = verify.scan_tree(tmp_path)
    assert any(f.path == "backend/test_voice.db" for f in findings)


def test_scan_finds_a_secret_assignment_in_a_configuration_file(tmp_path):
    _write(
        tmp_path / "backend" / "config.txt",
        "SYTHION_OLLAMA_API_KEY=0f7c1d2e3b4a5968778695a4b3c2d1e0f\n",
    )
    findings = verify.scan_tree(tmp_path)
    secret = [f for f in findings if f.kind == "secret-assignment"]
    assert secret, "the secret assignment must be reported"
    # The key NAME is reported, never the value.
    assert secret[0].key == "SYTHION_OLLAMA_API_KEY"
    assert "0f7c1d2e" not in secret[0].describe()


def test_scan_ignores_ordinary_code_assignments(tmp_path):
    _write(
        tmp_path / "backend" / "core" / "auth.py",
        'token = headers.get("x-control-token")\n'
        'expected_token = token_provider()\n'
        'OLLAMA_API_KEY = "ollama_api_key"\n',
    )
    findings = verify.scan_tree(tmp_path)
    assert not [f for f in findings if f.kind == "secret-assignment"]


def test_scan_accepts_a_safe_example(tmp_path):
    _write(
        tmp_path / "backend" / ".env.example",
        "SYTHION_OLLAMA_API_KEY=your-key-here\nSYTHION_SELECTED_MODEL=replace-me\n",
    )
    findings = verify.scan_tree(tmp_path)
    assert findings == []


def test_scan_rejects_an_example_that_carries_a_credential(tmp_path):
    _write(
        tmp_path / "backend" / ".env.example",
        "SYTHION_OLLAMA_API_KEY=0f7c1d2e3b4a5968778695a4b3c2d1e0f\n",
    )
    findings = verify.scan_tree(tmp_path)
    assert [f for f in findings if f.kind == "secret-assignment"]


def test_scan_reports_a_pem_private_key(tmp_path):
    _write(tmp_path / "backend" / "key.pem", "-----BEGIN RSA PRIVATE KEY-----\n")
    findings = verify.scan_tree(tmp_path)
    assert [f for f in findings if f.kind == "prohibited-file"]
    # A key file with a different name is still caught by its content.
    _write(tmp_path / "backend" / "material.txt", "-----BEGIN RSA PRIVATE KEY-----\n")
    findings = verify.scan_tree(tmp_path)
    assert [f for f in findings if f.kind == "secret-material"]


def test_vendored_third_party_code_is_not_scanned_for_secrets(tmp_path):
    _write(
        tmp_path / "python-runtime" / "Lib" / "site-packages" / "pkg" / "mod.py",
        'TOKEN = "0f7c1d2e3b4a5968778695a4b3c2d1e0f"\n',
    )
    findings = verify.scan_tree(tmp_path)
    assert not [f for f in findings if f.kind == "secret-assignment"]


def test_vendored_third_party_code_may_keep_its_own_tests_and_bytecode(tmp_path):
    _write(tmp_path / "python-runtime" / "Lib" / "site-packages" / "pkg" / "tests" / "t.py", "")
    _write(tmp_path / "python-runtime" / "Lib" / "site-packages" / "pkg" / "__pycache__" / "m.pyc", "")
    findings = verify.scan_tree(tmp_path)
    assert findings == []


# ---------------------------------------------------------------------------
# Pruning
# ---------------------------------------------------------------------------


def test_prune_removes_the_prohibited_artifacts(tmp_path):
    _write(tmp_path / "backend" / ".env", "SYTHION_OLLAMA_API_KEY=abc\n")
    _write(tmp_path / "backend" / "test_voice.db", "binary")
    _write(tmp_path / "backend" / "pytest-report.xml", "<report/>")
    _write(tmp_path / "backend" / "__pycache__" / "main.pyc", "x")
    _write(tmp_path / "backend" / ".pytest_cache" / "CACHEDIR.TAG", "x")
    _write(tmp_path / "backend" / "tests" / "test_x.py", "x")
    _write(tmp_path / "backend" / "main.py", "print('ok')\n")
    _write(tmp_path / "backend" / ".env.example", "SYTHION_OLLAMA_API_KEY=your-key-here\n")

    removed = verify.prune_tree(tmp_path)

    assert not (tmp_path / "backend" / ".env").exists()
    assert not (tmp_path / "backend" / "test_voice.db").exists()
    assert not (tmp_path / "backend" / "pytest-report.xml").exists()
    assert not (tmp_path / "backend" / "__pycache__").exists()
    assert not (tmp_path / "backend" / ".pytest_cache").exists()
    assert not (tmp_path / "backend" / "tests").exists()
    # The application code and the safe example survive.
    assert (tmp_path / "backend" / "main.py").exists()
    assert (tmp_path / "backend" / ".env.example").exists()
    assert verify.scan_tree(tmp_path) == []
    assert removed


def test_prune_leaves_the_vendored_runtime_alone(tmp_path):
    _write(tmp_path / "python-runtime" / "Lib" / "site-packages" / "pkg" / "tests" / "t.py", "")
    _write(tmp_path / "python-runtime" / "Lib" / "site-packages" / "pkg" / "m.pyc", "")

    verify.prune_tree(tmp_path)

    assert (tmp_path / "python-runtime" / "Lib" / "site-packages" / "pkg" / "tests" / "t.py").exists()
    assert (tmp_path / "python-runtime" / "Lib" / "site-packages" / "pkg" / "m.pyc").exists()


def test_the_ignore_patterns_keep_the_safe_example(tmp_path):
    import shutil

    patterns = verify.payload_ignore_patterns()
    src = tmp_path / "src"
    _write(src / ".env", "x")
    _write(src / ".env.example", "x")
    _write(src / "app.db", "x")
    _write(src / "tests" / "t.py", "x")
    _write(src / "main.py", "x")
    dst = tmp_path / "dst"

    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*patterns))

    assert not (dst / ".env").exists()
    assert (dst / ".env.example").exists()
    assert not (dst / "app.db").exists()
    assert not (dst / "tests").exists()
    assert (dst / "main.py").exists()


# ---------------------------------------------------------------------------
# The staged payload
# ---------------------------------------------------------------------------


def test_the_staged_release_payload_is_clean():
    """The staged resources tree must not carry secrets, databases or state."""
    staged = REPO_ROOT / "src-tauri" / "resources"
    if not staged.is_dir():
        pytest.skip("the release payload has not been staged")
    findings = verify.scan_tree(staged)
    assert not findings, "\n".join(f.describe() for f in findings)
