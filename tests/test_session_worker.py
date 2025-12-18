"""Tests for session_worker.py - Pure logic functions."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from session_worker import (
    get_worktree_path, get_worker_pane_for_topic, get_worker_process_for_topic,
    is_worker_pane, is_worker_process, create_claude_local_md, append_todo
)


# =============================================================================
# Worktree Path Tests
# =============================================================================


class TestWorktreePath:
    """Tests for worktree path functions."""

    def test_get_worktree_path_basic(self):
        """Test get_worktree_path constructs correct path."""
        result = get_worktree_path("/home/user/repo", "my_task")
        assert result == Path("/home/user/repo/trees/my_task")

    def test_get_worktree_path_nested(self):
        """Test get_worktree_path handles nested repo path."""
        result = get_worktree_path("/home/user/projects/my-org/repo", "feature-123")
        assert result == Path("/home/user/projects/my-org/repo/trees/feature-123")

    def test_get_worktree_path_returns_path_object(self):
        """Test get_worktree_path returns Path object."""
        result = get_worktree_path("/tmp/repo", "task")
        assert isinstance(result, Path)


# =============================================================================
# Worker Process Lookup Tests
# =============================================================================


class TestWorkerProcessLookup:
    """Tests for worker process lookup functions."""

    def test_get_worker_process_found(self, mock_registry):
        """Test get_worker_process_for_topic returns task_name from registry."""
        mock_registry.tasks = {
            "my_task": {"topic_id": 123, "status": "active"}
        }

        with patch("session_worker.get_registry", return_value=mock_registry):
            result = get_worker_process_for_topic(123)
            assert result == "my_task"

    def test_get_worker_process_not_found(self, mock_registry):
        """Test get_worker_process_for_topic returns None when not found."""
        mock_registry.tasks = {
            "other_task": {"topic_id": 456, "status": "active"}
        }

        with patch("session_worker.get_registry", return_value=mock_registry):
            result = get_worker_process_for_topic(123)
            assert result is None

    def test_get_worker_process_paused_returns_none(self, mock_registry):
        """Test get_worker_process_for_topic returns None for paused task."""
        mock_registry.tasks = {
            "my_task": {"topic_id": 123, "status": "paused"}
        }

        with patch("session_worker.get_registry", return_value=mock_registry):
            result = get_worker_process_for_topic(123)
            assert result is None

    def test_get_worker_pane_legacy_alias(self, mock_registry):
        """Test get_worker_pane_for_topic is alias for get_worker_process_for_topic."""
        mock_registry.tasks = {
            "my_task": {"topic_id": 123, "status": "active"}
        }

        with patch("session_worker.get_registry", return_value=mock_registry):
            result = get_worker_pane_for_topic(123)
            assert result == "my_task"

    def test_is_worker_process_true(self, mock_registry):
        """Test is_worker_process returns (True, topic_id) for known task."""
        mock_registry.tasks = {
            "my_task": {"topic_id": 123}
        }

        with patch("session_worker.get_registry", return_value=mock_registry):
            is_worker, topic_id = is_worker_process("my_task")
            assert is_worker is True
            assert topic_id == 123

    def test_is_worker_process_false(self, mock_registry):
        """Test is_worker_process returns (False, None) for unknown task."""
        mock_registry.tasks = {}

        with patch("session_worker.get_registry", return_value=mock_registry):
            is_worker, topic_id = is_worker_process("unknown_task")
            assert is_worker is False
            assert topic_id is None

    def test_is_worker_pane_by_task_name(self, mock_registry):
        """Test is_worker_pane finds task by task_name."""
        mock_registry.tasks = {
            "my_task": {"topic_id": 123}
        }

        with patch("session_worker.get_registry", return_value=mock_registry):
            is_worker, topic_id = is_worker_pane("my_task")
            assert is_worker is True
            assert topic_id == 123

    def test_is_worker_pane_by_pane_field(self, mock_registry):
        """Test is_worker_pane finds task by pane field (legacy compatibility)."""
        mock_registry.tasks = {
            "my_task": {"topic_id": 123, "pane": "ca-my_task:0.0"}
        }

        with patch("session_worker.get_registry", return_value=mock_registry):
            is_worker, topic_id = is_worker_pane("ca-my_task:0.0")
            assert is_worker is True
            assert topic_id == 123

    def test_is_worker_pane_not_found(self, mock_registry):
        """Test is_worker_pane returns (False, None) for unknown pane/task."""
        mock_registry.tasks = {
            "my_task": {"topic_id": 123, "pane": "ca-my_task:0.0"}
        }

        with patch("session_worker.get_registry", return_value=mock_registry):
            is_worker, topic_id = is_worker_pane("other:0.0")
            assert is_worker is False
            assert topic_id is None

    def test_is_worker_pane_empty_registry(self, mock_registry):
        """Test is_worker_pane with empty registry."""
        mock_registry.tasks = {}

        with patch("session_worker.get_registry", return_value=mock_registry):
            is_worker, topic_id = is_worker_pane("any:0.0")
            assert is_worker is False
            assert topic_id is None


# =============================================================================
# File Operation Tests
# =============================================================================


class TestFileOperations:
    """Tests for file creation/modification functions."""

    def test_create_claude_local_md_creates_file(self, temp_dir):
        """Test create_claude_local_md creates file with template."""
        create_claude_local_md(temp_dir, "test_task")

        path = Path(temp_dir) / "CLAUDE.local.md"
        assert path.exists()
        content = path.read_text()
        assert "test_task" in content
        assert "## Instructions" in content
        assert "## Learnings" in content

    def test_create_claude_local_md_does_not_overwrite(self, temp_dir):
        """Test create_claude_local_md preserves existing file."""
        path = Path(temp_dir) / "CLAUDE.local.md"
        path.write_text("existing content")

        create_claude_local_md(temp_dir, "test_task")

        assert path.read_text() == "existing content"

    def test_create_claude_local_md_with_description(self, temp_dir):
        """Test create_claude_local_md includes description."""
        create_claude_local_md(temp_dir, "test_task", "Fix the authentication bug")

        path = Path(temp_dir) / "CLAUDE.local.md"
        content = path.read_text()
        assert "Fix the authentication bug" in content

    def test_create_claude_local_md_default_description(self, temp_dir):
        """Test create_claude_local_md with no description shows placeholder."""
        create_claude_local_md(temp_dir, "test_task")

        path = Path(temp_dir) / "CLAUDE.local.md"
        content = path.read_text()
        assert "(No description provided)" in content

    def test_append_todo_creates_file(self, temp_dir):
        """Test append_todo creates TODO.local.md if missing."""
        result = append_todo(temp_dir, "Fix the bug")

        assert result is True
        path = Path(temp_dir) / "TODO.local.md"
        assert path.exists()
        content = path.read_text()
        assert "# TODO" in content
        assert "- [ ] Fix the bug" in content

    def test_append_todo_appends(self, temp_dir):
        """Test append_todo appends to existing file."""
        path = Path(temp_dir) / "TODO.local.md"
        path.write_text("# TODO\n\n- [ ] First item\n")

        result = append_todo(temp_dir, "Second item")

        assert result is True
        content = path.read_text()
        assert "- [ ] First item" in content
        assert "- [ ] Second item" in content

    def test_append_todo_format(self, temp_dir):
        """Test append_todo uses correct checkbox format."""
        append_todo(temp_dir, "Test item")

        path = Path(temp_dir) / "TODO.local.md"
        content = path.read_text()
        # Should have proper markdown checkbox format
        assert "- [ ] Test item\n" in content

    def test_append_todo_returns_true(self, temp_dir):
        """Test append_todo returns True on success."""
        result = append_todo(temp_dir, "Any item")
        assert result is True

    def test_append_todo_returns_false_on_error(self, temp_dir):
        """Test append_todo returns False on error."""
        # Create a directory where a file is expected (will cause write error)
        path = Path(temp_dir) / "TODO.local.md"
        path.mkdir()

        result = append_todo(temp_dir, "Should fail")
        assert result is False
