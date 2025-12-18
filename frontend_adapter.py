"""Abstract frontend adapter interface for Claude Army.

Defines the interface for frontend implementations (Telegram, CLI, headless, etc.).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator


@dataclass
class IncomingMessage:
    """Represents an incoming message from the frontend.

    Attributes:
        task_id: Task identifier (e.g., topic_id for Telegram, or "operator")
        text: Message text content (None for callback-only messages)
        callback_data: Button callback data (None for text-only messages)
        msg_id: Frontend message ID (for tracking state)
        reply_to_msg_id: Message ID this is replying to (None if not a reply)
        reply_to_message: Full reply-to message dict (for /debug command)
    """
    task_id: str
    text: str | None
    callback_data: str | None
    msg_id: str
    reply_to_msg_id: str | None
    reply_to_message: dict | None = None


class FrontendAdapter(ABC):
    """Abstract base class for frontend implementations.

    Each frontend (Telegram, CLI, etc.) implements this interface to provide
    consistent message sending/receiving across different frontends.
    """

    @abstractmethod
    async def send_message(
        self,
        task_id: str,
        content: str,
        buttons: list[dict] = None
    ) -> str:
        """Send a message to the frontend.

        Args:
            task_id: Target task identifier (topic_id, channel, etc.)
            content: Message content (text, markdown, etc.)
            buttons: Optional list of button dicts with 'text' and 'callback_data'
                     Example: [{"text": "Allow", "callback_data": "y"}]

        Returns:
            Message ID from the frontend (for tracking/updates)
        """
        pass

    @abstractmethod
    async def update_message(
        self,
        task_id: str,
        msg_id: str,
        content: str = None,
        buttons: list[dict] = None
    ):
        """Update an existing message.

        Args:
            task_id: Task identifier where message exists
            msg_id: Message ID to update
            content: New content (None to keep existing)
            buttons: New buttons (None to keep existing)
        """
        pass

    @abstractmethod
    async def delete_message(self, task_id: str, msg_id: str):
        """Delete a message.

        Args:
            task_id: Task identifier where message exists
            msg_id: Message ID to delete
        """
        pass

    @abstractmethod
    async def show_typing(self, task_id: str):
        """Show typing indicator for a task.

        Args:
            task_id: Task identifier to show typing in
        """
        pass

    @abstractmethod
    async def incoming_messages(self) -> AsyncIterator[IncomingMessage]:
        """Stream of incoming messages from the frontend.

        Yields:
            IncomingMessage objects as they arrive
        """
        pass
