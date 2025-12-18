"""Tests for frontend_adapter.py - FrontendAdapter abstract class and IncomingMessage."""

import pytest

from frontend_adapter import FrontendAdapter, IncomingMessage


class TestIncomingMessage:
    """Test IncomingMessage dataclass."""

    def test_incoming_message_all_fields(self):
        """Test IncomingMessage with all fields populated."""
        msg = IncomingMessage(
            task_id="test",
            text="Hello",
            callback_data=None,
            msg_id="123",
            reply_to_msg_id="100"
        )

        assert msg.task_id == "test"
        assert msg.text == "Hello"
        assert msg.callback_data is None
        assert msg.msg_id == "123"
        assert msg.reply_to_msg_id == "100"

    def test_incoming_message_with_callback(self):
        """Test IncomingMessage with callback data."""
        msg = IncomingMessage(
            task_id="operator",
            text=None,
            callback_data="allow:tool_123",
            msg_id="456",
            reply_to_msg_id=None
        )

        assert msg.task_id == "operator"
        assert msg.text is None
        assert msg.callback_data == "allow:tool_123"
        assert msg.msg_id == "456"
        assert msg.reply_to_msg_id is None

    def test_incoming_message_minimal(self):
        """Test IncomingMessage with minimal fields."""
        msg = IncomingMessage(
            task_id="task1",
            text="Hi",
            callback_data=None,
            msg_id="1",
            reply_to_msg_id=None
        )

        assert msg.task_id == "task1"
        assert msg.text == "Hi"
