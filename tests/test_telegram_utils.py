"""Tests for telegram_utils.py - Telegram formatting and utilities."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestTelegramUtils:
    """Test telegram_utils functions."""

    def test_escape_markdown_v2(self):
        """Test escape_markdown_v2 function."""
        from telegram_utils import escape_markdown_v2

        text = "Hello *world* with [brackets]"
        escaped = escape_markdown_v2(text)

        assert "\\*" in escaped
        assert "\\[" in escaped
        assert "\\]" in escaped

    def test_format_tool_permission_bash(self):
        """Test format_tool_permission for Bash tool."""
        from telegram_utils import format_tool_permission

        result = format_tool_permission("Bash", {"command": "ls -la", "description": "List files"})
        assert "ls -la" in result
        assert "List files" in result

    def test_format_tool_permission_edit(self):
        """Test format_tool_permission for Edit tool."""
        from telegram_utils import format_tool_permission

        result = format_tool_permission(
            "Edit",
            {"file_path": "/home/user/test.py", "old_string": "old", "new_string": "new"}
        )
        assert "test.py" in result
        assert "diff" in result

    def test_format_tool_permission_write(self):
        """Test format_tool_permission for Write tool."""
        from telegram_utils import format_tool_permission

        result = format_tool_permission(
            "Write",
            {"file_path": "/home/user/new.py", "content": "print('hello')"}
        )
        assert "new.py" in result
        assert "print" in result

    def test_format_tool_permission_read(self):
        """Test format_tool_permission for Read tool."""
        from telegram_utils import format_tool_permission

        result = format_tool_permission("Read", {"file_path": "/home/user/file.txt"})
        assert "file.txt" in result

    def test_format_tool_permission_ask_user_question(self):
        """Test format_tool_permission for AskUserQuestion tool."""
        from telegram_utils import format_tool_permission

        result = format_tool_permission(
            "AskUserQuestion",
            {"questions": [{"question": "Which option?", "options": [{"label": "A"}, {"label": "B"}]}]}
        )
        assert "Which option?" in result
        assert "A" in result
        assert "B" in result

    def test_format_tool_permission_unknown(self):
        """Test format_tool_permission for unknown tool."""
        from telegram_utils import format_tool_permission

        result = format_tool_permission("UnknownTool", {"arg1": "value1"})
        assert "UnknownTool" in result
        assert "arg1" in result

    def test_strip_home(self):
        """Test strip_home function."""
        from telegram_utils import strip_home

        home = str(Path.home())
        path = f"{home}/test/file.txt"
        result = strip_home(path)
        assert result == "test/file.txt"

        result = strip_home("/tmp/file.txt")
        assert result == "/tmp/file.txt"

    def test_shell_quote(self):
        """Test shell_quote escapes strings for shell use."""
        from telegram_utils import shell_quote

        assert shell_quote("hello") == "hello"
        assert shell_quote("hello world") == "'hello world'"
        # shlex.quote escapes single quotes in a specific way
        result = shell_quote("it's")
        assert "it" in result and "s" in result

    def test_log(self, capsys):
        """Test log prints with timestamp."""
        from telegram_utils import log

        log("test message")
        captured = capsys.readouterr()
        assert "test message" in captured.out
        assert "[" in captured.out  # timestamp bracket


class TestState:
    """Test State class for persistent storage."""

    def test_state_init_creates_empty_on_missing_file(self):
        """State initializes to empty dict when file doesn't exist."""
        from telegram_utils import State, STATE_FILE

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("telegram_utils.STATE_FILE", Path(tmpdir) / "nonexistent.json"):
                state = State()
                assert state.data == {}

    def test_state_init_reads_existing_file(self):
        """State loads data from existing file."""
        from telegram_utils import State

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text('{"123": {"field": "value"}}')
            with patch("telegram_utils.STATE_FILE", state_file):
                state = State()
                assert state.data == {"123": {"field": "value"}}

    def test_state_init_handles_invalid_json(self):
        """State returns empty dict on invalid JSON."""
        from telegram_utils import State

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text('invalid json{')
            with patch("telegram_utils.STATE_FILE", state_file):
                state = State()
                assert state.data == {}

    def test_state_get(self):
        """Test State.get retrieves entry by ID."""
        from telegram_utils import State

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text('{"123": {"key": "val"}}')
            with patch("telegram_utils.STATE_FILE", state_file):
                state = State()
                assert state.get("123") == {"key": "val"}
                assert state.get("999") is None

    def test_state_contains(self):
        """Test State.__contains__ checks membership."""
        from telegram_utils import State

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text('{"123": {}}')
            with patch("telegram_utils.STATE_FILE", state_file):
                state = State()
                assert "123" in state
                assert "999" not in state

    def test_state_iter(self):
        """Test State.__iter__ iterates over keys."""
        from telegram_utils import State

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text('{"a": {}, "b": {}}')
            with patch("telegram_utils.STATE_FILE", state_file):
                state = State()
                assert set(state) == {"a", "b"}

    def test_state_items(self):
        """Test State.items returns key-value pairs."""
        from telegram_utils import State

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text('{"x": {"val": 1}}')
            with patch("telegram_utils.STATE_FILE", state_file):
                state = State()
                items = list(state.items())
                assert items == [("x", {"val": 1})]

    def test_state_add_and_flush(self):
        """Test State.add adds entry and flushes to disk."""
        from telegram_utils import State

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text('{}')
            with patch("telegram_utils.STATE_FILE", state_file):
                state = State()
                state.add("msg1", {"tool": "Bash"})
                assert state.get("msg1") == {"tool": "Bash"}
                # Verify file was written
                assert '"msg1"' in state_file.read_text()

    def test_state_update(self):
        """Test State.update modifies existing entry."""
        from telegram_utils import State

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text('{"msg1": {"a": 1}}')
            with patch("telegram_utils.STATE_FILE", state_file):
                state = State()
                state.update("msg1", b=2)
                assert state.get("msg1") == {"a": 1, "b": 2}

    def test_state_update_nonexistent(self):
        """Test State.update does nothing for nonexistent entry."""
        from telegram_utils import State

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text('{}')
            with patch("telegram_utils.STATE_FILE", state_file):
                state = State()
                state.update("nonexistent", x=1)  # Should not raise
                assert state.get("nonexistent") is None

    def test_state_remove(self):
        """Test State.remove deletes entry."""
        from telegram_utils import State

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text('{"msg1": {"a": 1}}')
            with patch("telegram_utils.STATE_FILE", state_file):
                state = State()
                state.remove("msg1")
                assert state.get("msg1") is None

    def test_state_remove_nonexistent(self):
        """Test State.remove does nothing for nonexistent entry."""
        from telegram_utils import State

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text('{}')
            with patch("telegram_utils.STATE_FILE", state_file):
                state = State()
                state.remove("nonexistent")  # Should not raise

    def test_state_data_property(self):
        """Test State.data returns raw data dict."""
        from telegram_utils import State

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text('{"k": {"v": 1}}')
            with patch("telegram_utils.STATE_FILE", state_file):
                state = State()
                assert state.data == {"k": {"v": 1}}


class TestTelegramAPI:
    """Test Telegram API functions."""

    def test_send_telegram_success(self):
        """Test send_telegram returns response on success."""
        from telegram_utils import send_telegram

        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"ok": True, "result": {"message_id": 123}}

        with patch("requests.post", return_value=mock_resp) as mock_post:
            result = send_telegram("token", "chat123", "Hello")
            assert result == {"ok": True, "result": {"message_id": 123}}
            mock_post.assert_called_once()

    def test_send_telegram_with_reply_markup(self):
        """Test send_telegram includes reply_markup in payload."""
        from telegram_utils import send_telegram

        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"ok": True}

        with patch("requests.post", return_value=mock_resp) as mock_post:
            markup = {"inline_keyboard": [[{"text": "OK"}]]}
            send_telegram("token", "chat", "msg", reply_markup=markup)
            call_args = mock_post.call_args
            assert call_args[1]["json"]["reply_markup"] == markup

    def test_send_telegram_markdown_parse_error_retry(self):
        """Test send_telegram retries without parse_mode on markdown error."""
        from telegram_utils import send_telegram

        fail_resp = MagicMock()
        fail_resp.status_code = 400
        fail_resp.text = "can't parse entities"
        fail_resp.ok = False

        success_resp = MagicMock()
        success_resp.ok = True
        success_resp.json.return_value = {"ok": True}

        with patch("requests.post", side_effect=[fail_resp, success_resp]) as mock_post:
            result = send_telegram("token", "chat", "*bad markdown")
            assert result == {"ok": True}
            assert mock_post.call_count == 2
            # Second call should not have parse_mode
            second_call = mock_post.call_args_list[1]
            assert "parse_mode" not in second_call[1]["json"]

    def test_send_telegram_failure(self):
        """Test send_telegram returns None on failure."""
        from telegram_utils import send_telegram

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.ok = False
        mock_resp.text = "Server error"

        with patch("requests.post", return_value=mock_resp):
            result = send_telegram("token", "chat", "msg")
            assert result is None

    def test_answer_callback(self):
        """Test answer_callback sends correct request."""
        from telegram_utils import answer_callback

        with patch("requests.post") as mock_post:
            answer_callback("token", "callback123", "Done!")
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert "answerCallbackQuery" in call_args[0][0]
            assert call_args[1]["json"]["callback_query_id"] == "callback123"
            assert call_args[1]["json"]["text"] == "Done!"

    def test_send_reply(self):
        """Test send_reply sends reply to message."""
        from telegram_utils import send_reply

        with patch("requests.post") as mock_post:
            send_reply("token", "chat123", 456, "Reply text", parse_mode="Markdown")
            call_args = mock_post.call_args
            payload = call_args[1]["json"]
            assert payload["chat_id"] == "chat123"
            assert payload["reply_to_message_id"] == 456
            assert payload["text"] == "Reply text"
            assert payload["parse_mode"] == "Markdown"

    def test_send_reply_no_parse_mode(self):
        """Test send_reply without parse_mode."""
        from telegram_utils import send_reply

        with patch("requests.post") as mock_post:
            send_reply("token", "chat", 123, "text")
            payload = mock_post.call_args[1]["json"]
            assert "parse_mode" not in payload

    def test_update_message_buttons(self):
        """Test update_message_buttons updates reply markup."""
        from telegram_utils import update_message_buttons

        with patch("requests.post") as mock_post:
            update_message_buttons("token", "chat", 789, "Approved")
            call_args = mock_post.call_args
            assert "editMessageReplyMarkup" in call_args[0][0]
            payload = call_args[1]["json"]
            assert payload["message_id"] == 789
            assert payload["reply_markup"]["inline_keyboard"][0][0]["text"] == "Approved"

    def test_delete_message_success(self):
        """Test delete_message returns True on success."""
        from telegram_utils import delete_message

        mock_resp = MagicMock()
        mock_resp.ok = True

        with patch("requests.post", return_value=mock_resp):
            result = delete_message("token", "chat", 123)
            assert result is True

    def test_delete_message_failure(self):
        """Test delete_message returns False on failure."""
        from telegram_utils import delete_message

        mock_resp = MagicMock()
        mock_resp.ok = False

        with patch("requests.post", return_value=mock_resp):
            result = delete_message("token", "chat", 123)
            assert result is False

    def test_send_chat_action(self):
        """Test send_chat_action sends typing indicator."""
        from telegram_utils import send_chat_action

        with patch("requests.post") as mock_post:
            send_chat_action("token", "chat123")
            call_args = mock_post.call_args
            assert "sendChatAction" in call_args[0][0]
            assert call_args[1]["json"]["action"] == "typing"

    def test_send_chat_action_with_topic(self):
        """Test send_chat_action includes topic_id when provided."""
        from telegram_utils import send_chat_action

        with patch("requests.post") as mock_post:
            send_chat_action("token", "chat", topic_id=42)
            payload = mock_post.call_args[1]["json"]
            assert payload["message_thread_id"] == 42

    def test_register_bot_commands(self):
        """Test register_bot_commands sends commands to Telegram."""
        from telegram_utils import register_bot_commands

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_resp) as mock_post:
            register_bot_commands("token")
            call_args = mock_post.call_args
            assert "setMyCommands" in call_args[0][0]
            commands = call_args[1]["json"]["commands"]
            # Verify some expected commands
            cmd_names = [c["command"] for c in commands]
            assert "dump" in cmd_names
            assert "spawn" in cmd_names
            assert "help" in cmd_names


class TestForumAPI:
    """Test Telegram Forum API functions."""

    def test_get_chat_success(self):
        """Test get_chat returns chat info on success."""
        from telegram_utils import get_chat

        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"ok": True, "result": {"id": -123, "is_forum": True}}

        with patch("requests.post", return_value=mock_resp):
            result = get_chat("token", "chat123")
            assert result == {"id": -123, "is_forum": True}

    def test_get_chat_failure(self):
        """Test get_chat returns None on failure."""
        from telegram_utils import get_chat

        mock_resp = MagicMock()
        mock_resp.ok = False

        with patch("requests.post", return_value=mock_resp):
            result = get_chat("token", "bad_chat")
            assert result is None

    def test_is_forum_enabled_true(self):
        """Test is_forum_enabled returns True for forum chats."""
        from telegram_utils import is_forum_enabled

        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"ok": True, "result": {"is_forum": True}}

        with patch("requests.post", return_value=mock_resp):
            assert is_forum_enabled("token", "chat") is True

    def test_is_forum_enabled_false(self):
        """Test is_forum_enabled returns False for non-forum chats."""
        from telegram_utils import is_forum_enabled

        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"ok": True, "result": {"is_forum": False}}

        with patch("requests.post", return_value=mock_resp):
            assert is_forum_enabled("token", "chat") is False

    def test_is_forum_enabled_error(self):
        """Test is_forum_enabled returns False on error."""
        from telegram_utils import is_forum_enabled

        mock_resp = MagicMock()
        mock_resp.ok = False

        with patch("requests.post", return_value=mock_resp):
            assert is_forum_enabled("token", "bad") is False

    def test_create_forum_topic_success(self):
        """Test create_forum_topic returns topic info on success."""
        from telegram_utils import create_forum_topic

        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"ok": True, "result": {"message_thread_id": 42, "name": "Test"}}

        with patch("requests.post", return_value=mock_resp) as mock_post:
            result = create_forum_topic("token", "chat", "Test Topic")
            assert result == {"message_thread_id": 42, "name": "Test"}
            payload = mock_post.call_args[1]["json"]
            assert payload["name"] == "Test Topic"

    def test_create_forum_topic_with_icon_color(self):
        """Test create_forum_topic includes icon_color when provided."""
        from telegram_utils import create_forum_topic

        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"ok": True, "result": {"message_thread_id": 1}}

        with patch("requests.post", return_value=mock_resp) as mock_post:
            create_forum_topic("token", "chat", "Topic", icon_color=0x6FB9F0)
            payload = mock_post.call_args[1]["json"]
            assert payload["icon_color"] == 0x6FB9F0

    def test_create_forum_topic_no_rights(self):
        """Test create_forum_topic raises NoTopicRightsError on permission error."""
        from telegram_utils import create_forum_topic, NoTopicRightsError

        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.text = "not enough rights to manage topics"

        with patch("requests.post", return_value=mock_resp):
            try:
                create_forum_topic("token", "chat", "Topic")
                assert False, "Should have raised NoTopicRightsError"
            except NoTopicRightsError:
                pass

    def test_create_forum_topic_other_error(self):
        """Test create_forum_topic raises TopicCreationError on other errors."""
        from telegram_utils import create_forum_topic, TopicCreationError, NoTopicRightsError

        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.text = "some other error"

        with patch("requests.post", return_value=mock_resp):
            try:
                create_forum_topic("token", "chat", "Topic")
                assert False, "Should have raised TopicCreationError"
            except NoTopicRightsError:
                assert False, "Should not be NoTopicRightsError"
            except TopicCreationError:
                pass

    def test_close_forum_topic_success(self):
        """Test close_forum_topic returns True on success."""
        from telegram_utils import close_forum_topic

        mock_resp = MagicMock()
        mock_resp.ok = True

        with patch("requests.post", return_value=mock_resp) as mock_post:
            result = close_forum_topic("token", "chat", 42)
            assert result is True
            assert "closeForumTopic" in mock_post.call_args[0][0]

    def test_close_forum_topic_failure(self):
        """Test close_forum_topic returns False on failure."""
        from telegram_utils import close_forum_topic

        mock_resp = MagicMock()
        mock_resp.ok = False

        with patch("requests.post", return_value=mock_resp):
            assert close_forum_topic("token", "chat", 42) is False

    def test_delete_forum_topic_success(self):
        """Test delete_forum_topic returns True on success."""
        from telegram_utils import delete_forum_topic

        mock_resp = MagicMock()
        mock_resp.ok = True

        with patch("requests.post", return_value=mock_resp) as mock_post:
            result = delete_forum_topic("token", "chat", 42)
            assert result is True
            assert "deleteForumTopic" in mock_post.call_args[0][0]

    def test_delete_forum_topic_failure(self):
        """Test delete_forum_topic returns False on failure."""
        from telegram_utils import delete_forum_topic

        mock_resp = MagicMock()
        mock_resp.ok = False

        with patch("requests.post", return_value=mock_resp):
            assert delete_forum_topic("token", "chat", 42) is False

    def test_reopen_forum_topic_success(self):
        """Test reopen_forum_topic returns True on success."""
        from telegram_utils import reopen_forum_topic

        mock_resp = MagicMock()
        mock_resp.ok = True

        with patch("requests.post", return_value=mock_resp) as mock_post:
            result = reopen_forum_topic("token", "chat", 42)
            assert result is True
            assert "reopenForumTopic" in mock_post.call_args[0][0]

    def test_reopen_forum_topic_failure(self):
        """Test reopen_forum_topic returns False on failure."""
        from telegram_utils import reopen_forum_topic

        mock_resp = MagicMock()
        mock_resp.ok = False

        with patch("requests.post", return_value=mock_resp):
            assert reopen_forum_topic("token", "chat", 42) is False

    def test_edit_forum_topic_success(self):
        """Test edit_forum_topic returns True on success."""
        from telegram_utils import edit_forum_topic

        mock_resp = MagicMock()
        mock_resp.ok = True

        with patch("requests.post", return_value=mock_resp) as mock_post:
            result = edit_forum_topic("token", "chat", 42, name="New Name")
            assert result is True
            assert "editForumTopic" in mock_post.call_args[0][0]
            payload = mock_post.call_args[1]["json"]
            assert payload["name"] == "New Name"

    def test_edit_forum_topic_no_name(self):
        """Test edit_forum_topic without name parameter."""
        from telegram_utils import edit_forum_topic

        mock_resp = MagicMock()
        mock_resp.ok = True

        with patch("requests.post", return_value=mock_resp) as mock_post:
            edit_forum_topic("token", "chat", 42)
            payload = mock_post.call_args[1]["json"]
            assert "name" not in payload

    def test_edit_forum_topic_failure(self):
        """Test edit_forum_topic returns False on failure."""
        from telegram_utils import edit_forum_topic

        mock_resp = MagicMock()
        mock_resp.ok = False

        with patch("requests.post", return_value=mock_resp):
            assert edit_forum_topic("token", "chat", 42, name="X") is False

    def test_send_to_topic_success(self):
        """Test send_to_topic returns response on success."""
        from telegram_utils import send_to_topic

        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True, "result": {"message_id": 123}}

        with patch("requests.post", return_value=mock_resp) as mock_post:
            result = send_to_topic("token", "chat", 42, "Hello")
            assert result == {"ok": True, "result": {"message_id": 123}}
            payload = mock_post.call_args[1]["json"]
            assert payload["message_thread_id"] == 42

    def test_send_to_topic_general_topic(self):
        """Test send_to_topic doesn't include thread_id for General topic (1)."""
        from telegram_utils import send_to_topic

        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True}

        with patch("requests.post", return_value=mock_resp) as mock_post:
            send_to_topic("token", "chat", 1, "Hello")
            payload = mock_post.call_args[1]["json"]
            assert "message_thread_id" not in payload

    def test_send_to_topic_with_reply_markup(self):
        """Test send_to_topic includes reply_markup when provided."""
        from telegram_utils import send_to_topic

        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True}

        markup = {"inline_keyboard": [[{"text": "OK"}]]}
        with patch("requests.post", return_value=mock_resp) as mock_post:
            send_to_topic("token", "chat", 42, "msg", reply_markup=markup)
            payload = mock_post.call_args[1]["json"]
            assert payload["reply_markup"] == markup

    def test_send_to_topic_markdown_parse_error_retry(self):
        """Test send_to_topic retries without parse_mode on markdown error."""
        from telegram_utils import send_to_topic

        fail_resp = MagicMock()
        fail_resp.status_code = 400
        fail_resp.text = "can't parse entities"
        fail_resp.ok = False

        success_resp = MagicMock()
        success_resp.ok = True
        success_resp.json.return_value = {"ok": True}

        with patch("requests.post", side_effect=[fail_resp, success_resp]) as mock_post:
            result = send_to_topic("token", "chat", 42, "*bad markdown*")
            assert result == {"ok": True}
            assert mock_post.call_count == 2

    def test_send_to_topic_failure(self):
        """Test send_to_topic returns None on failure."""
        from telegram_utils import send_to_topic

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.ok = False
        mock_resp.text = "Server error"

        with patch("requests.post", return_value=mock_resp):
            result = send_to_topic("token", "chat", 42, "msg")
            assert result is None

    def test_get_chat_administrators_success(self):
        """Test get_chat_administrators returns list on success."""
        from telegram_utils import get_chat_administrators

        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"ok": True, "result": [{"user": {"id": 123}, "status": "creator"}]}

        with patch("requests.post", return_value=mock_resp):
            result = get_chat_administrators("token", "chat")
            assert result == [{"user": {"id": 123}, "status": "creator"}]

    def test_get_chat_administrators_failure(self):
        """Test get_chat_administrators returns None on failure."""
        from telegram_utils import get_chat_administrators

        mock_resp = MagicMock()
        mock_resp.ok = False

        with patch("requests.post", return_value=mock_resp):
            result = get_chat_administrators("token", "bad_chat")
            assert result is None
