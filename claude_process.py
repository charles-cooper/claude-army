"""ClaudeProcess - manages Claude subprocess with stream-json I/O."""

import asyncio
import json
import os
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Optional

from telegram_utils import log


def _set_pdeathsig():
    """Set PR_SET_PDEATHSIG to SIGTERM so child dies when parent exits.

    Linux-only. Best-effort - fails silently on non-Linux or permission errors.
    """
    if sys.platform != 'linux':
        return
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        PR_SET_PDEATHSIG = 1
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)
    except (OSError, AttributeError):
        pass


@dataclass
class SystemInit:
    """Claude system initialization event."""
    session_id: str
    tools: list[dict]
    model: str
    raw: dict = field(default_factory=dict)  # Full event data


@dataclass
class AssistantMessage:
    """Claude assistant message event."""
    content: list[dict]  # Content blocks (text, thinking, tool_use)
    model: str = ""
    msg_id: str = ""
    raw: dict = field(default_factory=dict)  # Full event data


@dataclass
class ToolUse:
    """Claude tool_use block within assistant message."""
    id: str
    name: str
    input: dict
    raw: dict = field(default_factory=dict)  # Full block data


@dataclass
class SessionResult:
    """Claude session completion event."""
    success: bool
    result: str = ""
    cost: float = 0.0
    turns: int = 0
    raw: dict = field(default_factory=dict)  # Full event data


@dataclass
class UserMessage:
    """User message sent to Claude (for echo/acknowledgment)."""
    content: list[dict]
    raw: dict = field(default_factory=dict)


class ClaudeProcess:
    """Manages Claude subprocess with stream-json I/O.

    Spawns `claude -p --output-format stream-json --input-format stream-json`
    and provides async interface for sending messages and receiving events.
    """

    def __init__(
        self,
        cwd: str,
        resume_session_id: Optional[str] = None,
        allowed_tools: Optional[list[str]] = None,
        extra_args: Optional[list[str]] = None,
    ):
        """Initialize ClaudeProcess.

        Args:
            cwd: Working directory for Claude subprocess
            resume_session_id: Session ID to resume (uses --resume)
            allowed_tools: List of allowed tools (uses --allowedTools)
            extra_args: Additional CLI arguments to pass to claude
        """
        self.cwd = cwd
        self.resume_session_id = resume_session_id
        self.allowed_tools = allowed_tools
        self.extra_args = extra_args or []

        self.process: Optional[asyncio.subprocess.Process] = None
        self.session_id: Optional[str] = None
        self._stdout_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._running = False

    async def start(self) -> bool:
        """Start the Claude subprocess.

        Returns True if started successfully, False otherwise.
        """
        if self.process is not None:
            log("ClaudeProcess already started")
            return False

        # Build command
        # -p enables print mode (required for multi-turn stream-json)
        # --verbose is required for stream-json output
        cmd = [
            "claude",
            "-p",
            "--verbose",
            "--output-format", "stream-json",
            "--input-format", "stream-json",
        ]

        # Add resume flag
        if self.resume_session_id:
            cmd.extend(["--resume", self.resume_session_id])

        # Add allowed tools
        if self.allowed_tools:
            cmd.extend(["--allowedTools", ",".join(self.allowed_tools)])

        # Add extra args
        cmd.extend(self.extra_args)

        log(f"Starting Claude: {' '.join(cmd)}")

        try:
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
                preexec_fn=_set_pdeathsig,
            )
            self._running = True

            # Start stdout and stderr reader tasks
            self._stdout_task = asyncio.create_task(self._read_stdout())
            self._stderr_task = asyncio.create_task(self._read_stderr())

            log(f"Claude subprocess started (pid={self.process.pid})")
            return True

        except Exception as e:
            log(f"Failed to start Claude: {e}")
            return False

    async def _read_stdout(self):
        """Read and parse JSONL from stdout, emit events to queue."""
        if not self.process or not self.process.stdout:
            return

        try:
            while self._running:
                line_bytes = await self.process.stdout.readline()
                if not line_bytes:
                    # EOF - process terminated
                    log("Claude process stdout closed")
                    break

                line = line_bytes.decode('utf-8').strip()
                if not line:
                    continue

                # Parse JSONL
                try:
                    event = json.loads(line)
                    await self._process_event(event)
                except json.JSONDecodeError as e:
                    log(f"Failed to parse JSON: {e} - line: {line[:100]}")
                    continue

        except asyncio.CancelledError:
            log("stdout reader cancelled")
        except Exception as e:
            log(f"Error reading stdout: {e}")
        finally:
            # Signal end of stream
            await self._event_queue.put(None)

    async def _read_stderr(self):
        """Read stderr and log it."""
        if not self.process or not self.process.stderr:
            return

        try:
            while self._running:
                line_bytes = await self.process.stderr.readline()
                if not line_bytes:
                    break

                line = line_bytes.decode('utf-8').strip()
                if line:
                    log(f"Claude stderr: {line}")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            log(f"Error reading stderr: {e}")

    async def _process_event(self, event: dict):
        """Process a single event and add typed objects to queue."""
        event_type = event.get("type")

        if event_type == "system":
            subtype = event.get("subtype")
            if subtype == "init":
                # System init event
                init = SystemInit(
                    session_id=event.get("session_id", ""),
                    tools=event.get("tools", []),
                    model=event.get("model", ""),
                    raw=event,
                )
                self.session_id = init.session_id
                await self._event_queue.put(init)
                log(f"Session initialized: {self.session_id}")

        elif event_type == "assistant":
            # Assistant message event
            message = event.get("message", {})
            msg = AssistantMessage(
                content=message.get("content", []),
                model=message.get("model", ""),
                msg_id=message.get("id", ""),
                raw=event,
            )
            await self._event_queue.put(msg)

        elif event_type == "user":
            # User message (echo from stdin)
            message = event.get("message", {})
            msg = UserMessage(
                content=message.get("content", []),
                raw=event,
            )
            await self._event_queue.put(msg)

        elif event_type == "result":
            # Session result event
            subtype = event.get("subtype", "")
            result = SessionResult(
                success=(subtype == "success"),
                result=event.get("result", ""),
                cost=event.get("total_cost_usd", 0.0),
                turns=event.get("turns", 0),
                raw=event,
            )
            await self._event_queue.put(result)
            log(f"Session result: success={result.success}, cost=${result.cost:.4f}")

        else:
            # Log unhandled event types for debugging
            log(f"Unhandled event type: {event_type} - {event}")

    async def send_message(self, text: str) -> bool:
        """Send a user message to Claude.

        Args:
            text: Message text to send

        Returns True if sent successfully, False otherwise.
        """
        if not self.process or not self.process.stdin:
            log("Cannot send message: process not running")
            return False

        # Create user message in stream-json format
        message = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": text,
                    }
                ]
            }
        }

        try:
            # Write JSONL to stdin
            line = json.dumps(message) + "\n"
            log(f"ClaudeProcess.send_message: writing to stdin: {line}")
            self.process.stdin.write(line.encode('utf-8'))
            await self.process.stdin.drain()
            log(f"ClaudeProcess.send_message: success")
            return True
        except Exception as e:
            log(f"ClaudeProcess.send_message: failed: {e}")
            return False

    async def events(self) -> AsyncIterator[SystemInit | AssistantMessage | UserMessage | SessionResult]:
        """Async iterator that yields typed events from Claude.

        Yields events until process terminates or is stopped.
        """
        while True:
            event = await self._event_queue.get()
            if event is None:
                # End of stream
                break
            yield event

    async def stop(self, timeout: float = 5.0) -> bool:
        """Stop the Claude subprocess gracefully.

        Alias for terminate() for interface compatibility.
        """
        return await self.terminate(timeout)

    async def terminate(self, timeout: float = 5.0) -> bool:
        """Terminate the Claude subprocess.

        Args:
            timeout: How long to wait for graceful shutdown before killing

        Returns True if terminated successfully.
        """
        if not self.process:
            return True

        self._running = False

        try:
            # Close stdin to signal end of input
            if self.process.stdin:
                self.process.stdin.close()
                await self.process.stdin.wait_closed()

            # Wait for process to exit
            try:
                await asyncio.wait_for(self.process.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                log("Claude didn't exit gracefully, killing")
                self.process.kill()
                await self.process.wait()

            # Cancel reader tasks
            if self._stdout_task:
                self._stdout_task.cancel()
                try:
                    await self._stdout_task
                except asyncio.CancelledError:
                    pass

            if self._stderr_task:
                self._stderr_task.cancel()
                try:
                    await self._stderr_task
                except asyncio.CancelledError:
                    pass

            log(f"Claude subprocess terminated (pid={self.process.pid})")
            self.process = None
            return True

        except Exception as e:
            log(f"Error terminating Claude: {e}")
            return False

    @property
    def is_running(self) -> bool:
        """Check if the subprocess is running."""
        return self.process is not None and self.process.returncode is None

    @property
    def pid(self) -> Optional[int]:
        """Get the process ID of the subprocess."""
        return self.process.pid if self.process else None

    async def wait(self) -> Optional[int]:
        """Wait for process to exit and return exit code."""
        if not self.process:
            return None
        return await self.process.wait()


# Helper functions for extracting content from events

def extract_tool_uses(message: AssistantMessage) -> list[ToolUse]:
    """Extract all tool_use blocks from an assistant message.

    Args:
        message: AssistantMessage event

    Returns list of ToolUse objects.
    """
    tools = []
    for block in message.content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            tool = ToolUse(
                id=block.get("id", ""),
                name=block.get("name", ""),
                input=block.get("input", {}),
                raw=block,
            )
            tools.append(tool)
    return tools


def extract_text(message: AssistantMessage) -> str:
    """Extract text content from an assistant message.

    Args:
        message: AssistantMessage event

    Returns concatenated text from all text blocks.
    """
    texts = []
    for block in message.content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "")
            if text:
                texts.append(text)
    return "\n".join(texts)


def has_thinking(message: AssistantMessage) -> bool:
    """Check if message contains thinking blocks.

    Args:
        message: AssistantMessage event

    Returns True if message has thinking blocks.
    """
    for block in message.content:
        if isinstance(block, dict) and block.get("type") == "thinking":
            return True
    return False
