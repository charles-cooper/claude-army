"""Worker Claude session management using ProcessManager.

Supports two task types:
- Worktree: isolated git worktree, cleanup deletes directory
- Session: existing directory, cleanup preserves directory
"""

import asyncio
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from telegram_utils import (
    log, create_forum_topic, close_forum_topic, delete_forum_topic,
    TopicCreationError, send_to_topic, escape_markdown_v2,
)
from registry import (
    get_config, get_registry, write_marker_file, read_marker_file,
    remove_marker_file, write_marker_file_pending
)

if TYPE_CHECKING:
    from process_manager import ProcessManager
    from claude_process import ClaudeProcess


SETUP_HOOK_NAME = ".claude-army-setup.sh"
DISCOVER_TRIGGER = Path("/tmp/claude-army-discover")


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


def trigger_daemon_discovery():
    """Signal the daemon to discover new transcripts immediately."""
    DISCOVER_TRIGGER.touch()


CLAUDE_LOCAL_TEMPLATE = """# Task: {task_name}

{description}

## Instructions

- Update this file with learnings as you work. These persist across sessions.
- Check TODO.local.md periodically for new tasks from the user.
- When you find new todos, add them to your todo stack (TodoWrite).
- Mark todos done in TODO.local.md after completing them.
- Periodically clean up TODO.local.md by removing completed items.

## Learnings

<!-- Add your learnings below -->
"""


def create_claude_local_md(directory: str, task_name: str, description: str = ""):
    """Create CLAUDE.local.md in task directory if it doesn't exist."""
    path = Path(directory) / "CLAUDE.local.md"
    if path.exists():
        return  # Don't overwrite existing file (preserves learnings)

    content = CLAUDE_LOCAL_TEMPLATE.format(
        task_name=task_name,
        description=description or "(No description provided)"
    )
    path.write_text(content)
    log(f"Created CLAUDE.local.md in {directory}")


def append_todo(directory: str, item: str) -> bool:
    """Append a todo item to TODO.local.md in the task directory.

    Creates the file with header if it doesn't exist.
    Returns True on success, False on failure.
    """
    path = Path(directory) / "TODO.local.md"
    try:
        if not path.exists():
            path.write_text("# TODO\n\n")
        with open(path, "a") as f:
            f.write(f"- [ ] {item}\n")
        log(f"Added todo to {directory}: {item[:50]}...")
        return True
    except Exception as e:
        log(f"Failed to append todo: {e}")
        return False


def _get_bot_token() -> str:
    """Get bot token from telegram config."""
    tg_config = json.loads((Path.home() / "telegram.json").read_text())
    return tg_config["bot_token"]


def update_topic_status(topic_id: int, task_name: str, status: str):
    """Update topic name to reflect task status."""
    # No status prefixes for now - topic name stays as task_name
    pass


def _create_task_topic_safely(
    directory: str,
    task_name: str,
    task_type: str,
    description: str,
    welcome_message: str,
    repo: str = None
) -> int:
    """Create Telegram topic with crash-safe marker pattern.

    Steps:
    1. Write pending marker (so recovery knows topic creation is in progress)
    2. Create Telegram topic
    3. Send welcome message to topic
    4. Complete marker with full metadata

    Returns topic_id. Raises TopicCreationError on failure.
    Caller is responsible for cleanup if this fails after step 1.
    """
    config = get_config()
    bot_token = _get_bot_token()

    # Step 1: Write pending marker
    write_marker_file_pending(directory, task_name)

    # Step 2: Create topic (may raise TopicCreationError)
    topic_result = create_forum_topic(bot_token, str(config.group_id), task_name)
    topic_id = topic_result.get("message_thread_id")

    # Step 3: Send welcome message
    send_to_topic(bot_token, str(config.group_id), topic_id, welcome_message)

    # Step 4: Complete marker with full metadata
    marker_data = {
        "name": task_name,
        "type": task_type,
        "description": description,
        "topic_id": topic_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if repo:
        marker_data["repo"] = repo
    write_marker_file(directory, marker_data)

    return topic_id


# ============ Worktree Operations ============

def get_worktree_path(repo_path: str, task_name: str) -> Path:
    """Get the worktree path for a task."""
    return Path(repo_path) / "trees" / task_name


def run_setup_hook(repo_path: str, task_name: str, worktree_path: Path) -> bool:
    """Run post-worktree setup hook if it exists."""
    hook_path = Path(repo_path) / SETUP_HOOK_NAME
    if not hook_path.exists():
        return True

    log(f"Running setup hook: {hook_path}")
    env = {
        **os.environ,
        "TASK_NAME": task_name,
        "REPO_PATH": repo_path,
        "WORKTREE_PATH": str(worktree_path)
    }

    result = subprocess.run(
        ["bash", str(hook_path)],
        cwd=str(worktree_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=60
    )

    if result.returncode != 0:
        log(f"Setup hook failed: {result.stderr}")
        return False

    log("Setup hook completed")
    return True


def create_worktree(repo_path: str, task_name: str, branch: str = None) -> Path | None:
    """Create a git worktree for a task. Returns worktree path on success."""
    worktree_path = get_worktree_path(repo_path, task_name)

    if worktree_path.exists():
        log(f"Worktree already exists: {worktree_path}")
        return worktree_path

    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    branch_arg = branch if branch else "HEAD"
    cmd = ["git", "-C", repo_path, "worktree", "add", "-b", task_name, str(worktree_path), branch_arg]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        # Try without creating new branch (if branch exists)
        cmd = ["git", "-C", repo_path, "worktree", "add", str(worktree_path), task_name]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            log(f"Failed to create worktree: {result.stderr}")
            return None

    log(f"Created worktree: {worktree_path}")
    run_setup_hook(repo_path, task_name, worktree_path)
    return worktree_path


def delete_worktree(repo_path: str, worktree_path: str) -> bool:
    """Delete a git worktree."""
    if not Path(worktree_path).exists():
        return True

    result = subprocess.run(
        ["git", "-C", repo_path, "worktree", "remove", "--force", worktree_path],
        capture_output=True, text=True
    )

    if result.returncode != 0:
        log(f"Failed to remove worktree: {result.stderr}")
        return False

    log(f"Deleted worktree: {worktree_path}")
    return True


# ============ Task Spawning ============

async def spawn_worktree_task_async(repo_path: str, task_name: str, description: str) -> dict | None:
    """Spawn a worktree task: create worktree, topic, marker, process.

    Returns task_data dict on success, None on failure.
    """
    config = get_config()
    if not config.is_configured():
        log("Not configured")
        return None

    pm = get_process_manager()
    if pm is None:
        log("ProcessManager not initialized")
        return None

    registry = get_registry()

    # Check for name collision
    if registry.get_task(task_name):
        log(f"Task already exists: {task_name}")
        return None

    # Create worktree
    worktree_path = create_worktree(repo_path, task_name)
    if not worktree_path:
        return None

    # Create topic with crash-safe pattern
    welcome = f"[rocket] *Task created*\n\n_{escape_markdown_v2(description)}_"
    try:
        topic_id = _create_task_topic_safely(
            directory=str(worktree_path),
            task_name=task_name,
            task_type="worktree",
            description=description,
            welcome_message=welcome,
            repo=repo_path
        )
    except TopicCreationError:
        delete_worktree(repo_path, str(worktree_path))
        raise

    # Update registry before spawning process
    task_data = {
        "type": "worktree",
        "path": str(worktree_path),
        "repo": repo_path,
        "topic_id": topic_id,
        "status": "active",
    }
    registry.add_task(task_name, task_data)

    # Create CLAUDE.local.md for the task
    create_claude_local_md(str(worktree_path), task_name, description)

    # Spawn Claude process with initial prompt
    confirm_prompt = (
        f"New task: {description}\n\n"
        "Please:\n"
        "1. Summarize what you understand the task to be\n"
        "2. Outline your planned approach\n"
        "3. Wait for user confirmation before starting work"
    )

    try:
        process = await pm.spawn_process(
            task_name=task_name,
            cwd=str(worktree_path),
            prompt=confirm_prompt,
        )
        # Update registry with session info
        task_data["session_id"] = process.session_id
        task_data["pid"] = process.pid
        registry.add_task(task_name, task_data)
    except Exception as e:
        log(f"Failed to spawn process: {e}")
        # Clean up on failure
        bot_token = _get_bot_token()
        close_forum_topic(bot_token, str(config.group_id), topic_id)
        delete_worktree(repo_path, str(worktree_path))
        registry.remove_task(task_name)
        return None

    trigger_daemon_discovery()
    log(f"Spawned worktree task: {task_name} at {worktree_path}")
    return task_data


def spawn_worktree_task(repo_path: str, task_name: str, description: str) -> dict | None:
    """Spawn a worktree task (sync wrapper).

    Returns task_data dict on success, None on failure.
    """
    try:
        loop = asyncio.get_running_loop()
        log("spawn_worktree_task called from async context - use spawn_worktree_task_async instead")
        return None
    except RuntimeError:
        return asyncio.run(spawn_worktree_task_async(repo_path, task_name, description))


async def spawn_session_async(directory: str, task_name: str, description: str) -> dict | None:
    """Spawn a session task in existing directory: create topic, marker, process.

    Returns task_data dict on success, None on failure.
    """
    config = get_config()
    if not config.is_configured():
        log("Not configured")
        return None

    pm = get_process_manager()
    if pm is None:
        log("ProcessManager not initialized")
        return None

    if not Path(directory).exists():
        log(f"Directory doesn't exist: {directory}")
        return None

    registry = get_registry()

    # Check for name collision
    if registry.get_task(task_name):
        log(f"Task already exists: {task_name}")
        return None

    # Check if task is already running in ProcessManager
    if pm.is_running(task_name):
        log(f"Process already running for task: {task_name}")
        return None

    # Create topic with crash-safe pattern
    welcome = f"[rocket] *Task created*\n\n_{escape_markdown_v2(description)}_"
    topic_id = _create_task_topic_safely(
        directory=directory,
        task_name=task_name,
        task_type="session",
        description=description,
        welcome_message=welcome
    )

    # Update registry
    task_data = {
        "type": "session",
        "path": directory,
        "topic_id": topic_id,
        "status": "active",
    }
    registry.add_task(task_name, task_data)

    # Create CLAUDE.local.md for the task
    create_claude_local_md(directory, task_name, description)

    # Spawn Claude process
    confirm_prompt = (
        f"New task: {description}\n\n"
        "Please:\n"
        "1. Summarize what you understand the task to be\n"
        "2. Outline your planned approach\n"
        "3. Wait for user confirmation before starting work"
    )

    try:
        process = await pm.spawn_process(
            task_name=task_name,
            cwd=directory,
            prompt=confirm_prompt,
        )
        # Update registry with session info
        task_data["session_id"] = process.session_id
        task_data["pid"] = process.pid
        registry.add_task(task_name, task_data)
    except Exception as e:
        log(f"Failed to spawn process: {e}")
        bot_token = _get_bot_token()
        close_forum_topic(bot_token, str(config.group_id), topic_id)
        registry.remove_task(task_name)
        return None

    trigger_daemon_discovery()
    log(f"Spawned session: {task_name} at {directory}")
    return task_data


def spawn_session(directory: str, task_name: str, description: str) -> dict | None:
    """Spawn a session task in existing directory (sync wrapper).

    Returns task_data dict on success, None on failure.
    """
    try:
        loop = asyncio.get_running_loop()
        log("spawn_session called from async context - use spawn_session_async instead")
        return None
    except RuntimeError:
        return asyncio.run(spawn_session_async(directory, task_name, description))


def register_existing_session(directory: str, task_name: str) -> dict | None:
    """Register an existing Claude session (auto-registration by daemon).

    Uses crash-safe topic creation pattern.

    Returns task_data dict on success, None if not configured/name collision/pending.
    Raises TopicCreationError if topic creation fails.
    """
    config = get_config()
    if not config.is_configured():
        return None

    registry = get_registry()

    # Check for name collision
    if registry.get_task(task_name):
        return None

    # Check for existing marker
    existing = read_marker_file(directory)
    if existing:
        if existing.get("topic_id"):
            # Already registered, just return existing data
            task_data = {
                "type": existing.get("type", "session"),
                "path": directory,
                "topic_id": existing["topic_id"],
                "status": "active",
            }
            registry.add_task(existing.get("name", task_name), task_data)
            log(f"Recovered from existing marker: {task_name}")
            return task_data
        if existing.get("pending_topic_name"):
            # Pending recovery in progress, skip
            log(f"Pending recovery for {directory}, skipping")
            return None

    # Create topic with crash-safe pattern
    welcome = escape_markdown_v2("[satellite] Session discovered")
    topic_id = _create_task_topic_safely(
        directory=directory,
        task_name=task_name,
        task_type="session",
        description="",
        welcome_message=welcome
    )

    # Update registry
    task_data = {
        "type": "session",
        "path": directory,
        "topic_id": topic_id,
        "status": "active",
    }
    registry.add_task(task_name, task_data)

    # Create CLAUDE.local.md for the task
    create_claude_local_md(directory, task_name)

    log(f"Registered existing session: {task_name} at {directory}")
    return task_data


# ============ Task Operations ============

async def stop_task_session_async(task_name: str) -> bool:
    """Stop the process for a task."""
    pm = get_process_manager()
    if pm is None:
        return True

    if not pm.is_running(task_name):
        return True

    try:
        await pm.stop_process(task_name)
        log(f"Stopped process: {task_name}")
        return True
    except Exception as e:
        log(f"Failed to stop process: {e}")
        return False


def stop_task_session(task_name: str) -> bool:
    """Stop the process for a task (sync wrapper)."""
    try:
        loop = asyncio.get_running_loop()
        asyncio.create_task(stop_task_session_async(task_name))
        return True
    except RuntimeError:
        return asyncio.run(stop_task_session_async(task_name))


async def pause_task_async(task_name: str) -> bool:
    """Pause a task (stop process, mark as paused)."""
    registry = get_registry()
    task_data = registry.get_task(task_name)
    if not task_data:
        return False

    topic_id = task_data.get("topic_id")
    path = task_data.get("path")

    # Stop process
    await stop_task_session_async(task_name)

    # Update marker
    marker = read_marker_file(path)
    if marker:
        marker["status"] = "paused"
        write_marker_file(path, marker)

    # Update registry - remove pid since process is stopped
    task_data["status"] = "paused"
    task_data.pop("pid", None)
    registry.add_task(task_name, task_data)

    # Update topic name
    if topic_id:
        update_topic_status(topic_id, task_name, "paused")

    log(f"Paused task: {task_name}")
    return True


def pause_task(task_name: str) -> bool:
    """Pause a task (sync wrapper)."""
    try:
        loop = asyncio.get_running_loop()
        asyncio.create_task(pause_task_async(task_name))
        return True
    except RuntimeError:
        return asyncio.run(pause_task_async(task_name))


async def resume_task_async(task_name: str) -> str | None:
    """Resume a paused task. Returns process identifier on success."""
    pm = get_process_manager()
    if pm is None:
        log("ProcessManager not initialized")
        return None

    registry = get_registry()
    task_data = registry.get_task(task_name)
    if not task_data:
        return None

    path = task_data.get("path")
    topic_id = task_data.get("topic_id")
    session_id = task_data.get("session_id")

    # Update marker
    marker = read_marker_file(path)
    if marker:
        marker["status"] = "active"
        write_marker_file(path, marker)

    # Check if already running
    if pm.is_running(task_name):
        log(f"Task already running: {task_name}")
        task_data["status"] = "active"
        registry.add_task(task_name, task_data)
        return task_name

    # Resume or spawn process
    try:
        if session_id:
            # Resume existing session
            process = await pm.resume_process(
                task_name=task_name,
                cwd=path,
                session_id=session_id,
            )
        else:
            # No session to resume - spawn new with description
            description = marker.get("description", task_name) if marker else task_name
            process = await pm.spawn_process(
                task_name=task_name,
                cwd=path,
                prompt=f"Resuming task: {description}",
            )
            task_data["session_id"] = process.session_id

        task_data["pid"] = process.pid
        task_data["status"] = "active"
        registry.add_task(task_name, task_data)

    except Exception as e:
        log(f"Failed to resume task {task_name}: {e}")
        return None

    # Update topic name
    if topic_id:
        update_topic_status(topic_id, task_name, "active")

    trigger_daemon_discovery()
    log(f"Resumed task: {task_name}")
    return task_name


def resume_task(task_name: str) -> str | None:
    """Resume a paused task (sync wrapper). Returns process identifier on success."""
    try:
        loop = asyncio.get_running_loop()
        log("resume_task called from async context - use resume_task_async instead")
        return None
    except RuntimeError:
        return asyncio.run(resume_task_async(task_name))


async def cleanup_task_async(task_name: str, archive_only: bool = False) -> bool:
    """Clean up a task. Behavior differs by type:
    - worktree: delete directory + delete topic
    - session: remove marker + delete topic (preserve directory)

    If archive_only=True, close topic instead of deleting.
    """
    registry = get_registry()
    task_data = registry.get_task(task_name)
    if not task_data:
        return False

    task_type = task_data.get("type", "session")
    path = task_data.get("path")
    topic_id = task_data.get("topic_id")
    repo = task_data.get("repo")

    # Stop process
    await stop_task_session_async(task_name)

    # Delete or close topic
    if topic_id:
        try:
            bot_token = _get_bot_token()
            config = get_config()
            if archive_only:
                update_topic_status(topic_id, task_name, "done")
                close_forum_topic(bot_token, str(config.group_id), topic_id)
            else:
                delete_forum_topic(bot_token, str(config.group_id), topic_id)
        except Exception as e:
            log(f"Failed to {'close' if archive_only else 'delete'} topic: {e}")

    # Type-specific cleanup
    if task_type == "worktree" and repo and path:
        delete_worktree(repo, path)
    elif task_type == "session" and path:
        remove_marker_file(path)

    # Remove from registry
    registry.remove_task(task_name)

    log(f"Cleaned up task: {task_name}")
    return True


def cleanup_task(task_name: str, archive_only: bool = False) -> bool:
    """Clean up a task (sync wrapper)."""
    try:
        loop = asyncio.get_running_loop()
        asyncio.create_task(cleanup_task_async(task_name, archive_only))
        return True
    except RuntimeError:
        return asyncio.run(cleanup_task_async(task_name, archive_only))


# ============ Worker Communication ============

def get_worker_process_for_topic(topic_id: int) -> str | None:
    """Get the worker process identifier for a topic ID."""
    registry = get_registry()
    result = registry.find_task_by_topic(topic_id)
    if result:
        name, task_data = result
        # Return task_name as process identifier if active
        if task_data.get("status") != "paused":
            return name
    return None


# Legacy alias for compatibility
def get_worker_pane_for_topic(topic_id: int) -> str | None:
    """Get the worker pane/process for a topic ID.

    This is a compatibility alias - returns task_name as identifier.
    """
    return get_worker_process_for_topic(topic_id)


async def send_to_worker_async(topic_id: int, text: str) -> bool:
    """Send text to the worker handling a topic. Resurrects if needed."""
    pm = get_process_manager()
    if pm is None:
        log("ProcessManager not initialized")
        return False

    registry = get_registry()
    config = get_config()
    result = registry.find_task_by_topic(topic_id)
    if not result:
        log(f"No task for topic {topic_id}")
        return False

    task_name, task_data = result
    path = task_data.get("path")

    # Check if process is running
    if pm.is_running(task_name):
        try:
            await pm.send_to_process(task_name, text)
            log(f"send_to_worker: task={task_name}, text_len={len(text)}")
            return True
        except Exception as e:
            log(f"Failed to send to {task_name}: {e}")
            # Fall through to resurrection

    # Process not running - check if paused
    if task_data.get("status") == "paused":
        log(f"Task {task_name} is paused, not resurrecting")
        return False

    # Notify user that we're recreating the session
    bot_token = _get_bot_token()
    if bot_token and config.group_id:
        send_to_topic(bot_token, str(config.group_id), topic_id,
                      escape_markdown_v2(f"[warning] Session not found, recreating {task_name}..."))

    # Resume the task
    process_id = await resume_task_async(task_name)
    if not process_id:
        log(f"Failed to resume task for topic {topic_id}")
        if bot_token and config.group_id:
            send_to_topic(bot_token, str(config.group_id), topic_id,
                          escape_markdown_v2(f"[x] Failed to recreate {task_name}"))
        return False

    # Wait a moment for process to be ready
    await asyncio.sleep(0.5)

    # Now send the message
    try:
        await pm.send_to_process(task_name, text)
        if bot_token and config.group_id:
            send_to_topic(bot_token, str(config.group_id), topic_id,
                          escape_markdown_v2(f"[checkmark] Session recreated, forwarding message"))
        return True
    except Exception as e:
        log(f"Failed to send to resurrected {task_name}: {e}")
        if bot_token and config.group_id:
            send_to_topic(bot_token, str(config.group_id), topic_id,
                          escape_markdown_v2(f"[warning] Session recreated but message failed"))
        return False


def send_to_worker(topic_id: int, text: str) -> bool:
    """Send text to the worker handling a topic (sync wrapper)."""
    try:
        loop = asyncio.get_running_loop()
        asyncio.create_task(send_to_worker_async(topic_id, text))
        return True
    except RuntimeError:
        return asyncio.run(send_to_worker_async(topic_id, text))


def is_worker_process(task_name: str) -> tuple[bool, int | None]:
    """Check if a task is a worker process. Returns (is_worker, topic_id)."""
    registry = get_registry()
    task_data = registry.get_task(task_name)
    if task_data:
        return True, task_data.get("topic_id")
    return False, None


def is_worker_pane(pane_or_task: str) -> tuple[bool, int | None]:
    """Check if a pane/task identifier is a worker. Returns (is_worker, topic_id).

    This is a compatibility function - in the new architecture, task_names
    are used as identifiers instead of tmux panes.

    For backwards compatibility, this searches by task_name first, then by
    checking if any task has this value as a pane.
    """
    registry = get_registry()

    # Try as task_name first
    task_data = registry.get_task(pane_or_task)
    if task_data:
        return True, task_data.get("topic_id")

    # Legacy: search by pane field (for compatibility during migration)
    for name, data in registry.get_all_tasks():
        if data.get("pane") == pane_or_task:
            return True, data.get("topic_id")

    return False, None


async def check_and_resurrect_task_async(task_name: str) -> str | None:
    """Check if task process exists, resurrect if needed. Returns process identifier."""
    pm = get_process_manager()
    if pm is None:
        return None

    registry = get_registry()
    task_data = registry.get_task(task_name)
    if not task_data:
        return None

    if task_data.get("status") == "paused":
        return None

    # Check if running in ProcessManager
    if pm.is_running(task_name):
        return task_name

    # Not running - resurrect
    log(f"Process missing, resurrecting: {task_name}")
    return await resume_task_async(task_name)


def check_and_resurrect_task(task_name: str) -> str | None:
    """Check if task process exists, resurrect if needed (sync wrapper)."""
    try:
        loop = asyncio.get_running_loop()
        log("check_and_resurrect_task called from async context - use async version")
        pm = get_process_manager()
        if pm and pm.is_running(task_name):
            return task_name
        return None
    except RuntimeError:
        return asyncio.run(check_and_resurrect_task_async(task_name))
