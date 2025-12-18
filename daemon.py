#!/usr/bin/env python3
"""Async main daemon - orchestrates ProcessManager, PermissionServer, and TelegramAdapter.

Main loop:
1. ProcessManager routes events from all Claude subprocesses
2. TelegramAdapter polls for messages and callbacks
3. PermissionManager handles tool permission requests via HTTP server
4. Route events between components:
   - Claude events -> Telegram notifications
   - Telegram messages -> Claude processes
   - Permission requests -> Telegram prompts -> responses
5. Handle bot commands (/spawn, /status, etc.)
"""

import asyncio
import json
import os
import signal
import sys
import threading
from pathlib import Path

from telegram_utils import log, send_to_topic, format_tool_permission
from registry import get_config, get_registry
from process_manager import ProcessManager
from permission_server import PermissionManager, start_permission_server, send_permission_notification
from telegram_adapter import TelegramAdapter
from claude_process import SystemInit, AssistantMessage, SessionResult, extract_tool_uses, extract_text
from bot_commands import CommandHandler

CONFIG_FILE = Path.home() / "telegram.json"
PID_FILE = Path("/tmp/claude-army-daemon.pid")


class DaemonAlreadyRunning(Exception):
    pass


def check_singleton():
    """Ensure only one daemon is running."""
    if PID_FILE.exists():
        pid = int(PID_FILE.read_text().strip())
        try:
            os.kill(pid, 0)
            raise DaemonAlreadyRunning(f"Daemon already running with PID {pid}")
        except OSError:
            pass
    PID_FILE.write_text(str(os.getpid()))


def cleanup_pid_file():
    """Remove PID file on exit."""
    PID_FILE.unlink(missing_ok=True)


class Daemon:
    """Main daemon coordinating all components."""

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id

        # Initialize components
        self.process_manager = ProcessManager()
        self.permission_manager = PermissionManager()
        self.telegram = TelegramAdapter(bot_token, chat_id)
        self.command_handler = CommandHandler(bot_token, chat_id, {}, self.process_manager)

        # Task group for concurrent tasks
        self._running = False
        self._shutdown_event = asyncio.Event()

    async def start(self):
        """Start the daemon."""
        self._running = True
        log(f"Starting daemon (PID {os.getpid()})...")

        # Start permission HTTP server in background thread
        permission_thread = threading.Thread(
            target=start_permission_server,
            args=(self.permission_manager, "localhost", 9000),
            daemon=True
        )
        permission_thread.start()
        log("Permission server started on localhost:9000")

        # Spawn operator process on startup
        await self._spawn_operator()

        # Setup signal handlers for graceful shutdown
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

        log("Daemon started successfully")

    async def _spawn_operator(self):
        """Spawn the operator Claude process."""
        config = get_config()
        registry = get_registry()

        # Check if operator is already running
        operator_data = registry.get_task("operator")
        if operator_data and operator_data.get("session_id"):
            session_id = operator_data["session_id"]
            log(f"Resuming operator session: {session_id}")
            try:
                from claude_process import ClaudeProcess
                process = ClaudeProcess(
                    cwd=str(Path.home()),
                    resume_session_id=session_id
                )
                started = await process.start()
                if started:
                    self.process_manager.processes["operator"] = process
                    self.process_manager._start_event_task("operator", process)
                    log("Operator session resumed")
                    return
            except Exception as e:
                log(f"Failed to resume operator: {e}, spawning new one")

        # Spawn new operator
        log("Spawning new operator session")
        try:
            from claude_process import ClaudeProcess
            cwd = str(Path.home())

            process = ClaudeProcess(cwd=cwd)
            started = await process.start()
            if not started:
                raise RuntimeError("Failed to start operator process")

            self.process_manager.processes["operator"] = process
            self.process_manager._start_event_task("operator", process)

            # Wait for session_id from init event (should come quickly)
            await asyncio.sleep(0.5)

            # Send initial prompt
            prompt = (
                "You are the Operator Claude for claude-army. "
                "You coordinate tasks, spawn workers, and handle high-level planning. "
                "Use the tools available to manage the task registry and spawn new workers as needed."
            )
            await process.send_message(prompt)

            # Update registry with operator task
            operator_data = {
                "type": "operator",
                "path": cwd,
                "topic_id": config.general_topic_id,
                "status": "active",
                "session_id": process.session_id,
                "pid": process.pid
            }
            registry.add_task("operator", operator_data)

            log("Operator spawned successfully")
        except Exception as e:
            log(f"Failed to spawn operator: {e}")

    async def run(self):
        """Main event loop."""
        # Launch concurrent tasks
        tasks = [
            asyncio.create_task(self._handle_claude_events()),
            asyncio.create_task(self._handle_telegram_messages()),
            asyncio.create_task(self._handle_permission_requests()),
        ]

        # Wait for shutdown signal
        await self._shutdown_event.wait()

        # Cancel all tasks
        for task in tasks:
            task.cancel()

        # Wait for tasks to finish cancelling
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _handle_claude_events(self):
        """Handle events from all Claude processes."""
        try:
            async for task_name, event in self.process_manager.all_events():
                try:
                    if isinstance(event, SystemInit):
                        await self._on_system_init(task_name, event)
                    elif isinstance(event, AssistantMessage):
                        await self._on_assistant_message(task_name, event)
                    elif isinstance(event, SessionResult):
                        await self._on_session_result(task_name, event)
                    elif isinstance(event, dict) and event.get("type") == "error":
                        await self._on_process_error(task_name, event)
                except Exception as e:
                    log(f"Error handling Claude event: {e}")
        except asyncio.CancelledError:
            log("Claude event handler cancelled")
            raise

    async def _handle_telegram_messages(self):
        """Handle messages from Telegram."""
        try:
            async for msg in self.telegram.incoming_messages():
                try:
                    # Handle callback queries (button clicks)
                    if msg.callback_data:
                        await self._handle_callback(msg)
                        continue

                    # Handle text messages
                    if msg.text:
                        # Check if it's a command
                        if msg.text.startswith("/"):
                            # Build a minimal telegram message dict for command handler
                            tg_msg = {
                                "text": msg.text,
                                "message_id": int(msg.msg_id),
                                "chat": {"id": int(self.chat_id)},
                                "message_thread_id": self._get_topic_id_for_task(msg.task_id)
                            }
                            handled = self.command_handler.handle_command(tg_msg)
                            if handled:
                                continue

                        # Route message to appropriate Claude process
                        await self._route_message_to_claude(msg.task_id, msg.text)

                except Exception as e:
                    log(f"Error handling Telegram message: {e}")
        except asyncio.CancelledError:
            log("Telegram handler cancelled")
            raise

    async def _handle_permission_requests(self):
        """Monitor and send notifications for pending permissions."""
        # This is handled by the HTTP server thread + send_permission_notification
        # We just need to check for new pending permissions periodically
        while self._running:
            try:
                # Check for pending permissions that don't have Telegram notifications yet
                with self.permission_manager._lock:
                    for tool_use_id, pending in list(self.permission_manager.pending.items()):
                        if pending.telegram_msg_id is None:
                            # Send notification
                            task_name = self._get_task_for_session(pending.session_id)
                            if task_name:
                                topic_id = self._get_topic_id_for_task(task_name)
                                if topic_id:
                                    send_permission_notification(
                                        self.permission_manager,
                                        self.bot_token,
                                        self.chat_id,
                                        topic_id,
                                        tool_use_id
                                    )
            except Exception as e:
                log(f"Error checking pending permissions: {e}")

            await asyncio.sleep(0.5)

    async def _on_system_init(self, task_name: str, event: SystemInit):
        """Handle system init event."""
        log(f"System init: {task_name} (session={event.session_id})")
        # Session ID is already persisted by ProcessManager

    async def _on_assistant_message(self, task_name: str, event: AssistantMessage):
        """Handle assistant message event."""
        # Extract text content
        text = extract_text(event)
        if text:
            # Send to Telegram (escape for MarkdownV2)
            from telegram_utils import escape_markdown_v2
            escaped_text = escape_markdown_v2(text)
            await self.telegram.send_message(task_name, escaped_text)

        # Check for tool uses (these will be handled by permission hooks)
        tools = extract_tool_uses(event)
        if tools:
            log(f"Assistant requested {len(tools)} tools: {[t.name for t in tools]}")

    async def _on_session_result(self, task_name: str, event: SessionResult):
        """Handle session result event."""
        status = "completed" if event.success else "failed"
        msg = f"Session {status} (cost: ${event.cost:.4f}, turns: {event.turns})"
        await self.telegram.send_message(task_name, msg)
        log(f"Session result: {task_name} - {status}")

    async def _on_process_error(self, task_name: str, event: dict):
        """Handle process error event."""
        error = event.get("error", "Unknown error")
        msg = f"Process error: {error}"
        await self.telegram.send_message(task_name, msg)
        log(f"Process error: {task_name} - {error}")

    async def _handle_callback(self, msg):
        """Handle Telegram callback (button click)."""
        if ":" not in msg.callback_data:
            return

        action, data = msg.callback_data.split(":", 1)

        # Permission callbacks
        if action in ("allow", "deny"):
            decision = action
            reason = "User decision" if action == "allow" else "User denied"
            if self.permission_manager.respond(data, decision, reason):
                # Update button to show decision
                label = "✓ Allowed" if action == "allow" else "✗ Denied"
                await self.telegram.update_message(msg.task_id, msg.msg_id, buttons=label)

    async def _route_message_to_claude(self, task_name: str, text: str):
        """Route a message to the appropriate Claude process."""
        # Map task_name to actual process (operator or specific task)
        if task_name == "operator" or not self.process_manager.get_process(task_name):
            # Send to operator
            await self.process_manager.send_to_process("operator", text)
        else:
            # Send to specific task
            await self.process_manager.send_to_process(task_name, text)

    def _get_topic_id_for_task(self, task_name: str) -> int | None:
        """Get Telegram topic_id for a task."""
        config = get_config()
        if task_name == "operator":
            return config.general_topic_id

        registry = get_registry()
        task_data = registry.get_task(task_name)
        if task_data:
            return task_data.get("topic_id")

        return None

    def _get_task_for_session(self, session_id: str) -> str | None:
        """Get task name for a session ID."""
        registry = get_registry()
        for task_name, task_data in registry.get_all_tasks():
            if task_data.get("session_id") == session_id:
                return task_name
        return None

    async def shutdown(self):
        """Graceful shutdown."""
        if not self._running:
            return

        log("Shutting down daemon...")
        self._running = False

        # Stop all processes
        await self.process_manager.stop_all()

        # Signal main loop to exit
        self._shutdown_event.set()

        log("Daemon shutdown complete")


async def main():
    """Main entry point."""
    try:
        check_singleton()
    except DaemonAlreadyRunning as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Load config
    if not CONFIG_FILE.exists():
        print(f"Error: {CONFIG_FILE} not found", file=sys.stderr)
        print("Create it with: {\"bot_token\": \"...\", \"chat_id\": \"...\"}", file=sys.stderr)
        return 1

    config = json.loads(CONFIG_FILE.read_text())
    bot_token = config.get("bot_token")
    chat_id = config.get("chat_id")

    if not bot_token or not chat_id:
        print("Error: bot_token and chat_id required in config", file=sys.stderr)
        return 1

    # Create and run daemon
    daemon = Daemon(bot_token, chat_id)

    try:
        await daemon.start()
        await daemon.run()
    except KeyboardInterrupt:
        await daemon.shutdown()
    except Exception as e:
        log(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        cleanup_pid_file()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
