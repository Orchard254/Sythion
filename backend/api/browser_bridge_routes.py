"""
Browser Bridge API Routes for Sythion.

Provides the WebSocket endpoint for browser extensions and REST status checks.
"""

import json
import logging
from typing import Any, Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.services.browser_bridge_service import BrowserBridgeService

logger = logging.getLogger("backend.api.browser_bridge")
router = APIRouter(prefix="/api/v1/browser", tags=["browser_bridge"])


@router.websocket("/ws")
async def browser_websocket_endpoint(websocket: WebSocket):
    """Bidirectional WebSocket connection for the Sythion browser extension.

    PROTECTED RUNTIME CONTRACT: this endpoint sits behind the same
    LocalControlBoundaryMiddleware as every other API route. The extension
    authenticates with the per-launch control token passed as a query-string
    parameter (browsers cannot set custom headers on WebSocket handshakes)
    plus a chrome-extension:// origin, and the peer must be on loopback.
    Unauthenticated or wrongly authenticated connections are rejected at the
    handshake by the middleware before this handler runs.
    """
    await websocket.accept()
    service = BrowserBridgeService.get_instance()
    
    try:
        # Initial handshake: receive client info
        init_data = await websocket.receive_text()
        try:
            client_info = json.loads(init_data)
        except Exception:
            client_info = {"browser": "Chromium"}

        await service.connect(websocket, client_info)
        await websocket.send_text(json.dumps({"type": "handshake_ok", "status": "connected"}))

        while True:
            data = await websocket.receive_text()
            service.handle_incoming_message(data)

    except WebSocketDisconnect:
        logger.info("Browser extension disconnected from /api/v1/browser/ws")
    except Exception as e:
        logger.warning("Browser WebSocket error: %s", e)
    finally:
        await service.disconnect()


@router.get("/status")
async def get_browser_bridge_status() -> Dict[str, Any]:
    """Check whether the browser extension is currently connected to Sythion."""
    service = BrowserBridgeService.get_instance()
    return {
        "connected": service.is_connected,
        "browser_info": service.browser_info if service.is_connected else None,
    }
