"""
Browser Bridge Service for Sythion.

Manages bidirectional communication between the Sythion FastAPI backend
and the Sythion Chrome/Edge browser extension via WebSockets.
"""

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger("backend.services.browser_bridge")


class BrowserBridgeService:
    """Singleton service managing browser extension WebSocket connections and RPC."""

    _instance: Optional["BrowserBridgeService"] = None

    def __init__(self):
        self._active_socket: Optional[WebSocket] = None
        self._pending_requests: Dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()
        self._browser_info: Dict[str, Any] = {}

    @classmethod
    def get_instance(cls) -> "BrowserBridgeService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def is_connected(self) -> bool:
        return self._active_socket is not None

    @property
    def browser_info(self) -> Dict[str, Any]:
        return dict(self._browser_info)

    async def connect(self, websocket: WebSocket, client_info: Optional[Dict[str, Any]] = None) -> None:
        """Register an active extension connection."""
        async with self._lock:
            if self._active_socket is not None:
                try:
                    await self._active_socket.close()
                except Exception as exc:
                    logger.debug("BrowserBridgeService.connect: error closing previous active socket: %s: %s",
                                 type(exc).__name__, exc)
            self._active_socket = websocket
            self._browser_info = client_info or {"name": "Chromium", "connected_at": asyncio.get_event_loop().time()}
            logger.info("Browser extension connected successfully: %s", self._browser_info)

    async def disconnect(self) -> None:
        """Clean up when extension disconnects."""
        async with self._lock:
            self._active_socket = None
            self._browser_info = {}
            # Cancel any pending requests
            for req_id, future in list(self._pending_requests.items()):
                if not future.done():
                    future.set_exception(RuntimeError("Browser extension disconnected"))
            self._pending_requests.clear()
            logger.info("Browser extension disconnected")

    def handle_incoming_message(self, data: str) -> None:
        """Handle incoming JSON-RPC response or event from the extension."""
        try:
            msg = json.loads(data)
            if msg.get("type") == "ping":
                if self._active_socket:
                    asyncio.create_task(self._active_socket.send_text(json.dumps({"type": "pong"})))
                return

            req_id = msg.get("id")
            if req_id and req_id in self._pending_requests:
                future = self._pending_requests.pop(req_id)
                if not future.done():
                    if msg.get("error"):
                        future.set_exception(RuntimeError(msg["error"]))
                    else:
                        future.set_result(msg.get("result"))
            else:
                logger.debug("Received event or unprompted message from browser: %s", msg)
        except Exception as e:
            logger.warning("Error parsing message from browser extension: %s", e)


    async def send_command(self, action: str, params: Optional[Dict[str, Any]] = None, timeout: float = 8.0) -> Any:
        """Send a command to the browser extension and wait for the result."""
        if not self._active_socket:
            raise RuntimeError("Browser extension is not connected. Load the Sythion extension in Chrome/Edge.")

        req_id = str(uuid.uuid4())
        payload = {
            "id": req_id,
            "action": action,
            "params": params or {},
        }

        future = asyncio.get_event_loop().create_future()
        self._pending_requests[req_id] = future

        try:
            await self._active_socket.send_text(json.dumps(payload))
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            self._pending_requests.pop(req_id, None)
            raise TimeoutError(f"Browser command '{action}' timed out after {timeout}s")
        except Exception:
            self._pending_requests.pop(req_id, None)
            raise

    # High-level helper methods for tools

    async def get_tabs(self) -> List[Dict[str, Any]]:
        """Retrieve all open tabs across all browser windows."""
        result = await self.send_command("get_tabs")
        return result.get("tabs", []) if isinstance(result, dict) else []

    async def switch_tab(self, tab_id: int) -> Dict[str, Any]:
        """Activate/focus a specific browser tab by ID."""
        return await self.send_command("switch_tab", {"tab_id": tab_id})

    async def open_tab(self, url: str, active: bool = True) -> Dict[str, Any]:
        """Open a new tab with the given URL."""
        return await self.send_command("open_tab", {"url": url, "active": active})

    async def close_tab(self, tab_id: int) -> Dict[str, Any]:
        """Close a specific browser tab by ID."""
        return await self.send_command("close_tab", {"tab_id": tab_id})

    async def read_page(self, tab_id: Optional[int] = None) -> Dict[str, Any]:
        """Extract clean text content, title, and metadata from the active or specified tab."""
        return await self.send_command("read_page", {"tab_id": tab_id} if tab_id else {})

    async def interact_page(
        self,
        action_type: str,
        selector: Optional[str] = None,
        text: Optional[str] = None,
        key: Optional[str] = None,
        tab_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Click, type, scroll, or press keys on the active or specified webpage."""
        return await self.send_command("interact_page", {
            "action_type": action_type,
            "selector": selector,
            "text": text,
            "key": key,
            "tab_id": tab_id,
        })

    async def take_screenshot(

        self,
        tab_id: Optional[int] = None,
        window_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Capture a visual PNG screenshot of the active or specified browser tab."""
        return await self.send_command("take_screenshot", {
            "tab_id": tab_id,
            "window_id": window_id,
        })
