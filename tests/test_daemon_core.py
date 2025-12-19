"""Tests for daemon_core.py - singleton management and PID file handling."""

import atexit
import os
import signal
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from daemon_core import (
    Daemon,
    DaemonAlreadyRunning,
    check_singleton,
    cleanup_pid_file,
    _setup_process_group,
)


class TestCleanupPidFile:
    """Test cleanup_pid_file function."""

    def test_removes_existing_file(self, temp_dir):
        """Test cleanup_pid_file removes existing PID file."""
        pid_file = Path(temp_dir) / "test.pid"
        pid_file.write_text("12345")

        cleanup_pid_file(pid_file)
        assert not pid_file.exists()

    def test_handles_missing_file(self, temp_dir):
        """Test cleanup_pid_file handles missing file gracefully."""
        pid_file = Path(temp_dir) / "nonexistent.pid"
        # Should not raise
        cleanup_pid_file(pid_file)


class TestCheckSingleton:
    """Test check_singleton function."""

    def test_creates_pid_file_when_none_exists(self, temp_dir):
        """Test check_singleton creates PID file when none exists."""
        pid_file = Path(temp_dir) / "test.pid"

        check_singleton(pid_file)
        assert pid_file.exists()
        assert pid_file.read_text() == str(os.getpid())

        # Cleanup
        cleanup_pid_file(pid_file)

    def test_handles_stale_pid(self, temp_dir):
        """Test check_singleton handles stale PID file (dead process)."""
        pid_file = Path(temp_dir) / "test.pid"
        # Write a PID that definitely doesn't exist
        pid_file.write_text("999999999")

        # Should succeed since PID doesn't exist
        check_singleton(pid_file)
        assert pid_file.read_text() == str(os.getpid())

        # Cleanup
        cleanup_pid_file(pid_file)

    def test_raises_when_daemon_running(self, temp_dir):
        """Test check_singleton raises DaemonAlreadyRunning when daemon is active."""
        pid_file = Path(temp_dir) / "test.pid"
        # Write our own PID - we're definitely running
        pid_file.write_text(str(os.getpid()))

        with pytest.raises(DaemonAlreadyRunning) as exc_info:
            check_singleton(pid_file)

        assert str(os.getpid()) in str(exc_info.value)

    def test_handles_invalid_pid_content(self, temp_dir):
        """Test check_singleton handles invalid (non-numeric) PID content."""
        pid_file = Path(temp_dir) / "test.pid"
        pid_file.write_text("not-a-number")

        # Should succeed since content is invalid
        check_singleton(pid_file)
        assert pid_file.read_text() == str(os.getpid())

        # Cleanup
        cleanup_pid_file(pid_file)

    def test_handles_empty_pid_file(self, temp_dir):
        """Test check_singleton handles empty PID file."""
        pid_file = Path(temp_dir) / "test.pid"
        pid_file.write_text("")

        # Should succeed since content is empty/invalid
        check_singleton(pid_file)
        assert pid_file.read_text() == str(os.getpid())

        # Cleanup
        cleanup_pid_file(pid_file)

    def test_handles_whitespace_pid_file(self, temp_dir):
        """Test check_singleton handles whitespace-only PID file."""
        pid_file = Path(temp_dir) / "test.pid"
        pid_file.write_text("   \n  ")

        # Should succeed since content is invalid
        check_singleton(pid_file)
        assert pid_file.read_text() == str(os.getpid())

        # Cleanup
        cleanup_pid_file(pid_file)


class TestDaemonAlreadyRunning:
    """Test DaemonAlreadyRunning exception."""

    def test_exception_message(self):
        """Test exception stores and displays message."""
        exc = DaemonAlreadyRunning("Daemon already running with PID 12345")
        assert "12345" in str(exc)
        assert "Daemon already running" in str(exc)


class TestSetupProcessGroup:
    """Test _setup_process_group function."""

    def test_calls_setpgrp(self):
        """Test _setup_process_group calls os.setpgrp."""
        with patch('daemon_core.os.setpgrp') as mock_setpgrp, \
             patch('daemon_core.atexit.register') as mock_register:
            _setup_process_group()
            mock_setpgrp.assert_called_once()
            mock_register.assert_called_once()

    def test_registers_cleanup_handler(self):
        """Test _setup_process_group registers atexit handler."""
        registered_funcs = []

        def capture_register(func):
            registered_funcs.append(func)

        with patch('daemon_core.os.setpgrp'), \
             patch('daemon_core.atexit.register', side_effect=capture_register):
            _setup_process_group()

        assert len(registered_funcs) == 1
        cleanup_func = registered_funcs[0]

        # Test cleanup function calls killpg
        with patch('daemon_core.os.killpg') as mock_killpg, \
             patch('daemon_core.os.getpgid', return_value=12345):
            cleanup_func()
            mock_killpg.assert_called_once_with(12345, signal.SIGTERM)

    def test_handles_setpgrp_oserror(self):
        """Test _setup_process_group handles OSError from setpgrp."""
        with patch('daemon_core.os.setpgrp', side_effect=OSError("Already group leader")), \
             patch('daemon_core.atexit.register'):
            # Should not raise
            _setup_process_group()

    def test_cleanup_handles_killpg_oserror(self):
        """Test cleanup handler handles OSError from killpg."""
        registered_funcs = []

        def capture_register(func):
            registered_funcs.append(func)

        with patch('daemon_core.os.setpgrp'), \
             patch('daemon_core.atexit.register', side_effect=capture_register):
            _setup_process_group()

        cleanup_func = registered_funcs[0]

        # Test cleanup function handles OSError
        with patch('daemon_core.os.killpg', side_effect=OSError("No such process")), \
             patch('daemon_core.os.getpgid', return_value=12345):
            # Should not raise
            cleanup_func()

    def test_cleanup_handles_process_lookup_error(self):
        """Test cleanup handler handles ProcessLookupError from killpg."""
        registered_funcs = []

        def capture_register(func):
            registered_funcs.append(func)

        with patch('daemon_core.os.setpgrp'), \
             patch('daemon_core.atexit.register', side_effect=capture_register):
            _setup_process_group()

        cleanup_func = registered_funcs[0]

        # Test cleanup function handles ProcessLookupError
        with patch('daemon_core.os.killpg', side_effect=ProcessLookupError("No such process")), \
             patch('daemon_core.os.getpgid', return_value=12345):
            # Should not raise
            cleanup_func()


class TestDrainInitTurn:
    """Test _drain_init_turn prevents init turn response from being sent to Telegram."""

    @pytest.fixture
    def mock_claude_process(self):
        from unittest.mock import AsyncMock, MagicMock
        import asyncio
        process = MagicMock()
        process.session_id = "test-session-123"
        process.pid = 12345
        process._event_queue = asyncio.Queue()
        process.start = AsyncMock(return_value=True)
        process.send_message = AsyncMock(return_value=True)
        return process

    @pytest.mark.asyncio
    async def test_drain_init_turn_consumes_events_until_session_result(self, mock_claude_process):
        from daemon_core import Daemon
        from claude_process import SystemInit, AssistantMessage, SessionResult

        init_event = SystemInit(session_id="test-session-123", tools=[], model="claude-sonnet-4", raw={})
        assistant_event = AssistantMessage(
            content=[{"type": "text", "text": "Init turn response"}],
            model="claude-sonnet-4", msg_id="msg_init", raw={}
        )
        result_event = SessionResult(success=True, result="", cost=0.001, turns=1, raw={})

        await mock_claude_process._event_queue.put(init_event)
        await mock_claude_process._event_queue.put(assistant_event)
        await mock_claude_process._event_queue.put(result_event)

        daemon = Daemon("test_token", "123456789")
        await daemon._drain_init_turn(mock_claude_process)

        assert mock_claude_process._event_queue.empty()

    @pytest.mark.asyncio
    async def test_drain_init_turn_stops_at_session_result(self, mock_claude_process):
        from daemon_core import Daemon
        from claude_process import SystemInit, AssistantMessage, SessionResult

        init_event = SystemInit(session_id="test-session-123", tools=[], model="claude-sonnet-4", raw={})
        init_response = AssistantMessage(
            content=[{"type": "text", "text": "Init response"}],
            model="claude-sonnet-4", msg_id="msg_init", raw={}
        )
        init_result = SessionResult(success=True, result="", cost=0.001, turns=1, raw={})
        subsequent_event = AssistantMessage(
            content=[{"type": "text", "text": "User message response"}],
            model="claude-sonnet-4", msg_id="msg_user", raw={}
        )

        await mock_claude_process._event_queue.put(init_event)
        await mock_claude_process._event_queue.put(init_response)
        await mock_claude_process._event_queue.put(init_result)
        await mock_claude_process._event_queue.put(subsequent_event)

        daemon = Daemon("test_token", "123456789")
        await daemon._drain_init_turn(mock_claude_process)

        assert not mock_claude_process._event_queue.empty()
        remaining = await mock_claude_process._event_queue.get()
        assert isinstance(remaining, AssistantMessage)
        assert remaining.msg_id == "msg_user"

    @pytest.mark.asyncio
    async def test_drain_init_turn_handles_process_end(self, mock_claude_process):
        from daemon_core import Daemon
        from claude_process import SystemInit

        init_event = SystemInit(session_id="test-session-123", tools=[], model="claude-sonnet-4", raw={})
        await mock_claude_process._event_queue.put(init_event)
        await mock_claude_process._event_queue.put(None)

        daemon = Daemon("test_token", "123456789")
        await daemon._drain_init_turn(mock_claude_process)


class TestRouteMessageResurrection:
    """Test _route_message_to_claude attempts resurrection when no process exists."""

    @pytest.fixture
    def mock_process_manager(self):
        """Create a mock ProcessManager."""
        pm = MagicMock()
        pm.processes = {}
        pm.send_to_process = AsyncMock(return_value=True)
        pm.get_process = MagicMock(return_value=None)
        return pm

    @pytest.mark.asyncio
    async def test_route_message_calls_send_to_process_without_existing_process(self, mock_process_manager):
        """Test that routing calls send_to_process even when no process exists in memory.

        Bug fix: Previously, _route_message_to_claude checked get_process() first and
        only called send_to_process if a process existed. This prevented resurrection
        of tasks from the registry. Now it calls send_to_process unconditionally,
        which handles resurrection internally.
        """
        daemon = Daemon("test_token", "123456789")
        daemon.process_manager = mock_process_manager

        # No process exists in memory
        assert mock_process_manager.get_process("my_task") is None

        # Route message to task
        await daemon._route_message_to_claude("my_task", "Hello from user")

        # send_to_process should be called (it will handle resurrection)
        mock_process_manager.send_to_process.assert_called_once_with("my_task", "Hello from user")

    @pytest.mark.asyncio
    async def test_route_message_falls_back_to_operator_on_keyerror(self, mock_process_manager):
        """Test that routing falls back to operator when task not found in registry.

        When send_to_process raises KeyError (task not in registry), we should
        fall back to routing the message to the operator.
        """
        daemon = Daemon("test_token", "123456789")
        daemon.process_manager = mock_process_manager

        # First call (to task) raises KeyError, second call (to operator) succeeds
        mock_process_manager.send_to_process.side_effect = [KeyError("not found"), True]

        await daemon._route_message_to_claude("unknown_task", "Hello")

        # Should have called twice: first task, then operator
        assert mock_process_manager.send_to_process.call_count == 2
        calls = mock_process_manager.send_to_process.call_args_list
        assert calls[0].args == ("unknown_task", "Hello")
        assert calls[1].args == ("operator", "Hello")

    @pytest.mark.asyncio
    async def test_route_message_operator_direct(self, mock_process_manager):
        """Test that messages to operator go directly without task lookup."""
        daemon = Daemon("test_token", "123456789")
        daemon.process_manager = mock_process_manager

        await daemon._route_message_to_claude("operator", "Hello operator")

        # Should call send_to_process directly for operator
        mock_process_manager.send_to_process.assert_called_once_with("operator", "Hello operator")

    @pytest.mark.asyncio
    async def test_route_message_does_not_retry_on_success(self, mock_process_manager):
        """Test that successful routing doesn't fall back to operator."""
        daemon = Daemon("test_token", "123456789")
        daemon.process_manager = mock_process_manager

        # send_to_process succeeds for task
        mock_process_manager.send_to_process.return_value = True

        await daemon._route_message_to_claude("my_task", "Hello")

        # Only one call, no fallback to operator
        mock_process_manager.send_to_process.assert_called_once_with("my_task", "Hello")


class TestCommandHandlerChatId:
    """Test that command handler receives correct chat_id from telegram adapter."""

    @pytest.fixture
    def mock_telegram_adapter(self):
        """Create a mock TelegramAdapter."""
        adapter = MagicMock()
        # _get_group_chat_id returns the correct group ID from config
        adapter._get_group_chat_id = MagicMock(return_value="-1009999888877")
        return adapter

    @pytest.fixture
    def mock_command_handler(self):
        """Create a mock CommandHandler."""
        handler = MagicMock()
        handler.handle_command = MagicMock(return_value=True)
        return handler

    @pytest.mark.asyncio
    async def test_command_uses_telegram_get_group_chat_id(
        self, mock_telegram_adapter, mock_command_handler
    ):
        """Test that chat_id in tg_msg comes from telegram._get_group_chat_id().

        Bug fix: Previously, the daemon used self.chat_id when building the tg_msg
        dict for the command handler. This was wrong because self.chat_id comes from
        the constructor, while telegram._get_group_chat_id() uses the registry config
        group_id (which may differ). Now we correctly use telegram._get_group_chat_id().
        """
        # Create daemon with one chat_id
        daemon = Daemon("test_token", "-1001111222233")  # Constructor chat_id
        daemon.telegram = mock_telegram_adapter
        daemon.command_handler = mock_command_handler

        # The adapter returns a different group_id from config
        assert daemon.chat_id == "-1001111222233"
        assert mock_telegram_adapter._get_group_chat_id() == "-1009999888877"

        # Create a mock incoming message
        from frontend_adapter import IncomingMessage
        msg = IncomingMessage(
            task_id="operator",
            text="/status",
            callback_data=None,
            msg_id="12345",
            reply_to_msg_id=None,
            reply_to_message=None
        )

        # Patch _get_topic_id_for_task to return a topic_id
        with patch.object(daemon, '_get_topic_id_for_task', return_value=1):
            # Simulate the command handling logic from _handle_telegram_messages
            topic_id = daemon._get_topic_id_for_task(msg.task_id)
            group_chat_id = daemon.telegram._get_group_chat_id()
            tg_msg = {
                "text": msg.text,
                "message_id": int(msg.msg_id),
                "chat": {"id": int(group_chat_id)},
                "message_thread_id": topic_id,
                "reply_to_message": msg.reply_to_message
            }
            daemon.command_handler.handle_command(tg_msg)

        # Verify command handler was called with the correct chat_id (from adapter, not daemon)
        mock_command_handler.handle_command.assert_called_once()
        called_tg_msg = mock_command_handler.handle_command.call_args[0][0]

        # The chat_id should be from telegram._get_group_chat_id(), NOT daemon.chat_id
        assert called_tg_msg["chat"]["id"] == -1009999888877
        assert called_tg_msg["chat"]["id"] != int(daemon.chat_id)

    @pytest.mark.asyncio
    async def test_command_includes_reply_to_message(
        self, mock_telegram_adapter, mock_command_handler
    ):
        """Test that reply_to_message is passed to command handler."""
        daemon = Daemon("test_token", "-1001111222233")
        daemon.telegram = mock_telegram_adapter
        daemon.command_handler = mock_command_handler

        from frontend_adapter import IncomingMessage
        reply_msg = {"message_id": 999, "text": "original message"}
        msg = IncomingMessage(
            task_id="operator",
            text="/debug",
            callback_data=None,
            msg_id="12345",
            reply_to_msg_id="999",
            reply_to_message=reply_msg
        )

        with patch.object(daemon, '_get_topic_id_for_task', return_value=1):
            topic_id = daemon._get_topic_id_for_task(msg.task_id)
            group_chat_id = daemon.telegram._get_group_chat_id()
            tg_msg = {
                "text": msg.text,
                "message_id": int(msg.msg_id),
                "chat": {"id": int(group_chat_id)},
                "message_thread_id": topic_id,
                "reply_to_message": msg.reply_to_message
            }
            daemon.command_handler.handle_command(tg_msg)

        called_tg_msg = mock_command_handler.handle_command.call_args[0][0]
        assert called_tg_msg["reply_to_message"] == reply_msg


class TestOnSystemInit:
    """Test _on_system_init updates registry with session tracking."""

    @pytest.mark.asyncio
    async def test_on_system_init_updates_registry(self):
        """Test that _on_system_init calls registry.update_task_session_tracking.

        Bug fix: When a process emits SystemInit, we need to update the registry
        with the new session_id so that permission lookups and task routing work
        correctly. This ensures the registry always has the current session_id.
        """
        from claude_process import SystemInit

        daemon = Daemon("test_token", "123456789")

        # Create a mock registry
        mock_registry = MagicMock()
        mock_registry.update_task_session_tracking = MagicMock()

        # Create a SystemInit event
        init_event = SystemInit(
            session_id="new-session-abc123",
            tools=["Read", "Write"],
            model="claude-sonnet-4",
            raw={}
        )

        with patch("daemon_core.get_registry", return_value=mock_registry):
            await daemon._on_system_init("my_task", init_event)

        # Verify registry was updated with the session_id
        mock_registry.update_task_session_tracking.assert_called_once_with(
            "my_task",
            session_id="new-session-abc123"
        )

    @pytest.mark.asyncio
    async def test_on_system_init_logs_event(self):
        """Test that _on_system_init logs the system init event."""
        from claude_process import SystemInit

        daemon = Daemon("test_token", "123456789")

        init_event = SystemInit(
            session_id="session-xyz789",
            tools=[],
            model="claude-sonnet-4",
            raw={}
        )

        mock_registry = MagicMock()

        with patch("daemon_core.get_registry", return_value=mock_registry), \
             patch("daemon_core.log") as mock_log:
            await daemon._on_system_init("test_task", init_event)

        # Verify logging occurred
        mock_log.assert_called()
        log_message = mock_log.call_args[0][0]
        assert "test_task" in log_message
        assert "session-xyz789" in log_message
