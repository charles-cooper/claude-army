#!/bin/bash
# Install Claude Army hooks
#
# Installs:
#   - PreToolUse hook for permission management (permission_hook.py)
#   - Notification/Compact hooks for Telegram (telegram-hook.py)
#
# Merges hooks into ~/.claude/settings.json, preserving existing hooks.
# Safe to run multiple times - won't duplicate hooks.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="$HOME/telegram.json"
SETTINGS_FILE="$HOME/.claude/settings.json"

echo "Claude Army - Install"
echo "====================="
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

TELEGRAM_HOOK_CMD="python3 $SCRIPT_DIR/telegram-hook.py"
PERMISSION_HOOK_CMD="$SCRIPT_DIR/permission_hook.py"

if [ -f "$SETTINGS_FILE" ]; then
    # Merge with existing settings
    python3 << EOF
import json
from pathlib import Path

settings_file = Path("$SETTINGS_FILE")
settings = json.loads(settings_file.read_text())

hooks = settings.setdefault("hooks", {})

# PreToolUse hook for permission management
pretooluse_hooks = hooks.setdefault("PreToolUse", [])
perm_entry = {
    "matcher": "*",
    "hooks": [{"type": "command", "command": "$PERMISSION_HOOK_CMD", "timeout": 300}]
}
# Check if hook with same command already exists
if not any(
    h.get("matcher") == "*" and
    any(hh.get("command") == "$PERMISSION_HOOK_CMD" for hh in h.get("hooks", []))
    for h in pretooluse_hooks
):
    pretooluse_hooks.append(perm_entry)
    print("Installed PreToolUse permission hook.")
else:
    print("PreToolUse permission hook already installed.")

# Notification hooks (telegram)
notif_hooks = hooks.setdefault("Notification", [])
for matcher in ["permission_prompt"]:
    entry = {"matcher": matcher, "hooks": [{"type": "command", "command": "$TELEGRAM_HOOK_CMD"}]}
    if not any(h.get("matcher") == matcher and h.get("hooks", [{}])[0].get("command") == "$TELEGRAM_HOOK_CMD" for h in notif_hooks):
        notif_hooks.append(entry)

# PreCompact hooks (auto and manual)
precompact_hooks = hooks.setdefault("PreCompact", [])
for matcher in ["auto", "manual"]:
    entry = {"matcher": matcher, "hooks": [{"type": "command", "command": "$TELEGRAM_HOOK_CMD"}]}
    if not any(h.get("matcher") == matcher and h.get("hooks", [{}])[0].get("command") == "$TELEGRAM_HOOK_CMD" for h in precompact_hooks):
        precompact_hooks.append(entry)

# PostCompact hooks (auto and manual)
postcompact_hooks = hooks.setdefault("PostCompact", [])
for matcher in ["auto", "manual"]:
    entry = {"matcher": matcher, "hooks": [{"type": "command", "command": "$TELEGRAM_HOOK_CMD"}]}
    if not any(h.get("matcher") == matcher and h.get("hooks", [{}])[0].get("command") == "$TELEGRAM_HOOK_CMD" for h in postcompact_hooks):
        postcompact_hooks.append(entry)

settings_file.write_text(json.dumps(settings, indent=2))
print("Hooks installed.")
EOF
else
    # Create new settings
    cat > "$SETTINGS_FILE" << EOF
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "$PERMISSION_HOOK_CMD",
            "timeout": 300
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
            "command": "$TELEGRAM_HOOK_CMD"
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "matcher": "auto",
        "hooks": [
          {
            "type": "command",
            "command": "$TELEGRAM_HOOK_CMD"
          }
        ]
      },
      {
        "matcher": "manual",
        "hooks": [
          {
            "type": "command",
            "command": "$TELEGRAM_HOOK_CMD"
          }
        ]
      }
    ],
    "PostCompact": [
      {
        "matcher": "auto",
        "hooks": [
          {
            "type": "command",
            "command": "$TELEGRAM_HOOK_CMD"
          }
        ]
      },
      {
        "matcher": "manual",
        "hooks": [
          {
            "type": "command",
            "command": "$TELEGRAM_HOOK_CMD"
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
echo "Done! Installed:"
echo "  - PreToolUse hook for permission management (permission_hook.py)"
echo "  - Notification hooks for Telegram alerts (telegram-hook.py)"
echo "  - Compact hooks for context compaction alerts"
echo
echo "Run 'uninstall.sh' to remove hooks."
