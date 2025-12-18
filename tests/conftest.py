"""Pytest configuration and shared fixtures for integration tests."""

import asyncio
import json
import queue
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add parent directory to path so tests can import from claude-army modules
sys.path.insert(0, str(Path(__file__).parent.parent))

# Enable pytest-asyncio
pytest_plugins = ['pytest_asyncio']


# =============================================================================
# JSONL Test Fixtures - Realistic Claude stream-json output
# =============================================================================

SYSTEM_INIT_EVENT = {
    "type": "system",
    "subtype": "init",
    "session_id": "test-session-abc123",
    "tools": [
        {"name": "Read", "type": "built_in"},
        {"name": "Write", "type": "built_in"},
        {"name": "Edit", "type": "built_in"},
        {"name": "Bash", "type": "built_in"},
        {"name": "Glob", "type": "built_in"},
        {"name": "Grep", "type": "built_in"},
    ],
    "model": "claude-sonnet-4-20250514",
}

ASSISTANT_TEXT_MESSAGE = {
    "type": "assistant",
    "message": {
        "id": "msg_01ABC123",
        "role": "assistant",
        "model": "claude-sonnet-4-20250514",
        "content": [
            {"type": "text", "text": "I'll help you with that task."}
        ],
    },
}

ASSISTANT_TOOL_USE_MESSAGE = {
    "type": "assistant",
    "message": {
        "id": "msg_02DEF456",
        "role": "assistant",
        "model": "claude-sonnet-4-20250514",
        "content": [
            {"type": "text", "text": "Let me read the file first."},
            {
                "type": "tool_use",
                "id": "toolu_01GHI789",
                "name": "Read",
                "input": {"file_path": "/home/user/test.py"},
            },
        ],
    },
}

ASSISTANT_BASH_TOOL_MESSAGE = {
    "type": "assistant",
    "message": {
        "id": "msg_03JKL012",
        "role": "assistant",
        "model": "claude-sonnet-4-20250514",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_02MNO345",
                "name": "Bash",
                "input": {
                    "command": "ls -la",
                    "description": "List files in directory",
                },
            },
        ],
    },
}

ASSISTANT_THINKING_MESSAGE = {
    "type": "assistant",
    "message": {
        "id": "msg_04PQR678",
        "role": "assistant",
        "model": "claude-sonnet-4-20250514",
        "content": [
            {"type": "thinking", "thinking": "Let me analyze this problem..."},
            {"type": "text", "text": "Here's my analysis."},
        ],
    },
}

USER_MESSAGE_ECHO = {
    "type": "user",
    "message": {
        "role": "user",
        "content": [{"type": "text", "text": "Hello Claude!"}],
    },
}

SESSION_RESULT_SUCCESS = {
    "type": "result",
    "subtype": "success",
    "result": "Task completed successfully.",
    "total_cost_usd": 0.0042,
    "is_error": False,
    "duration_ms": 5234,
    "duration_api_ms": 4800,
    "num_turns": 3,
    "session_id": "test-session-abc123",
}

SESSION_RESULT_ERROR = {
    "type": "result",
    "subtype": "error",
    "result": "Error: Something went wrong",
    "total_cost_usd": 0.001,
    "is_error": True,
    "duration_ms": 1000,
    "duration_api_ms": 800,
    "num_turns": 1,
    "session_id": "test-session-abc123",
}


# =============================================================================
# Mock Claude Subprocess
# =============================================================================


class MockClaudeSubprocess:
    """Mock Claude subprocess that emits JSONL events to stdout.

    Simulates the Claude CLI with stream-json output format.
    """

    def __init__(self, events: list[dict] | None = None, delay: float = 0.01):
        """Initialize mock subprocess.

        Args:
            events: List of events to emit (default: init + text message + result)
            delay: Delay between events in seconds
        """
        self.events = events or [
            SYSTEM_INIT_EVENT,
            ASSISTANT_TEXT_MESSAGE,
            SESSION_RESULT_SUCCESS,
        ]
        self.delay = delay
        self.stdin_messages: list[dict] = []
        self.returncode: int | None = None
        self.pid = 12345

        # Pipes for stdin/stdout/stderr
        self._stdout_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._stderr_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._stdin_buffer: list[str] = []
        self._closed = False

    @property
    def stdin(self):
        """Mock stdin with write() and drain()."""
        mock = MagicMock()
        mock.write = self._stdin_write
        mock.drain = AsyncMock()
        mock.close = MagicMock()
        mock.wait_closed = AsyncMock()
        return mock

    @property
    def stdout(self):
        """Mock stdout with readline()."""
        mock = MagicMock()
        mock.readline = self._stdout_readline
        return mock

    @property
    def stderr(self):
        """Mock stderr with readline()."""
        mock = MagicMock()
        mock.readline = self._stderr_readline
        return mock

    def _stdin_write(self, data: bytes):
        """Capture stdin writes."""
        line = data.decode('utf-8').strip()
        if line:
            try:
                msg = json.loads(line)
                self.stdin_messages.append(msg)
            except json.JSONDecodeError:
                pass

    async def _stdout_readline(self) -> bytes:
        """Return next event as JSONL."""
        try:
            return await asyncio.wait_for(self._stdout_queue.get(), timeout=0.1)
        except asyncio.TimeoutError:
            return b""

    async def _stderr_readline(self) -> bytes:
        """Return stderr (empty for mock)."""
        try:
            return await asyncio.wait_for(self._stderr_queue.get(), timeout=0.1)
        except asyncio.TimeoutError:
            return b""

    async def wait(self) -> int:
        """Wait for process to complete."""
        return self.returncode or 0

    def kill(self):
        """Kill the process."""
        self.returncode = -9
        self._closed = True

    async def emit_events(self):
        """Emit all events to stdout queue."""
        for event in self.events:
            line = json.dumps(event) + "\n"
            await self._stdout_queue.put(line.encode('utf-8'))
            await asyncio.sleep(self.delay)
        # Signal EOF
        self.returncode = 0


# =============================================================================
# Mock Telegram API Server
# =============================================================================


class MockTelegramServer:
    """Mock Telegram API server for testing.

    Tracks sent messages and provides mock updates.
    """

    def __init__(self, port: int = 0):
        self.port = port
        self._init_state()
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def _init_state(self):
        """Initialize/reset mutable state."""
        self.sent_messages: list[dict] = []
        self.pending_updates: list[dict] = []
        self.callback_answers: list[dict] = []
        self.edited_messages: list[dict] = []
        self.deleted_messages: list[int] = []
        self.update_id_counter = 1
        self.message_id_counter = 100

    def reset(self):
        """Reset state between tests (reuse server)."""
        self._init_state()

    def start(self):
        """Start the mock server in background thread."""
        handler = self._create_handler()
        self._server = HTTPServer(("localhost", self.port), handler)
        # Use shorter poll interval for faster shutdown
        self._server.timeout = 0.01
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._serve_with_timeout, daemon=True)
        self._thread.start()

    def _serve_with_timeout(self):
        """Serve with short poll interval for fast shutdown."""
        while self._server:
            self._server.handle_request()

    def stop(self):
        """Stop the mock server."""
        if self._server:
            server = self._server
            self._server = None
            server.server_close()

    @property
    def base_url(self) -> str:
        """Get base URL for API calls."""
        return f"http://localhost:{self.port}"

    def add_update(self, update: dict):
        """Add an update to be returned by getUpdates."""
        update["update_id"] = self.update_id_counter
        self.update_id_counter += 1
        self.pending_updates.append(update)

    def add_message_update(
        self,
        text: str,
        chat_id: int = -1001234567890,
        topic_id: int | None = None,
        from_user_id: int = 123456,
    ):
        """Add a message update."""
        msg_id = self.message_id_counter
        self.message_id_counter += 1

        message = {
            "message_id": msg_id,
            "from": {"id": from_user_id, "is_bot": False, "first_name": "Test"},
            "chat": {"id": chat_id, "type": "supergroup"},
            "date": 1700000000,
            "text": text,
        }
        if topic_id:
            message["message_thread_id"] = topic_id

        self.add_update({"message": message})
        return msg_id

    def add_callback_update(
        self,
        callback_data: str,
        msg_id: int,
        chat_id: int = -1001234567890,
        topic_id: int | None = None,
    ):
        """Add a callback query update."""
        callback = {
            "id": f"callback_{self.update_id_counter}",
            "from": {"id": 123456, "is_bot": False, "first_name": "Test"},
            "message": {
                "message_id": msg_id,
                "chat": {"id": chat_id},
            },
            "chat_instance": "test_instance",
            "data": callback_data,
        }
        if topic_id:
            callback["message"]["message_thread_id"] = topic_id

        self.add_update({"callback_query": callback})

    def _create_handler(self):
        """Create HTTP handler for mock API."""
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass  # Suppress logging

            def do_POST(self):
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length)
                data = json.loads(body) if body else {}

                # Route based on path
                path = self.path.split("/")[-1]

                if path == "getUpdates":
                    response = self._handle_get_updates(data)
                elif path == "sendMessage":
                    response = self._handle_send_message(data)
                elif path == "answerCallbackQuery":
                    response = self._handle_answer_callback(data)
                elif path == "editMessageReplyMarkup":
                    response = self._handle_edit_message(data)
                elif path == "deleteMessage":
                    response = self._handle_delete_message(data)
                elif path == "sendChatAction":
                    response = {"ok": True, "result": True}
                else:
                    response = {"ok": False, "description": f"Unknown method: {path}"}

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(response).encode())

            def do_GET(self):
                # Same as POST for Telegram API
                self.do_POST()

            def _handle_get_updates(self, data: dict) -> dict:
                offset = data.get("offset", 0)
                # Return updates with update_id >= offset
                updates = [u for u in server.pending_updates if u["update_id"] >= offset]
                # Clear returned updates
                server.pending_updates = [u for u in server.pending_updates if u["update_id"] < offset]
                return {"ok": True, "result": updates}

            def _handle_send_message(self, data: dict) -> dict:
                msg_id = server.message_id_counter
                server.message_id_counter += 1
                server.sent_messages.append({
                    "message_id": msg_id,
                    **data,
                })
                return {
                    "ok": True,
                    "result": {
                        "message_id": msg_id,
                        "chat": {"id": data.get("chat_id")},
                        "text": data.get("text"),
                    },
                }

            def _handle_answer_callback(self, data: dict) -> dict:
                server.callback_answers.append(data)
                return {"ok": True, "result": True}

            def _handle_edit_message(self, data: dict) -> dict:
                server.edited_messages.append(data)
                return {"ok": True, "result": {"message_id": data.get("message_id")}}

            def _handle_delete_message(self, data: dict) -> dict:
                server.deleted_messages.append(data.get("message_id"))
                return {"ok": True, "result": True}

        return Handler


# =============================================================================
# Mock Frontend Adapter
# =============================================================================


from frontend_adapter import FrontendAdapter, IncomingMessage


class MockFrontendAdapter(FrontendAdapter):
    """Mock frontend adapter for testing."""

    def __init__(self):
        self.sent_messages: list[dict] = []
        self.updated_messages: list[dict] = []
        self.deleted_messages: list[dict] = []
        self.typing_shown: list[str] = []
        self.incoming_queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        self.message_id_counter = 1

    async def send_message(
        self,
        task_id: str,
        content: str,
        buttons: list[dict] | None = None,
    ) -> str:
        msg_id = str(self.message_id_counter)
        self.message_id_counter += 1
        self.sent_messages.append({
            "msg_id": msg_id,
            "task_id": task_id,
            "content": content,
            "buttons": buttons,
        })
        return msg_id

    async def update_message(
        self,
        task_id: str,
        msg_id: str,
        content: str | None = None,
        buttons: list[dict] | None = None,
    ):
        self.updated_messages.append({
            "task_id": task_id,
            "msg_id": msg_id,
            "content": content,
            "buttons": buttons,
        })

    async def delete_message(self, task_id: str, msg_id: str):
        self.deleted_messages.append({"task_id": task_id, "msg_id": msg_id})

    async def show_typing(self, task_id: str):
        self.typing_shown.append(task_id)

    async def incoming_messages(self) -> AsyncIterator[IncomingMessage]:
        while True:
            msg = await self.incoming_queue.get()
            yield msg

    def add_incoming_message(
        self,
        task_id: str,
        text: str | None = None,
        callback_data: str | None = None,
        msg_id: str = "1",
        reply_to_msg_id: str | None = None,
    ):
        """Add message to incoming queue."""
        msg = IncomingMessage(
            task_id=task_id,
            text=text,
            callback_data=callback_data,
            msg_id=msg_id,
            reply_to_msg_id=reply_to_msg_id,
        )
        self.incoming_queue.put_nowait(msg)


# =============================================================================
# Helper Functions
# =============================================================================


def wait_for_pending(permission_manager, tool_use_id: str, timeout: float = 1.0):
    """Poll until permission is pending (avoids fixed sleep)."""
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if permission_manager.get_pending(tool_use_id) is not None:
            return True
        time.sleep(0.001)  # 1ms poll interval
    return False


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture(scope="module")
def _mock_telegram_server_instance():
    """Module-scoped mock Telegram server (single instance for fast tests)."""
    server = MockTelegramServer()
    server.start()
    yield server
    server.stop()


@pytest.fixture
def mock_telegram_server(_mock_telegram_server_instance):
    """Create mock Telegram server, reset between tests."""
    _mock_telegram_server_instance.reset()
    return _mock_telegram_server_instance


@pytest.fixture
def mock_frontend():
    """Create mock frontend adapter."""
    return MockFrontendAdapter()


@pytest.fixture
def permission_manager():
    """Create fresh PermissionManager."""
    from permission_server import PermissionManager
    return PermissionManager()


@pytest.fixture
def temp_dir():
    """Create temporary directory for tests."""
    with tempfile.TemporaryDirectory() as d:
        yield d


# =============================================================================
# Pure Logic Test Fixtures
# =============================================================================


@pytest.fixture
def mock_registry():
    """Mock registry with configurable tasks."""
    registry = MagicMock()
    registry.tasks = {}

    def get_task(name):
        return registry.tasks.get(name)

    def find_task_by_topic(topic_id):
        for name, data in registry.tasks.items():
            if data.get("topic_id") == topic_id:
                return (name, data)
        return None

    def get_all_tasks():
        return list(registry.tasks.items())

    def add_task(name, data):
        registry.tasks[name] = data

    registry.get_task = get_task
    registry.find_task_by_topic = find_task_by_topic
    registry.get_all_tasks = get_all_tasks
    registry.add_task = add_task
    return registry


@pytest.fixture
def mock_config():
    """Mock config with configurable values."""
    config = MagicMock()
    config.group_id = -1001234567890
    config.general_topic_id = 1
    config.operator_pane = "operator:0.0"

    def is_configured():
        return config.group_id is not None

    config.is_configured = is_configured
    return config


@pytest.fixture
def transcript_file(temp_dir):
    """Create a transcript file for testing."""
    path = Path(temp_dir) / "transcript.jsonl"

    def write_lines(lines):
        path.write_text("\n".join(lines) + "\n")
        return str(path)

    return write_lines
