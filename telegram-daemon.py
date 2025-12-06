#!/usr/bin/env python3
"""Daemon that polls Telegram for replies and sends them to Claude."""

import fcntl
import json
import re
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
    with open(LOCK_FILE, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        try:
            return json.loads(STATE_FILE.read_text())
        except:
            return {}


def write_state(state: dict):
    """Write state file with locking."""
    with open(LOCK_FILE, "a") as lock:
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


def tool_already_handled(transcript_path: str, tool_use_id: str) -> bool:
    """Check if a tool_use has a corresponding tool_result in the transcript."""
    if not transcript_path or not tool_use_id:
        return False
    try:
        with open(transcript_path) as f:
            for line in f:
                if tool_use_id in line and '"tool_result"' in line:
                    return True
    except Exception as e:
        print(f"  Error checking transcript: {e}", flush=True)
    return False


def get_pending_tool_from_transcript(transcript_path: str) -> str | None:
    """Check transcript for any pending tool_use (no corresponding tool_result).

    Returns the tool_use_id if pending, None otherwise.
    """
    if not transcript_path:
        return None
    try:
        tool_uses = set()
        tool_results = set()
        with open(transcript_path) as f:
            for line in f:
                # Look for tool_use entries
                if '"tool_use"' in line and '"type":"tool_use"' in line:
                    match = re.search(r'"id"\s*:\s*"(toolu_[^"]+)"', line)
                    if match:
                        tool_uses.add(match.group(1))
                # Look for tool_result entries
                if '"tool_result"' in line:
                    match = re.search(r'"tool_use_id"\s*:\s*"(toolu_[^"]+)"', line)
                    if match:
                        tool_results.add(match.group(1))

        pending = tool_uses - tool_results
        if pending:
            return pending.pop()
    except Exception as e:
        print(f"  Error checking transcript for pending: {e}", flush=True)
    return None


def send_to_pane(pane: str, text: str) -> bool:
    """Send text to a tmux pane."""
    try:
        # Regular input: clear line, send text (literal), then Enter
        subprocess.run(["tmux", "send-keys", "-t", pane, "C-u"], check=True)
        subprocess.run(["tmux", "send-keys", "-t", pane, "-l", text], check=True)
        time.sleep(0.1)
        subprocess.run(["tmux", "send-keys", "-t", pane, "Enter"], check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"  Error: {e}", flush=True)
        return False


def send_text_to_permission_prompt(pane: str, text: str) -> bool:
    """Send text reply to a permission prompt.

    Navigate to "Tell Claude something else" (option 3), type text, submit.
    """
    try:
        subprocess.run(["tmux", "send-keys", "-t", pane, "C-u"], check=True)
        subprocess.run(["tmux", "send-keys", "-t", pane, "Down"], check=True)
        time.sleep(0.02)
        subprocess.run(["tmux", "send-keys", "-t", pane, "Down"], check=True)
        time.sleep(0.02)
        subprocess.run(["tmux", "send-keys", "-t", pane, "-l", text], check=True)
        time.sleep(0.1)
        subprocess.run(["tmux", "send-keys", "-t", pane, "Enter"], check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"  Error: {e}", flush=True)
        return False


def send_permission_response(pane: str, response: str) -> bool:
    """Send permission response via arrow keys.

    Options are: 1) Yes  2) Yes+auto  3) Tell Claude something else
    y = Enter (select first option)
    a = Down Enter (select second option - always allow)
    n = Down Down Enter (select third option)
    """
    try:
        if response == "y":
            # First option is Yes - just press Enter
            subprocess.run(["tmux", "send-keys", "-t", pane, "Enter"], check=True)
        elif response == "a":
            # Second option is "Yes, and don't ask again" - Down Enter
            subprocess.run(["tmux", "send-keys", "-t", pane, "Down"], check=True)
            time.sleep(0.02)
            subprocess.run(["tmux", "send-keys", "-t", pane, "Enter"], check=True)
        else:  # n
            # Third option is "tell Claude something else" - Down Down Enter
            subprocess.run(["tmux", "send-keys", "-t", pane, "Down"], check=True)
            time.sleep(0.02)
            subprocess.run(["tmux", "send-keys", "-t", pane, "Down"], check=True)
            time.sleep(0.02)
            subprocess.run(["tmux", "send-keys", "-t", pane, "Enter"], check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"  Error: {e}", flush=True)
        return False


def answer_callback(bot_token: str, callback_id: str, text: str = None):
    """Answer a callback query to dismiss the loading state."""
    requests.post(
        f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery",
        json={"callback_query_id": callback_id, "text": text}
    )


def send_reply(bot_token: str, chat_id: str, reply_to_msg_id: int, text: str):
    """Send a reply message on Telegram."""
    requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "reply_to_message_id": reply_to_msg_id}
    )


def update_message_after_action(bot_token: str, chat_id: str, msg_id: int, action: str, tool_name: str = None):
    """Update message to show which action was taken."""
    if action == "y":
        label = "✓ Allowed"
    elif action == "a":
        label = f"✓ Always: {tool_name}" if tool_name else "✓ Always allowed"
    elif action == "n":
        label = "📝 Reply with instructions"
    elif action == "replied":
        label = "💬 Replied"
    else:
        label = "⏰ Expired"
    requests.post(
        f"https://api.telegram.org/bot{bot_token}/editMessageReplyMarkup",
        json={
            "chat_id": chat_id,
            "message_id": msg_id,
            "reply_markup": {"inline_keyboard": [[{"text": label, "callback_data": "_"}]]}
        }
    )


def cleanup_dead_panes(state: dict) -> dict:
    """Remove entries for panes that no longer exist."""
    live = {}
    for msg_id, entry in state.items():
        pane = entry.get("pane")
        if pane and pane_exists(pane):
            live[msg_id] = entry
    return live


def mark_tui_handled(state: dict, bot_token: str, chat_id: str) -> int:
    """Mark permission prompts that were handled via TUI. Returns count marked."""
    count = 0
    for msg_id, entry in state.items():
        if entry.get("handled"):
            continue
        if entry.get("type") != "permission_prompt":
            continue
        transcript_path = entry.get("transcript_path")
        tool_use_id = entry.get("tool_use_id")
        if tool_already_handled(transcript_path, tool_use_id):
            entry["handled"] = True
            update_message_after_action(bot_token, chat_id, int(msg_id), "stale")
            count += 1
    return count


def is_stale(msg_id: int, pane: str, state: dict) -> bool:
    """Check if a message is stale (newer message exists for same pane)."""
    latest = max(
        (int(mid) for mid, e in state.items() if e.get("pane") == pane),
        default=0
    )
    return msg_id < latest


def get_pending_permission(pane: str, state: dict) -> tuple[str | None, dict | None]:
    """Get the pending permission prompt for a pane (if any).

    Checks transcript to determine if permission is truly pending.
    Returns (msg_id, entry) or (None, None).
    """
    # Find all permission prompts for this pane, sorted by msg_id desc
    candidates = [
        (mid, e) for mid, e in state.items()
        if e.get("pane") == pane and e.get("type") == "permission_prompt"
    ]
    candidates.sort(key=lambda x: int(x[0]), reverse=True)

    for msg_id, entry in candidates:
        transcript_path = entry.get("transcript_path")
        tool_use_id = entry.get("tool_use_id")
        if not tool_already_handled(transcript_path, tool_use_id):
            return msg_id, entry
    return None, None


def handle_permission_response(
    pane: str, response: str, bot_token: str, cb_id: str, chat_id, msg_id: int, tool_name: str = None
) -> bool:
    """Handle y/n/a permission response using arrow key navigation.

    Returns True if successfully handled (should remove from state).
    """
    labels = {"y": "Allowed", "a": f"Always: {tool_name}" if tool_name else "Always allowed", "n": "Denied"}
    label = labels.get(response, "Unknown")
    if send_permission_response(pane, response):
        answer_callback(bot_token, cb_id, label)
        update_message_after_action(bot_token, chat_id, msg_id, response, tool_name)
        print(f"  Sent {label} to pane {pane}", flush=True)
        return True
    else:
        answer_callback(bot_token, cb_id, "Failed: pane dead")
        print(f"  Failed (pane {pane} dead)", flush=True)
        return True  # Still remove from state - pane is dead


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
                    state = cleaned

                # Mark TUI-handled prompts
                tui_count = mark_tui_handled(state, bot_token, chat_id)
                if tui_count:
                    print(f"Marked {tui_count} TUI-handled prompts", flush=True)

                write_state(state)
                last_cleanup = time.time()

            for update in resp.json().get("result", []):
                offset = update["update_id"] + 1

                # Handle callback queries (button clicks)
                callback = update.get("callback_query")
                if callback:
                    cb_id = callback["id"]
                    cb_data = callback.get("data", "")
                    cb_msg = callback.get("message", {})
                    cb_msg_id = cb_msg.get("message_id")
                    cb_chat_id = cb_msg.get("chat", {}).get("id")
                    print(f"Callback: {cb_data} on msg_id={cb_msg_id}", flush=True)

                    if cb_data == "_":
                        answer_callback(bot_token, cb_id, "Already handled")
                        continue

                    if str(cb_msg_id) not in state:
                        answer_callback(bot_token, cb_id, "Session not found")
                        print(f"  Skipping: msg_id not in state", flush=True)
                        continue

                    entry = state[str(cb_msg_id)]
                    pane = entry["pane"]

                    if entry.get("handled"):
                        answer_callback(bot_token, cb_id, "Already handled")
                        print(f"  Already handled", flush=True)
                        continue

                    if is_stale(cb_msg_id, pane, state):
                        answer_callback(bot_token, cb_id, "Stale prompt")
                        update_message_after_action(bot_token, cb_chat_id, cb_msg_id, "stale")
                        state[str(cb_msg_id)]["handled"] = True
                        write_state(state)
                        print(f"  Stale prompt for pane {pane}", flush=True)
                        continue

                    is_permission = entry.get("type") == "permission_prompt"

                    # Check if tool was already handled via TUI
                    transcript_path = entry.get("transcript_path")
                    tool_use_id = entry.get("tool_use_id")
                    if is_permission and tool_already_handled(transcript_path, tool_use_id):
                        answer_callback(bot_token, cb_id, "Already handled in TUI")
                        update_message_after_action(bot_token, cb_chat_id, cb_msg_id, "stale")
                        state[str(cb_msg_id)]["handled"] = True
                        write_state(state)
                        print(f"  Already handled in TUI (tool_use_id={tool_use_id})", flush=True)
                        continue

                    if cb_data in ("y", "n", "a"):
                        if is_permission:
                            tool_name = entry.get("tool_name")
                            if handle_permission_response(pane, cb_data, bot_token, cb_id, cb_chat_id, cb_msg_id, tool_name):
                                state[str(cb_msg_id)]["handled"] = True
                                write_state(state)
                        else:
                            answer_callback(bot_token, cb_id, "No active prompt")
                            print(f"  Ignoring y/n/a: not a permission prompt", flush=True)
                    else:
                        if send_to_pane(pane, cb_data):
                            answer_callback(bot_token, cb_id, f"Sent: {cb_data}")
                            print(f"  Sent to pane {pane}: {cb_data}", flush=True)
                        else:
                            answer_callback(bot_token, cb_id, "Failed")
                            print(f"  Failed (pane {pane} dead)", flush=True)
                    continue

                # Handle regular messages
                msg = update.get("message", {})
                if not msg:
                    continue

                print(f"Update: {update.get('update_id')} msg_id={msg.get('message_id')}", flush=True)

                if str(msg.get("chat", {}).get("id")) != chat_id:
                    print(f"  Skipping: wrong chat", flush=True)
                    continue

                reply_to = msg.get("reply_to_message", {}).get("message_id")
                text = msg.get("text", "")
                print(f"  reply_to={reply_to} text={text[:30] if text else None}", flush=True)

                if reply_to and str(reply_to) in state and text:
                    entry = state[str(reply_to)]
                    pane = entry.get("pane")
                    transcript_path = entry.get("transcript_path")
                    if not pane:
                        print(f"  Skipping: no pane in entry", flush=True)
                        continue

                    # Check transcript directly for pending tool_use (more reliable than state)
                    pending_tool_id = get_pending_tool_from_transcript(transcript_path)

                    if pending_tool_id:
                        # There's a pending permission - check if user is replying to it
                        entry_tool_id = entry.get("tool_use_id")
                        if entry_tool_id == pending_tool_id:
                            # User is replying to THE pending permission - use permission input
                            if send_text_to_permission_prompt(pane, text):
                                print(f"  Sent to permission prompt on pane {pane}: {text[:50]}...", flush=True)
                                update_message_after_action(bot_token, chat_id, reply_to, "replied")
                            else:
                                print(f"  Failed (pane {pane} dead)", flush=True)
                        else:
                            # User replied to something else but there's a pending permission
                            # Block: don't want to mess up TUI state
                            # TODO: Option 2 - send anyway as regular input
                            # TODO: Option 3 - queue and send after permission handled
                            print(f"  Blocked: transcript has pending tool ({pending_tool_id[:20]}...), reply to that first", flush=True)
                            send_reply(bot_token, chat_id, msg.get("message_id"), "⚠️ Ignored: there's a pending permission prompt. Please respond to that first.")
                    else:
                        # No pending permission - send as regular input
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
