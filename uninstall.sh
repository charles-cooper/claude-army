#!/bin/bash
# Uninstall Claude Army hooks
#
# Removes only our hooks from ~/.claude/settings.json, preserving other hooks.
#
# Test cases:
#
# 1. Only our hooks -> hooks section removed entirely:
#    {"hooks": {"PreToolUse": [our_hook], "Notification": [our_hook], ...}}
#    becomes: {}
#
# 2. Mixed with other hooks -> only ours removed:
#    {"hooks": {"PreToolUse": [our_hook, other_hook]}}
#    becomes: {"hooks": {"PreToolUse": [other_hook]}}

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SETTINGS_FILE="$HOME/.claude/settings.json"
TELEGRAM_HOOK_CMD="python3 $SCRIPT_DIR/telegram-hook.py"
PERMISSION_HOOK_CMD="$SCRIPT_DIR/permission_hook.py"

echo "Removing hooks from $SETTINGS_FILE..."

if [ -f "$SETTINGS_FILE" ]; then
    python3 << EOF
import json
from pathlib import Path

settings_file = Path("$SETTINGS_FILE")
settings = json.loads(settings_file.read_text())
hooks = settings.get("hooks", {})

# Remove PreToolUse permission hook
if "PreToolUse" in hooks:
    hooks["PreToolUse"] = [
        h for h in hooks["PreToolUse"]
        if not any(hh.get("command") == "$PERMISSION_HOOK_CMD" for hh in h.get("hooks", []))
    ]
    if not hooks["PreToolUse"]:
        del hooks["PreToolUse"]
    print("Removed PreToolUse permission hook.")

# Remove from Notification
if "Notification" in hooks:
    hooks["Notification"] = [h for h in hooks["Notification"]
                             if h.get("hooks", [{}])[0].get("command") != "$TELEGRAM_HOOK_CMD"]
    if not hooks["Notification"]:
        del hooks["Notification"]

# Remove from PreCompact
if "PreCompact" in hooks:
    hooks["PreCompact"] = [h for h in hooks["PreCompact"]
                           if h.get("hooks", [{}])[0].get("command") != "$TELEGRAM_HOOK_CMD"]
    if not hooks["PreCompact"]:
        del hooks["PreCompact"]

# Remove from PostCompact
if "PostCompact" in hooks:
    hooks["PostCompact"] = [h for h in hooks["PostCompact"]
                            if h.get("hooks", [{}])[0].get("command") != "$TELEGRAM_HOOK_CMD"]
    if not hooks["PostCompact"]:
        del hooks["PostCompact"]

if not hooks:
    del settings["hooks"]

settings_file.write_text(json.dumps(settings, indent=2))
print("Done.")
EOF
else
    echo "No settings file found."
fi

echo
echo "Hooks removed. Config file ~/telegram.json was NOT removed."
