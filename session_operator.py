"""Operator Claude session management using ProcessManager.

Manages the operator Claude process via ClaudeProcess/ProcessManager
instead of tmux sessions.
"""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from telegram_utils import log
from registry import get_config, get_registry

if TYPE_CHECKING:
    from process_manager import ProcessManager
    from claude_process import ClaudeProcess

# Session naming (kept for registry compatibility)
OPERATOR_TASK_NAME = "operator"
OPERATOR_DIR = Path(__file__).parent / "operator"

# Global ProcessManager instance (set by daemon on startup)
_process_manager: "ProcessManager | None" = None


def set_process_manager(pm: "ProcessManager") -> None:
    """Set the global ProcessManager instance.

    Called by daemon on startup to share the ProcessManager.
    """
    global _process_manager
    _process_manager = pm


def get_process_manager() -> "ProcessManager | None":
    """Get the global ProcessManager instance."""
    return _process_manager


def session_exists(task_name: str = OPERATOR_TASK_NAME) -> bool:
    """Check if the operator process is running.

    Args:
        task_name: Task identifier (default: "operator")

    Returns:
        True if process exists and is running
    """
    pm = get_process_manager()
    if pm is None:
        return False
    return pm.is_running(task_name)


def get_process(task_name: str = OPERATOR_TASK_NAME) -> "ClaudeProcess | None":
    """Get the ClaudeProcess instance for a task.

    Args:
        task_name: Task identifier (default: "operator")

    Returns:
        ClaudeProcess instance or None if not found
    """
    pm = get_process_manager()
    if pm is None:
        return None
    return pm.get_process(task_name)


def get_process_id(task_name: str = OPERATOR_TASK_NAME) -> str | None:
    """Get the process ID for a task.

    This replaces get_pane_id() - returns a process identifier
    that can be stored in registry and used for routing.

    Args:
        task_name: Task identifier (default: "operator")

    Returns:
        Process identifier string (task_name) or None if not running
    """
    if session_exists(task_name):
        return task_name
    return None


async def start_operator_session_async() -> str | None:
    """Start the Operator Claude session asynchronously.

    Returns:
        Process identifier on success, None on failure
    """
    pm = get_process_manager()
    if pm is None:
        log("ProcessManager not initialized")
        return None

    if session_exists():
        log("Operator session already exists")
        return OPERATOR_TASK_NAME

    log("Starting Operator Claude session...")

    # Ensure operator directory exists
    OPERATOR_DIR.mkdir(parents=True, exist_ok=True)

    # Create symlinks to specs if they don't exist
    symlinks = {
        "SPEC.md": "../SPEC.md",
        "AGENTS.md": "../OPERATOR_AGENTS.template.md",  # Operator's instructions
        "CLAUDE.md": "AGENTS.md",  # Claude reads CLAUDE.md by default
    }
    for name, target in symlinks.items():
        link = OPERATOR_DIR / name
        if not link.exists():
            try:
                link.symlink_to(target)
            except OSError as e:
                log(f"Failed to create symlink {name}: {e}")

    # Check for existing session to resume
    registry = get_registry()
    operator_data = registry.get_task(OPERATOR_TASK_NAME)
    session_id = operator_data.get("session_id") if operator_data else None

    try:
        from claude_process import ClaudeProcess

        if session_id:
            # Resume existing session
            log(f"Resuming operator session: {session_id}")
            process = ClaudeProcess(
                cwd=str(OPERATOR_DIR),
                resume_session_id=session_id
            )
        else:
            # Start new session
            process = ClaudeProcess(cwd=str(OPERATOR_DIR))

        started = await process.start()
        if not started:
            log("Failed to start operator process")
            return None

        # Register with ProcessManager
        pm.processes[OPERATOR_TASK_NAME] = process
        pm._start_event_task(OPERATOR_TASK_NAME, process)

        # Wait for session_id from init event
        await asyncio.sleep(0.5)

        # Send initial prompt for new sessions
        if not session_id:
            prompt = (
                "You are the Operator Claude for claude-army. "
                "You coordinate tasks, spawn workers, and handle high-level planning. "
                "Use the tools available to manage the task registry and spawn new workers as needed."
            )
            await process.send_message(prompt)

        # Update registry
        config = get_config()
        task_data = {
            "type": "operator",
            "path": str(OPERATOR_DIR),
            "topic_id": config.general_topic_id,
            "status": "active",
            "session_id": process.session_id,
            "pid": process.pid
        }
        registry.add_task(OPERATOR_TASK_NAME, task_data)

        log(f"Operator session started: {OPERATOR_TASK_NAME} (session={process.session_id})")
        return OPERATOR_TASK_NAME

    except Exception as e:
        log(f"Failed to start operator: {e}")
        return None


def start_operator_session() -> str | None:
    """Start the Operator Claude session (sync wrapper).

    Returns:
        Process identifier on success, None on failure
    """
    try:
        loop = asyncio.get_running_loop()
        # We're in an async context, create task
        future = asyncio.ensure_future(start_operator_session_async())
        # Can't block here, return None and let caller handle async
        log("start_operator_session called from async context - use start_operator_session_async instead")
        return None
    except RuntimeError:
        # No running loop, create one
        return asyncio.run(start_operator_session_async())


async def stop_operator_session_async() -> bool:
    """Stop the Operator Claude session asynchronously."""
    pm = get_process_manager()
    if pm is None:
        return True

    if not session_exists():
        return True

    try:
        await pm.stop_process(OPERATOR_TASK_NAME)
        log("Operator session stopped")
        return True
    except Exception as e:
        log(f"Failed to stop operator: {e}")
        return False


def stop_operator_session() -> bool:
    """Stop the Operator Claude session (sync wrapper)."""
    try:
        loop = asyncio.get_running_loop()
        log("stop_operator_session called from async context - use stop_operator_session_async instead")
        return False
    except RuntimeError:
        return asyncio.run(stop_operator_session_async())


async def send_to_operator_async(text: str) -> bool:
    """Send text to the Operator Claude process asynchronously.

    Args:
        text: Message text to send

    Returns:
        True on success, False on failure
    """
    config = get_config()

    if not config.is_configured():
        log("Operator not configured")
        return False

    pm = get_process_manager()
    if pm is None:
        log("ProcessManager not initialized")
        return False

    # Check if operator is running, resurrect if needed
    if not session_exists():
        log("Operator not running, starting...")
        result = await start_operator_session_async()
        if not result:
            log("Failed to start operator")
            return False

    try:
        await pm.send_to_process(OPERATOR_TASK_NAME, text)
        log(f"send_to_operator: text_len={len(text)}")
        return True
    except Exception as e:
        log(f"Failed to send to operator: {e}")
        return False


def send_to_operator(text: str) -> bool:
    """Send text to the Operator Claude process (sync wrapper).

    Args:
        text: Message text to send

    Returns:
        True on success, False on failure
    """
    try:
        loop = asyncio.get_running_loop()
        # We're in an async context - need to use create_task
        # But we can't return the result synchronously
        # For now, schedule and return True optimistically
        asyncio.create_task(send_to_operator_async(text))
        return True
    except RuntimeError:
        # No running loop, create one
        return asyncio.run(send_to_operator_async(text))


async def check_and_resurrect_operator_async() -> str | None:
    """Check if operator is running, resurrect if needed.

    Returns:
        Process identifier or None
    """
    config = get_config()

    if not config.is_configured():
        return None

    if session_exists():
        return OPERATOR_TASK_NAME

    log("Operator session missing, resurrecting...")
    return await start_operator_session_async()


def check_and_resurrect_operator() -> str | None:
    """Check if operator is running, resurrect if needed (sync wrapper)."""
    try:
        loop = asyncio.get_running_loop()
        log("check_and_resurrect_operator called from async context - use async version")
        return OPERATOR_TASK_NAME if session_exists() else None
    except RuntimeError:
        return asyncio.run(check_and_resurrect_operator_async())


def is_operator_process(process_id: str) -> bool:
    """Check if a process identifier is the operator.

    Args:
        process_id: Process identifier to check

    Returns:
        True if this is the operator process
    """
    return process_id == OPERATOR_TASK_NAME


# Legacy alias for compatibility with code checking pane identity
def is_operator_pane(pane: str) -> bool:
    """Check if a pane/process identifier is the operator.

    This is a compatibility alias - the new architecture uses
    process identifiers (task names) instead of tmux panes.

    Args:
        pane: Pane or process identifier to check

    Returns:
        True if this is the operator
    """
    # In new architecture, pane is actually task_name
    return pane == OPERATOR_TASK_NAME
