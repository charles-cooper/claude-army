#!/usr/bin/env python3
"""Telegram daemon - watches transcripts and polls Telegram.

Main loop:
1. Poll transcripts for new tool_use entries (every ~1 second)
2. Poll Telegram for responses (5 second timeout)
3. Send notifications for pending tools
4. Handle Telegram callbacks and messages
"""

import json
import subprocess
import time
from pathlib import Path

from telegram_utils import (
    read_state, write_state, pane_exists,
    escape_markdown, format_tool_permission, strip_home,
    send_telegram
)
from transcript_watcher import TranscriptManager, PendingTool
from telegram_poller import TelegramPoller

CONFIG_FILE = Path.home() / "telegram.json"

CLEANUP_INTERVAL = 300  # 5 minutes


class TmuxNotAvailable(Exception):
    pass


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


def send_notification(bot_token: str, chat_id: str, tool: PendingTool) -> int | None:
    """Send Telegram notification for a pending tool. Returns message_id."""
    project = strip_home(tool.cwd)
    prefix = f"{escape_markdown(tool.assistant_text)}\n\n---\n\n" if tool.assistant_text else ""
    tool_desc = format_tool_permission(tool.tool_name, tool.tool_input)
    msg = f"`{project}`\n\n{prefix}{tool_desc}"

    always_label = f"✓ Always: {tool.tool_name}"
    reply_markup = {
        "inline_keyboard": [[
            {"text": "✓ Allow", "callback_data": "y"},
            {"text": always_label, "callback_data": "a"},
            {"text": "✗ Deny", "callback_data": "n"}
        ]]
    }

    result = send_telegram(bot_token, chat_id, msg, tool.tool_name, reply_markup)
    if not result:
        return None

    msg_id = result.get("result", {}).get("message_id")
    if msg_id:
        state = read_state()
        state[str(msg_id)] = {
            "pane": tool.pane,
            "type": "permission_prompt",
            "transcript_path": tool.transcript_path,
            "tool_use_id": tool.tool_id,
            "tool_name": tool.tool_name,
            "cwd": tool.cwd
        }
        write_state(state)
        print(f"Notified: {tool.tool_name} (msg_id={msg_id}, tool_id={tool.tool_id[:20]}...)", flush=True)

    return msg_id


def main():
    check_tmux()

    config = json.loads(CONFIG_FILE.read_text())
    bot_token, chat_id = config["bot_token"], config["chat_id"]

    print("Starting daemon...", flush=True)

    # Initialize components
    transcript_mgr = TranscriptManager()
    telegram_poller = TelegramPoller(bot_token, chat_id, timeout=5)

    # Bootstrap from state
    state = read_state()
    transcript_mgr.add_from_state(state)

    last_cleanup = time.time()
    last_discover = 0

    print("Watching transcripts and polling Telegram...", flush=True)

    while True:
        try:
            now = time.time()

            # Periodic discovery of new transcripts (every 30 seconds)
            if now - last_discover > 30:
                transcript_mgr.discover_transcripts()
                last_discover = now

            # Check transcripts for new tool_use
            pending_tools = transcript_mgr.check_all()
            for tool in pending_tools:
                send_notification(bot_token, chat_id, tool)

            # Poll Telegram (with short timeout to allow transcript polling)
            updates = telegram_poller.poll()
            telegram_poller.process_updates(updates)

            # Periodic cleanup (every 5 minutes)
            if now - last_cleanup > CLEANUP_INTERVAL:
                state = read_state()
                cleaned = cleanup_dead_panes(state)
                if len(cleaned) != len(state):
                    print(f"Cleaned {len(state) - len(cleaned)} dead entries", flush=True)
                    write_state(cleaned)

                transcript_mgr.cleanup_dead()
                last_cleanup = now

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}", flush=True)
            import traceback
            traceback.print_exc()
            time.sleep(5)


if __name__ == "__main__":
    main()
