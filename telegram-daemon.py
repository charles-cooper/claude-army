#!/usr/bin/env python3
"""Daemon that polls Telegram for replies and sends them to Claude."""

import fcntl
import json
import requests
import subprocess
import sys
import time
from pathlib import Path

CONFIG_FILE = Path.home() / "telegram.json"
STATE_FILE = Path("/tmp/claude-telegram-state.json")
LOCK_FILE = Path("/tmp/claude-telegram-state.lock")


def read_state() -> dict:
    """Read state file with locking."""
    if not STATE_FILE.exists():
        return {}
    with open(LOCK_FILE, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        try:
            return json.loads(STATE_FILE.read_text())
        except:
            return {}


def write_state(state: dict):
    """Write state file with locking."""
    with open(LOCK_FILE, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        STATE_FILE.write_text(json.dumps(state))


class TmuxNotAvailable(Exception):
    pass


def check_tmux():
    """Verify tmux is available."""
    result = subprocess.run(["tmux", "list-sessions"], capture_output=True)
    if result.returncode != 0:
        raise TmuxNotAvailable("tmux not available or no sessions running")


def pane_exists(pane: str) -> bool:
    """Check if a tmux pane exists."""
    result = subprocess.run(
        ["tmux", "has-session", "-t", pane],
        capture_output=True
    )
    return result.returncode == 0


def send_to_pane(pane: str, text: str) -> bool:
    """Send text to a tmux pane."""
    try:
        subprocess.run(["tmux", "send-keys", "-t", pane, text], check=True)
        subprocess.run(["tmux", "send-keys", "-t", pane, "Enter"], check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"  Error: {e}", flush=True)
        return False


def cleanup_dead_panes(state: dict) -> dict:
    """Remove entries for panes that no longer exist."""
    live = {}
    for msg_id, entry in state.items():
        pane = entry.get("pane")
        if pane and pane_exists(pane):
            live[msg_id] = entry
    return live


CLEANUP_INTERVAL = 300  # 5 minutes


def main():
    check_tmux()

    config = json.loads(CONFIG_FILE.read_text())
    bot_token, chat_id = config["bot_token"], config["chat_id"]

    print("Polling Telegram for replies...", flush=True)
    offset = 0
    last_cleanup = time.time()

    while True:
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{bot_token}/getUpdates",
                params={"offset": offset, "timeout": 30}
            )
            if not resp.ok:
                continue

            state = read_state()

            # Time-based cleanup
            if time.time() - last_cleanup > CLEANUP_INTERVAL:
                cleaned = cleanup_dead_panes(state)
                if len(cleaned) != len(state):
                    print(f"Cleaned {len(state) - len(cleaned)} dead entries", flush=True)
                    write_state(cleaned)
                    state = cleaned
                last_cleanup = time.time()

            for update in resp.json().get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                print(f"Update: {update.get('update_id')} msg_id={msg.get('message_id')}", flush=True)

                if str(msg.get("chat", {}).get("id")) != chat_id:
                    print(f"  Skipping: wrong chat", flush=True)
                    continue

                reply_to = msg.get("reply_to_message", {}).get("message_id")
                text = msg.get("text", "")
                print(f"  reply_to={reply_to} text={text[:30] if text else None}", flush=True)

                if reply_to and str(reply_to) in state and text:
                    pane = state[str(reply_to)]["pane"]
                    if send_to_pane(pane, text):
                        print(f"  Sent to pane {pane}: {text[:50]}...", flush=True)
                    else:
                        print(f"  Failed (pane {pane} dead)", flush=True)

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
