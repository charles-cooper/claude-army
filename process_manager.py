"""ProcessManager - orchestrates multiple ClaudeProcess instances.

Manages a pool of Claude subprocess instances, routing events and persisting session state.
"""

import asyncio
from typing import AsyncIterator, Protocol

from telegram_utils import log
from registry import get_registry


class Event(Protocol):
    """Base protocol for Claude process events."""
    type: str


class ClaudeProcess(Protocol):
    """Protocol defining the ClaudeProcess interface.

    Actual implementation in claude_process.py.
    """

    async def start(self) -> str:
        """Start the Claude subprocess.

        Returns session_id from init event.
        """
        ...

    async def send_message(self, message: str) -> None:
        """Send a user message to Claude."""
        ...

    async def stop(self) -> None:
        """Stop the Claude subprocess gracefully."""
        ...

    async def events(self) -> AsyncIterator[Event]:
        """Async iterator yielding events from the subprocess."""
        ...

    @property
    def session_id(self) -> str | None:
        """The Claude session ID (available after start)."""
        ...

    @property
    def is_running(self) -> bool:
        """Check if the subprocess is running."""
        ...

    @property
    def pid(self) -> int | None:
        """The subprocess PID (available after start)."""
        ...


class ProcessManager:
    """Manages multiple ClaudeProcess instances.

    Responsibilities:
    - Spawn/resume processes with proper configuration
    - Route messages to specific processes
    - Multiplex events from all processes
    - Persist session IDs to registry
    - Handle process crashes/restarts
    """

    def __init__(self):
        self.processes: dict[str, ClaudeProcess] = {}
        self._event_tasks: dict[str, asyncio.Task] = {}
        self._event_queue: asyncio.Queue[tuple[str, Event]] = asyncio.Queue()
        self._shutdown = False

    async def spawn_process(
        self,
        task_name: str,
        cwd: str,
        prompt: str,
        allowed_tools: list[str] | None = None
    ) -> ClaudeProcess:
        """Spawn a new Claude process for a task.

        Args:
            task_name: Unique task identifier
            cwd: Working directory for the process
            prompt: Initial prompt to send to Claude
            allowed_tools: Optional list of allowed tool names (for auto-allow)

        Returns:
            The spawned ClaudeProcess instance

        Raises:
            ValueError: If task_name already exists
            RuntimeError: If process fails to start
        """
        if task_name in self.processes:
            raise ValueError(f"Process already exists: {task_name}")

        # Import here to avoid circular dependency
        from claude_process import ClaudeProcess as ClaudeProcessImpl

        # Create process instance
        process = ClaudeProcessImpl(
            cwd=cwd,
            allowed_tools=allowed_tools
        )

        # Start subprocess and get session_id
        session_id = await process.start()

        # Persist session_id and pid to registry
        registry = get_registry()
        task_data = registry.get_task(task_name)
        if task_data:
            task_data["session_id"] = session_id
            task_data["pid"] = process.pid
            registry.add_task(task_name, task_data)

        # Store process
        self.processes[task_name] = process

        # Start event monitoring task
        self._start_event_task(task_name, process)

        # Send initial prompt
        await process.send_message(prompt)

        log(f"Spawned process: {task_name} (session={session_id})")
        return process

    async def resume_process(
        self,
        task_name: str,
        cwd: str,
        session_id: str,
        allowed_tools: list[str] | None = None
    ) -> ClaudeProcess:
        """Resume an existing Claude session.

        Args:
            task_name: Unique task identifier
            cwd: Working directory for the process
            session_id: Previous session ID to resume
            allowed_tools: Optional list of allowed tool names

        Returns:
            The resumed ClaudeProcess instance

        Raises:
            ValueError: If task_name already exists
            RuntimeError: If process fails to resume
        """
        if task_name in self.processes:
            raise ValueError(f"Process already exists: {task_name}")

        from claude_process import ClaudeProcess as ClaudeProcessImpl

        # Create process with resume flag
        process = ClaudeProcessImpl(
            cwd=cwd,
            resume_session_id=session_id,
            allowed_tools=allowed_tools
        )

        # Start subprocess (will use --resume)
        resumed_session_id = await process.start()
        assert resumed_session_id == session_id, "Session ID mismatch on resume"

        # Persist updated pid to registry
        registry = get_registry()
        task_data = registry.get_task(task_name)
        if task_data:
            task_data["pid"] = process.pid
            registry.add_task(task_name, task_data)

        # Store process
        self.processes[task_name] = process

        # Start event monitoring
        self._start_event_task(task_name, process)

        log(f"Resumed process: {task_name} (session={session_id})")
        return process

    def register_process(
        self,
        task_name: str,
        process: ClaudeProcess,
        start_events: bool = True
    ) -> None:
        """Register an externally-started process.

        Use this when you need fine-grained control over process startup
        (e.g., draining init turn before starting event monitoring).

        Args:
            task_name: Unique task identifier
            process: Already-started ClaudeProcess instance
            start_events: If True, immediately start event monitoring.
                         If False, call start_event_monitoring() later.

        Raises:
            ValueError: If task_name already exists
        """
        if task_name in self.processes:
            raise ValueError(f"Process already exists: {task_name}")

        self.processes[task_name] = process
        if start_events:
            self._start_event_task(task_name, process)

    def start_event_monitoring(self, task_name: str) -> None:
        """Start event monitoring for a registered process.

        Call this after register_process(start_events=False) when ready
        to begin receiving events.

        Args:
            task_name: Task identifier of registered process

        Raises:
            KeyError: If task_name not registered
            ValueError: If event monitoring already started
        """
        if task_name not in self.processes:
            raise KeyError(f"Process not registered: {task_name}")
        if task_name in self._event_tasks:
            raise ValueError(f"Event monitoring already started: {task_name}")

        self._start_event_task(task_name, self.processes[task_name])

    async def send_to_process(
        self,
        task_name: str,
        message: str,
        cwd: str | None = None,
        allowed_tools: list[str] | None = None,
    ) -> bool:
        """Send a message to a specific process, resurrecting if needed.

        If process exists and is running, sends message directly.
        If process doesn't exist or isn't running, attempts resurrection via resume.

        Args:
            task_name: Target task identifier
            message: Message to send
            cwd: Working directory (required for resurrection)
            allowed_tools: Optional list of allowed tools (for resurrection)

        Returns:
            True if message sent successfully, False otherwise

        Raises:
            KeyError: If task_name doesn't exist and no cwd provided for resurrection
        """
        log(f"send_to_process: task_name={task_name}, message={message}")
        process = self.processes.get(task_name)
        log(f"send_to_process: process={process}, is_running={process.is_running if process else None}")

        # Check if process exists and is running
        if process is not None and process.is_running:
            log(f"send_to_process: sending to existing process")
            result = await process.send_message(message)
            log(f"send_to_process: send_message result={result}")
            return result

        # Process dead or missing - try resurrection
        if process is not None:
            # Clean up dead process
            log(f"Process {task_name} not running, resurrecting...")
            if task_name in self._event_tasks:
                self._event_tasks[task_name].cancel()
                try:
                    await self._event_tasks[task_name]
                except asyncio.CancelledError:
                    pass
                del self._event_tasks[task_name]
            del self.processes[task_name]

        # Get session_id from registry for resume
        registry = get_registry()
        task_data = registry.get_task(task_name)

        if not task_data:
            raise KeyError(f"Process not found and no registry entry: {task_name}")

        session_id = task_data.get("session_id")
        task_cwd = cwd or task_data.get("path")

        if not task_cwd:
            raise KeyError(f"Cannot resurrect {task_name}: no cwd available")

        # Import here to avoid circular dependency
        from claude_process import ClaudeProcess as ClaudeProcessImpl

        # Create process with resume flag if we have a session
        process = ClaudeProcessImpl(
            cwd=task_cwd,
            resume_session_id=session_id,
            allowed_tools=allowed_tools
        )

        # Start subprocess
        started = await process.start()
        if not started:
            log(f"Failed to resurrect process: {task_name}")
            return False

        # Update registry with new pid
        task_data["pid"] = process.pid
        if process.session_id:
            task_data["session_id"] = process.session_id
        registry.add_task(task_name, task_data)

        # Store process and start event monitoring
        self.processes[task_name] = process
        self._start_event_task(task_name, process)

        log(f"Resurrected process: {task_name} (session={process.session_id})")

        # Send the message
        return await process.send_message(message)

    async def stop_process(self, task_name: str) -> None:
        """Stop a specific process.

        Args:
            task_name: Task identifier to stop

        Raises:
            KeyError: If task_name doesn't exist
        """
        if task_name not in self.processes:
            raise KeyError(f"Process not found: {task_name}")

        process = self.processes[task_name]

        # Stop the process
        await process.stop()

        # Cancel event monitoring task
        if task_name in self._event_tasks:
            self._event_tasks[task_name].cancel()
            try:
                await self._event_tasks[task_name]
            except asyncio.CancelledError:
                pass
            del self._event_tasks[task_name]

        # Remove from processes dict
        del self.processes[task_name]

        log(f"Stopped process: {task_name}")

    async def stop_all(self) -> None:
        """Stop all processes gracefully."""
        self._shutdown = True

        # Stop all processes
        tasks = []
        for task_name in list(self.processes.keys()):
            tasks.append(self.stop_process(task_name))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        log("All processes stopped")

    async def all_events(self) -> AsyncIterator[tuple[str, Event]]:
        """Yield events from all processes.

        Yields:
            Tuple of (task_name, event) for each event from any process

        This is the main event loop for the daemon - it multiplexes events
        from all running processes into a single stream.
        """
        while not self._shutdown:
            try:
                # Wait for next event from any process
                task_name, event = await asyncio.wait_for(
                    self._event_queue.get(),
                    timeout=1.0
                )
                yield task_name, event
            except asyncio.TimeoutError:
                # Check for shutdown periodically
                continue
            except Exception as e:
                log(f"Error in event stream: {e}")
                break

    def _start_event_task(self, task_name: str, process: ClaudeProcess) -> None:
        """Start background task to monitor process events.

        This task reads from process.events() and forwards to the shared queue.
        """
        async def event_monitor():
            """Monitor events from a single process."""
            try:
                async for event in process.events():
                    await self._event_queue.put((task_name, event))
            except asyncio.CancelledError:
                log(f"Event monitor cancelled: {task_name}")
                raise
            except Exception as e:
                log(f"Event monitor error for {task_name}: {e}")
                # Process crashed - notify via special event
                await self._event_queue.put((task_name, {
                    "type": "error",
                    "error": str(e)
                }))

                # Remove crashed process
                if task_name in self.processes:
                    del self.processes[task_name]

        task = asyncio.create_task(event_monitor())
        self._event_tasks[task_name] = task

    def get_process(self, task_name: str) -> ClaudeProcess | None:
        """Get a process by task name.

        Args:
            task_name: Task identifier

        Returns:
            ClaudeProcess instance or None if not found
        """
        return self.processes.get(task_name)

    def get_all_tasks(self) -> list[str]:
        """Get list of all active task names."""
        return list(self.processes.keys())

    def is_running(self, task_name: str) -> bool:
        """Check if a process is running.

        Args:
            task_name: Task identifier

        Returns:
            True if process exists and is running
        """
        process = self.processes.get(task_name)
        return process is not None and process.is_running

    async def cleanup_crashed_processes(self) -> list[str]:
        """Clean up crashed processes from registry.

        Checks all tasks in registry with PIDs to see if the process is still alive.
        Removes stale PID/session_id entries from crashed processes.

        Returns:
            List of task names that had crashed processes cleaned up
        """
        import os
        import signal

        registry = get_registry()
        cleaned = []

        for task_name, task_data in registry.get_all_tasks():
            # Skip tasks we're actively managing
            if task_name in self.processes:
                continue

            pid = task_data.get("pid")
            if not pid:
                continue

            # Check if process is alive
            try:
                # Send signal 0 to check if process exists
                os.kill(pid, 0)
                # Process exists, not crashed
            except OSError:
                # Process doesn't exist - crashed
                log(f"Cleaning up crashed process: {task_name} (pid={pid})")
                task_data.pop("pid", None)
                # Don't remove session_id - we can still resume
                registry.add_task(task_name, task_data)
                cleaned.append(task_name)

        return cleaned
