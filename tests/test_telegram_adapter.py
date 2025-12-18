"""Tests for telegram_adapter.py - TelegramAdapter messaging."""

import asyncio
import pytest
from unittest.mock import MagicMock, patch

import requests


class TestTelegramAdapterShutdown:
    """Test TelegramAdapter shutdown behavior."""

    def test_stop_sets_shutdown_flag(self):
        """Test stop() sets _shutdown flag."""
        with patch("telegram_adapter.get_config") as mock_config:
            mock_cfg = MagicMock()
            mock_cfg.get.return_value = 0
            mock_config.return_value = mock_cfg

            from telegram_adapter import TelegramAdapter
            adapter = TelegramAdapter("TOKEN", "CHAT", timeout=1)

            assert adapter._shutdown is False
            adapter.stop()
            assert adapter._shutdown is True

    def test_init_sets_shutdown_false(self):
        """Test __init__ initializes _shutdown to False."""
        with patch("telegram_adapter.get_config") as mock_config:
            mock_cfg = MagicMock()
            mock_cfg.get.return_value = 0
            mock_config.return_value = mock_cfg

            from telegram_adapter import TelegramAdapter
            adapter = TelegramAdapter("TOKEN", "CHAT", timeout=1)

            assert adapter._shutdown is False


class TestMockTelegramServer:
    """Test mock Telegram server behaves correctly."""

    def test_get_updates_returns_pending(self, mock_telegram_server):
        """Test getUpdates returns pending updates."""
        import requests

        mock_telegram_server.add_message_update("Hello", topic_id=123)

        resp = requests.post(
            f"{mock_telegram_server.base_url}/bot_TOKEN/getUpdates",
            json={"offset": 0, "timeout": 1},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert len(data["result"]) == 1
        assert data["result"][0]["message"]["text"] == "Hello"

    def test_send_message_tracks_sent(self, mock_telegram_server):
        """Test sendMessage tracks sent messages."""
        import requests

        resp = requests.post(
            f"{mock_telegram_server.base_url}/bot_TOKEN/sendMessage",
            json={
                "chat_id": -1001234567890,
                "text": "Test message",
                "message_thread_id": 456,
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "message_id" in data["result"]

        assert len(mock_telegram_server.sent_messages) == 1
        assert mock_telegram_server.sent_messages[0]["text"] == "Test message"

    def test_callback_handling(self, mock_telegram_server):
        """Test callback query handling."""
        import requests

        mock_telegram_server.add_callback_update("allow:toolu_123", msg_id=100)

        resp = requests.post(
            f"{mock_telegram_server.base_url}/bot_TOKEN/getUpdates",
            json={"offset": 0},
        )

        data = resp.json()
        assert len(data["result"]) == 1

        callback = data["result"][0]["callback_query"]
        assert callback["data"] == "allow:toolu_123"

        resp = requests.post(
            f"{mock_telegram_server.base_url}/bot_TOKEN/answerCallbackQuery",
            json={"callback_query_id": callback["id"], "text": "OK"},
        )

        assert resp.json()["ok"] is True
        assert len(mock_telegram_server.callback_answers) == 1


@pytest.mark.asyncio
class TestTelegramAdapterIntegration:
    """Test TelegramAdapter with mocked Telegram API."""

    async def test_sends_to_correct_topic(self, mock_telegram_server):
        """Test messages are sent to correct Telegram topic."""
        import requests

        with patch("telegram_adapter.get_registry") as mock_registry, \
             patch("telegram_adapter.get_config") as mock_config:

            mock_reg_instance = MagicMock()
            mock_reg_instance.get_task.return_value = {"topic_id": 789}
            mock_registry.return_value = mock_reg_instance

            mock_cfg_instance = MagicMock()
            mock_cfg_instance.general_topic_id = 1
            mock_cfg_instance.get.return_value = 0
            mock_config.return_value = mock_cfg_instance

            original_post = requests.post

            def patched_post(url, **kwargs):
                if "api.telegram.org" in url:
                    method = url.split("/")[-1]
                    return original_post(
                        f"{mock_telegram_server.base_url}/bot_TOKEN/{method}",
                        **kwargs,
                    )
                return original_post(url, **kwargs)

            with patch.object(requests, "post", patched_post):
                from telegram_adapter import TelegramAdapter

                adapter = TelegramAdapter(
                    bot_token="TOKEN",
                    chat_id="-1001234567890",
                    timeout=1,
                )

                msg_id = await adapter.send_message(
                    task_id="test_task",
                    content="Hello world",
                )

                assert msg_id != ""
                assert len(mock_telegram_server.sent_messages) == 1
                sent = mock_telegram_server.sent_messages[0]
                assert sent["message_thread_id"] == 789

    async def test_operator_uses_general_topic(self, mock_telegram_server):
        """Test operator messages go to general topic."""
        import requests

        with patch("telegram_adapter.get_registry") as mock_registry, \
             patch("telegram_adapter.get_config") as mock_config:

            mock_reg_instance = MagicMock()
            mock_registry.return_value = mock_reg_instance

            mock_cfg_instance = MagicMock()
            mock_cfg_instance.general_topic_id = 1  # General topic
            mock_cfg_instance.get.return_value = 0
            mock_config.return_value = mock_cfg_instance

            original_post = requests.post

            def patched_post(url, **kwargs):
                if "api.telegram.org" in url:
                    method = url.split("/")[-1]
                    return original_post(
                        f"{mock_telegram_server.base_url}/bot_TOKEN/{method}",
                        **kwargs,
                    )
                return original_post(url, **kwargs)

            with patch.object(requests, "post", patched_post):
                from telegram_adapter import TelegramAdapter

                adapter = TelegramAdapter(
                    bot_token="TOKEN",
                    chat_id="-1001234567890",
                    timeout=1,
                )

                await adapter.send_message(
                    task_id="operator",
                    content="Operator message",
                )

                sent = mock_telegram_server.sent_messages[0]
                assert "message_thread_id" not in sent or sent.get("message_thread_id") == 1


@pytest.mark.asyncio
class TestTelegramAdapterAdvanced:
    """Additional TelegramAdapter tests."""

    async def test_get_topic_id_fallback_to_numeric(self, mock_telegram_server):
        """Test _get_topic_id handles numeric task_id."""
        with patch("telegram_adapter.get_registry") as mock_registry, \
             patch("telegram_adapter.get_config") as mock_config:

            mock_reg_instance = MagicMock()
            mock_reg_instance.get_task.return_value = None
            mock_reg_instance.find_task_by_topic.return_value = ("numeric_task", {})
            mock_registry.return_value = mock_reg_instance

            mock_cfg_instance = MagicMock()
            mock_cfg_instance.general_topic_id = 1
            mock_cfg_instance.get.return_value = 0
            mock_config.return_value = mock_cfg_instance

            from telegram_adapter import TelegramAdapter

            adapter = TelegramAdapter("TOKEN", "-1001234567890", timeout=1)
            topic_id = adapter._get_topic_id("123")
            assert topic_id == 123

    async def test_get_task_id_from_topic_fallback(self, mock_telegram_server):
        """Test _get_task_id_from_topic fallback to string."""
        with patch("telegram_adapter.get_registry") as mock_registry, \
             patch("telegram_adapter.get_config") as mock_config:

            mock_reg_instance = MagicMock()
            mock_reg_instance.find_task_by_topic.return_value = None
            mock_registry.return_value = mock_reg_instance

            mock_cfg_instance = MagicMock()
            mock_cfg_instance.general_topic_id = 1
            mock_config.return_value = mock_cfg_instance

            from telegram_adapter import TelegramAdapter

            adapter = TelegramAdapter("TOKEN", "-1001234567890", timeout=1)
            task_id = adapter._get_task_id_from_topic(999)
            assert task_id == "999"

    async def test_delete_message(self, mock_telegram_server):
        """Test delete_message."""
        import requests

        with patch("telegram_adapter.get_registry") as mock_registry, \
             patch("telegram_adapter.get_config") as mock_config:

            mock_reg_instance = MagicMock()
            mock_registry.return_value = mock_reg_instance

            mock_cfg_instance = MagicMock()
            mock_cfg_instance.general_topic_id = 1
            mock_cfg_instance.get.return_value = 0
            mock_config.return_value = mock_cfg_instance

            original_post = requests.post

            def patched_post(url, **kwargs):
                if "api.telegram.org" in url:
                    method = url.split("/")[-1]
                    return original_post(
                        f"{mock_telegram_server.base_url}/bot_TOKEN/{method}",
                        **kwargs,
                    )
                return original_post(url, **kwargs)

            with patch.object(requests, "post", patched_post):
                from telegram_adapter import TelegramAdapter

                adapter = TelegramAdapter("TOKEN", "-1001234567890", timeout=1)
                await adapter.delete_message("operator", "456")
                assert 456 in mock_telegram_server.deleted_messages

    async def test_get_topic_id_value_error(self, mock_telegram_server):
        """Test _get_topic_id returns None for non-numeric non-existent task_id."""
        with patch("telegram_adapter.get_registry") as mock_registry, \
             patch("telegram_adapter.get_config") as mock_config:

            mock_reg_instance = MagicMock()
            mock_reg_instance.get_task.return_value = None
            mock_reg_instance.find_task_by_topic.return_value = None
            mock_registry.return_value = mock_reg_instance

            mock_cfg_instance = MagicMock()
            mock_cfg_instance.general_topic_id = 1
            mock_cfg_instance.get.return_value = 0
            mock_config.return_value = mock_cfg_instance

            from telegram_adapter import TelegramAdapter

            adapter = TelegramAdapter("TOKEN", "-1001234567890", timeout=1)
            # Non-numeric task_id that doesn't exist should return None
            topic_id = adapter._get_topic_id("nonexistent_task")
            assert topic_id is None

    async def test_get_topic_id_numeric_not_found(self, mock_telegram_server):
        """Test _get_topic_id returns None for numeric task_id not found in registry."""
        with patch("telegram_adapter.get_registry") as mock_registry, \
             patch("telegram_adapter.get_config") as mock_config:

            mock_reg_instance = MagicMock()
            mock_reg_instance.get_task.return_value = None
            mock_reg_instance.find_task_by_topic.return_value = None  # Not found
            mock_registry.return_value = mock_reg_instance

            mock_cfg_instance = MagicMock()
            mock_cfg_instance.general_topic_id = 1
            mock_cfg_instance.get.return_value = 0
            mock_config.return_value = mock_cfg_instance

            from telegram_adapter import TelegramAdapter

            adapter = TelegramAdapter("TOKEN", "-1001234567890", timeout=1)
            topic_id = adapter._get_topic_id("999")
            assert topic_id is None

    async def test_get_task_id_from_topic_registry_lookup(self, mock_telegram_server):
        """Test _get_task_id_from_topic finds task in registry."""
        with patch("telegram_adapter.get_registry") as mock_registry, \
             patch("telegram_adapter.get_config") as mock_config:

            mock_reg_instance = MagicMock()
            mock_reg_instance.find_task_by_topic.return_value = ("my_task", {"topic_id": 500})
            mock_registry.return_value = mock_reg_instance

            mock_cfg_instance = MagicMock()
            mock_cfg_instance.general_topic_id = 1
            mock_config.return_value = mock_cfg_instance

            from telegram_adapter import TelegramAdapter

            adapter = TelegramAdapter("TOKEN", "-1001234567890", timeout=1)
            task_id = adapter._get_task_id_from_topic(500)
            assert task_id == "my_task"

    async def test_send_message_no_topic_found_fallback(self, mock_telegram_server):
        """Test send_message falls back to general topic when task_id not found."""
        with patch("telegram_adapter.get_registry") as mock_registry, \
             patch("telegram_adapter.get_config") as mock_config, \
             patch("telegram_adapter.log") as mock_log:

            mock_reg_instance = MagicMock()
            mock_reg_instance.get_task.return_value = None
            mock_reg_instance.find_task_by_topic.return_value = None
            mock_registry.return_value = mock_reg_instance

            mock_cfg_instance = MagicMock()
            mock_cfg_instance.general_topic_id = 1
            mock_cfg_instance.get.return_value = 0
            mock_config.return_value = mock_cfg_instance

            original_post = requests.post

            def patched_post(url, **kwargs):
                if "api.telegram.org" in url:
                    method = url.split("/")[-1]
                    return original_post(
                        f"{mock_telegram_server.base_url}/bot_TOKEN/{method}",
                        **kwargs,
                    )
                return original_post(url, **kwargs)

            with patch.object(requests, "post", patched_post):
                from telegram_adapter import TelegramAdapter

                adapter = TelegramAdapter("TOKEN", "-1001234567890", timeout=1)
                msg_id = await adapter.send_message(
                    task_id="nonexistent",
                    content="Test",
                )

                # Should log warning
                mock_log.assert_called()
                # Should still send (to general topic)
                assert msg_id != ""

    async def test_send_message_with_buttons(self, mock_telegram_server):
        """Test send_message with inline keyboard buttons."""
        with patch("telegram_adapter.get_registry") as mock_registry, \
             patch("telegram_adapter.get_config") as mock_config:

            mock_reg_instance = MagicMock()
            mock_reg_instance.get_task.return_value = {"topic_id": 123}
            mock_registry.return_value = mock_reg_instance

            mock_cfg_instance = MagicMock()
            mock_cfg_instance.general_topic_id = 1
            mock_cfg_instance.get.return_value = 0
            mock_config.return_value = mock_cfg_instance

            original_post = requests.post

            def patched_post(url, **kwargs):
                if "api.telegram.org" in url:
                    method = url.split("/")[-1]
                    return original_post(
                        f"{mock_telegram_server.base_url}/bot_TOKEN/{method}",
                        **kwargs,
                    )
                return original_post(url, **kwargs)

            with patch.object(requests, "post", patched_post):
                from telegram_adapter import TelegramAdapter

                adapter = TelegramAdapter("TOKEN", "-1001234567890", timeout=1)
                msg_id = await adapter.send_message(
                    task_id="test_task",
                    content="Choose option",
                    buttons=[
                        {"text": "Allow", "callback_data": "allow:123"},
                        {"text": "Deny", "callback_data": "deny:123"},
                    ],
                )

                assert msg_id != ""
                assert len(mock_telegram_server.sent_messages) == 1
                sent = mock_telegram_server.sent_messages[0]
                assert "reply_markup" in sent

    async def test_send_message_empty_response(self, mock_telegram_server):
        """Test send_message returns empty string when API returns no message_id."""
        with patch("telegram_adapter.get_registry") as mock_registry, \
             patch("telegram_adapter.get_config") as mock_config, \
             patch("telegram_adapter.send_to_topic") as mock_send:

            mock_reg_instance = MagicMock()
            mock_reg_instance.get_task.return_value = {"topic_id": 123}
            mock_registry.return_value = mock_reg_instance

            mock_cfg_instance = MagicMock()
            mock_cfg_instance.general_topic_id = 1
            mock_cfg_instance.get.return_value = 0
            mock_config.return_value = mock_cfg_instance

            # Return response with no message_id
            mock_send.return_value = {"ok": True, "result": {}}

            from telegram_adapter import TelegramAdapter

            adapter = TelegramAdapter("TOKEN", "-1001234567890", timeout=1)
            msg_id = await adapter.send_message("test_task", "Test")
            assert msg_id == ""

    async def test_send_message_null_response(self, mock_telegram_server):
        """Test send_message returns empty string when API returns None."""
        with patch("telegram_adapter.get_registry") as mock_registry, \
             patch("telegram_adapter.get_config") as mock_config, \
             patch("telegram_adapter.send_to_topic") as mock_send:

            mock_reg_instance = MagicMock()
            mock_reg_instance.get_task.return_value = {"topic_id": 123}
            mock_registry.return_value = mock_reg_instance

            mock_cfg_instance = MagicMock()
            mock_cfg_instance.general_topic_id = 1
            mock_cfg_instance.get.return_value = 0
            mock_config.return_value = mock_cfg_instance

            mock_send.return_value = None

            from telegram_adapter import TelegramAdapter

            adapter = TelegramAdapter("TOKEN", "-1001234567890", timeout=1)
            msg_id = await adapter.send_message("test_task", "Test")
            assert msg_id == ""

    async def test_update_message_none_buttons(self, mock_telegram_server):
        """Test update_message does nothing when buttons is None."""
        with patch("telegram_adapter.get_registry") as mock_registry, \
             patch("telegram_adapter.get_config") as mock_config:

            mock_reg_instance = MagicMock()
            mock_registry.return_value = mock_reg_instance

            mock_cfg_instance = MagicMock()
            mock_cfg_instance.general_topic_id = 1
            mock_cfg_instance.get.return_value = 0
            mock_config.return_value = mock_cfg_instance

            from telegram_adapter import TelegramAdapter

            adapter = TelegramAdapter("TOKEN", "-1001234567890", timeout=1)
            # Should return early without making any API calls
            await adapter.update_message("operator", "123", content="new", buttons=None)
            # No assertions needed - just checking it doesn't error

    async def test_update_message_string_label(self, mock_telegram_server):
        """Test update_message with string label for buttons."""
        with patch("telegram_adapter.get_registry") as mock_registry, \
             patch("telegram_adapter.get_config") as mock_config, \
             patch("telegram_adapter.update_message_buttons") as mock_update:

            mock_reg_instance = MagicMock()
            mock_registry.return_value = mock_reg_instance

            mock_cfg_instance = MagicMock()
            mock_cfg_instance.general_topic_id = 1
            mock_cfg_instance.group_id = -1001234567890
            mock_cfg_instance.get.return_value = 0
            mock_config.return_value = mock_cfg_instance

            from telegram_adapter import TelegramAdapter

            adapter = TelegramAdapter("TOKEN", "-1001234567890", timeout=1)
            await adapter.update_message("operator", "123", buttons="Allowed")

            mock_update.assert_called_once_with(
                "TOKEN", "-1001234567890", 123, "Allowed"
            )

    async def test_update_message_button_list(self, mock_telegram_server):
        """Test update_message with button list."""
        with patch("telegram_adapter.get_registry") as mock_registry, \
             patch("telegram_adapter.get_config") as mock_config:

            mock_reg_instance = MagicMock()
            mock_registry.return_value = mock_reg_instance

            mock_cfg_instance = MagicMock()
            mock_cfg_instance.general_topic_id = 1
            mock_cfg_instance.get.return_value = 0
            mock_config.return_value = mock_cfg_instance

            from telegram_adapter import TelegramAdapter

            adapter = TelegramAdapter("TOKEN", "-1001234567890", timeout=1)

            # Patch the session's post method to redirect to mock server
            original_post = adapter._session.post

            def patched_post(url, **kwargs):
                if "api.telegram.org" in url:
                    method = url.split("/")[-1]
                    return original_post(
                        f"{mock_telegram_server.base_url}/bot_TOKEN/{method}",
                        **kwargs,
                    )
                return original_post(url, **kwargs)

            with patch.object(adapter._session, "post", patched_post):
                await adapter.update_message(
                    "operator",
                    "100",
                    buttons=[{"text": "Done", "callback_data": "done"}],
                )

                assert len(mock_telegram_server.edited_messages) == 1

    async def test_show_typing(self, mock_telegram_server):
        """Test show_typing sends chat action."""
        with patch("telegram_adapter.get_registry") as mock_registry, \
             patch("telegram_adapter.get_config") as mock_config, \
             patch("telegram_adapter.send_chat_action") as mock_action:

            mock_reg_instance = MagicMock()
            mock_reg_instance.get_task.return_value = {"topic_id": 456}
            mock_registry.return_value = mock_reg_instance

            mock_cfg_instance = MagicMock()
            mock_cfg_instance.general_topic_id = 1
            mock_cfg_instance.group_id = -1001234567890
            mock_cfg_instance.get.return_value = 0
            mock_config.return_value = mock_cfg_instance

            from telegram_adapter import TelegramAdapter

            adapter = TelegramAdapter("TOKEN", "-1001234567890", timeout=1)
            await adapter.show_typing("test_task")

            mock_action.assert_called_once_with(
                "TOKEN", "-1001234567890", action="typing", topic_id=456
            )

    async def test_show_typing_no_topic(self, mock_telegram_server):
        """Test show_typing does nothing when topic not found."""
        with patch("telegram_adapter.get_registry") as mock_registry, \
             patch("telegram_adapter.get_config") as mock_config, \
             patch("telegram_adapter.send_chat_action") as mock_action:

            mock_reg_instance = MagicMock()
            mock_reg_instance.get_task.return_value = None
            mock_reg_instance.find_task_by_topic.return_value = None
            mock_registry.return_value = mock_reg_instance

            mock_cfg_instance = MagicMock()
            mock_cfg_instance.general_topic_id = 1
            mock_cfg_instance.get.return_value = 0
            mock_config.return_value = mock_cfg_instance

            from telegram_adapter import TelegramAdapter

            adapter = TelegramAdapter("TOKEN", "-1001234567890", timeout=1)
            await adapter.show_typing("nonexistent")

            mock_action.assert_not_called()


@pytest.mark.asyncio
class TestTelegramAdapterGroupChatId:
    """Test _get_group_chat_id routing logic."""

    async def test_get_group_chat_id_uses_registry_config(self, mock_telegram_server):
        """Test _get_group_chat_id prefers config.group_id over constructor chat_id."""
        with patch("telegram_adapter.get_registry") as mock_registry, \
             patch("telegram_adapter.get_config") as mock_config:

            mock_reg_instance = MagicMock()
            mock_registry.return_value = mock_reg_instance

            mock_cfg_instance = MagicMock()
            mock_cfg_instance.group_id = -1009999999999  # Different from constructor
            mock_cfg_instance.get.return_value = 0
            mock_config.return_value = mock_cfg_instance

            from telegram_adapter import TelegramAdapter

            adapter = TelegramAdapter("TOKEN", "-1001234567890", timeout=1)
            chat_id = adapter._get_group_chat_id()

            # Should use config.group_id, not constructor chat_id
            assert chat_id == "-1009999999999"

    async def test_get_group_chat_id_fallback_to_constructor(self, mock_telegram_server):
        """Test _get_group_chat_id falls back to constructor chat_id when config.group_id is None."""
        with patch("telegram_adapter.get_registry") as mock_registry, \
             patch("telegram_adapter.get_config") as mock_config:

            mock_reg_instance = MagicMock()
            mock_registry.return_value = mock_reg_instance

            mock_cfg_instance = MagicMock()
            mock_cfg_instance.group_id = None  # Not configured
            mock_cfg_instance.get.return_value = 0
            mock_config.return_value = mock_cfg_instance

            from telegram_adapter import TelegramAdapter

            adapter = TelegramAdapter("TOKEN", "-1001234567890", timeout=1)
            chat_id = adapter._get_group_chat_id()

            # Should fall back to constructor chat_id
            assert chat_id == "-1001234567890"


@pytest.mark.asyncio
class TestTelegramAdapterIncoming:
    """Test TelegramAdapter incoming_messages method."""

    async def test_incoming_messages_text(self, mock_telegram_server, telegram_adapter_with_mock):
        """Test incoming_messages yields text messages."""
        mock_telegram_server.add_message_update("Hello", topic_id=123)

        messages = []
        async for msg in telegram_adapter_with_mock.incoming_messages():
            messages.append(msg)
            break  # Only get first message

        assert len(messages) == 1
        assert messages[0].text == "Hello"
        assert messages[0].task_id == "test_task"

    async def test_incoming_messages_callback(self, mock_telegram_server, telegram_adapter_with_mock, mock_telegram_config):
        """Test incoming_messages yields callback queries."""
        mock_cfg, mock_reg = mock_telegram_config
        mock_reg.find_task_by_topic.return_value = None

        with patch("telegram_adapter.answer_callback") as mock_answer:
            mock_telegram_server.add_callback_update("allow:toolu_123", msg_id=200, topic_id=456)

            messages = []
            async for msg in telegram_adapter_with_mock.incoming_messages():
                messages.append(msg)
                break

            assert len(messages) == 1
            assert messages[0].callback_data == "allow:toolu_123"
            assert messages[0].text is None
            mock_answer.assert_called_once()

    async def test_incoming_messages_forum_topic_created(self, mock_telegram_server, telegram_adapter_with_mock, mock_telegram_config):
        """Test incoming_messages handles forum_topic_created events."""
        mock_cfg, mock_reg = mock_telegram_config
        mock_reg.find_task_by_topic.return_value = None

        # Add a forum_topic_created event
        mock_telegram_server.add_update({
            "message": {
                "message_id": 1,
                "chat": {"id": -1001234567890},
                "message_thread_id": 999,
                "forum_topic_created": {"name": "New Task"},
            }
        })
        # Add a regular message after
        mock_telegram_server.add_message_update("After topic", topic_id=123)

        messages = []
        count = 0
        async for msg in telegram_adapter_with_mock.incoming_messages():
            messages.append(msg)
            count += 1
            if count >= 1:
                break

        # Should have stored topic mapping
        mock_cfg.store_topic_mapping.assert_called_with(999, "New Task")
        # Should yield the regular message
        assert len(messages) == 1
        assert messages[0].text == "After topic"

    async def test_incoming_messages_dm(self, mock_telegram_server, telegram_adapter_with_mock):
        """Test incoming_messages handles DM messages as operator."""
        # Add a DM (private chat) message
        mock_telegram_server.add_update({
            "message": {
                "message_id": 50,
                "chat": {"id": 12345, "type": "private"},
                "from": {"id": 12345, "is_bot": False, "first_name": "User"},
                "text": "DM message",
            }
        })

        messages = []
        async for msg in telegram_adapter_with_mock.incoming_messages():
            messages.append(msg)
            break

        assert len(messages) == 1
        assert messages[0].text == "DM message"
        assert messages[0].task_id == "operator"

    async def test_incoming_messages_wrong_chat(self, mock_telegram_server, telegram_adapter_with_mock, mock_telegram_config):
        """Test incoming_messages ignores messages from wrong chat."""
        mock_cfg, mock_reg = mock_telegram_config
        mock_reg.find_task_by_topic.return_value = None

        # Add message from wrong chat
        mock_telegram_server.add_update({
            "message": {
                "message_id": 60,
                "chat": {"id": -999999999, "type": "supergroup"},
                "from": {"id": 12345, "is_bot": False, "first_name": "User"},
                "text": "Wrong chat message",
            }
        })
        # Add a valid message
        mock_telegram_server.add_message_update("Valid message", topic_id=123)

        messages = []
        async for msg in telegram_adapter_with_mock.incoming_messages():
            messages.append(msg)
            break

        # Should only get the valid message
        assert len(messages) == 1
        assert messages[0].text == "Valid message"

    async def test_incoming_messages_no_text(self, mock_telegram_server, telegram_adapter_with_mock, mock_telegram_config):
        """Test incoming_messages skips messages without text."""
        mock_cfg, mock_reg = mock_telegram_config
        mock_reg.find_task_by_topic.return_value = None

        # Add a message without text (e.g., sticker, photo)
        mock_telegram_server.add_update({
            "message": {
                "message_id": 70,
                "chat": {"id": -1001234567890, "type": "supergroup"},
                "from": {"id": 12345, "is_bot": False, "first_name": "User"},
                "sticker": {"file_id": "abc123"},
            }
        })
        # Add a text message after
        mock_telegram_server.add_message_update("Text after sticker", topic_id=1)

        messages = []
        async for msg in telegram_adapter_with_mock.incoming_messages():
            messages.append(msg)
            break

        # Should only get the text message
        assert len(messages) == 1
        assert messages[0].text == "Text after sticker"

    async def test_incoming_messages_request_error(self, mock_telegram_config):
        """Test incoming_messages handles request errors gracefully."""
        from telegram_adapter import TelegramAdapter
        adapter = TelegramAdapter("TOKEN", "-1001234567890", timeout=1)

        call_count = [0]

        def failing_get(url, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 1:
                raise requests.exceptions.ConnectionError("Network error")
            # Return empty updates on subsequent calls
            mock_resp = MagicMock()
            mock_resp.ok = True
            mock_resp.json.return_value = {"ok": True, "result": []}
            return mock_resp

        with patch.object(adapter._session, "get", failing_get):
            # Run for a short time to test error handling
            async def run_with_timeout():
                async for msg in adapter.incoming_messages():
                    return msg

            try:
                await asyncio.wait_for(run_with_timeout(), timeout=0.2)
            except asyncio.TimeoutError:
                pass  # Expected

            # Should have logged the error
            assert call_count[0] >= 1

    async def test_incoming_messages_reply_to(self, mock_telegram_server, telegram_adapter_with_mock, mock_telegram_config):
        """Test incoming_messages parses reply_to_message."""
        mock_cfg, mock_reg = mock_telegram_config
        mock_reg.find_task_by_topic.return_value = ("task", {})

        # Add a message with reply
        mock_telegram_server.add_update({
            "message": {
                "message_id": 80,
                "chat": {"id": -1001234567890, "type": "supergroup"},
                "message_thread_id": 123,
                "from": {"id": 12345, "is_bot": False, "first_name": "User"},
                "text": "Reply message",
                "reply_to_message": {"message_id": 50},
            }
        })

        messages = []
        async for msg in telegram_adapter_with_mock.incoming_messages():
            messages.append(msg)
            break

        assert len(messages) == 1
        assert messages[0].reply_to_msg_id == "50"
