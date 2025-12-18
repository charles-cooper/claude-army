"""Core daemon functionality - orchestrates ProcessManager, PermissionServer, and TelegramAdapter.

This module contains all daemon logic in an importable form.
The telegram-daemon.py script is the executable entry point.

Threading model:
- Main event loop: asyncio (handles Claude events, Telegram polling, permission checks)
- Permission HTTP server: separate daemon thread (threading.Thread)
- Telegram polling: uses asyncio.to_thread() for blocking HTTP calls
- Claude subprocesses: managed via asyncio.create_subprocess_exec()

Shutdown:
- Signal received (SIGINT/SIGTERM) -> immediate os._exit(0)
- Process group cleanup via atexit ensures child processes are terminated
- PID file cleaned up via atexit
"""

import asyncio
import atexit
import json
import os
import signal
import sys
import threading
from pathlib import Path


def _setup_process_group():
    """Setup process group and atexit cleanup for orphan handling.

    Creates a new process group so all children can be killed together.
    Registers atexit handler to terminate process group on exit.
    """
    try:
        os.setpgrp()
    except OSError:
        pass  # May fail if already group leader

    def cleanup_process_group():
        try:
            os.killpg(os.getpgid(0), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass

    atexit.register(cleanup_process_group)

from telegram_utils import log, escape_markdown_v2
from registry import get_config, get_registry
from process_manager import ProcessManager
from permission_server import PermissionManager, start_permission_server, send_permission_notification
from telegram_adapter import TelegramAdapter
from claude_process import ClaudeProcess, SystemInit, AssistantMessage, SessionResult, extract_tool_uses, extract_text
from bot_commands import CommandHandler

DEFAULT_CONFIG_FILE = Path.home() / "telegram.json"
DEFAULT_PID_FILE = Path("/tmp/claude-army-daemon.pid")


class DaemonAlreadyRunning(Exception):
    """Raised when another daemon instance is already running."""
    pass


def check_singleton(pid_file: Path = DEFAULT_PID_FILE) -> None:
    """Ensure only one daemon is running.

    Args:
        pid_file: Path to PID file for singleton check.

    Raises:
        DaemonAlreadyRunning: If another daemon is running.
    """
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)
            raise DaemonAlreadyRunning(f"Daemon already running with PID {pid}")
        except (ValueError, OSError):
            # Invalid PID or process not running - safe to continue
            pass
    pid_file.write_text(str(os.getpid()))
    atexit.register(lambda: cleanup_pid_file(pid_file))


def cleanup_pid_file(pid_file: Path = DEFAULT_PID_FILE) -> None:
    """Remove PID file on exit.

    Args:
        pid_file: Path to PID file to remove.
    """
    pid_file.unlink(missing_ok=True)


class Daemon:
    """Main daemon coordinating all components."""

    def __init__(self, bot_token: str, chat_id: str):
        """Initialize daemon with Telegram credentials.

        Args:
            bot_token: Telegram bot token.
            chat_id: Telegram chat/group ID.
        """
        self.bot_token = bot_token
        self.chat_id = chat_id

        # Initialize components
        self.process_manager = ProcessManager()
        self.permission_manager = PermissionManager()
        self.telegram = TelegramAdapter(bot_token, chat_id)
        self.command_handler = CommandHandler(bot_token, chat_id, {}, self.process_manager)

        self._running = False

    async def start(self) -> None:
        """Start the daemon and all components."""
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

        # Setup signal handlers - just exit immediately
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self.shutdown)

        log("Daemon started successfully")

    async def _spawn_operator(self) -> None:
        """Spawn the operator Claude process."""
        config = get_config()
        registry = get_registry()

        # Check if operator is already running
        operator_data = registry.get_task("operator")
        if operator_data and operator_data.get("session_id"):
            session_id = operator_data["session_id"]
            log(f"Resuming operator session: {session_id}")
            try:
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

    async def run(self) -> None:
        """Main event loop - runs until signal handler calls os._exit()."""
        await asyncio.gather(
            self._handle_claude_events(),
            self._handle_telegram_messages(),
            self._handle_permission_requests(),
        )

    async def _handle_claude_events(self) -> None:
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

    async def _handle_telegram_messages(self) -> None:
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

    async def _handle_permission_requests(self) -> None:
        """Monitor and send notifications for pending permissions."""
        try:
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
        except asyncio.CancelledError:
            log("Permission request handler cancelled")
            raise

    async def _on_system_init(self, task_name: str, event: SystemInit) -> None:
        """Handle system init event."""
        log(f"System init: {task_name} (session={event.session_id})")

    async def _on_assistant_message(self, task_name: str, event: AssistantMessage) -> None:
        """Handle assistant message event."""
        # Extract text content
        text = extract_text(event)
        if text:
            # Send to Telegram (escape for MarkdownV2)
            escaped_text = escape_markdown_v2(text)
            await self.telegram.send_message(task_name, escaped_text)

        # Check for tool uses (these will be handled by permission hooks)
        tools = extract_tool_uses(event)
        if tools:
            log(f"Assistant requested {len(tools)} tools: {[t.name for t in tools]}")

    async def _on_session_result(self, task_name: str, event: SessionResult) -> None:
        """Handle session result event - marks end of a turn, not end of session."""
        # Just log, don't send to Telegram (noisy for multi-turn)
        status = "ok" if event.success else "error"
        log(f"Turn complete: {task_name} ({status}, ${event.cost:.4f})")

    async def _on_process_error(self, task_name: str, event: dict) -> None:
        """Handle process error event."""
        error = event.get("error", "Unknown error")
        msg = f"Process error: {error}"
        await self.telegram.send_message(task_name, msg)
        log(f"Process error: {task_name} - {error}")

    async def _handle_callback(self, msg) -> None:
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

    async def _route_message_to_claude(self, task_name: str, text: str) -> None:
        """Route a message to the appropriate Claude process.

        Uses existing process if running, otherwise resurrects from registry.
        Falls back to operator for unknown tasks.
        """
        # Try to send to the specific task first
        try:
            if task_name != "operator" and self.process_manager.get_process(task_name):
                success = await self.process_manager.send_to_process(task_name, text)
                if success:
                    return
        except KeyError:
            pass

        # Fall back to operator
        try:
            await self.process_manager.send_to_process("operator", text)
        except KeyError:
            log(f"No operator process available for message: {text[:100]}")

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

    def shutdown(self) -> None:
        """Immediate shutdown. No graceful cleanup needed - OS handles it."""
        print("\nShutting down...", flush=True)
        cleanup_pid_file()
        os._exit(0)


async def main(config_file: Path = DEFAULT_CONFIG_FILE, pid_file: Path = DEFAULT_PID_FILE) -> int:
    """Main entry point.

    Args:
        config_file: Path to config file with bot_token and chat_id.
        pid_file: Path to PID file for singleton check.

    Returns:
        Exit code (0 for success, 1 for error).
    """
    # Setup process group for orphan handling
    _setup_process_group()

    try:
        check_singleton(pid_file)
    except DaemonAlreadyRunning as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Load config
    if not config_file.exists():
        print(f"Error: {config_file} not found", file=sys.stderr)
        print('Create it with: {"bot_token": "...", "chat_id": "..."}', file=sys.stderr)
        return 1

    config = json.loads(config_file.read_text())
    bot_token = config.get("bot_token")
    chat_id = config.get("chat_id")

    if not bot_token or not chat_id:
        print("Error: bot_token and chat_id required in config", file=sys.stderr)
        return 1

    # Create and run daemon
    # Normal exit: signal handler calls os._exit(0)
    # Error exit: exception propagates, we log and return 1
    daemon = Daemon(bot_token, chat_id)

    try:
        await daemon.start()
        await daemon.run()
        # run() never returns normally - signal handler exits
    except Exception as e:
        log(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
        cleanup_pid_file(pid_file)
        return 1
