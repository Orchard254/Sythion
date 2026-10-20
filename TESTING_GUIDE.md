# Sythion (Synth) Real-World Testing Guide

## Purpose

This guide is for **manual / operator-style validation** of the trusted runtime
core. For automated test commands and the current verified baseline, see
[`docs/TESTING.md`](docs/TESTING.md).

## Before you test

Read first:

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md)
- [`docs/acceptance-test-map.md`](docs/acceptance-test-map.md)

## Current verified automated baseline

The automated baseline is stated in exactly one place:
[`docs/TESTING.md`](docs/TESTING.md) ("Current verified baseline").

That number is a MEASUREMENT, not a figure typed into prose.
`python scripts/measure_test_baseline.py` produces it, and
`python scripts/measure_test_baseline.py --check` proves that this file and
the other baseline documents still agree with it.

This guide deliberately does not repeat the counts: a second copy of a
measured figure is a second thing that can go stale without anybody noticing.

## Runtime configuration before manual testing

### Cloud authentication

For development convenience you may use `.env` or shell environment variables.
Use the real `SYTHION_` prefix, for example:

```text
SYTHION_OLLAMA_CLOUD_BASE_URL=https://ollama.com
SYTHION_OLLAMA_API_KEY=your_api_key_here
SYTHION_FILE_ROOTS=C:/Users/YourName/Documents,C:/Users/YourName/Downloads
```

For the intended runtime path, prefer the write-only secret flow and the
onboarding model-selection flow rather than relying on a hard-coded selected
model string.

### Start the sidecar in development

From the repository root (with auto-reload on file edits):

```bash
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload --reload-dir backend
```

### Optional smoke test

With the sidecar running:

```bash
python scripts/smoke_test.py --all
```

## Manual validation scenarios

### 1. Health and startup

- start the backend/sidecar cleanly;
- verify `/api/v1/health` responds;
- confirm startup logs do not report missing critical configuration other than
  intentionally unset dev items.

### 2. Task lifecycle

- create a simple read-only task (for example: time or battery);
- verify the task moves through created/running/completed;
- verify the result is evidence-based rather than free-form narration;
- verify task events are present.

### 3. Cancellation

- start a task that runs long enough to stop;
- invoke stop through the UI or API;
- verify no subsequent OS action begins after cancellation is accepted.

### 4. File-root boundary

- test an in-root read or write;
- test an out-of-root target and confirm deterministic rejection;
- do not treat manual root widening as a code fix.

### 5. GUI/manual fixture tests

- use the dedicated fixture app or other approved Windows-native targets;
- verify bounded observation/focus/invoke/value behavior;
- verify elevated or unsafe targets are denied.

### 6. Voice/privacy checks

- enable voice mode intentionally;
- verify capture starts only while expected;
- verify voice assets are installed explicitly, not downloaded at startup;
- verify local stop behavior if voice features are under test.

## Release-style checks still requiring manual execution

These remain important even with strong automated coverage:

- packaged Tauri app launch/shutdown behavior;
- clean-machine install/launch/stop/restart/upgrade/uninstall;
- stale-sidecar and stale-lease recovery under real operator conditions.

## Notes

- This file is intentionally a practical operator checklist, not the canonical
  architecture or status source.
- If this guide ever conflicts with `docs/ARCHITECTURE.md` or
  `docs/PROJECT_STATUS.md`, those documents win.
