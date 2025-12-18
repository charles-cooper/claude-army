"""Telegram frontend adapter implementation.

Implements the FrontendAdapter interface for Telegram, handling:
- Message sending to topics (worker tasks) and general topic (operator)
- Polling for updates (messages, callbacks)
- Button interactions
- Permission prompt handling

Threading model:
- All public methods are async-safe (use asyncio.to_thread for blocking I/O)
- Internal _parse_* methods are sync (pure computation)
- Call stop() before cancelling to signal clean shutdown
"""

import asyncio
from typing import AsyncIterator

import requests

from frontend_adapter import FrontendAdapter, IncomingMessage
from registry import get_config, get_registry
from telegram_utils import (
    log, send_to_topic, update_message_buttons, answer_callback,
    send_chat_action, delete_message as tg_delete_message
)


class TelegramAdapter(FrontendAdapter):
    """Telegram implementation of FrontendAdapter.

    Maps task_id to Telegram topic_id via registry:
    - "operator" -> general_topic_id (from config)
    - Other task_id -> topic_id from registry

    Polls Telegram API for updates and yields IncomingMessage objects.
    """

    def __init__(self, bot_token: str, chat_id: str, timeout: int = 5):
        """Initialize Telegram adapter.

        Args:
            bot_token: Telegram bot token
            chat_id: Telegram chat/group ID
            timeout: Long polling timeout in seconds (must be >= 1)
        """
        assert isinstance(timeout, int) and timeout >= 1
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout

        # Load offset from config for crash recovery
        config = get_config()
        self.offset = config.get("telegram_offset", 0)

        # Session for connection pooling
        self._session = requests.Session()

        # Shutdown flag for clean exit
        self._shutdown = False

    def stop(self) -> None:
        """Signal the adapter to stop polling.

        Call this before cancelling tasks to ensure clean shutdown.
        """
        self._shutdown = True
        self._session.close()

    def _get_group_chat_id(self) -> str:
        """Get the group chat ID to use for sending messages.

        Uses registry config group_id if available, otherwise falls back to
        the chat_id passed to constructor. This ensures outgoing messages
        go to the same group that incoming messages are filtered from.
        """
        config = get_config()
        return str(config.group_id) if config.group_id else self.chat_id

    def _get_topic_id(self, task_id: str) -> int | None:
        """Get Telegram topic_id for a task_id.

        Args:
            task_id: Task identifier (e.g., "operator" or task name)

        Returns:
            Telegram topic_id or None if not found
        """
        if task_id == "operator":
            return get_config().general_topic_id

        # Look up task in registry
        registry = get_registry()
        task_data = registry.get_task(task_id)
        if task_data:
            return task_data.get("topic_id")

        # Try looking up by topic_id directly (if task_id is numeric)
        try:
            topic_id = int(task_id)
            result = registry.find_task_by_topic(topic_id)
            if result:
                return topic_id
        except ValueError:
            pass

        return None

    def _get_task_id_from_topic(self, topic_id: int | None) -> str:
        """Get task_id from Telegram topic_id.

        Args:
            topic_id: Telegram topic ID (None for DM or General topic)

        Returns:
            Task identifier ("operator" or task name)
        """
        config = get_config()

        # None or general_topic_id -> operator
        if topic_id is None or topic_id == config.general_topic_id:
            return "operator"

        # Look up in registry
        registry = get_registry()
        result = registry.find_task_by_topic(topic_id)
        if result:
            task_name, _ = result
            return task_name

        # Fallback: use topic_id as string
        return str(topic_id)

    async def send_message(
        self,
        task_id: str,
        content: str,
        buttons: list[dict] = None
    ) -> str:
        """Send message to Telegram topic.

        Args:
            task_id: Task identifier
            content: Message text (MarkdownV2 formatted)
            buttons: Optional inline keyboard buttons

        Returns:
            Message ID as string
        """
        topic_id = self._get_topic_id(task_id)
        if topic_id is None:
            log(f"Warning: No topic_id found for task_id={task_id}")
            # Fallback to general topic
            topic_id = get_config().general_topic_id

        reply_markup = None
        if buttons:
            # Convert button list to Telegram inline keyboard format
            # Single row of buttons
            reply_markup = {
                "inline_keyboard": [
                    [{"text": btn["text"], "callback_data": btn["callback_data"]}
                     for btn in buttons]
                ]
            }

        target_chat_id = self._get_group_chat_id()
        log(f"send_message: chat_id={target_chat_id}, topic_id={topic_id}, task_id={task_id}")
        response = send_to_topic(
            self.bot_token,
            target_chat_id,
            topic_id,
            content,
            reply_markup=reply_markup,
            parse_mode="MarkdownV2"
        )

        if response:
            msg_id = response.get("result", {}).get("message_id")
            return str(msg_id) if msg_id else ""

        return ""

    async def update_message(
        self,
        task_id: str,
        msg_id: str,
        content: str = None,
        buttons: list[dict] = None
    ):
        """Update message buttons in Telegram.

        Note: Currently only supports updating buttons (Telegram limitation
        for inline keyboard updates). Content updates would require editMessageText.

        Args:
            task_id: Task identifier (unused, msg_id is global)
            msg_id: Message ID to update
            content: New content (currently ignored)
            buttons: New button configuration or label string
        """
        if buttons is None:
            return

        target_chat_id = self._get_group_chat_id()

        # Support both list[dict] format and simple string label
        if isinstance(buttons, str):
            # Single disabled button with label
            update_message_buttons(
                self.bot_token,
                target_chat_id,
                int(msg_id),
                buttons
            )
        else:
            # Full button list
            reply_markup = {
                "inline_keyboard": [
                    [{"text": btn["text"], "callback_data": btn["callback_data"]}
                     for btn in buttons]
                ]
            }
            self._session.post(
                f"https://api.telegram.org/bot{self.bot_token}/editMessageReplyMarkup",
                json={
                    "chat_id": target_chat_id,
                    "message_id": int(msg_id),
                    "reply_markup": reply_markup
                }
            )

    async def delete_message(self, task_id: str, msg_id: str):
        """Delete a Telegram message.

        Args:
            task_id: Task identifier (unused, msg_id is global)
            msg_id: Message ID to delete
        """
        tg_delete_message(self.bot_token, self._get_group_chat_id(), int(msg_id))

    async def show_typing(self, task_id: str):
        """Show typing indicator in Telegram topic.

        Args:
            task_id: Task identifier
        """
        topic_id = self._get_topic_id(task_id)
        if topic_id:
            send_chat_action(
                self.bot_token,
                self._get_group_chat_id(),
                action="typing",
                topic_id=topic_id
            )

    async def incoming_messages(self) -> AsyncIterator[IncomingMessage]:
        """Poll Telegram for updates and yield IncomingMessage objects.

        Handles:
        - Text messages (from DMs, General topic, task topics)
        - Callback queries (button clicks)
        - Forum topic creation events (for crash recovery)

        Yields:
            IncomingMessage objects

        Note:
            Call stop() to signal shutdown. The generator will exit cleanly
            after the current poll completes.
        """
        while not self._shutdown:
            try:
                # Poll Telegram API (in thread to avoid blocking event loop)
                resp = await asyncio.to_thread(
                    self._session.get,
                    f"https://api.telegram.org/bot{self.bot_token}/getUpdates",
                    params={"offset": self.offset, "timeout": self.timeout},
                    timeout=self.timeout + 2
                )

                if self._shutdown:
                    return

                if not resp.ok:
                    await asyncio.sleep(1)
                    continue

                updates = resp.json().get("result", [])

                if updates:
                    log(f"Got {len(updates)} Telegram updates")

                for update in updates:
                    if self._shutdown:
                        return

                    # Update offset for next poll
                    self.offset = update["update_id"] + 1

                    # Persist offset for crash recovery
                    get_config().set("telegram_offset", self.offset)

                    # Handle forum_topic_created events (store mapping)
                    msg = update.get("message", {})
                    if msg.get("forum_topic_created"):
                        topic_id = msg.get("message_thread_id")
                        name = msg["forum_topic_created"].get("name")
                        if topic_id and name:
                            get_config().store_topic_mapping(topic_id, name)
                            log(f"Stored topic mapping: {topic_id} -> {name}")
                        continue

                    # Handle callback queries (button clicks)
                    callback = update.get("callback_query")
                    if callback:
                        yield self._parse_callback(callback)
                        continue

                    # Handle regular messages
                    if msg:
                        incoming = self._parse_message(msg)
                        if incoming:
                            yield incoming

            except asyncio.CancelledError:
                log("Telegram poller cancelled")
                return
            except requests.exceptions.RequestException as e:
                log(f"Telegram poll error: {e}")
                await asyncio.sleep(1)
            except Exception as e:
                log(f"Unexpected error in incoming_messages: {e}")
                await asyncio.sleep(1)

    def _parse_message(self, msg: dict) -> IncomingMessage | None:
        """Parse a Telegram message into IncomingMessage.

        Args:
            msg: Telegram message dict

        Returns:
            IncomingMessage or None if message should be ignored
        """
        text = msg.get("text")
        if not text:
            return None

        msg_id = str(msg.get("message_id"))
        chat_id = str(msg.get("chat", {}).get("id"))
        topic_id = msg.get("message_thread_id")
        reply_to = msg.get("reply_to_message", {}).get("message_id")

        # Ignore messages from wrong chat
        config = get_config()
        is_dm = msg.get("chat", {}).get("type") == "private"
        is_group = chat_id == str(config.group_id)

        if not is_dm and not is_group:
            return None

        # Determine task_id based on message context
        if is_dm:
            task_id = "operator"
        else:
            task_id = self._get_task_id_from_topic(topic_id)

        return IncomingMessage(
            task_id=task_id,
            text=text,
            callback_data=None,
            msg_id=msg_id,
            reply_to_msg_id=str(reply_to) if reply_to else None
        )

    def _parse_callback(self, callback: dict) -> IncomingMessage:
        """Parse a Telegram callback query into IncomingMessage.

        Args:
            callback: Telegram callback_query dict

        Returns:
            IncomingMessage representing the button click
        """
        cb_id = callback["id"]
        cb_data = callback.get("data", "")
        cb_msg = callback.get("message", {})
        msg_id = str(cb_msg.get("message_id"))
        topic_id = cb_msg.get("message_thread_id")

        # Answer callback to dismiss loading state
        answer_callback(self.bot_token, cb_id)

        # Determine task_id
        task_id = self._get_task_id_from_topic(topic_id)

        return IncomingMessage(
            task_id=task_id,
            text=None,
            callback_data=cb_data,
            msg_id=msg_id,
            reply_to_msg_id=None
        )
