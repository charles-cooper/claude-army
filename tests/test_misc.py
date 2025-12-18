"""Tests for miscellaneous modules - FrontendAdapter, daemon helpers, full flow integration."""

import asyncio
import queue
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_process import (
    AssistantMessage,
    ClaudeProcess,
    SessionResult,
    SystemInit,
    extract_text,
    extract_tool_uses,
)

from conftest import (
    MockClaudeSubprocess,
    wait_for_pending,
    SYSTEM_INIT_EVENT,
    ASSISTANT_TEXT_MESSAGE,
    ASSISTANT_BASH_TOOL_MESSAGE,
    SESSION_RESULT_SUCCESS,
)


class TestFrontendAdapter:
    """Test FrontendAdapter abstract class and IncomingMessage."""

    def test_incoming_message_dataclass(self):
        """Test IncomingMessage dataclass."""
        from frontend_adapter import IncomingMessage

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


class TestDaemonHelpers:
    """Test daemon helper functions."""

    def test_cleanup_pid_file(self, temp_dir):
        """Test cleanup_pid_file removes PID file."""
        pid_file = Path(temp_dir) / "test.pid"
        pid_file.write_text("12345")

        with patch("daemon.PID_FILE", pid_file):
            from daemon import cleanup_pid_file
            cleanup_pid_file()
            assert not pid_file.exists()


@pytest.mark.asyncio
class TestFullFlowIntegration:
    """Test full flow: user message -> Claude -> response -> frontend."""

    async def test_user_message_to_response(self, mock_frontend, temp_dir):
        """Test complete message flow with mocked components."""
        mock_proc = MockClaudeSubprocess(events=[
            SYSTEM_INIT_EVENT,
            ASSISTANT_TEXT_MESSAGE,
            SESSION_RESULT_SUCCESS,
        ])

        claude_received = []

        original_send = mock_proc._stdin_write
        def tracking_send(data):
            original_send(data)
            claude_received.append(data)
        mock_proc._stdin_write = tracking_send

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            process = ClaudeProcess(cwd=temp_dir)

            emit_task = asyncio.create_task(mock_proc.emit_events())

            await process.start()

            user_text = "Hello Claude, help me with something"
            await process.send_message(user_text)

            events = []
            async for event in process.events():
                events.append(event)

                if isinstance(event, AssistantMessage):
                    text = extract_text(event)
                    if text:
                        await mock_frontend.send_message("operator", text)

                if isinstance(event, SessionResult):
                    break

            await emit_task

            assert len(events) == 3  # init, assistant, result
            assert isinstance(events[0], SystemInit)
            assert isinstance(events[1], AssistantMessage)
            assert isinstance(events[2], SessionResult)

            assert len(mock_frontend.sent_messages) == 1
            assert mock_frontend.sent_messages[0]["content"] == "I'll help you with that task."
            assert mock_frontend.sent_messages[0]["task_id"] == "operator"

    async def test_permission_flow(self, mock_frontend, permission_manager, temp_dir):
        """Test permission request flow with Bash tool."""
        mock_proc = MockClaudeSubprocess(events=[
            SYSTEM_INIT_EVENT,
            ASSISTANT_BASH_TOOL_MESSAGE,
        ])

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            process = ClaudeProcess(cwd=temp_dir)

            emit_task = asyncio.create_task(mock_proc.emit_events())

            await process.start()

            tool_use_event = None
            async for event in process.events():
                if isinstance(event, AssistantMessage):
                    tools = extract_tool_uses(event)
                    if tools:
                        tool_use_event = tools[0]
                        break

            await emit_task

            assert tool_use_event is not None
            assert tool_use_event.name == "Bash"

            result_queue = queue.Queue()

            def request_permission():
                decision, reason = permission_manager.request_permission(
                    tool_name=tool_use_event.name,
                    tool_input=tool_use_event.input,
                    tool_use_id=tool_use_event.id,
                    session_id=process.session_id or "test",
                    cwd=temp_dir,
                )
                result_queue.put((decision, reason))

            perm_thread = threading.Thread(target=request_permission)
            perm_thread.start()

            assert wait_for_pending(permission_manager, tool_use_event.id)

            pending = permission_manager.get_pending(tool_use_event.id)
            assert pending is not None

            buttons = [
                {"text": "Allow", "callback_data": f"allow:{tool_use_event.id}"},
                {"text": "Deny", "callback_data": f"deny:{tool_use_event.id}"},
            ]
            msg_id = await mock_frontend.send_message(
                "operator",
                f"Permission for {tool_use_event.name}",
                buttons=buttons,
            )

            permission_manager.register_telegram_msg(tool_use_event.id, int(msg_id))

            permission_manager.respond_by_msg_id(int(msg_id), "allow", "User approved")

            perm_thread.join(timeout=1.0)

            decision, reason = result_queue.get(timeout=1.0)
            assert decision == "allow"

            await mock_frontend.update_message(
                "operator",
                msg_id,
                buttons=[{"text": "Allowed", "callback_data": "_"}],
            )

            assert len(mock_frontend.updated_messages) == 1
