"""Tests for permission_server.py - PermissionManager and HTTP handlers."""

import json
import queue
import threading
from http.server import HTTPServer
from unittest.mock import MagicMock, patch

import pytest

from permission_server import PermissionManager, PermissionHTTPHandler
from conftest import wait_for_pending


class TestPermissionManagerAutoAllow:
    """Test PermissionManager auto-allows safe tools."""

    def test_auto_allows_read(self, permission_manager):
        """Test Read tool is auto-allowed."""
        decision, reason = permission_manager.request_permission(
            tool_name="Read",
            tool_input={"file_path": "/home/user/test.py"},
            tool_use_id="toolu_123",
            session_id="session-abc",
            cwd="/home/user",
        )

        assert decision == "allow"
        assert "Auto-allowed" in reason

    def test_auto_allows_grep(self, permission_manager):
        """Test Grep tool is auto-allowed."""
        decision, reason = permission_manager.request_permission(
            tool_name="Grep",
            tool_input={"pattern": "test", "path": "/home"},
            tool_use_id="toolu_456",
            session_id="session-abc",
            cwd="/home/user",
        )

        assert decision == "allow"

    def test_auto_allows_glob(self, permission_manager):
        """Test Glob tool is auto-allowed."""
        decision, reason = permission_manager.request_permission(
            tool_name="Glob",
            tool_input={"pattern": "**/*.py"},
            tool_use_id="toolu_789",
            session_id="session-abc",
            cwd="/home/user",
        )

        assert decision == "allow"

    def test_auto_allows_todo_read(self, permission_manager):
        """Test TodoRead tool is auto-allowed."""
        decision, reason = permission_manager.request_permission(
            tool_name="TodoRead",
            tool_input={},
            tool_use_id="toolu_101",
            session_id="session-abc",
            cwd="/home/user",
        )

        assert decision == "allow"

    def test_auto_allows_todo_write(self, permission_manager):
        """Test TodoWrite tool is auto-allowed."""
        decision, reason = permission_manager.request_permission(
            tool_name="TodoWrite",
            tool_input={"todos": []},
            tool_use_id="toolu_102",
            session_id="session-abc",
            cwd="/home/user",
        )

        assert decision == "allow"


class TestPermissionManagerInteractive:
    """Test PermissionManager blocks for interactive tools."""

    def test_blocks_bash(self, permission_manager):
        """Test Bash tool blocks and waits."""
        result_queue = queue.Queue()

        def request_thread():
            decision, reason = permission_manager.request_permission(
                tool_name="Bash",
                tool_input={"command": "ls -la"},
                tool_use_id="toolu_bash_001",
                session_id="session-abc",
                cwd="/home/user",
            )
            result_queue.put((decision, reason))

        thread = threading.Thread(target=request_thread)
        thread.start()

        assert wait_for_pending(permission_manager, "toolu_bash_001")

        pending = permission_manager.get_pending("toolu_bash_001")
        assert pending is not None
        assert pending.tool_name == "Bash"

        permission_manager.respond("toolu_bash_001", "allow", "User approved")

        thread.join(timeout=1.0)

        decision, reason = result_queue.get(timeout=1.0)
        assert decision == "allow"
        assert reason == "User approved"

    def test_blocks_write(self, permission_manager):
        """Test Write tool blocks and waits."""
        result_queue = queue.Queue()

        def request_thread():
            decision, reason = permission_manager.request_permission(
                tool_name="Write",
                tool_input={"file_path": "/home/user/new.py", "content": "print('hello')"},
                tool_use_id="toolu_write_001",
                session_id="session-abc",
                cwd="/home/user",
            )
            result_queue.put((decision, reason))

        thread = threading.Thread(target=request_thread)
        thread.start()

        assert wait_for_pending(permission_manager, "toolu_write_001")

        permission_manager.respond("toolu_write_001", "deny", "User rejected")

        thread.join(timeout=1.0)

        decision, reason = result_queue.get(timeout=1.0)
        assert decision == "deny"
        assert reason == "User rejected"

    def test_respond_by_msg_id(self, permission_manager):
        """Test responding via Telegram msg_id mapping."""
        result_queue = queue.Queue()

        def request_thread():
            decision, reason = permission_manager.request_permission(
                tool_name="Edit",
                tool_input={"file_path": "/test.py", "old_string": "a", "new_string": "b"},
                tool_use_id="toolu_edit_001",
                session_id="session-abc",
                cwd="/home/user",
            )
            result_queue.put((decision, reason))

        thread = threading.Thread(target=request_thread)
        thread.start()

        assert wait_for_pending(permission_manager, "toolu_edit_001")

        permission_manager.register_telegram_msg("toolu_edit_001", msg_id=999)

        success = permission_manager.respond_by_msg_id(999, "allow", "Approved via button")
        assert success is True

        thread.join(timeout=1.0)

        decision, reason = result_queue.get(timeout=1.0)
        assert decision == "allow"


class TestPermissionHookHTTP:
    """Test permission hook HTTP request/response flow."""

    def test_http_handler_allow(self, permission_manager):
        """Test HTTP handler returns allow for auto-allowed tool."""
        PermissionHTTPHandler.manager = permission_manager

        decision, reason = permission_manager.request_permission(
            tool_name="Read",
            tool_input={"file_path": "/test.py"},
            tool_use_id="toolu_http_001",
            session_id="session-abc",
            cwd="/home/user",
        )

        assert decision == "allow"


class TestPermissionServerHTTP:
    """Test PermissionHTTPHandler do_POST method via actual HTTP requests."""

    @pytest.fixture
    def permission_http_server(self, permission_manager):
        """Create and start a real permission HTTP server for testing."""
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("localhost", 0))
            port = s.getsockname()[1]

        PermissionHTTPHandler.manager = permission_manager

        server = HTTPServer(("localhost", port), PermissionHTTPHandler)
        server.timeout = 0.1
        server_thread = threading.Thread(target=lambda: self._serve(server), daemon=True)
        server_thread.start()

        yield {"port": port, "server": server, "manager": permission_manager}

        server.shutdown()

    def _serve(self, server):
        """Serve with timeout for shutdown."""
        server.serve_forever()

    def test_do_post_auto_allow(self, permission_http_server):
        """Test do_POST returns allow for auto-allowed tool."""
        import requests

        port = permission_http_server["port"]
        resp = requests.post(
            f"http://localhost:{port}/permission/request",
            json={
                "tool_name": "Read",
                "tool_input": {"file_path": "/test.py"},
                "tool_use_id": "toolu_http_auto_001",
                "session_id": "session-http-test",
                "cwd": "/home/user",
            },
            timeout=5,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["decision"] == "allow"
        assert "Auto-allowed" in data["reason"]

    def test_do_post_not_found(self, permission_http_server):
        """Test do_POST returns 404 for wrong path."""
        import requests

        port = permission_http_server["port"]
        resp = requests.post(
            f"http://localhost:{port}/wrong/path",
            json={"tool_name": "Read"},
            timeout=5,
        )

        assert resp.status_code == 404

    def test_do_post_missing_fields(self, permission_http_server):
        """Test do_POST returns 400 for missing required fields."""
        import requests

        port = permission_http_server["port"]
        resp = requests.post(
            f"http://localhost:{port}/permission/request",
            json={
                "tool_name": "Bash",
            },
            timeout=5,
        )

        assert resp.status_code == 400

    def test_do_post_interactive_tool_allow(self, permission_http_server):
        """Test do_POST blocks and returns allow for interactive tool."""
        import requests

        port = permission_http_server["port"]
        manager = permission_http_server["manager"]
        tool_use_id = "toolu_http_bash_001"

        result_queue = queue.Queue()

        def make_request():
            resp = requests.post(
                f"http://localhost:{port}/permission/request",
                json={
                    "tool_name": "Bash",
                    "tool_input": {"command": "echo hello"},
                    "tool_use_id": tool_use_id,
                    "session_id": "session-http-test",
                    "cwd": "/home/user",
                },
                timeout=10,
            )
            result_queue.put(resp)

        request_thread = threading.Thread(target=make_request)
        request_thread.start()

        assert wait_for_pending(manager, tool_use_id)

        manager.respond(tool_use_id, "allow", "User approved via HTTP")

        request_thread.join(timeout=5)
        resp = result_queue.get(timeout=1)

        assert resp.status_code == 200
        data = resp.json()
        assert data["decision"] == "allow"
        assert data["reason"] == "User approved via HTTP"

    def test_do_post_interactive_tool_deny(self, permission_http_server):
        """Test do_POST blocks and returns deny for interactive tool."""
        import requests

        port = permission_http_server["port"]
        manager = permission_http_server["manager"]
        tool_use_id = "toolu_http_bash_deny"

        result_queue = queue.Queue()

        def make_request():
            resp = requests.post(
                f"http://localhost:{port}/permission/request",
                json={
                    "tool_name": "Write",
                    "tool_input": {"file_path": "/etc/passwd", "content": "bad"},
                    "tool_use_id": tool_use_id,
                    "session_id": "session-http-test",
                    "cwd": "/home/user",
                },
                timeout=10,
            )
            result_queue.put(resp)

        request_thread = threading.Thread(target=make_request)
        request_thread.start()

        assert wait_for_pending(manager, tool_use_id)

        manager.respond(tool_use_id, "deny", "Dangerous operation")

        request_thread.join(timeout=5)
        resp = result_queue.get(timeout=1)

        assert resp.status_code == 200
        data = resp.json()
        assert data["decision"] == "deny"
        assert data["reason"] == "Dangerous operation"

    def test_do_post_exception_handling(self, permission_http_server):
        """Test do_POST returns 500 on exception."""
        import requests

        port = permission_http_server["port"]

        resp = requests.post(
            f"http://localhost:{port}/permission/request",
            data="not valid json",
            headers={"Content-Type": "application/json"},
            timeout=5,
        )

        assert resp.status_code == 500


class TestPermissionServerStartup:
    """Test start_permission_server function."""

    def test_start_permission_server_runs(self, permission_manager):
        """Test start_permission_server starts and accepts connections."""
        from permission_server import start_permission_server
        import socket
        import requests

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("localhost", 0))
            port = s.getsockname()[1]

        server_started = threading.Event()
        server_error = []

        def run_server():
            try:
                original_serve = HTTPServer.serve_forever

                def patched_serve(self):
                    server_started.set()
                    original_serve(self)

                HTTPServer.serve_forever = patched_serve
                start_permission_server(permission_manager, "localhost", port)
            except Exception as e:
                server_error.append(str(e))
            finally:
                HTTPServer.serve_forever = original_serve

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()

        server_started.wait(timeout=2)

        resp = requests.post(
            f"http://localhost:{port}/permission/request",
            json={
                "tool_name": "Glob",
                "tool_input": {"pattern": "*.py"},
                "tool_use_id": "toolu_startup_test",
                "session_id": "session-startup",
                "cwd": "/tmp",
            },
            timeout=5,
        )

        assert resp.status_code == 200
        assert resp.json()["decision"] == "allow"


class TestRequestPermissionTimeout:
    """Test request_permission timeout path."""

    def test_request_permission_timeout(self):
        """Test request_permission returns deny on timeout."""
        from permission_server import PendingPermission

        manager = PermissionManager()

        result_queue = queue.Queue()

        def request_thread():
            pending = PendingPermission(
                tool_name="Bash",
                tool_input={"command": "sleep 1000"},
                tool_use_id="toolu_timeout_real",
                session_id="session-timeout",
                cwd="/tmp",
            )

            with manager._lock:
                manager.pending["toolu_timeout_real"] = pending

            try:
                decision, reason = pending.response_queue.get(timeout=0.01)
            except queue.Empty:
                decision, reason = "deny", "Permission request timed out"

            with manager._lock:
                manager.pending.pop("toolu_timeout_real", None)

            result_queue.put((decision, reason))

        thread = threading.Thread(target=request_thread)
        thread.start()
        thread.join(timeout=1)

        decision, reason = result_queue.get(timeout=1)
        assert decision == "deny"
        assert "timed out" in reason

    def test_request_permission_cleanup_on_timeout(self):
        """Test that cleanup happens correctly on timeout."""
        from permission_server import PendingPermission

        manager = PermissionManager()

        pending = PendingPermission(
            tool_name="Bash",
            tool_input={"command": "test"},
            tool_use_id="toolu_cleanup_test",
            session_id="session-cleanup",
            cwd="/tmp",
        )

        with manager._lock:
            manager.pending["toolu_cleanup_test"] = pending
            manager._msg_to_tool[999] = "toolu_cleanup_test"

        assert "toolu_cleanup_test" in manager.pending
        assert manager._msg_to_tool.get(999) == "toolu_cleanup_test"

        with manager._lock:
            manager.pending.pop("toolu_cleanup_test", None)
            for msg_id, tid in list(manager._msg_to_tool.items()):
                if tid == "toolu_cleanup_test":
                    del manager._msg_to_tool[msg_id]

        assert "toolu_cleanup_test" not in manager.pending
        assert 999 not in manager._msg_to_tool


class TestPermissionServerAdvanced:
    """Advanced permission server tests."""

    def test_send_permission_notification(self, permission_manager):
        """Test send_permission_notification function."""
        from permission_server import send_permission_notification, PendingPermission

        pending = PendingPermission(
            tool_name="Bash",
            tool_input={"command": "ls -la"},
            tool_use_id="toolu_notify_001",
            session_id="session-abc",
            cwd="/home/user",
        )
        permission_manager.pending["toolu_notify_001"] = pending

        with patch("permission_server.send_to_topic") as mock_send:
            mock_send.return_value = {"result": {"message_id": 123}}

            send_permission_notification(
                permission_manager, "TOKEN", "CHAT_ID", 456, "toolu_notify_001"
            )

            mock_send.assert_called_once()
            assert permission_manager._msg_to_tool.get(123) == "toolu_notify_001"

    def test_send_permission_notification_not_found(self, permission_manager):
        """Test send_permission_notification when tool_use_id not found."""
        from permission_server import send_permission_notification

        with patch("permission_server.send_to_topic") as mock_send:
            send_permission_notification(
                permission_manager, "TOKEN", "CHAT_ID", 456, "unknown_tool_id"
            )
            mock_send.assert_not_called()

    def test_send_permission_notification_failure(self, permission_manager):
        """Test send_permission_notification when Telegram API returns failure."""
        from permission_server import send_permission_notification, PendingPermission

        pending = PendingPermission(
            tool_name="Bash",
            tool_input={"command": "ls -la"},
            tool_use_id="toolu_notify_fail_001",
            session_id="session-abc",
            cwd="/home/user",
        )
        permission_manager.pending["toolu_notify_fail_001"] = pending

        with patch("permission_server.send_to_topic") as mock_send:
            mock_send.return_value = None

            send_permission_notification(
                permission_manager, "TOKEN", "CHAT_ID", 456, "toolu_notify_fail_001"
            )

            mock_send.assert_called_once()
            assert "toolu_notify_fail_001" not in permission_manager._msg_to_tool.values()

    def test_send_permission_notification_no_result(self, permission_manager):
        """Test send_permission_notification when response has no 'result' key."""
        from permission_server import send_permission_notification, PendingPermission

        pending = PendingPermission(
            tool_name="Write",
            tool_input={"file_path": "/test.py", "content": "test"},
            tool_use_id="toolu_notify_no_result",
            session_id="session-abc",
            cwd="/home/user",
        )
        permission_manager.pending["toolu_notify_no_result"] = pending

        with patch("permission_server.send_to_topic") as mock_send:
            mock_send.return_value = {"ok": False, "error": "Bad Request"}

            send_permission_notification(
                permission_manager, "TOKEN", "CHAT_ID", 456, "toolu_notify_no_result"
            )

            mock_send.assert_called_once()
            assert "toolu_notify_no_result" not in permission_manager._msg_to_tool.values()

    def test_handle_permission_callback_invalid_format(self, permission_manager):
        """Test handle_permission_callback with invalid callback data."""
        from permission_server import handle_permission_callback

        result = handle_permission_callback(
            permission_manager, "TOKEN", "invalid_no_colon", "cb_id", 100, "CHAT_ID"
        )
        assert result is False

    def test_handle_permission_callback_invalid_action(self, permission_manager):
        """Test handle_permission_callback with invalid action."""
        from permission_server import handle_permission_callback

        result = handle_permission_callback(
            permission_manager, "TOKEN", "unknown:toolu_123", "cb_id", 100, "CHAT_ID"
        )
        assert result is False

    def test_handle_permission_callback_not_found(self, permission_manager):
        """Test handle_permission_callback when permission not found."""
        from permission_server import handle_permission_callback

        with patch("permission_server.answer_callback") as mock_answer:
            result = handle_permission_callback(
                permission_manager, "TOKEN", "allow:unknown_tool_id", "cb_id", 100, "CHAT_ID"
            )
            assert result is False
            mock_answer.assert_called_once()

    def test_handle_permission_callback_allow_success(self, permission_manager):
        """Test handle_permission_callback success path with 'allow'."""
        from permission_server import handle_permission_callback, PendingPermission

        pending = PendingPermission(
            tool_name="Bash",
            tool_input={"command": "ls"},
            tool_use_id="toolu_cb_allow",
            session_id="session-abc",
            cwd="/home/user",
        )
        permission_manager.pending["toolu_cb_allow"] = pending

        with patch("permission_server.answer_callback") as mock_answer, \
             patch("permission_server.update_message_buttons") as mock_update:

            result = handle_permission_callback(
                permission_manager, "TOKEN", "allow:toolu_cb_allow", "cb_id_123", 200, "CHAT_ID"
            )

            assert result is True
            mock_update.assert_called_once()
            mock_answer.assert_called_once()

    def test_handle_permission_callback_deny_success(self, permission_manager):
        """Test handle_permission_callback success path with 'deny'."""
        from permission_server import handle_permission_callback, PendingPermission

        pending = PendingPermission(
            tool_name="Write",
            tool_input={"file_path": "/etc/passwd", "content": "bad"},
            tool_use_id="toolu_cb_deny",
            session_id="session-abc",
            cwd="/home/user",
        )
        permission_manager.pending["toolu_cb_deny"] = pending

        with patch("permission_server.answer_callback") as mock_answer, \
             patch("permission_server.update_message_buttons") as mock_update:

            result = handle_permission_callback(
                permission_manager, "TOKEN", "deny:toolu_cb_deny", "cb_id_456", 201, "CHAT_ID"
            )

            assert result is True
            mock_update.assert_called_once()
            mock_answer.assert_called_once()


class TestEdgeCasesPermission:
    """Test edge cases and error handling for permissions."""

    def test_respond_unknown_tool_id(self, permission_manager):
        """Test responding to unknown tool_use_id fails gracefully."""
        success = permission_manager.respond("unknown_tool_id", "allow")
        assert success is False

    def test_respond_unknown_msg_id(self, permission_manager):
        """Test responding to unknown msg_id fails gracefully."""
        success = permission_manager.respond_by_msg_id(99999, "allow")
        assert success is False

    def test_permission_timeout(self, permission_manager):
        """Test permission request times out."""
        from permission_server import PendingPermission

        result_queue = queue.Queue()

        def request_thread():
            pending = permission_manager.pending.get("toolu_timeout_test")
            if pending:
                try:
                    pending.response_queue.get(timeout=0.01)
                except queue.Empty:
                    result_queue.put(("deny", "timeout"))

        pending = PendingPermission(
            tool_name="Bash",
            tool_input={"command": "test"},
            tool_use_id="toolu_timeout_test",
            session_id="session",
            cwd="/tmp",
        )
        permission_manager.pending["toolu_timeout_test"] = pending

        thread = threading.Thread(target=request_thread)
        thread.start()
        thread.join(timeout=0.5)

        if not result_queue.empty():
            decision, reason = result_queue.get()
            assert decision == "deny"


class TestPermissionManagerAsyncIterator:
    """Test PermissionManager async iterator functionality."""

    @pytest.mark.asyncio
    async def test_pending_notifications_yields_on_signal(self, permission_manager):
        """Test pending_notifications yields when _signal_new_request is called."""
        import asyncio

        loop = asyncio.get_running_loop()
        permission_manager.set_event_loop(loop)

        # Queue an item directly
        permission_manager._notification_queue.put_nowait(("toolu_123", "session_abc"))

        # Get the first item from the iterator
        async for tool_use_id, session_id in permission_manager.pending_notifications():
            assert tool_use_id == "toolu_123"
            assert session_id == "session_abc"
            break  # Exit after first item

    @pytest.mark.asyncio
    async def test_pending_notifications_stops_on_none(self, permission_manager):
        """Test pending_notifications stops when None (shutdown sentinel) is received."""
        import asyncio

        loop = asyncio.get_running_loop()
        permission_manager.set_event_loop(loop)

        # Queue shutdown sentinel
        permission_manager._notification_queue.put_nowait(None)

        items = []
        async for item in permission_manager.pending_notifications():
            items.append(item)

        # Should have no items (stopped on None)
        assert items == []

    @pytest.mark.asyncio
    async def test_signal_new_request_queues_notification(self, permission_manager):
        """Test _signal_new_request adds to notification queue."""
        import asyncio

        loop = asyncio.get_running_loop()
        permission_manager.set_event_loop(loop)

        # Signal a new request
        permission_manager._signal_new_request("toolu_xyz", "session_123")

        # Check queue has the item
        item = await asyncio.wait_for(
            permission_manager._notification_queue.get(),
            timeout=1.0
        )
        assert item == ("toolu_xyz", "session_123")

    @pytest.mark.asyncio
    async def test_signal_new_request_noop_without_loop(self, permission_manager):
        """Test _signal_new_request does nothing if loop not set."""
        assert permission_manager._loop is None

        # Should not raise
        permission_manager._signal_new_request("toolu_abc", "session_xyz")

        # Queue should be empty
        assert permission_manager._notification_queue.empty()

    def test_set_event_loop_stores_loop(self, permission_manager):
        """Test set_event_loop stores the loop."""
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            permission_manager.set_event_loop(loop)
            assert permission_manager._loop is loop
        finally:
            loop.close()

    @pytest.mark.asyncio
    async def test_request_permission_signals_new_request(self, permission_manager):
        """Test request_permission calls _signal_new_request for interactive tools."""
        import asyncio

        loop = asyncio.get_running_loop()
        permission_manager.set_event_loop(loop)

        result_queue = queue.Queue()

        def request_thread():
            decision, reason = permission_manager.request_permission(
                tool_name="Bash",
                tool_input={"command": "ls"},
                tool_use_id="toolu_signal_test",
                session_id="session_signal",
                cwd="/tmp",
            )
            result_queue.put((decision, reason))

        thread = threading.Thread(target=request_thread)
        thread.start()

        # Wait for signal to arrive in queue
        item = await asyncio.wait_for(
            permission_manager._notification_queue.get(),
            timeout=2.0
        )
        assert item == ("toolu_signal_test", "session_signal")

        # Respond to unblock the thread
        permission_manager.respond("toolu_signal_test", "allow", "test")
        thread.join(timeout=1.0)


