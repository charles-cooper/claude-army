"""Tests for telegram_poller.py - Pure logic functions."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from telegram_poller import (
    tool_already_handled, get_pending_tool_from_transcript, get_action_label,
    TelegramPoller
)
from telegram_utils import State


# =============================================================================
# Transcript Function Tests
# =============================================================================


class TestTranscriptFunctions:
    """Tests for transcript parsing functions."""

    # tool_already_handled tests

    def test_tool_already_handled_found(self, transcript_file):
        """Test tool_already_handled returns True when tool_result exists."""
        lines = [
            '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_123","name":"Bash"}]}}',
            '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"toolu_123","content":"ok"}]}}',
        ]
        path = transcript_file(lines)

        result = tool_already_handled(path, "toolu_123")
        assert result is True

    def test_tool_already_handled_not_found(self, transcript_file):
        """Test tool_already_handled returns False when no tool_result."""
        lines = [
            '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_123","name":"Bash"}]}}',
        ]
        path = transcript_file(lines)

        result = tool_already_handled(path, "toolu_123")
        assert result is False

    def test_tool_already_handled_empty_file(self, temp_dir):
        """Test tool_already_handled returns False for empty file."""
        path = Path(temp_dir) / "empty.jsonl"
        path.write_text("")

        result = tool_already_handled(str(path), "toolu_123")
        assert result is False

    def test_tool_already_handled_file_not_exists(self):
        """Test tool_already_handled returns False for missing file."""
        result = tool_already_handled("/nonexistent/path.jsonl", "toolu_123")
        assert result is False

    def test_tool_already_handled_none_path(self):
        """Test tool_already_handled returns False for None path."""
        result = tool_already_handled(None, "toolu_123")
        assert result is False

    def test_tool_already_handled_none_tool_id(self, transcript_file):
        """Test tool_already_handled returns False for None tool_id."""
        lines = ['{"type":"assistant"}']
        path = transcript_file(lines)

        result = tool_already_handled(path, None)
        assert result is False

    # get_pending_tool_from_transcript tests

    def test_get_pending_tool_found(self, transcript_file):
        """Test get_pending_tool_from_transcript returns pending tool_use_id."""
        lines = [
            '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_456","name":"Bash"}]}}',
        ]
        path = transcript_file(lines)

        result = get_pending_tool_from_transcript(path)
        assert result == "toolu_456"

    def test_get_pending_tool_all_handled(self, transcript_file):
        """Test get_pending_tool_from_transcript returns None when all handled."""
        lines = [
            '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_456","name":"Bash"}]}}',
            '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"toolu_456","content":"ok"}]}}',
        ]
        path = transcript_file(lines)

        result = get_pending_tool_from_transcript(path)
        assert result is None

    def test_get_pending_tool_empty_file(self, temp_dir):
        """Test get_pending_tool_from_transcript returns None for empty file."""
        path = Path(temp_dir) / "empty.jsonl"
        path.write_text("")

        result = get_pending_tool_from_transcript(str(path))
        assert result is None

    def test_get_pending_tool_no_tools(self, transcript_file):
        """Test get_pending_tool_from_transcript returns None when no tool_use."""
        lines = [
            '{"type":"assistant","message":{"content":[{"type":"text","text":"Hello"}]}}',
        ]
        path = transcript_file(lines)

        result = get_pending_tool_from_transcript(path)
        assert result is None

    def test_get_pending_tool_multiple_pending(self, transcript_file):
        """Test get_pending_tool_from_transcript returns one of multiple pending."""
        lines = [
            '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_111","name":"Bash"}]}}',
            '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_222","name":"Read"}]}}',
        ]
        path = transcript_file(lines)

        result = get_pending_tool_from_transcript(path)
        # Should return one of the pending tools
        assert result in ["toolu_111", "toolu_222"]

    def test_get_pending_tool_none_path(self):
        """Test get_pending_tool_from_transcript returns None for None path."""
        result = get_pending_tool_from_transcript(None)
        assert result is None


# =============================================================================
# Action Label Tests
# =============================================================================


class TestActionLabels:
    """Tests for get_action_label."""

    def test_get_action_label_y(self):
        """Test get_action_label returns 'Allowed' for 'y'."""
        result = get_action_label("y")
        assert "Allowed" in result

    def test_get_action_label_a(self):
        """Test get_action_label returns 'Always' for 'a'."""
        result = get_action_label("a")
        assert "Always" in result

    def test_get_action_label_n(self):
        """Test get_action_label returns 'Reply' for 'n'."""
        result = get_action_label("n")
        assert "Reply" in result

    def test_get_action_label_replied(self):
        """Test get_action_label returns 'Replied' for 'replied'."""
        result = get_action_label("replied")
        assert "Replied" in result

    def test_get_action_label_unknown(self):
        """Test get_action_label returns 'Expired' for unknown action."""
        result = get_action_label("unknown")
        assert "Expired" in result


# =============================================================================
# TelegramPoller Method Tests
# =============================================================================


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


class TestTelegramPollerMethods:
    """Tests for TelegramPoller instance methods (pure logic only)."""

    def _create_poller(self):
        """Create a TelegramPoller with mocked dependencies."""
        mock_config = MagicMock()
        mock_config.get = MagicMock(return_value=0)

        with patch("telegram_poller.get_config", return_value=mock_config), \
             patch("telegram_poller.CommandHandler"):
            state = MockState()
            poller = TelegramPoller("TOKEN", "-1001234567890", state)
            return poller

    # _has_pending_permission tests

    def test_has_pending_permission_found(self):
        """Test _has_pending_permission returns True for unhandled permission."""
        poller = self._create_poller()

        # Add an unhandled permission prompt to state
        poller.state.set("100", {
            "type": "permission_prompt",
            "pane": "my_pane:0.0",
            "handled": False,
        })

        result = poller._has_pending_permission("my_pane:0.0")
        assert result is True

    def test_has_pending_permission_all_handled(self):
        """Test _has_pending_permission returns False when all handled."""
        poller = self._create_poller()

        # Add a handled permission prompt
        poller.state.set("100", {
            "type": "permission_prompt",
            "pane": "my_pane:0.0",
            "handled": True,
        })

        result = poller._has_pending_permission("my_pane:0.0")
        assert result is False

    def test_has_pending_permission_wrong_pane(self):
        """Test _has_pending_permission returns False for different pane."""
        poller = self._create_poller()

        # Add permission for a different pane
        poller.state.set("100", {
            "type": "permission_prompt",
            "pane": "other_pane:0.0",
            "handled": False,
        })

        result = poller._has_pending_permission("my_pane:0.0")
        assert result is False

    def test_has_pending_permission_empty_state(self):
        """Test _has_pending_permission returns False for empty state."""
        poller = self._create_poller()

        result = poller._has_pending_permission("my_pane:0.0")
        assert result is False

    def test_has_pending_permission_non_permission_type(self):
        """Test _has_pending_permission ignores non-permission types."""
        poller = self._create_poller()

        # Add a different type of entry
        poller.state.set("100", {
            "type": "notification",
            "pane": "my_pane:0.0",
            "handled": False,
        })

        result = poller._has_pending_permission("my_pane:0.0")
        assert result is False

    # _is_stale_notification tests

    def test_is_stale_notification_stale(self):
        """Test _is_stale_notification returns True for older notification."""
        poller = self._create_poller()

        # Add two notifications for same pane
        poller.state.set("100", {"pane": "my_pane:0.0"})
        poller.state.set("200", {"pane": "my_pane:0.0"})

        result = poller._is_stale_notification(100, "my_pane:0.0")
        assert result is True

    def test_is_stale_notification_latest(self):
        """Test _is_stale_notification returns False for latest notification."""
        poller = self._create_poller()

        # Add two notifications for same pane
        poller.state.set("100", {"pane": "my_pane:0.0"})
        poller.state.set("200", {"pane": "my_pane:0.0"})

        result = poller._is_stale_notification(200, "my_pane:0.0")
        assert result is False

    def test_is_stale_notification_only_one(self):
        """Test _is_stale_notification returns False when only one notification."""
        poller = self._create_poller()

        poller.state.set("100", {"pane": "my_pane:0.0"})

        result = poller._is_stale_notification(100, "my_pane:0.0")
        assert result is False

    def test_is_stale_notification_different_pane(self):
        """Test _is_stale_notification ignores notifications for different pane."""
        poller = self._create_poller()

        # Add notifications for different panes
        poller.state.set("100", {"pane": "my_pane:0.0"})
        poller.state.set("200", {"pane": "other_pane:0.0"})

        # 100 is the latest for my_pane:0.0
        result = poller._is_stale_notification(100, "my_pane:0.0")
        assert result is False

    # _format_incoming_message tests

    def test_format_incoming_message_basic(self):
        """Test _format_incoming_message formats text with metadata."""
        poller = self._create_poller()

        msg = {
            "message_id": 123,
            "text": "Hello world",
            "from": {"first_name": "John"},
        }

        result = poller._format_incoming_message(msg)
        assert "msg_id=123" in result
        assert "from=John" in result
        assert "Hello world" in result

    def test_format_incoming_message_with_topic(self):
        """Test _format_incoming_message includes topic_id."""
        poller = self._create_poller()

        msg = {
            "message_id": 123,
            "message_thread_id": 456,
            "text": "Hello",
            "from": {"first_name": "John"},
        }

        result = poller._format_incoming_message(msg)
        assert "topic=456" in result

    def test_format_incoming_message_with_reply(self):
        """Test _format_incoming_message includes reply context."""
        poller = self._create_poller()

        msg = {
            "message_id": 123,
            "text": "My reply",
            "from": {"first_name": "John"},
            "reply_to_message": {
                "message_id": 100,
                "text": "Original message",
                "from": {"first_name": "Jane"},
            },
        }

        result = poller._format_incoming_message(msg)
        assert "Replying to msg_id=100" in result
        assert "Jane" in result
        assert "Original message" in result

    def test_format_incoming_message_with_state(self):
        """Test _format_incoming_message includes state info."""
        poller = self._create_poller()

        # Add state entry for the replied-to message
        poller.state.set("100", {
            "type": "permission_prompt",
            "pane": "my_pane:0.0",
        })

        msg = {
            "message_id": 123,
            "text": "My reply",
            "from": {"first_name": "John"},
            "reply_to_message": {
                "message_id": 100,
                "text": "Original",
                "from": {"first_name": "Jane"},
            },
        }

        result = poller._format_incoming_message(msg)
        assert "State:" in result
        assert "permission_prompt" in result

    def test_format_incoming_message_skip_topic_root_reply(self):
        """Test _format_incoming_message skips self-reply to topic root."""
        poller = self._create_poller()

        # Message replying to topic root (message_thread_id == reply_to_message.message_id)
        msg = {
            "message_id": 123,
            "message_thread_id": 456,
            "text": "Hello",
            "from": {"first_name": "John"},
            "reply_to_message": {
                "message_id": 456,  # Same as topic id
                "text": "Topic root",
                "from": {"first_name": "Bot"},
            },
        }

        result = poller._format_incoming_message(msg)
        # Should NOT include reply context for topic root
        assert "Replying to" not in result

    def test_format_incoming_message_truncates_long_reply(self):
        """Test _format_incoming_message truncates long reply text."""
        poller = self._create_poller()

        long_text = "A" * 500

        msg = {
            "message_id": 123,
            "text": "My reply",
            "from": {"first_name": "John"},
            "reply_to_message": {
                "message_id": 100,
                "text": long_text,
                "from": {"first_name": "Jane"},
            },
        }

        result = poller._format_incoming_message(msg)
        # Reply text should be truncated to 200 chars
        # The original text is 500 chars, truncated to 200
        # Count A's in the result
        a_count = result.count("A")
        assert a_count == 200

    def test_format_incoming_message_missing_from(self):
        """Test _format_incoming_message handles missing from field."""
        poller = self._create_poller()

        msg = {
            "message_id": 123,
            "text": "Hello",
        }

        result = poller._format_incoming_message(msg)
        assert "from=Unknown" in result
