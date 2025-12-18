"""Tests for daemon_core.py - singleton management and PID file handling."""

import atexit
import os
import signal
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from daemon_core import (
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
