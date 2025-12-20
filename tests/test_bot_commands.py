"""Tests for bot_commands.py - Command parsing and handlers."""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from bot_commands import (
    parse_command_args, build_spawn_prompt, build_cleanup_prompt,
    build_summarize_prompt, build_operator_intervention_prompt, CommandHandler
)
from registry import reset_singletons


# Create a simple mock State for testing
class MockState:
    """Mock State class that mimics the real State for testing."""

    def __init__(self):
        self._data = {}

    def set(self, key, value):
        self._data[key] = value

    def get(self, key):
        return self._data.get(key)

    def update(self, key, **kwargs):
        if key in self._data:
            self._data[key].update(kwargs)
        else:
            self._data[key] = kwargs

    def items(self):
        return self._data.items()

    def __contains__(self, key):
        return key in self._data


class TestBotCommands:
    """Test bot command handlers."""

    def test_parse_command_args(self):
        """Test parse_command_args function."""
        assert parse_command_args("/spawn foo bar") == "foo bar"
        assert parse_command_args("/spawn@mybot foo bar") == "foo bar"
        assert parse_command_args("/spawn") is None
        assert parse_command_args("/spawn@mybot") is None

    def test_parse_command_args_whitespace(self):
        """Test parse_command_args with extra whitespace."""
        # Leading whitespace in args should be stripped
        assert parse_command_args("/spawn   foo bar") == "foo bar"
        # Trailing whitespace should be stripped
        assert parse_command_args("/spawn foo bar  ") == "foo bar"
        # Only command, no args
        assert parse_command_args("/spawn   ") is None

    def test_parse_command_args_multiline(self):
        """Test parse_command_args with multiline text."""
        result = parse_command_args("/spawn line1\nline2\nline3")
        assert result == "line1\nline2\nline3"

    def test_build_spawn_prompt(self):
        """Test build_spawn_prompt function."""
        prompt = build_spawn_prompt("Create a test task")
        assert "SPAWN REQUEST" in prompt
        assert "Create a test task" in prompt

        prompt = build_spawn_prompt(
            "Fix the bug",
            task_name="existing_task",
            task_data={"type": "session", "path": "/home/test"}
        )
        assert "existing_task" in prompt
        assert "session" in prompt

    def test_build_cleanup_prompt(self):
        """Test build_cleanup_prompt function."""
        prompt = build_cleanup_prompt("my_task", {"type": "session", "path": "/tmp", "topic_id": 123})
        assert "CLEANUP REQUEST" in prompt
        assert "my_task" in prompt
        assert "cleanup_task" in prompt

    def test_build_summarize_prompt(self):
        """Test build_summarize_prompt function."""
        prompt = build_summarize_prompt([])
        assert "SUMMARIZE REQUEST" in prompt
        assert "No active tasks" in prompt

        tasks = [("task1", {"type": "session", "status": "active", "path": "/tmp"})]
        prompt = build_summarize_prompt(tasks)
        assert "task1" in prompt
        assert "session" in prompt

    @pytest.mark.asyncio
    async def test_command_handler_handle_help(self, temp_dir):
        """Test /help command."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)), \
             patch("bot_commands.send_reply") as mock_reply:

            handler = CommandHandler("TOKEN", "-1001234567890", {})
            msg = {
                "text": "/help",
                "message_id": 1,
                "chat": {"id": -1001234567890},
                "message_thread_id": None
            }

            result = await handler.handle_command(msg)
            assert result is True
            mock_reply.assert_called_once()
            call_args = mock_reply.call_args[0]
            assert "Claude Army Commands" in call_args[3]

    @pytest.mark.asyncio
    async def test_command_handler_unrecognized_command(self, temp_dir):
        """Test unrecognized command returns False."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)):

            handler = CommandHandler("TOKEN", "-1001234567890", {})
            msg = {
                "text": "/unknown_command",
                "message_id": 1,
                "chat": {"id": -1001234567890},
                "message_thread_id": None
            }

            result = await handler.handle_command(msg)
            assert result is False


# =============================================================================
# Additional Pure Logic Tests
# =============================================================================


class TestBuildPromptFunctions:
    """Additional tests for prompt building functions."""

    def test_build_spawn_prompt_with_reply_context(self):
        """Test build_spawn_prompt includes reply context."""
        prompt = build_spawn_prompt(
            "Create a test feature",
            task_name="parent_task",
            task_data={"type": "worktree", "path": "/home/test"},
            reply_ctx="[Replying to msg_id=100 from Jane at 10:30:00]\nOriginal message text"
        )
        assert "SPAWN REQUEST" in prompt
        assert "parent_task" in prompt
        assert "Context:" in prompt
        assert "Original message text" in prompt

    def test_build_spawn_prompt_minimal(self):
        """Test build_spawn_prompt with minimal args."""
        prompt = build_spawn_prompt("Simple task")
        assert "SPAWN REQUEST" in prompt
        assert "Simple task" in prompt
        # Should not include task context lines if not provided
        assert "From task:" not in prompt
        assert "Context:" not in prompt

    def test_build_cleanup_prompt_all_fields(self):
        """Test build_cleanup_prompt includes all task data fields."""
        task_data = {
            "type": "worktree",
            "path": "/home/test/project",
            "topic_id": 456,
            "status": "active",
        }
        prompt = build_cleanup_prompt("my_task", task_data)
        assert "CLEANUP REQUEST" in prompt
        assert "my_task" in prompt
        assert "worktree" in prompt
        assert "/home/test/project" in prompt
        assert "456" in prompt
        assert "active" in prompt
        assert "cleanup_task" in prompt

    def test_build_cleanup_prompt_missing_fields(self):
        """Test build_cleanup_prompt handles missing dict keys gracefully."""
        # Minimal task data with missing fields
        task_data = {}
        prompt = build_cleanup_prompt("orphan_task", task_data)
        # Should use defaults for missing fields
        assert "CLEANUP REQUEST" in prompt
        assert "orphan_task" in prompt
        assert "session" in prompt  # default type
        assert "?" in prompt  # default for missing values

    def test_build_summarize_prompt_with_multiple_tasks(self):
        """Test build_summarize_prompt with multiple tasks."""
        tasks = [
            ("task1", {"type": "session", "status": "active", "path": "/tmp/task1"}),
            ("task2", {"type": "worktree", "status": "paused", "path": "/tmp/task2"}),
        ]
        prompt = build_summarize_prompt(tasks)
        assert "SUMMARIZE REQUEST" in prompt
        assert "task1" in prompt
        assert "task2" in prompt
        assert "session" in prompt
        assert "worktree" in prompt

    def test_build_summarize_prompt_with_todo_files(self, temp_dir):
        """Test build_summarize_prompt includes TODO files."""
        # Create a TODO.local.md file
        task_path = Path(temp_dir)
        todo_file = task_path / "TODO.local.md"
        todo_file.write_text("- [ ] Fix the bug\n- [ ] Add tests\n")

        tasks = [
            ("my_task", {"type": "session", "status": "active", "path": str(task_path)}),
        ]
        prompt = build_summarize_prompt(tasks)
        assert "TODO.local.md:" in prompt
        assert "Fix the bug" in prompt
        assert "Add tests" in prompt

    def test_build_summarize_prompt_handles_read_errors(self, temp_dir):
        """Test build_summarize_prompt handles file read errors gracefully."""
        # Create a directory where a file would be expected
        task_path = Path(temp_dir)
        todo_dir = task_path / "TODO.local.md"
        todo_dir.mkdir()  # Create directory instead of file

        tasks = [
            ("my_task", {"type": "session", "status": "active", "path": str(task_path)}),
        ]
        # Should not raise exception
        prompt = build_summarize_prompt(tasks)
        assert "SUMMARIZE REQUEST" in prompt


class TestOperatorInterventionPrompt:
    """Tests for build_operator_intervention_prompt."""

    def test_build_operator_intervention_prompt_basic(self):
        """Test basic formatting of operator intervention prompt."""
        task_data = {
            "type": "session",
            "path": "/home/test",
            "session_id": "sess-123",
            "pid": 12345,
        }
        prompt = build_operator_intervention_prompt(
            "stuck_task",
            task_data,
            "",  # no pane output
            "Help me fix this"
        )
        assert "OPERATOR INTERVENTION REQUEST" in prompt
        assert "stuck_task" in prompt
        assert "Help me fix this" in prompt
        assert "session" in prompt
        assert "/home/test" in prompt

    def test_build_operator_intervention_prompt_with_pane_output(self):
        """Test prompt includes pane output when provided."""
        task_data = {"type": "session", "path": "/tmp"}
        pane_output = "Error: Connection refused\nRetrying in 5 seconds..."
        prompt = build_operator_intervention_prompt(
            "my_task",
            task_data,
            pane_output,
            "Getting errors"
        )
        assert "Current output:" in prompt
        assert "Connection refused" in prompt

    def test_build_operator_intervention_prompt_no_message(self):
        """Test prompt handles empty user message."""
        task_data = {"type": "session", "path": "/tmp"}
        prompt = build_operator_intervention_prompt(
            "my_task",
            task_data,
            "",
            ""  # Empty user message
        )
        assert "(no message - just get it unstuck)" in prompt

    def test_build_operator_intervention_prompt_placeholder_output(self):
        """Test prompt handles placeholder pane output."""
        task_data = {"type": "session", "path": "/tmp"}
        # When pane output is the placeholder, don't include it
        prompt = build_operator_intervention_prompt(
            "my_task",
            task_data,
            "(use tools to inspect)",
            "Help"
        )
        # Should not have "Current output:" section
        assert "Current output:" not in prompt


class TestFormatReplyContext:
    """Tests for _format_reply_context method."""

    def test_format_reply_context_basic(self, temp_dir):
        """Test basic reply context formatting."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)):

            handler = CommandHandler("TOKEN", "-1001234567890", {})
            msg = {
                "reply_to_message": {
                    "message_id": 100,
                    "text": "Original message",
                    "from": {"first_name": "John"},
                    "date": 1700000000,
                },
            }

            result = handler._format_reply_context(msg)
            assert result is not None
            assert "msg_id=100" in result
            assert "John" in result
            assert "Original message" in result

    def test_format_reply_context_with_state(self, temp_dir):
        """Test reply context includes state info."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)):

            state = MockState()
            state.set("100", {"type": "permission_prompt", "pane": "test:0.0"})
            handler = CommandHandler("TOKEN", "-1001234567890", state)

            msg = {
                "reply_to_message": {
                    "message_id": 100,
                    "text": "Original",
                    "from": {"first_name": "Jane"},
                    "date": 1700000000,
                },
            }

            result = handler._format_reply_context(msg)
            assert "State:" in result
            assert "permission_prompt" in result

    def test_format_reply_context_no_reply(self, temp_dir):
        """Test returns None when no reply_to_message."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)):

            handler = CommandHandler("TOKEN", "-1001234567890", {})
            msg = {"text": "Just a message"}

            result = handler._format_reply_context(msg)
            assert result is None

    def test_format_reply_context_truncates_long_text(self, temp_dir):
        """Test truncates long reply text to 500 chars."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)):

            handler = CommandHandler("TOKEN", "-1001234567890", {})
            long_text = "X" * 1000
            msg = {
                "reply_to_message": {
                    "message_id": 100,
                    "text": long_text,
                    "from": {"first_name": "Jane"},
                    "date": 1700000000,
                },
            }

            result = handler._format_reply_context(msg)
            # Should have exactly 500 X's in the result
            assert result.count("X") == 500


class TestGetTaskNameForTopic:
    """Tests for _get_task_name_for_topic method."""

    def test_get_task_name_for_topic_general(self, temp_dir, mock_config):
        """Test returns 'operator' for General topic."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        mock_config.general_topic_id = 1

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)), \
             patch("bot_commands.get_config", return_value=mock_config):

            handler = CommandHandler("TOKEN", "-1001234567890", {})
            # topic_id == general_topic_id should return "operator"
            result = handler._get_task_name_for_topic(1)
            assert result == "operator"

    def test_get_task_name_for_topic_none(self, temp_dir, mock_config):
        """Test returns 'operator' for None topic."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)), \
             patch("bot_commands.get_config", return_value=mock_config):

            handler = CommandHandler("TOKEN", "-1001234567890", {})
            result = handler._get_task_name_for_topic(None)
            assert result == "operator"

    def test_get_task_name_for_topic_found(self, temp_dir, mock_registry, mock_config):
        """Test returns task name when found in registry."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        mock_registry.tasks = {
            "my_task": {"topic_id": 456, "pane": "test:0.0"}
        }
        mock_config.general_topic_id = 1

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)), \
             patch("bot_commands.get_config", return_value=mock_config), \
             patch("bot_commands.get_registry", return_value=mock_registry):

            handler = CommandHandler("TOKEN", "-1001234567890", {})
            result = handler._get_task_name_for_topic(456)
            assert result == "my_task"

    def test_get_task_name_for_topic_not_found(self, temp_dir, mock_registry, mock_config):
        """Test returns None when topic not in registry."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        mock_registry.tasks = {}
        mock_config.general_topic_id = 1

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)), \
             patch("bot_commands.get_config", return_value=mock_config), \
             patch("bot_commands.get_registry", return_value=mock_registry):

            handler = CommandHandler("TOKEN", "-1001234567890", {})
            result = handler._get_task_name_for_topic(999)
            assert result is None


class TestHandleStop:
    """Tests for /stop command handler."""

    @pytest.mark.asyncio
    async def test_stop_from_general_topic_rejected(self, temp_dir, mock_config):
        """Test /stop from general topic (no topic_id) is rejected as operator."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        mock_config.general_topic_id = 1

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)), \
             patch("bot_commands.get_config", return_value=mock_config), \
             patch("bot_commands.send_reply") as mock_reply:

            handler = CommandHandler("TOKEN", "-1001234567890", {})
            msg = {
                "text": "/stop",
                "message_id": 1,
                "chat": {"id": -1001234567890},
                "message_thread_id": None  # General topic
            }

            result = await handler.handle_command(msg)
            assert result is True
            mock_reply.assert_called_once()
            call_args = mock_reply.call_args[0]
            assert "Cannot stop operator" in call_args[3]

    @pytest.mark.asyncio
    async def test_stop_unknown_topic(self, temp_dir, mock_config, mock_registry):
        """Test /stop from unknown topic returns no task message."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        mock_config.general_topic_id = 1
        mock_registry.tasks = {}  # No tasks registered

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)), \
             patch("bot_commands.get_config", return_value=mock_config), \
             patch("bot_commands.get_registry", return_value=mock_registry), \
             patch("bot_commands.send_reply") as mock_reply:

            handler = CommandHandler("TOKEN", "-1001234567890", {})
            msg = {
                "text": "/stop",
                "message_id": 1,
                "chat": {"id": -1001234567890},
                "message_thread_id": 999  # Unknown topic
            }

            result = await handler.handle_command(msg)
            assert result is True
            mock_reply.assert_called_once()
            call_args = mock_reply.call_args[0]
            assert "No task in this topic" in call_args[3]

    @pytest.mark.asyncio
    async def test_stop_operator_rejected(self, temp_dir, mock_config):
        """Test /stop operator is rejected."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        mock_config.general_topic_id = 1

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)), \
             patch("bot_commands.get_config", return_value=mock_config), \
             patch("bot_commands.send_reply") as mock_reply:

            handler = CommandHandler("TOKEN", "-1001234567890", {})
            msg = {
                "text": "/stop operator",
                "message_id": 1,
                "chat": {"id": -1001234567890},
                "message_thread_id": None
            }

            result = await handler.handle_command(msg)
            assert result is True
            mock_reply.assert_called_once()
            call_args = mock_reply.call_args[0]
            assert "Cannot stop operator" in call_args[3]

    @pytest.mark.asyncio
    async def test_stop_no_process_manager(self, temp_dir, mock_config, mock_registry):
        """Test /stop with no process manager."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        mock_registry.tasks = {"my_task": {"topic_id": 456}}
        mock_config.general_topic_id = 1

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)), \
             patch("bot_commands.get_config", return_value=mock_config), \
             patch("bot_commands.get_registry", return_value=mock_registry), \
             patch("bot_commands.send_reply") as mock_reply:

            # No process_manager
            handler = CommandHandler("TOKEN", "-1001234567890", {}, process_manager=None)
            msg = {
                "text": "/stop my_task",
                "message_id": 1,
                "chat": {"id": -1001234567890},
                "message_thread_id": None
            }

            result = await handler.handle_command(msg)
            assert result is True
            mock_reply.assert_called_once()
            call_args = mock_reply.call_args[0]
            assert "Process manager not available" in call_args[3]

    @pytest.mark.asyncio
    async def test_stop_task_not_running(self, temp_dir, mock_config, mock_registry):
        """Test /stop when task is not running."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        mock_registry.tasks = {"my_task": {"topic_id": 456}}
        mock_config.general_topic_id = 1

        mock_pm = MagicMock()
        mock_pm.is_running.return_value = False

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)), \
             patch("bot_commands.get_config", return_value=mock_config), \
             patch("bot_commands.get_registry", return_value=mock_registry), \
             patch("bot_commands.send_reply") as mock_reply:

            handler = CommandHandler("TOKEN", "-1001234567890", {}, process_manager=mock_pm)
            msg = {
                "text": "/stop my_task",
                "message_id": 1,
                "chat": {"id": -1001234567890},
                "message_thread_id": None
            }

            result = await handler.handle_command(msg)
            assert result is True
            mock_reply.assert_called_once()
            call_args = mock_reply.call_args[0]
            assert "is not running" in call_args[3]
            mock_pm.is_running.assert_called_with("my_task")

    @pytest.mark.asyncio
    async def test_stop_success(self, temp_dir, mock_config, mock_registry):
        """Test /stop successfully stops task."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        mock_registry.tasks = {"my_task": {"topic_id": 456}}
        mock_config.general_topic_id = 1

        mock_pm = MagicMock()
        mock_pm.is_running.return_value = True
        mock_pm.stop_process = MagicMock()

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)), \
             patch("bot_commands.get_config", return_value=mock_config), \
             patch("bot_commands.get_registry", return_value=mock_registry), \
             patch("bot_commands.send_reply") as mock_reply, \
             patch("bot_commands.asyncio.create_task") as mock_create_task:

            mock_create_task.side_effect = lambda coro: coro.close()

            handler = CommandHandler("TOKEN", "-1001234567890", {}, process_manager=mock_pm)
            msg = {
                "text": "/stop my_task",
                "message_id": 1,
                "chat": {"id": -1001234567890},
                "message_thread_id": None
            }

            result = await handler.handle_command(msg)
            assert result is True
            mock_reply.assert_called_once()
            call_args = mock_reply.call_args[0]
            assert "Stopping" in call_args[3]
            assert "my_task" in call_args[3]
            mock_create_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_infers_task_from_topic(self, temp_dir, mock_config, mock_registry):
        """Test /stop infers task name from topic."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        mock_registry.tasks = {"my_task": {"topic_id": 456}}
        mock_config.general_topic_id = 1

        mock_pm = MagicMock()
        mock_pm.is_running.return_value = True
        mock_pm.stop_process = MagicMock()

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)), \
             patch("bot_commands.get_config", return_value=mock_config), \
             patch("bot_commands.get_registry", return_value=mock_registry), \
             patch("bot_commands.send_reply") as mock_reply, \
             patch("bot_commands.asyncio.create_task") as mock_create_task:

            mock_create_task.side_effect = lambda coro: coro.close()

            handler = CommandHandler("TOKEN", "-1001234567890", {}, process_manager=mock_pm)
            msg = {
                "text": "/stop",
                "message_id": 1,
                "chat": {"id": -1001234567890},
                "message_thread_id": 456  # Topic for my_task
            }

            result = await handler.handle_command(msg)
            assert result is True
            mock_reply.assert_called_once()
            call_args = mock_reply.call_args[0]
            assert "Stopping" in call_args[3]
            assert "my_task" in call_args[3]
            mock_pm.is_running.assert_called_with("my_task")
            mock_create_task.assert_called_once()


class TestHandleConnect:
    """Tests for /connect command."""

    @pytest.mark.asyncio
    async def test_connect_no_task_in_topic(self, temp_dir, mock_config):
        """Test /connect from topic with no task."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        mock_config.general_topic_id = 1

        mock_registry = MagicMock()
        mock_registry.find_task_by_topic.return_value = None

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)), \
             patch("bot_commands.get_config", return_value=mock_config), \
             patch("bot_commands.get_registry", return_value=mock_registry), \
             patch("bot_commands.send_reply") as mock_reply:

            handler = CommandHandler("TOKEN", "-1001234567890", {})
            msg = {
                "text": "/connect",
                "message_id": 1,
                "chat": {"id": -1001234567890},
                "message_thread_id": 999  # Unknown topic
            }

            result = await handler.handle_command(msg)
            assert result is True
            mock_reply.assert_called_once()
            call_args = mock_reply.call_args[0]
            assert "No task in this topic" in call_args[3]

    @pytest.mark.asyncio
    async def test_connect_success(self, temp_dir, mock_config, mock_registry):
        """Test /connect with valid task."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        mock_registry.tasks = {
            "my_task": {
                "topic_id": 456,
                "session_id": "ses_abc123",
                "path": "/home/user/project"
            }
        }
        mock_config.general_topic_id = 1

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)), \
             patch("bot_commands.get_config", return_value=mock_config), \
             patch("bot_commands.get_registry", return_value=mock_registry), \
             patch("bot_commands.send_reply") as mock_reply:

            handler = CommandHandler("TOKEN", "-1001234567890", {})
            msg = {
                "text": "/connect",
                "message_id": 1,
                "chat": {"id": -1001234567890},
                "message_thread_id": 456
            }

            result = await handler.handle_command(msg)
            assert result is True
            mock_reply.assert_called_once()
            call_args = mock_reply.call_args[0]
            assert "cd /home/user/project" in call_args[3]
            assert "claude --resume ses_abc123" in call_args[3]

    @pytest.mark.asyncio
    async def test_connect_no_session_id(self, temp_dir, mock_config, mock_registry):
        """Test /connect when task has no session_id."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        mock_registry.tasks = {
            "my_task": {
                "topic_id": 456,
                "path": "/home/user/project"
                # No session_id
            }
        }
        mock_config.general_topic_id = 1

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)), \
             patch("bot_commands.get_config", return_value=mock_config), \
             patch("bot_commands.get_registry", return_value=mock_registry), \
             patch("bot_commands.send_reply") as mock_reply:

            handler = CommandHandler("TOKEN", "-1001234567890", {})
            msg = {
                "text": "/connect",
                "message_id": 1,
                "chat": {"id": -1001234567890},
                "message_thread_id": 456
            }

            result = await handler.handle_command(msg)
            assert result is True
            mock_reply.assert_called_once()
            call_args = mock_reply.call_args[0]
            assert "has no active session" in call_args[3]

    @pytest.mark.asyncio
    async def test_connect_no_path(self, temp_dir, mock_config, mock_registry):
        """Test /connect when task has no path."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        mock_registry.tasks = {
            "my_task": {
                "topic_id": 456,
                "session_id": "ses_abc123"
                # No path
            }
        }
        mock_config.general_topic_id = 1

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)), \
             patch("bot_commands.get_config", return_value=mock_config), \
             patch("bot_commands.get_registry", return_value=mock_registry), \
             patch("bot_commands.send_reply") as mock_reply:

            handler = CommandHandler("TOKEN", "-1001234567890", {})
            msg = {
                "text": "/connect",
                "message_id": 1,
                "chat": {"id": -1001234567890},
                "message_thread_id": 456
            }

            result = await handler.handle_command(msg)
            assert result is True
            mock_reply.assert_called_once()
            call_args = mock_reply.call_args[0]
            assert "has no path recorded" in call_args[3]

    @pytest.mark.asyncio
    async def test_connect_infers_task_from_topic(self, temp_dir, mock_config, mock_registry):
        """Test /connect infers task from topic."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        mock_registry.tasks = {
            "my_task": {
                "topic_id": 456,
                "session_id": "ses_xyz789",
                "path": "/tmp/work"
            }
        }
        mock_config.general_topic_id = 1

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)), \
             patch("bot_commands.get_config", return_value=mock_config), \
             patch("bot_commands.get_registry", return_value=mock_registry), \
             patch("bot_commands.send_reply") as mock_reply:

            handler = CommandHandler("TOKEN", "-1001234567890", {})
            msg = {
                "text": "/connect",
                "message_id": 1,
                "chat": {"id": -1001234567890},
                "message_thread_id": 456
            }

            result = await handler.handle_command(msg)
            assert result is True
            mock_reply.assert_called_once()
            call_args = mock_reply.call_args[0]
            assert "cd /tmp/work" in call_args[3]
            assert "claude --resume ses_xyz789" in call_args[3]


class TestHandleStatus:
    """Tests for /status command handler."""

    @pytest.mark.asyncio
    async def test_status_no_tasks(self, temp_dir, mock_registry, mock_config):
        """Test /status with no tasks shows 'No tasks'."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        mock_registry.tasks = {}
        mock_config.general_topic_id = 1

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)), \
             patch("bot_commands.get_registry", return_value=mock_registry), \
             patch("bot_commands.get_config", return_value=mock_config), \
             patch("bot_commands.send_reply") as mock_reply:

            handler = CommandHandler("TOKEN", "-1001234567890", {})
            msg = {
                "text": "/status",
                "message_id": 1,
                "chat": {"id": -1001234567890},
                "message_thread_id": None
            }

            result = await handler.handle_command(msg)
            assert result is True
            mock_reply.assert_called_once()
            call_args = mock_reply.call_args[0]
            assert "Status" in call_args[3]
            assert "No tasks" in call_args[3]

    @pytest.mark.asyncio
    async def test_status_running_task(self, temp_dir, mock_registry, mock_config):
        """Test /status shows running task correctly."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        mock_registry.tasks = {
            "my_task": {"topic_id": 456, "session_id": "ses_123", "path": "/tmp"}
        }
        mock_config.general_topic_id = 1

        mock_pm = MagicMock()
        mock_pm.is_running.return_value = True

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)), \
             patch("bot_commands.get_registry", return_value=mock_registry), \
             patch("bot_commands.get_config", return_value=mock_config), \
             patch("bot_commands.send_reply") as mock_reply:

            handler = CommandHandler("TOKEN", "-1001234567890", {}, process_manager=mock_pm)
            msg = {
                "text": "/status",
                "message_id": 1,
                "chat": {"id": -1001234567890},
                "message_thread_id": None
            }

            result = await handler.handle_command(msg)
            assert result is True
            mock_reply.assert_called_once()
            call_args = mock_reply.call_args[0]
            assert "my_task" in call_args[3]
            assert "Running" in call_args[3]
            mock_pm.is_running.assert_called_with("my_task")

    @pytest.mark.asyncio
    async def test_status_stopped_task(self, temp_dir, mock_registry, mock_config):
        """Test /status shows stopped task correctly."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        mock_registry.tasks = {
            "my_task": {"topic_id": 456, "session_id": "ses_123", "path": "/tmp"}
        }
        mock_config.general_topic_id = 1

        mock_pm = MagicMock()
        mock_pm.is_running.return_value = False

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)), \
             patch("bot_commands.get_registry", return_value=mock_registry), \
             patch("bot_commands.get_config", return_value=mock_config), \
             patch("bot_commands.send_reply") as mock_reply:

            handler = CommandHandler("TOKEN", "-1001234567890", {}, process_manager=mock_pm)
            msg = {
                "text": "/status",
                "message_id": 1,
                "chat": {"id": -1001234567890},
                "message_thread_id": None
            }

            result = await handler.handle_command(msg)
            assert result is True
            mock_reply.assert_called_once()
            call_args = mock_reply.call_args[0]
            assert "my_task" in call_args[3]
            assert "Stopped" in call_args[3]

    @pytest.mark.asyncio
    async def test_status_permission_blocked(self, temp_dir, mock_registry, mock_config):
        """Test /status shows task waiting for permission."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        session_id = "ses_blocked_123"
        mock_registry.tasks = {
            "blocked_task": {"topic_id": 456, "session_id": session_id, "path": "/tmp"}
        }
        mock_config.general_topic_id = 1

        mock_pm = MagicMock()
        mock_pm.is_running.return_value = True

        # Create mock pending permission
        mock_pending = MagicMock()
        mock_pending.session_id = session_id
        mock_pending.tool_name = "Bash"

        mock_perm_mgr = MagicMock()
        mock_perm_mgr.pending = {"tool_123": mock_pending}

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)), \
             patch("bot_commands.get_registry", return_value=mock_registry), \
             patch("bot_commands.get_config", return_value=mock_config), \
             patch("bot_commands.send_reply") as mock_reply:

            handler = CommandHandler(
                "TOKEN", "-1001234567890", {},
                process_manager=mock_pm,
                permission_manager=mock_perm_mgr
            )
            msg = {
                "text": "/status",
                "message_id": 1,
                "chat": {"id": -1001234567890},
                "message_thread_id": None
            }

            result = await handler.handle_command(msg)
            assert result is True
            mock_reply.assert_called_once()
            call_args = mock_reply.call_args[0]
            assert "blocked_task" in call_args[3]
            assert "Permission" in call_args[3]
            assert "Bash" in call_args[3]
            # Should also show pending count
            assert "1 pending permission" in call_args[3]

    @pytest.mark.asyncio
    async def test_status_multiple_tasks(self, temp_dir, mock_registry, mock_config):
        """Test /status with mix of running/stopped/blocked tasks."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        mock_registry.tasks = {
            "running_task": {"topic_id": 100, "session_id": "ses_run", "path": "/tmp/a"},
            "stopped_task": {"topic_id": 200, "session_id": "ses_stop", "path": "/tmp/b"},
            "blocked_task": {"topic_id": 300, "session_id": "ses_block", "path": "/tmp/c"},
        }
        mock_config.general_topic_id = 1

        mock_pm = MagicMock()

        def is_running(name):
            return name in ("running_task", "blocked_task")
        mock_pm.is_running.side_effect = is_running

        # Pending permission for blocked_task
        mock_pending = MagicMock()
        mock_pending.session_id = "ses_block"
        mock_pending.tool_name = "Write"

        mock_perm_mgr = MagicMock()
        mock_perm_mgr.pending = {"tool_xyz": mock_pending}

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)), \
             patch("bot_commands.get_registry", return_value=mock_registry), \
             patch("bot_commands.get_config", return_value=mock_config), \
             patch("bot_commands.send_reply") as mock_reply:

            handler = CommandHandler(
                "TOKEN", "-1001234567890", {},
                process_manager=mock_pm,
                permission_manager=mock_perm_mgr
            )
            msg = {
                "text": "/status",
                "message_id": 1,
                "chat": {"id": -1001234567890},
                "message_thread_id": None
            }

            result = await handler.handle_command(msg)
            assert result is True
            mock_reply.assert_called_once()
            call_args = mock_reply.call_args[0]
            response_text = call_args[3]

            # Check running task
            assert "running_task" in response_text
            assert "Running" in response_text

            # Check stopped task
            assert "stopped_task" in response_text
            assert "Stopped" in response_text

            # Check blocked task (should show Permission, not Running)
            assert "blocked_task" in response_text
            assert "Permission" in response_text
            assert "Write" in response_text

    @pytest.mark.asyncio
    async def test_status_in_task_topic_shows_detailed_info(self, temp_dir, mock_registry, mock_config):
        """Test /status in task topic shows detailed task info."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        mock_registry.tasks = {
            "my_task": {
                "topic_id": 456,
                "session_id": "ses_abc123def456",
                "path": "/home/ubuntu/project",
                "pid": 12345
            }
        }
        mock_config.general_topic_id = 1

        mock_pm = MagicMock()
        mock_pm.is_running.return_value = True

        mock_perm_mgr = MagicMock()
        mock_perm_mgr.pending = {}

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)), \
             patch("bot_commands.get_registry", return_value=mock_registry), \
             patch("bot_commands.get_config", return_value=mock_config), \
             patch("bot_commands.send_reply") as mock_reply:

            handler = CommandHandler(
                "TOKEN", "-1001234567890", {},
                process_manager=mock_pm,
                permission_manager=mock_perm_mgr
            )
            msg = {
                "text": "/status",
                "message_id": 1,
                "chat": {"id": -1001234567890},
                "message_thread_id": 456  # Task topic
            }

            result = await handler.handle_command(msg)
            assert result is True
            mock_reply.assert_called_once()
            call_args = mock_reply.call_args[0]
            response_text = call_args[3]

            # Check detailed info
            assert "Status: my_task" in response_text
            assert "Running" in response_text
            assert "PID: 12345" in response_text
            assert "ses_abc123de" in response_text  # Truncated session ID (12 chars)
            assert "/home/ubuntu/project" in response_text

    @pytest.mark.asyncio
    async def test_status_in_task_topic_with_permission_block(self, temp_dir, mock_registry, mock_config):
        """Test /status in task topic shows permission block."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        session_id = "ses_blocked_xyz"
        mock_registry.tasks = {
            "my_task": {
                "topic_id": 456,
                "session_id": session_id,
                "path": "/tmp/work",
                "pid": 9999
            }
        }
        mock_config.general_topic_id = 1

        mock_pm = MagicMock()
        mock_pm.is_running.return_value = True

        mock_pending = MagicMock()
        mock_pending.session_id = session_id
        mock_pending.tool_name = "Bash"

        mock_perm_mgr = MagicMock()
        mock_perm_mgr.pending = {"tool_123": mock_pending}

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)), \
             patch("bot_commands.get_registry", return_value=mock_registry), \
             patch("bot_commands.get_config", return_value=mock_config), \
             patch("bot_commands.send_reply") as mock_reply:

            handler = CommandHandler(
                "TOKEN", "-1001234567890", {},
                process_manager=mock_pm,
                permission_manager=mock_perm_mgr
            )
            msg = {
                "text": "/status",
                "message_id": 1,
                "chat": {"id": -1001234567890},
                "message_thread_id": 456
            }

            result = await handler.handle_command(msg)
            assert result is True
            mock_reply.assert_called_once()
            call_args = mock_reply.call_args[0]
            response_text = call_args[3]

            assert "Status: my_task" in response_text
            assert "Permission" in response_text
            assert "Bash" in response_text

    @pytest.mark.asyncio
    async def test_status_in_task_topic_with_stats(self, temp_dir, mock_registry, mock_config):
        """Test /status in task topic shows cost/turns/last_activity."""
        reset_singletons()

        import time as time_module

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        mock_registry.tasks = {
            "my_task": {
                "topic_id": 456,
                "session_id": "ses_xyz789",
                "path": "/tmp/work",
                "pid": 1234
            }
        }
        mock_config.general_topic_id = 1

        mock_pm = MagicMock()
        mock_pm.is_running.return_value = True

        mock_perm_mgr = MagicMock()
        mock_perm_mgr.pending = {}

        # Stats getter
        def get_task_stats(task_name):
            if task_name == "my_task":
                return {
                    "cost": 0.0234,
                    "turns": 5,
                    "last_activity": time_module.time() - 120  # 2 minutes ago
                }
            return None

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)), \
             patch("bot_commands.get_registry", return_value=mock_registry), \
             patch("bot_commands.get_config", return_value=mock_config), \
             patch("bot_commands.send_reply") as mock_reply:

            handler = CommandHandler(
                "TOKEN", "-1001234567890", {},
                process_manager=mock_pm,
                permission_manager=mock_perm_mgr,
                get_task_stats=get_task_stats
            )
            msg = {
                "text": "/status",
                "message_id": 1,
                "chat": {"id": -1001234567890},
                "message_thread_id": 456
            }

            result = await handler.handle_command(msg)
            assert result is True
            mock_reply.assert_called_once()
            call_args = mock_reply.call_args[0]
            response_text = call_args[3]

            assert "Status: my_task" in response_text
            assert "$0.0234" in response_text
            assert "5 turns" in response_text
            assert "2m ago" in response_text

    @pytest.mark.asyncio
    async def test_status_in_operator_topic_shows_global_status(self, temp_dir, mock_registry, mock_config):
        """Test /status in operator/general topic shows global status."""
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        registry_path = Path(temp_dir) / "registry.json"

        mock_registry.tasks = {
            "task1": {"topic_id": 100, "session_id": "ses_1", "path": "/tmp/a"},
            "task2": {"topic_id": 200, "session_id": "ses_2", "path": "/tmp/b"},
        }
        mock_config.general_topic_id = 1

        mock_pm = MagicMock()
        mock_pm.is_running.return_value = True

        mock_perm_mgr = MagicMock()
        mock_perm_mgr.pending = {}

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)), \
             patch("bot_commands.get_registry", return_value=mock_registry), \
             patch("bot_commands.get_config", return_value=mock_config), \
             patch("bot_commands.send_reply") as mock_reply:

            handler = CommandHandler(
                "TOKEN", "-1001234567890", {},
                process_manager=mock_pm,
                permission_manager=mock_perm_mgr
            )
            msg = {
                "text": "/status",
                "message_id": 1,
                "chat": {"id": -1001234567890},
                "message_thread_id": 1  # General topic (operator)
            }

            result = await handler.handle_command(msg)
            assert result is True
            mock_reply.assert_called_once()
            call_args = mock_reply.call_args[0]
            response_text = call_args[3]

            # Should show global status (not detailed task status)
            assert "*Status*" in response_text
            assert "task1" in response_text
            assert "task2" in response_text
            # Should NOT show detailed info like PID
            assert "PID:" not in response_text
