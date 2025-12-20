"""Tests for claude_process.py - ClaudeProcess event parsing and subprocess integration."""

import asyncio
import json
import sys

import pytest
from unittest.mock import patch, MagicMock

from claude_process import (
    AssistantMessage,
    ClaudeProcess,
    SessionResult,
    SystemInit,
    ToolUse,
    UserMessage,
    extract_text,
    extract_tool_uses,
    has_thinking,
    _set_pdeathsig,
)

from conftest import (
    MockClaudeSubprocess,
    SYSTEM_INIT_EVENT,
    ASSISTANT_TEXT_MESSAGE,
    ASSISTANT_TOOL_USE_MESSAGE,
    ASSISTANT_BASH_TOOL_MESSAGE,
    ASSISTANT_THINKING_MESSAGE,
    USER_MESSAGE_ECHO,
    SESSION_RESULT_SUCCESS,
    SESSION_RESULT_ERROR,
)


class TestClaudeProcessEventParsing:
    """Test ClaudeProcess parses events correctly."""

    def test_parse_system_init(self):
        """Test parsing system/init event."""
        event = SYSTEM_INIT_EVENT.copy()
        init = SystemInit(
            session_id=event["session_id"],
            tools=event["tools"],
            model=event["model"],
            raw=event,
        )

        assert init.session_id == "test-session-abc123"
        assert init.model == "claude-sonnet-4-20250514"
        assert len(init.tools) == 6
        assert init.tools[0]["name"] == "Read"

    def test_parse_assistant_message_text(self):
        """Test parsing assistant message with text."""
        event = ASSISTANT_TEXT_MESSAGE.copy()
        message = event["message"]
        msg = AssistantMessage(
            content=message["content"],
            model=message["model"],
            msg_id=message["id"],
            raw=event,
        )

        assert msg.msg_id == "msg_01ABC123"
        assert msg.model == "claude-sonnet-4-20250514"
        assert len(msg.content) == 1
        assert msg.content[0]["type"] == "text"

        text = extract_text(msg)
        assert text == "I'll help you with that task."

    def test_parse_assistant_message_tool_use(self):
        """Test parsing assistant message with tool_use."""
        event = ASSISTANT_TOOL_USE_MESSAGE.copy()
        message = event["message"]
        msg = AssistantMessage(
            content=message["content"],
            model=message["model"],
            msg_id=message["id"],
            raw=event,
        )

        tools = extract_tool_uses(msg)
        assert len(tools) == 1

        tool = tools[0]
        assert tool.id == "toolu_01GHI789"
        assert tool.name == "Read"
        assert tool.input == {"file_path": "/home/user/test.py"}

    def test_parse_assistant_message_bash(self):
        """Test parsing Bash tool use."""
        event = ASSISTANT_BASH_TOOL_MESSAGE.copy()
        message = event["message"]
        msg = AssistantMessage(
            content=message["content"],
            model=message["model"],
            msg_id=message["id"],
            raw=event,
        )

        tools = extract_tool_uses(msg)
        assert len(tools) == 1

        tool = tools[0]
        assert tool.name == "Bash"
        assert tool.input["command"] == "ls -la"
        assert tool.input["description"] == "List files in directory"

    def test_parse_assistant_message_thinking(self):
        """Test parsing message with thinking block."""
        event = ASSISTANT_THINKING_MESSAGE.copy()
        message = event["message"]
        msg = AssistantMessage(
            content=message["content"],
            model=message["model"],
            msg_id=message["id"],
            raw=event,
        )

        assert has_thinking(msg) is True
        text = extract_text(msg)
        assert text == "Here's my analysis."

    def test_parse_session_result_success(self):
        """Test parsing session result (success)."""
        event = SESSION_RESULT_SUCCESS.copy()
        result = SessionResult(
            success=(event["subtype"] == "success"),
            result=event["result"],
            cost=event["total_cost_usd"],
            turns=event["num_turns"],
            raw=event,
        )

        assert result.success is True
        assert result.result == "Task completed successfully."
        assert result.cost == 0.0042
        assert result.turns == 3

    def test_parse_session_result_error(self):
        """Test parsing session result (error)."""
        event = SESSION_RESULT_ERROR.copy()
        result = SessionResult(
            success=(event["subtype"] == "success"),
            result=event["result"],
            cost=event["total_cost_usd"],
            turns=event["num_turns"],
            raw=event,
        )

        assert result.success is False
        assert "Error" in result.result

    def test_parse_user_message(self):
        """Test parsing user message echo."""
        event = USER_MESSAGE_ECHO.copy()
        message = event["message"]
        msg = UserMessage(
            content=message["content"],
            raw=event,
        )

        assert len(msg.content) == 1
        assert msg.content[0]["text"] == "Hello Claude!"


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_extract_text_empty_content(self):
        """Test extract_text with empty content."""
        msg = AssistantMessage(content=[], raw={})
        text = extract_text(msg)
        assert text == ""

    def test_extract_tool_uses_no_tools(self):
        """Test extract_tool_uses with no tool_use blocks."""
        msg = AssistantMessage(
            content=[{"type": "text", "text": "No tools here"}],
            raw={},
        )
        tools = extract_tool_uses(msg)
        assert tools == []

    def test_has_thinking_no_thinking(self):
        """Test has_thinking with no thinking block."""
        msg = AssistantMessage(
            content=[{"type": "text", "text": "Plain text"}],
            raw={},
        )
        assert has_thinking(msg) is False


class TestJSONLFormat:
    """Validate JSONL event format matches documentation."""

    def test_system_init_has_required_fields(self):
        """Test system/init event has all required fields."""
        event = SYSTEM_INIT_EVENT

        assert event["type"] == "system"
        assert event["subtype"] == "init"
        assert "session_id" in event
        assert "tools" in event
        assert "model" in event

    def test_assistant_message_structure(self):
        """Test assistant message structure."""
        event = ASSISTANT_TEXT_MESSAGE

        assert event["type"] == "assistant"
        assert "message" in event

        msg = event["message"]
        assert msg["role"] == "assistant"
        assert "content" in msg
        assert isinstance(msg["content"], list)

    def test_tool_use_block_structure(self):
        """Test tool_use block structure."""
        event = ASSISTANT_TOOL_USE_MESSAGE
        content = event["message"]["content"]

        tool_block = next(b for b in content if b["type"] == "tool_use")

        assert "id" in tool_block
        assert "name" in tool_block
        assert "input" in tool_block
        assert tool_block["id"].startswith("toolu_")

    def test_result_event_has_metadata(self):
        """Test result event has all metadata fields."""
        event = SESSION_RESULT_SUCCESS

        assert event["type"] == "result"
        assert event["subtype"] in ("success", "error")
        assert "total_cost_usd" in event
        assert "is_error" in event
        assert "duration_ms" in event
        assert "num_turns" in event
        assert "session_id" in event
        assert "result" in event


@pytest.mark.asyncio
class TestClaudeProcessIntegration:
    """Test ClaudeProcess with mocked subprocess."""

    async def test_receives_system_init(self, temp_dir):
        """Test ClaudeProcess receives system/init event."""
        mock_proc = MockClaudeSubprocess(events=[SYSTEM_INIT_EVENT])

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            process = ClaudeProcess(cwd=temp_dir)

            # Start process (now waits for and returns session_id)
            emit_task = asyncio.create_task(mock_proc.emit_events())
            session_id = await process.start()
            assert session_id == "test-session-abc123"

            # SystemInit event should still be in queue
            events_received = []
            async for event in process.events():
                events_received.append(event)
                if isinstance(event, SystemInit):
                    break

            await emit_task

            assert len(events_received) == 1
            assert isinstance(events_received[0], SystemInit)
            assert events_received[0].session_id == "test-session-abc123"

    async def test_receives_assistant_text(self, temp_dir):
        """Test ClaudeProcess parses assistant text message."""
        mock_proc = MockClaudeSubprocess(events=[
            SYSTEM_INIT_EVENT,
            ASSISTANT_TEXT_MESSAGE,
        ])

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            process = ClaudeProcess(cwd=temp_dir)
            emit_task = asyncio.create_task(mock_proc.emit_events())

            await process.start()

            events_received = []
            async for event in process.events():
                events_received.append(event)
                if isinstance(event, AssistantMessage):
                    break

            await emit_task

            # Should have init + assistant message
            assert len(events_received) == 2
            msg = events_received[1]
            assert isinstance(msg, AssistantMessage)
            assert extract_text(msg) == "I'll help you with that task."

    async def test_receives_tool_use(self, temp_dir):
        """Test ClaudeProcess parses tool_use message."""
        mock_proc = MockClaudeSubprocess(events=[
            SYSTEM_INIT_EVENT,
            ASSISTANT_TOOL_USE_MESSAGE,
        ])

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            process = ClaudeProcess(cwd=temp_dir)
            emit_task = asyncio.create_task(mock_proc.emit_events())

            await process.start()

            events_received = []
            async for event in process.events():
                events_received.append(event)
                if isinstance(event, AssistantMessage):
                    break

            await emit_task

            msg = events_received[1]
            tools = extract_tool_uses(msg)
            assert len(tools) == 1
            assert tools[0].name == "Read"


@pytest.mark.asyncio
class TestClaudeProcessTerminate:
    """Test ClaudeProcess termination behavior."""

    async def test_terminate_not_started(self, temp_dir):
        """Test terminate when process not started returns True."""
        process = ClaudeProcess(cwd=temp_dir)
        result = await process.terminate()
        assert result is True

    async def test_terminate_graceful_exit(self, temp_dir):
        """Test terminate with graceful exit."""
        mock_proc = MockClaudeSubprocess(events=[SYSTEM_INIT_EVENT])
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            process = ClaudeProcess(cwd=temp_dir)
            emit_task = asyncio.create_task(mock_proc.emit_events())
            await process.start()
            await emit_task

            result = await process.terminate(timeout=0.5)
            assert result is True
            assert process.process is None

    async def test_terminate_timeout_kill(self, temp_dir):
        """Test terminate kills process after timeout."""
        mock_proc = MockClaudeSubprocess(events=[SYSTEM_INIT_EVENT])

        async def slow_wait():
            # Only return after kill() sets returncode to -9
            while mock_proc.returncode != -9:
                await asyncio.sleep(0.05)
            return mock_proc.returncode

        mock_proc.wait = slow_wait

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            process = ClaudeProcess(cwd=temp_dir)
            emit_task = asyncio.create_task(mock_proc.emit_events())
            await process.start()
            await emit_task

            # Reset returncode after emit_events set it to 0, before terminate
            mock_proc.returncode = None
            result = await process.terminate(timeout=0.1)
            assert result is True
            assert mock_proc.returncode == -9

    async def test_terminate_sends_sigterm(self, temp_dir):
        """Test terminate sends SIGTERM after closing stdin."""
        mock_proc = MockClaudeSubprocess(events=[SYSTEM_INIT_EVENT])
        terminate_called = []

        def mock_terminate():
            terminate_called.append(True)
            mock_proc.returncode = 0

        mock_proc.terminate = mock_terminate

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            process = ClaudeProcess(cwd=temp_dir)
            emit_task = asyncio.create_task(mock_proc.emit_events())
            await process.start()
            await emit_task

            result = await process.terminate(timeout=0.5)
            assert result is True
            assert len(terminate_called) == 1  # terminate() was called

    async def test_start_already_started(self, temp_dir):
        """Test start when already started raises RuntimeError."""
        mock_proc = MockClaudeSubprocess(events=[SYSTEM_INIT_EVENT])

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            process = ClaudeProcess(cwd=temp_dir)
            emit_task = asyncio.create_task(mock_proc.emit_events())
            session_id = await process.start()
            await emit_task
            assert session_id == "test-session-abc123"

            with pytest.raises(RuntimeError, match="already started"):
                await process.start()

    async def test_start_with_resume_session_id(self, temp_dir):
        """Test start with resume_session_id adds --resume flag."""
        mock_proc = MockClaudeSubprocess(events=[SYSTEM_INIT_EVENT])
        captured_cmd = []

        async def capture_exec(*args, **kwargs):
            captured_cmd.extend(args)
            return mock_proc

        with patch("asyncio.create_subprocess_exec", side_effect=capture_exec):
            process = ClaudeProcess(cwd=temp_dir, resume_session_id="old-session-123")
            emit_task = asyncio.create_task(mock_proc.emit_events())
            await process.start()
            await emit_task

            assert "--resume" in captured_cmd
            assert "old-session-123" in captured_cmd

    async def test_start_with_allowed_tools(self, temp_dir):
        """Test start with allowed_tools adds --allowedTools flag."""
        mock_proc = MockClaudeSubprocess(events=[SYSTEM_INIT_EVENT])
        captured_cmd = []

        async def capture_exec(*args, **kwargs):
            captured_cmd.extend(args)
            return mock_proc

        with patch("asyncio.create_subprocess_exec", side_effect=capture_exec):
            process = ClaudeProcess(cwd=temp_dir, allowed_tools=["Read", "Grep"])
            emit_task = asyncio.create_task(mock_proc.emit_events())
            await process.start()
            await emit_task

            assert "--allowedTools" in captured_cmd
            assert "Read,Grep" in captured_cmd

    async def test_start_exception_raises(self, temp_dir):
        """Test start raises RuntimeError on exception."""
        async def raise_error(*args, **kwargs):
            raise OSError("Cannot start process")

        with patch("asyncio.create_subprocess_exec", side_effect=raise_error):
            process = ClaudeProcess(cwd=temp_dir)
            with pytest.raises(RuntimeError, match="Failed to start Claude"):
                await process.start()

    async def test_send_message_not_running(self, temp_dir):
        """Test send_message when process not running returns False."""
        process = ClaudeProcess(cwd=temp_dir)
        result = await process.send_message("Hello")
        assert result is False

    async def test_is_running_property(self, temp_dir):
        """Test is_running property."""
        mock_proc = MockClaudeSubprocess(events=[SYSTEM_INIT_EVENT])

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            process = ClaudeProcess(cwd=temp_dir)
            assert process.is_running is False

            # Manually control returncode for test
            mock_proc.returncode = None  # Set None after start to simulate running
            emit_task = asyncio.create_task(mock_proc.emit_events())
            await process.start()

            await emit_task
            # After emit_events, returncode is 0
            assert process.is_running is False

    async def test_pid_property(self, temp_dir):
        """Test pid property."""
        mock_proc = MockClaudeSubprocess(events=[SYSTEM_INIT_EVENT])

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            process = ClaudeProcess(cwd=temp_dir)
            assert process.pid is None

            emit_task = asyncio.create_task(mock_proc.emit_events())
            await process.start()
            await emit_task
            assert process.pid == 12345

    async def test_wait_method(self, temp_dir):
        """Test wait method."""
        mock_proc = MockClaudeSubprocess(events=[SYSTEM_INIT_EVENT])

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            process = ClaudeProcess(cwd=temp_dir)
            result = await process.wait()
            assert result is None

            emit_task = asyncio.create_task(mock_proc.emit_events())
            await process.start()
            await emit_task
            result = await process.wait()
            assert result == 0


@pytest.mark.asyncio
class TestClaudeProcessErrorPaths:
    """Test ClaudeProcess error handling paths."""

    async def test_read_stdout_no_process(self, temp_dir):
        """Test _read_stdout returns early when no process."""
        process = ClaudeProcess(cwd=temp_dir)
        assert process.process is None
        await process._read_stdout()

    async def test_read_stdout_empty_line(self, temp_dir):
        """Test _read_stdout skips empty lines."""
        mock_proc = MockClaudeSubprocess(events=[])

        async def emit_with_empty_line():
            await mock_proc._stdout_queue.put(b"\n")  # Empty line
            await mock_proc._stdout_queue.put(b"   \n")  # Whitespace-only line
            line = json.dumps(SYSTEM_INIT_EVENT) + "\n"
            await mock_proc._stdout_queue.put(line.encode('utf-8'))
            await asyncio.sleep(0.05)
            mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            process = ClaudeProcess(cwd=temp_dir)
            emit_task = asyncio.create_task(emit_with_empty_line())
            await process.start()

            events = []
            async for event in process.events():
                events.append(event)
                if isinstance(event, SystemInit):
                    break

            await emit_task
            assert len(events) == 1
            assert isinstance(events[0], SystemInit)

    async def test_read_stdout_json_decode_error(self, temp_dir):
        """Test _read_stdout handles JSON decode error."""
        mock_proc = MockClaudeSubprocess(events=[])

        async def emit_invalid_json():
            await mock_proc._stdout_queue.put(b"not valid json\n")
            await mock_proc._stdout_queue.put(b"{invalid: json}\n")
            line = json.dumps(SYSTEM_INIT_EVENT) + "\n"
            await mock_proc._stdout_queue.put(line.encode('utf-8'))
            await asyncio.sleep(0.05)
            mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            process = ClaudeProcess(cwd=temp_dir)
            emit_task = asyncio.create_task(emit_invalid_json())
            await process.start()

            events = []
            async for event in process.events():
                events.append(event)
                if isinstance(event, SystemInit):
                    break

            await emit_task
            assert len(events) == 1
            assert isinstance(events[0], SystemInit)

    async def test_read_stdout_exception(self, temp_dir):
        """Test _read_stdout exception before init causes start() to timeout."""
        mock_proc = MockClaudeSubprocess(events=[])

        call_count = 0
        async def failing_readline():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Simulated read error")
            return b""

        mock_proc.stdout.readline = failing_readline

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            process = ClaudeProcess(cwd=temp_dir)
            # start() waits for session_id, which won't arrive due to error
            # We use a short outer timeout to avoid waiting 30s for internal timeout
            with pytest.raises((asyncio.TimeoutError, RuntimeError)):
                await asyncio.wait_for(process.start(), timeout=1.0)

    async def test_read_stderr_no_process(self, temp_dir):
        """Test _read_stderr returns early when no process."""
        process = ClaudeProcess(cwd=temp_dir)
        assert process.process is None
        await process._read_stderr()

    async def test_read_stderr_logs_output(self, temp_dir):
        """Test _read_stderr logs stderr content."""
        mock_proc = MockClaudeSubprocess(events=[SYSTEM_INIT_EVENT])

        async def emit_stderr():
            await mock_proc._stderr_queue.put(b"Warning: something happened\n")
            await mock_proc._stderr_queue.put(b"Another warning\n")
            await asyncio.sleep(0.05)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("claude_process.log") as mock_log:
            process = ClaudeProcess(cwd=temp_dir)
            emit_task = asyncio.create_task(emit_stderr())
            emit_events_task = asyncio.create_task(mock_proc.emit_events())
            await process.start()

            await asyncio.sleep(0.1)
            await emit_task
            await emit_events_task

            stderr_calls = [c for c in mock_log.call_args_list
                          if "stderr:" in str(c)]
            assert len(stderr_calls) >= 1

    async def test_read_stderr_exception(self, temp_dir):
        """Test _read_stderr handles exception."""
        mock_proc = MockClaudeSubprocess(events=[SYSTEM_INIT_EVENT])

        async def failing_stderr_readline():
            raise RuntimeError("Simulated stderr read error")

        mock_proc._stderr_readline = failing_stderr_readline

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("claude_process.log") as mock_log:
            process = ClaudeProcess(cwd=temp_dir)
            emit_task = asyncio.create_task(mock_proc.emit_events())
            await process.start()

            await asyncio.sleep(0.1)
            await emit_task

            error_calls = [c for c in mock_log.call_args_list
                         if "Error reading stderr" in str(c)]
            assert len(error_calls) >= 1

    async def test_process_event_user_message(self, temp_dir):
        """Test _process_event handles user message."""
        mock_proc = MockClaudeSubprocess(events=[
            SYSTEM_INIT_EVENT,
            USER_MESSAGE_ECHO,
        ])

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            process = ClaudeProcess(cwd=temp_dir)
            emit_task = asyncio.create_task(mock_proc.emit_events())
            await process.start()

            events = []
            async for event in process.events():
                events.append(event)
                if isinstance(event, UserMessage):
                    break

            await emit_task

            assert len(events) == 2
            assert isinstance(events[0], SystemInit)
            assert isinstance(events[1], UserMessage)
            assert events[1].content[0]["text"] == "Hello Claude!"

    async def test_send_message_exception(self, temp_dir):
        """Test send_message handles exception."""
        mock_proc = MockClaudeSubprocess(events=[SYSTEM_INIT_EVENT])

        def failing_write(data):
            raise BrokenPipeError("Pipe is broken")

        mock_proc._stdin_write = failing_write

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            process = ClaudeProcess(cwd=temp_dir)
            emit_task = asyncio.create_task(mock_proc.emit_events())
            await process.start()
            await emit_task

            result = await process.send_message("This should fail")
            assert result is False

    async def test_events_break_on_none(self, temp_dir):
        """Test events() generator breaks on None."""
        mock_proc = MockClaudeSubprocess(events=[SYSTEM_INIT_EVENT])

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            process = ClaudeProcess(cwd=temp_dir)
            emit_task = asyncio.create_task(mock_proc.emit_events())
            await process.start()

            events = []
            async for event in process.events():
                events.append(event)

            await emit_task

            assert len(events) == 1
            assert isinstance(events[0], SystemInit)

    async def test_terminate_cancelled_error_stdout(self, temp_dir):
        """Test terminate handles CancelledError on stdout task."""
        mock_proc = MockClaudeSubprocess(events=[SYSTEM_INIT_EVENT])

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            process = ClaudeProcess(cwd=temp_dir)
            emit_task = asyncio.create_task(mock_proc.emit_events())
            await process.start()
            await emit_task

            assert process._stdout_task is not None

            result = await process.terminate(timeout=0.5)
            assert result is True

    async def test_terminate_cancelled_error_stderr(self, temp_dir):
        """Test terminate handles CancelledError on stderr task."""
        mock_proc = MockClaudeSubprocess(events=[SYSTEM_INIT_EVENT])

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            process = ClaudeProcess(cwd=temp_dir)
            emit_task = asyncio.create_task(mock_proc.emit_events())
            await process.start()
            await emit_task

            assert process._stderr_task is not None

            result = await process.terminate(timeout=0.5)
            assert result is True

    async def test_terminate_exception_returns_false(self, temp_dir):
        """Test terminate returns False on exception."""
        mock_proc = MockClaudeSubprocess(events=[SYSTEM_INIT_EVENT])

        async def failing_wait():
            raise RuntimeError("Process wait failed")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            process = ClaudeProcess(cwd=temp_dir)
            emit_task = asyncio.create_task(mock_proc.emit_events())
            await process.start()
            await emit_task

            mock_proc.wait = failing_wait

            result = await process.terminate(timeout=0.5)
            assert result is False


class TestPdeathsig:
    """Test _set_pdeathsig helper function."""

    def test_pdeathsig_non_linux(self):
        """Test _set_pdeathsig does nothing on non-Linux."""
        with patch.object(sys, 'platform', 'darwin'):
            # Should not raise, just return silently
            _set_pdeathsig()

    def test_pdeathsig_linux_success(self):
        """Test _set_pdeathsig calls prctl on Linux."""
        with patch.object(sys, 'platform', 'linux'):
            mock_libc = MagicMock()
            mock_libc.prctl = MagicMock(return_value=0)

            with patch('ctypes.CDLL', return_value=mock_libc):
                _set_pdeathsig()
                mock_libc.prctl.assert_called_once_with(1, 15, 0, 0, 0)  # PR_SET_PDEATHSIG=1, SIGTERM=15

    def test_pdeathsig_linux_oserror(self):
        """Test _set_pdeathsig handles OSError gracefully."""
        with patch.object(sys, 'platform', 'linux'):
            with patch('ctypes.CDLL', side_effect=OSError("No such file")):
                # Should not raise
                _set_pdeathsig()

    def test_pdeathsig_linux_attribute_error(self):
        """Test _set_pdeathsig handles AttributeError gracefully."""
        with patch.object(sys, 'platform', 'linux'):
            mock_libc = MagicMock()
            del mock_libc.prctl  # Make prctl access raise AttributeError

            with patch('ctypes.CDLL', return_value=mock_libc):
                # Should not raise
                _set_pdeathsig()
