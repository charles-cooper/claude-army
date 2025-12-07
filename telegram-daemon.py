#!/usr/bin/env python3
"""Telegram daemon - watches transcripts and polls Telegram.

Main loop:
1. Poll transcripts for new tool_use entries (every ~1 second)
2. Poll Telegram for responses (5 second timeout)
3. Send notifications for pending tools
4. Handle Telegram callbacks and messages
"""

import atexit
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from telegram_utils import (
    read_state, write_state, pane_exists,
    escape_markdown, format_tool_permission, strip_home,
    send_telegram, log, update_message_buttons
)
from transcript_watcher import TranscriptManager, PendingTool, CompactionEvent
from telegram_poller import TelegramPoller

CONFIG_FILE = Path.home() / "telegram.json"
PID_FILE = Path("/tmp/claude-telegram-daemon.pid")

CLEANUP_INTERVAL = 300  # 5 minutes


class DaemonAlreadyRunning(Exception):
    pass


class TmuxNotAvailable(Exception):
    pass


def handle_sigterm(signum, frame):
    """Handle SIGTERM by exiting cleanly."""
    sys.exit(0)


def check_singleton():
    """Ensure only one daemon is running."""
    if PID_FILE.exists():
        pid = int(PID_FILE.read_text().strip())
        # Check if process is still running
        try:
            os.kill(pid, 0)
            raise DaemonAlreadyRunning(f"Daemon already running with PID {pid}")
        except OSError:
            # Process not running, stale PID file
            pass
    # Write our PID
    PID_FILE.write_text(str(os.getpid()))
    atexit.register(PID_FILE.unlink, missing_ok=True)
    signal.signal(signal.SIGTERM, handle_sigterm)


def check_tmux():
    """Verify tmux is available."""
    result = subprocess.run(["tmux", "list-sessions"], capture_output=True)
    if result.returncode != 0:
        raise TmuxNotAvailable("tmux not available or no sessions running")


def cleanup_dead_panes(state: dict) -> dict:
    """Remove entries for panes that no longer exist."""
    live = {}
    for msg_id, entry in state.items():
        pane = entry.get("pane")
        if pane and pane_exists(pane):
            live[msg_id] = entry
    return live


def expire_old_buttons(bot_token: str, chat_id: str, pane: str, state: dict) -> bool:
    """Expire buttons for old messages on this pane (if newer exists). Returns True if any expired."""
    # Find the latest unhandled message for this pane
    pane_msgs = [(int(mid), e) for mid, e in state.items()
                 if e.get("pane") == pane and not e.get("handled")]
    if len(pane_msgs) <= 1:
        return False  # Nothing to expire

    latest = max(mid for mid, _ in pane_msgs)
    changed = False
    for msg_id, entry in pane_msgs:
        if msg_id < latest:
            update_message_buttons(bot_token, chat_id, msg_id, "⏰ Expired")
            entry["handled"] = True
            changed = True
    return changed


def expire_handled_buttons(bot_token: str, chat_id: str, state: dict, transcript_mgr) -> bool:
    """Expire buttons for tools that were handled in the TUI. Returns True if any expired."""
    changed = False
    for msg_id, entry in state.items():
        if entry.get("handled"):
            continue
        tool_use_id = entry.get("tool_use_id")
        if not tool_use_id:
            continue
        # Check if this tool has a result in any watcher
        transcript_path = entry.get("transcript_path")
        if transcript_path and transcript_path in transcript_mgr.watchers:
            watcher = transcript_mgr.watchers[transcript_path]
            if tool_use_id in watcher.tool_results:
                update_message_buttons(bot_token, chat_id, int(msg_id), "⏰ Expired")
                entry["handled"] = True
                changed = True
                log(f"Expired (handled in TUI): msg_id={msg_id}")
    return changed


def send_compaction_notification(bot_token: str, chat_id: str, event: CompactionEvent):
    """Send Telegram notification for a compaction event."""
    project = strip_home(event.cwd)
    msg = f"`{project}`\n\n🔄 Context compacted ({event.trigger}, {event.pre_tokens:,} tokens)"
    send_telegram(bot_token, chat_id, msg)
    log(f"Notified: compaction ({event.trigger})")


def send_notification(bot_token: str, chat_id: str, tool: PendingTool, state: dict) -> int | None:
    """Send Telegram notification for a pending tool. Returns message_id. Updates state in-place."""
    project = strip_home(tool.cwd)
    prefix = f"{escape_markdown(tool.assistant_text)}\n\n---\n\n" if tool.assistant_text else ""
    tool_desc = format_tool_permission(tool.tool_name, tool.tool_input)
    msg = f"`{project}`\n\n{prefix}{tool_desc}"

    reply_markup = {
        "inline_keyboard": [[
            {"text": "Allow", "callback_data": "y"},
            {"text": "Deny", "callback_data": "n"}
        ]]
    }

    result = send_telegram(bot_token, chat_id, msg, tool.tool_name, reply_markup)
    if not result:
        return None

    msg_id = result.get("result", {}).get("message_id")
    if msg_id:
        state[str(msg_id)] = {
            "pane": tool.pane,
            "type": "permission_prompt",
            "transcript_path": tool.transcript_path,
            "tool_use_id": tool.tool_id,
            "tool_name": tool.tool_name,
            "cwd": tool.cwd
        }
        log(f"Notified: {tool.tool_name} (msg_id={msg_id}, tool_id={tool.tool_id[:20]}...)")

    return msg_id


def main():
    check_singleton()
    check_tmux()

    config = json.loads(CONFIG_FILE.read_text())
    bot_token, chat_id = config["bot_token"], config["chat_id"]

    log(f"Starting daemon (PID {os.getpid()})...")

    # Initialize components
    transcript_mgr = TranscriptManager()
    telegram_poller = TelegramPoller(bot_token, chat_id, timeout=30)
    update_queue = queue.Queue()

    def telegram_poll_thread():
        """Background thread for Telegram long-polling."""
        while True:
            try:
                updates = telegram_poller.poll()
                if updates:
                    update_queue.put(updates)
            except Exception as e:
                log(f"Telegram thread error: {e}")
                time.sleep(1)

    telegram_thread = threading.Thread(target=telegram_poll_thread, daemon=True)
    telegram_thread.start()

    # Bootstrap from state and discover transcripts
    state = read_state()
    transcript_mgr.add_from_state(state)
    transcript_mgr.discover_transcripts()

    last_cleanup = time.time()
    last_discover = time.time()

    log("Watching transcripts and polling Telegram...")

    while True:
        try:
            now = time.time()
            state_changed = False

            # Periodic discovery of new transcripts (every 30 seconds)
            if now - last_discover > 30:
                transcript_mgr.discover_transcripts()
                last_discover = now

            # Check transcripts for new tool_use and compactions
            pending_tools, compactions = transcript_mgr.check_all()
            for tool in pending_tools:
                if send_notification(bot_token, chat_id, tool, state):
                    state_changed = True
            for event in compactions:
                send_compaction_notification(bot_token, chat_id, event)

            # Process any Telegram updates from background thread
            while not update_queue.empty():
                telegram_poller.process_updates(update_queue.get_nowait(), state)
                state_changed = True  # Assume updates may have changed state

            # Expire buttons for handled tools and old messages
            if expire_handled_buttons(bot_token, chat_id, state, transcript_mgr):
                state_changed = True
            for pane in transcript_mgr.pane_to_transcript:
                if expire_old_buttons(bot_token, chat_id, pane, state):
                    state_changed = True

            # Periodic cleanup (every 5 minutes)
            if now - last_cleanup > CLEANUP_INTERVAL:
                cleaned = cleanup_dead_panes(state)
                if len(cleaned) != len(state):
                    log(f"Cleaned {len(state) - len(cleaned)} dead entries")
                    state = cleaned
                    state_changed = True

                transcript_mgr.cleanup_dead()
                last_cleanup = now

            if state_changed:
                write_state(state)

            time.sleep(0.1)

        except KeyboardInterrupt:
            break
        except Exception as e:
            log(f"Error: {e}")
            traceback.print_exc()


if __name__ == "__main__":
    main()
