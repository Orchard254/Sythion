"""Hermetic tests for scripts/synth_companion.py (A6 browser companion launcher)."""

import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import synth_companion as sc  # noqa: E402

SECRET = "ctl-A6-super-secret-token-9f3ac1"


def _token_file(tmp_path: Path) -> Path:
    path = tmp_path / "token.json"
    path.write_text('{"token": "%s", "version": 1}' % SECRET, encoding="utf-8")
    return path


# --- A6: URL construction --------------------------------------------------


def test_url_contains_surface_marker_and_loopback():
    url = sc.build_companion_url(8787)
    assert "127.0.0.1" in url
    assert "?surface=companion#/companion" in url
    assert url == "http://127.0.0.1:8787/?surface=companion#/companion"


# --- A6: token secrecy -----------------------------------------------------


def test_token_never_appears_in_stdout_or_stderr(tmp_path, capsys):
    token_file = _token_file(tmp_path)
    code = sc.main(["--token-file", str(token_file), "--no-browser"])
    captured = capsys.readouterr()
    assert code == 0
    assert SECRET not in captured.out
    assert SECRET not in captured.err


# --- A6: --no-browser ------------------------------------------------------


def test_no_browser_suppresses_browser_launch(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(sc.webbrowser, "open_new", lambda url: called.append(url))
    code = sc.main(["--token-file", str(_token_file(tmp_path)), "--no-browser"])
    assert code == 0
    assert called == []


# --- A6: missing token file ------------------------------------------------


def test_missing_token_file_fails_cleanly(tmp_path, capsys):
    missing = tmp_path / "does-not-exist.json"
    code = sc.main(["--token-file", str(missing), "--no-browser"])
    captured = capsys.readouterr()
    assert code != 0
    assert "token" in captured.err.lower()
    assert "not found" in captured.err.lower()
    assert "--token-file" in captured.err
    assert SECRET not in captured.out + captured.err


# --- A6: runtime bootstrap injection --------------------------------------


def test_bootstrap_injected_before_app_bundle():
    html = "<!doctype html><html><head><title>C</title></head><body></body></html>"
    injected = sc.inject_bootstrap(html, SECRET)
    assert SECRET in injected
    assert injected.index(SECRET) < injected.index("</head>")
    assert sc.SESSION_TOKEN_KEY in injected


def test_bootstrap_escapes_script_terminator():
    injected = sc.inject_bootstrap("<head></head>", "a</script><evil>")
    assert "</script><evil>" not in injected


# --- A6: static server path traversal + serving ---------------------------


def _safe_path_for(root: Path, path: str):
    handler = object.__new__(sc.CompanionHandler)
    handler._root = root.resolve()
    handler.path = path
    return handler._safe_path()


def test_safe_path_rejects_traversal(tmp_path):
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    assert _safe_path_for(tmp_path, "/../secret.txt") is None
    assert _safe_path_for(tmp_path, "/%2e%2e/secret.txt") is None
    assert _safe_path_for(tmp_path, "/..\\secret.txt") is None


def test_safe_path_allows_contained_file(tmp_path):
    (tmp_path / "index.html").write_text("ok", encoding="utf-8")
    assert _safe_path_for(tmp_path, "/index.html") == (tmp_path / "index.html")


def test_static_server_serves_index_and_refuses_traversal(tmp_path):
    (tmp_path / "index.html").write_text(
        "<!doctype html><html><head></head><body>companion</body></html>",
        encoding="utf-8",
    )
    (tmp_path / "secret.txt").write_text("TOP-SECRET-FILE", encoding="utf-8")

    server = sc.build_server(tmp_path, SECRET, 0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
            body = resp.read().decode("utf-8")
        assert "companion" in body
        assert SECRET in body  # token delivered into the served HTML at runtime

        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/%2e%2e/secret.txt", timeout=5)
        assert excinfo.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        sc.main(["--help"])
    assert excinfo.value.code == 0
    assert "--no-browser" in capsys.readouterr().out
