"""Device-authenticated WebSocket gateway skeleton for the first-party relay.

The socket is authenticated by a per-device credential and an explicit
``conversation.write`` grant in a dedicated ``/device/`` scope. It does not
carry the owner control token and does not expose owner approval routes.
Transport is expected to be loopback-terminating (LAN via a secured local
network, remote via VPN); TLS/WSS is terminated in front of the sidecar.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from backend.core.device_scope import device_query_credentials
from backend.core.handshake import read_device_channel_config
from backend.models.database import get_db
from backend.services.relay import (
    close_session,
    open_session,
    poll_messages,
    post_message,
)
from backend.services.remote_access import check_access, verify_device_credential
from backend.services.remote_action_tracking import (
    complete_remote_action,
    fail_remote_action,
    request_idempotency_key,
    start_remote_action,
)

logger = logging.getLogger("backend.api.device_gateway")
router = APIRouter()


@router.websocket("/device/relay/ws")
async def device_relay_ws(websocket: WebSocket, db: Session = Depends(get_db)):
    """Bounded message transport for a registered device.

    Frames from the server carry a monotonically increasing ``sequence`` (the
    durable RelayMessage id). The client may send ``ack``, ``poll`` or ``send``
    frames. Nothing here invokes a model or grants authority: a send is only a
    durable owner message; orchestration/verification is a later stage.
    """
    config = read_device_channel_config()
    if not (config["enabled"] and config["ws_enabled"] and config["relay_enabled"]):
        await websocket.close(code=1008, reason="device channel disabled")
        return

    device_id, token = device_query_credentials(websocket.scope)

    if not verify_device_credential(db, device_id, token):
        await websocket.close(code=1008, reason="invalid device credential")
        return
    allowed, reason = check_access(db, device_id, "conversation.write")
    if not allowed:
        await websocket.close(code=1008, reason=f"device access denied: {reason}")
        return

    session = open_session(db, device_id)
    db.commit()
    last_sent = 0
    last_ack = 0
    await websocket.accept()

    async def push_new_messages() -> None:
        nonlocal last_sent
        messages = poll_messages(db, session["session_id"], after_id=last_sent)
        for message in messages:
            await websocket.send_text(json.dumps({
                "type": "message",
                "sequence": int(message["id"]),
                "role": message["role"],
                "kind": message["kind"],
                "text": message["text"],
                "authorized": False,
            }))
            last_sent = int(message["id"])
        if messages:
            db.commit()

    try:
        await websocket.send_text(json.dumps({
            "type": "session",
            "session_id": session["session_id"],
            "device_id": device_id,
            "sequence": 0,
            "authorized": False,
        }))
        await push_new_messages()

        while True:
            raw = await websocket.receive_text()
            try:
                frame: Any = json.loads(raw)
            except (TypeError, ValueError):
                await websocket.send_text(json.dumps({
                    "type": "error", "reason": "invalid_json", "authorized": False}))
                continue
            if not isinstance(frame, dict):
                await websocket.send_text(json.dumps({
                    "type": "error", "reason": "frame_must_be_object", "authorized": False}))
                continue

            frame_type = frame.get("type")
            if frame_type == "ack":
                sequence = frame.get("sequence")
                if isinstance(sequence, int) and sequence >= last_ack:
                    last_ack = sequence
                continue
            if frame_type == "poll":
                await push_new_messages()
                continue
            if frame_type == "send":
                text = frame.get("text")
                if not isinstance(text, str) or not text.strip():
                    await websocket.send_text(json.dumps({
                        "type": "error", "reason": "text_required", "authorized": False}))
                    continue
                action = start_remote_action(
                    db, device_id, request_idempotency_key(frame))
                if action.get("terminal_replay"):
                    await websocket.send_text(json.dumps({
                        "type": "error", "reason": "terminal_replay_refused",
                        "action_id": action["action_id"], "authorized": False,
                    }))
                    continue
                db.commit()
                try:
                    posted = post_message(db, session["session_id"], text)
                    finished = complete_remote_action(
                        db, action["action_id"], f"relay-message:{posted['id']}")
                    db.commit()
                    await websocket.send_text(json.dumps({
                        "type": "accepted",
                        "sequence": int(posted["id"]),
                        "action_id": action["action_id"],
                        "action_state": finished["state"],
                        "authorized": False,
                    }))
                except Exception as exc:
                    db.rollback()
                    fail_remote_action(
                        db, action["action_id"],
                        f"relay_send_failed:{type(exc).__name__}", uncertain=True,
                    )
                    db.commit()
                    await websocket.send_text(json.dumps({
                        "type": "error", "reason": "send_recorded_uncertain",
                        "action_id": action["action_id"], "authorized": False,
                    }))
                continue

            await websocket.send_text(json.dumps({
                "type": "error", "reason": "unknown_frame_type", "authorized": False}))
    except WebSocketDisconnect:
        logger.info("Device relay socket disconnected for %s", device_id)
    except Exception as exc:  # pragma: no cover - never crash the server loop
        logger.warning("Device relay socket error for %s: %s", device_id, exc)
    finally:
        try:
            close_session(db, session["session_id"])
            db.commit()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass
