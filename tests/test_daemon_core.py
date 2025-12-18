"""Tests for daemon_core.py - singleton management and PID file handling."""

import os
from pathlib import Path

import pytest

from daemon_core import (
    DaemonAlreadyRunning,
    check_singleton,
    cleanup_pid_file,
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
