"""Tests for session_worker.py - Pure logic functions."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from session_worker import (
    _get_session_name, get_worktree_path, get_worker_pane_for_topic,
    is_worker_pane, create_claude_local_md, append_todo
)


# =============================================================================
# Session Naming Tests
# =============================================================================


class TestSessionNaming:
    """Tests for session naming functions."""

    def test_get_session_name_basic(self):
        """Test _get_session_name prefixes with 'ca-'."""
        result = _get_session_name("my_task")
        assert result == "ca-my_task"

    def test_get_session_name_with_numbers(self):
        """Test _get_session_name with numeric characters."""
        result = _get_session_name("task123")
        assert result == "ca-task123"

    def test_get_session_name_with_underscores(self):
        """Test _get_session_name with underscores."""
        result = _get_session_name("my_complex_task_name")
        assert result == "ca-my_complex_task_name"

    def test_get_session_name_with_hyphens(self):
        """Test _get_session_name with hyphens."""
        result = _get_session_name("my-task-name")
        assert result == "ca-my-task-name"


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
# Worker Pane Lookup Tests
# =============================================================================


class TestWorkerPaneLookup:
    """Tests for worker pane lookup functions."""

    def test_get_worker_pane_found(self, mock_registry):
        """Test get_worker_pane_for_topic returns pane from registry."""
        mock_registry.tasks = {
            "my_task": {"topic_id": 123, "pane": "ca-my_task:0.0"}
        }

        with patch("session_worker.get_registry", return_value=mock_registry):
            result = get_worker_pane_for_topic(123)
            assert result == "ca-my_task:0.0"

    def test_get_worker_pane_not_found(self, mock_registry):
        """Test get_worker_pane_for_topic returns None when not found."""
        mock_registry.tasks = {
            "other_task": {"topic_id": 456, "pane": "ca-other:0.0"}
        }

        with patch("session_worker.get_registry", return_value=mock_registry):
            result = get_worker_pane_for_topic(123)
            assert result is None

    def test_get_worker_pane_no_pane_in_data(self, mock_registry):
        """Test get_worker_pane_for_topic when task exists but no pane."""
        mock_registry.tasks = {
            "my_task": {"topic_id": 123}  # No pane key
        }

        with patch("session_worker.get_registry", return_value=mock_registry):
            result = get_worker_pane_for_topic(123)
            assert result is None

    def test_is_worker_pane_true(self, mock_registry):
        """Test is_worker_pane returns (True, topic_id) for worker pane."""
        mock_registry.tasks = {
            "my_task": {"topic_id": 123, "pane": "ca-my_task:0.0"}
        }

        with patch("session_worker.get_registry", return_value=mock_registry):
            is_worker, topic_id = is_worker_pane("ca-my_task:0.0")
            assert is_worker is True
            assert topic_id == 123

    def test_is_worker_pane_false(self, mock_registry):
        """Test is_worker_pane returns (False, None) for non-worker pane."""
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
