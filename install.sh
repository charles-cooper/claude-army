#!/bin/bash
# Install Claude Code Telegram notifications
#
# Merges hooks into ~/.claude/settings.json, preserving existing hooks.
# Safe to run multiple times - won't duplicate hooks.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="$HOME/telegram.json"
SETTINGS_FILE="$HOME/.claude/settings.json"

echo "Claude Code Telegram Notifications - Install"
echo "============================================="
echo

# Check for requests
if ! python3 -c "import requests" 2>/dev/null; then
    echo "Installing requests..."
    pip3 install --user requests
fi

# Configure telegram bot
if [ -f "$CONFIG_FILE" ]; then
    echo "Telegram config already exists at $CONFIG_FILE"
    read -p "Overwrite? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Keeping existing config."
    else
        rm "$CONFIG_FILE"
    fi
fi

if [ ! -f "$CONFIG_FILE" ]; then
    echo "To get a bot token:"
    echo "  1. Message @BotFather on Telegram"
    echo "  2. Send /newbot and follow prompts"
    echo "  3. Copy the token"
    echo
    read -p "Bot token: " BOT_TOKEN

    echo
    echo "To get your chat ID:"
    echo "  1. Message your bot"
    echo "  2. Visit: https://api.telegram.org/bot<TOKEN>/getUpdates"
    echo "  3. Find 'chat':{'id': NUMBER}"
    echo
    read -p "Chat ID: " CHAT_ID

    echo "{\"bot_token\": \"$BOT_TOKEN\", \"chat_id\": \"$CHAT_ID\"}" > "$CONFIG_FILE"
    echo "Saved to $CONFIG_FILE"
fi

# Install hooks
echo
echo "Installing Claude Code hooks..."

mkdir -p "$HOME/.claude"

HOOK_CMD="python3 $SCRIPT_DIR/telegram-hook.py"

if [ -f "$SETTINGS_FILE" ]; then
    # Merge with existing settings
    python3 << EOF
import json
from pathlib import Path

settings_file = Path("$SETTINGS_FILE")
settings = json.loads(settings_file.read_text())

hooks = settings.setdefault("hooks", {})

# Stop hook
stop_hooks = hooks.setdefault("Stop", [])
hook_entry = {"hooks": [{"type": "command", "command": "$HOOK_CMD"}]}
if not any(h.get("hooks", [{}])[0].get("command") == "$HOOK_CMD" for h in stop_hooks):
    stop_hooks.append(hook_entry)

# Notification hooks
notif_hooks = hooks.setdefault("Notification", [])
for matcher in ["permission_prompt", "idle_prompt"]:
    entry = {"matcher": matcher, "hooks": [{"type": "command", "command": "$HOOK_CMD"}]}
    if not any(h.get("matcher") == matcher and h.get("hooks", [{}])[0].get("command") == "$HOOK_CMD" for h in notif_hooks):
        notif_hooks.append(entry)

settings_file.write_text(json.dumps(settings, indent=2))
print("Hooks installed.")
EOF
else
    # Create new settings
    cat > "$SETTINGS_FILE" << EOF
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "$HOOK_CMD"
          }
        ]
      }
    ],
    "Notification": [
      {
        "matcher": "permission_prompt",
        "hooks": [
          {
            "type": "command",
            "command": "$HOOK_CMD"
          }
        ]
      },
      {
        "matcher": "idle_prompt",
        "hooks": [
          {
            "type": "command",
            "command": "$HOOK_CMD"
          }
        ]
      }
    ]
  }
}
EOF
    echo "Created $SETTINGS_FILE"
fi

echo
echo "Done! You'll now receive Telegram notifications when:"
echo "  - Claude stops and waits for input"
echo "  - Claude asks for permission (Bash, Edit, Write)"
echo "  - Claude is idle for 60+ seconds"
echo
echo "Test by running Claude and triggering a permission prompt."
