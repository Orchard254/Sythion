"""Exercise the actual app's CORS stack without starting its lifespan."""

import os
from pathlib import Path
import subprocess
import sys


def test_http_exception_exposes_only_dispatch_verdict(tmp_path):
    # A fresh interpreter avoids an already-imported app or settings pointing
    # at a real profile. Prototype mode also prevents reading backend/.env.
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("SYTHION_")}
    env.pop("Sythion (Synth)_DATA_DIR", None)
    env.update({
        "SYTHION_DATA_DIR": str(tmp_path / "profile"),
        "SYTHION_LOG_DIR": str(tmp_path / "logs"),
        "SYTHION_SECRET_BACKEND": "memory",
        "SYTHION_PROTOTYPE_BUILD": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    })
    code = r'''
import asyncio
import httpx
from fastapi import HTTPException
from backend.main import app

@app.get("/_test_cors_dispatch_verdict")
async def synthetic_exception():
    raise HTTPException(status_code=409, detail="synthetic", headers={
        "X-Sythion-Dispatch-Verdict": "no-dispatch",
        "X-Unrelated": "not-exposed",
    })

async def check():
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/_test_cors_dispatch_verdict", headers={
            "Origin": "http://tauri.localhost",
        })
    assert response.status_code == 409
    assert response.headers["x-sythion-dispatch-verdict"] == "no-dispatch"
    assert response.headers["x-unrelated"] == "not-exposed"
    assert response.headers["access-control-allow-origin"] == "http://tauri.localhost"
    assert response.headers["access-control-expose-headers"].lower() == "x-sythion-dispatch-verdict"

asyncio.run(check())
'''
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[2],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
