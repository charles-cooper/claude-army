"""Permission server for Claude tool calls.

Manages permission requests from hooks, integrates with Telegram callbacks.
Thread-safe HTTP server that blocks hook requests until user responds.
"""

import asyncio
import json
import queue
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import AsyncIterator, Literal

from telegram_utils import (
    format_tool_permission, send_to_topic, answer_callback,
    update_message_buttons, log
)


@dataclass
class PendingPermission:
    """Tracks a pending permission request."""
    tool_name: str
    tool_input: dict
    tool_use_id: str
    session_id: str
    cwd: str
    response_queue: queue.Queue = field(default_factory=queue.Queue)
    telegram_msg_id: int | None = None


class PermissionManager:
    """Manages permission requests and responses.

    Thread-safe. Tracks pending permissions by tool_use_id.
    Auto-allows configured tools (Read, Grep, Glob, etc).
    Maps Telegram msg_id to tool_use_id for callback routing.
    """

    def __init__(self):
        self.pending: dict[str, PendingPermission] = {}
        self.auto_allow: set[str] = {
            "Read", "Grep", "Glob", "TodoRead", "TodoWrite"
        }
        self._lock = threading.Lock()
        # Map Telegram msg_id -> tool_use_id for callback routing
        self._msg_to_tool: dict[int, str] = {}
        # Async notification queue for permission requests
        self._notification_queue: asyncio.Queue = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Set asyncio loop for cross-thread signaling."""
        self._loop = loop

    def _signal_new_request(self, tool_use_id: str, session_id: str) -> None:
        """Signal asyncio loop about new request (called from HTTP thread)."""
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(
                self._notification_queue.put_nowait,
                (tool_use_id, session_id)
            )

    async def pending_notifications(self) -> AsyncIterator[tuple[str, str]]:
        """Yields (tool_use_id, session_id) for permissions needing notification."""
        while True:
            item = await self._notification_queue.get()
            if item is None:  # shutdown sentinel
                break
            yield item

    def request_permission(
        self,
        tool_name: str,
        tool_input: dict,
        tool_use_id: str,
        session_id: str,
        cwd: str
    ) -> tuple[Literal["allow", "deny"], str]:
        """Request permission for a tool call. Blocks until response.

        Returns (decision, reason).
        Auto-allows tools in auto_allow set.
        """
        # Auto-allow safe tools
        if tool_name in self.auto_allow:
            log(f"Auto-allowed: {tool_name} ({tool_use_id[:20]}...)")
            return ("allow", f"Auto-allowed: {tool_name}")

        # Create pending permission
        pending = PendingPermission(
            tool_name=tool_name,
            tool_input=tool_input,
            tool_use_id=tool_use_id,
            session_id=session_id,
            cwd=cwd
        )

        with self._lock:
            self.pending[tool_use_id] = pending

        log(f"Permission requested: {tool_name} ({tool_use_id[:20]}...)")

        # Signal async loop about new request
        self._signal_new_request(tool_use_id, session_id)

        # Block until response (or timeout)
        try:
            decision, reason = pending.response_queue.get(timeout=300)  # 5 min
            log(f"Permission {decision}: {tool_name} ({tool_use_id[:20]}...)")
            return (decision, reason)

        except queue.Empty:
            log(f"Permission timeout: {tool_name} ({tool_use_id[:20]}...)")
            return ("deny", "Permission request timed out")

        finally:
            # Clean up
            with self._lock:
                self.pending.pop(tool_use_id, None)
                # Clean up msg mapping if exists
                for msg_id, tid in list(self._msg_to_tool.items()):
                    if tid == tool_use_id:
                        del self._msg_to_tool[msg_id]

    def respond(self, tool_use_id: str, decision: Literal["allow", "deny"], reason: str = ""):
        """Respond to a pending permission request by tool_use_id.

        Unblocks the waiting request_permission call.
        """
        with self._lock:
            pending = self.pending.get(tool_use_id)
            if not pending:
                log(f"Respond failed: tool_use_id not found ({tool_use_id[:20]}...)")
                return False

        pending.response_queue.put((decision, reason))
        log(f"Responded {decision}: {pending.tool_name} ({tool_use_id[:20]}...)")
        return True

    def respond_by_msg_id(self, msg_id: int, decision: Literal["allow", "deny"], reason: str = ""):
        """Respond to a pending permission request by Telegram msg_id.

        Used for Telegram callback routing.
        """
        with self._lock:
            tool_use_id = self._msg_to_tool.get(msg_id)
            if not tool_use_id:
                log(f"Respond failed: msg_id {msg_id} not mapped to tool_use_id")
                return False

        return self.respond(tool_use_id, decision, reason)

    def register_telegram_msg(self, tool_use_id: str, msg_id: int):
        """Register a Telegram message for callback routing."""
        with self._lock:
            pending = self.pending.get(tool_use_id)
            if pending:
                pending.telegram_msg_id = msg_id
                self._msg_to_tool[msg_id] = tool_use_id
                log(f"Registered msg_id {msg_id} -> {tool_use_id[:20]}...")

    def get_pending(self, tool_use_id: str) -> PendingPermission | None:
        """Get pending permission by tool_use_id."""
        with self._lock:
            return self.pending.get(tool_use_id)


class PermissionHTTPHandler(BaseHTTPRequestHandler):
    """HTTP handler for permission requests.

    Expects POST to /permission/request with JSON:
    {
        "tool_name": str,
        "tool_input": dict,
        "tool_use_id": str,
        "session_id": str,
        "cwd": str
    }

    Blocks until user responds, then returns:
    {
        "decision": "allow" | "deny",
        "reason": str
    }
    """

    # Class variable set by server
    manager: PermissionManager = None

    def log_message(self, format, *args):
        """Override to use our log function."""
        log(f"HTTP: {format % args}")

    def do_POST(self):
        """Handle POST request."""
        if self.path != "/permission/request":
            self.send_error(404, "Not found")
            return

        try:
            # Read request body
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)

            tool_name = data.get("tool_name")
            tool_input = data.get("tool_input", {})
            tool_use_id = data.get("tool_use_id")
            session_id = data.get("session_id")
            cwd = data.get("cwd", "")

            if not all([tool_name, tool_use_id, session_id]):
                self.send_error(400, "Missing required fields")
                return

            # Request permission (blocks until response)
            decision, reason = self.manager.request_permission(
                tool_name, tool_input, tool_use_id, session_id, cwd
            )

            # Send response
            response = {
                "decision": decision,
                "reason": reason
            }

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

        except Exception as e:
            log(f"HTTP handler error: {e}")
            self.send_error(500, str(e))


def start_permission_server(manager: PermissionManager, host: str = "localhost", port: int = 9000):
    """Start the permission HTTP server.

    Runs in current thread (blocking). Use threading.Thread to run in background.
    """
    PermissionHTTPHandler.manager = manager

    server = HTTPServer((host, port), PermissionHTTPHandler)
    log(f"Permission server listening on {host}:{port}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("Permission server shutting down")
        server.shutdown()


def send_permission_notification(
    manager: PermissionManager,
    bot_token: str,
    chat_id: str,
    topic_id: int,
    tool_use_id: str
):
    """Send Telegram notification for a permission request.

    Sends message with Allow/Deny buttons, registers msg_id for callbacks.
    """
    pending = manager.get_pending(tool_use_id)
    if not pending:
        log(f"Cannot send notification: tool_use_id not found ({tool_use_id[:20]}...)")
        return

    # Format permission message
    text = format_tool_permission(
        pending.tool_name,
        pending.tool_input,
        markdown_v2=True
    )

    # Add buttons
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "✓ Allow", "callback_data": f"allow:{tool_use_id}"},
                {"text": "✗ Deny", "callback_data": f"deny:{tool_use_id}"}
            ]
        ]
    }

    # Send to topic
    resp = send_to_topic(bot_token, chat_id, topic_id, text, reply_markup)

    if resp and "result" in resp:
        msg_id = resp["result"]["message_id"]
        manager.register_telegram_msg(tool_use_id, msg_id)
        log(f"Sent permission notification: msg_id={msg_id}")
    else:
        log(f"Failed to send permission notification")


# Example integration with Telegram callbacks:
def handle_permission_callback(
    manager: PermissionManager,
    bot_token: str,
    callback_data: str,
    callback_id: str,
    msg_id: int,
    chat_id: str
):
    """Handle Telegram callback for permission response.

    callback_data format: "allow:tool_use_id" or "deny:tool_use_id"
    """
    if ":" not in callback_data:
        return False

    action, tool_use_id = callback_data.split(":", 1)

    if action not in ("allow", "deny"):
        return False

    # Respond to permission
    if not manager.respond(tool_use_id, action):
        answer_callback(bot_token, callback_id, "Permission not found")
        return False

    # Update button
    label = "✓ Allowed" if action == "allow" else "✗ Denied"
    update_message_buttons(bot_token, chat_id, msg_id, label)

    # Answer callback
    answer_callback(bot_token, callback_id, label)

    return True
