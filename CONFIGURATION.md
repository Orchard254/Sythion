# Sythion (Synth) Configuration Guide

## Purpose

This guide covers configuration for the **trusted runtime core** in this
repository.

For architectural context, see:

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`docs/INTELLIGENCE_ARCHITECTURE.md`](docs/INTELLIGENCE_ARCHITECTURE.md)
- [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md)

## Configuration modes

### 1. Packaged / runtime configuration

For the intended runtime path, treat the packaged Tauri app and sidecar as the
primary entrypoint. In that path:

- the sidecar receives a per-launch control token from the launcher handshake;
- the sidecar binds to loopback only;
- secrets should live in the runtime's write-only secret storage / Windows
  secret storage flow;
- the selected model should come from authenticated onboarding, not from a
  stale hard-coded model string.

### 2. Development configuration

During local development, `.env` or shell environment variables may still be
used as a convenience path.

## Recommended development variables

```text
SYTHION_OLLAMA_CLOUD_BASE_URL=https://ollama.com
SYTHION_OLLAMA_API_KEY=your_api_key_here
SYTHION_FILE_ROOTS=C:/Users/YourName/Documents,C:/Users/YourName/Downloads
```

Notes:

- The exact variable names used by the repo should be validated against
  `backend/core/config.py` before changing setup instructions.
- Do not rely on a hard-coded selected model string when authenticated
  onboarding can supply the exact available model ID.

## Model setup

The professional path is:

1. configure Cloud authentication;
2. start the runtime;
3. use onboarding to list authenticated model IDs;
4. select one exact returned model ID;
5. persist only non-secret model metadata.

This avoids stale or invented model identifiers.

## File roots and local safety

Approved file roots are part of the runtime safety boundary.

Use them to define what file operations are allowed. The owner may also grant
additional roots through the runtime settings flow, which is preferable to
teaching users to widen access through ad-hoc code changes.

## Voice and privacy settings

Voice assets and voice behavior should follow the runtime privacy contract:

- no automatic asset downloads at startup;
- explicit asset installation and verification;
- local capture/transcription lifecycle;
- local final-result TTS only.

## Validation

After configuration changes, verify with the current commands in
[`docs/TESTING.md`](docs/TESTING.md) and the acceptance gates in
[`docs/acceptance-test-map.md`](docs/acceptance-test-map.md).

## Historical note

Earlier revisions of this file described `.env` as the primary production
configuration path and listed fixed model IDs. Treat that older guidance as
historical development convenience, not current architecture truth.
