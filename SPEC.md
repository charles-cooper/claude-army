# Telegram Notifier Specification

## Overview

The daemon watches Claude transcript files for permission prompts and sends Telegram notifications. It also polls Telegram for responses and injects them into Claude via tmux.

**Architecture:**
- `telegram-daemon.py` - Main daemon, orchestrates transcript watching and Telegram polling
- `transcript_watcher.py` - Watches transcript files for new tool_use entries
- `telegram_poller.py` - Handles Telegram updates (callbacks, messages)
- `telegram_utils.py` - Shared utilities (formatting, state, API calls)
- `telegram-hook.py` - Legacy hook (still works but daemon is faster)

## Transcript Watching

### Discovery
The daemon discovers transcripts via:
1. State file entries (transcripts from previous notifications)
2. tmux panes (scans `~/.claude/projects/{encoded-cwd}/*.jsonl`)

### Polling
- Reads from last known position (append-only file)
- Checks every ~1 second
- Detects new `tool_use` entries and sends notifications
- Tracks `tool_result` entries to prune notified set (memory management)

### Transcript Format
JSONL file, each line:
```json
{
  "type": "assistant" | "user",
  "message": {
    "content": [
      {"type": "text", "text": "..."},
      {"type": "tool_use", "id": "toolu_...", "name": "Bash", "input": {...}}
    ]
  }
}
```

Tool results appear as:
```json
{
  "type": "user",
  "message": {
    "content": [
      {"type": "tool_result", "tool_use_id": "toolu_..."}
    ]
  }
}
```

## Notifications

### Message Format
```
`project-name`

[assistant text if any]

---

Claude is asking permission to run:
```bash
command here
```
_description_
```

### Buttons
Permission prompts get three buttons:
- `✓ Allow` (callback_data: "y")
- `✓ Always: {tool_name}` (callback_data: "a")
- `✗ Deny` (callback_data: "n")

### Tool Formatting

| Tool | Format |
|------|--------|
| Bash | Code block with command + description |
| Edit | Unified diff |
| Write | File path + content in code block |
| Read | File path |
| AskUserQuestion | Questions with options |
| Other | JSON of input |

### Markdown Escaping
- `_`, `*`, `[`, `]` escaped with backslash in plain text
- Triple backticks escaped in plain text
- Single backticks left alone (inline code)
- Triple backticks inside code blocks replaced with `'''`

## Telegram Polling

### Update Types

#### Callback Query (button click)
```json
{
  "callback_query": {
    "id": "...",
    "data": "y" | "a" | "n" | "_",
    "message": {"message_id": 123, "chat": {"id": 456}}
  }
}
```

#### Message (text reply)
```json
{
  "message": {
    "message_id": 124,
    "chat": {"id": 456},
    "text": "user input",
    "reply_to_message": {"message_id": 123}
  }
}
```

### Response Handling

| Action | Condition | tmux Keys |
|--------|-----------|-----------|
| Allow | `data="y"` | Enter |
| Always | `data="a"` | Down Enter |
| Deny | `data="n"` | Down Down Enter |
| Text reply | Reply to permission msg | Down Down + text + Enter |

### Button Updates
After action:
- Allow → "✓ Allowed"
- Always → "✓ Always: {tool_name}"
- Deny → "📝 Reply with instructions"
- Text reply → "💬 Replied"
- Stale → "⏰ Expired"

### Text Reply Logic
1. Find pane and transcript_path from replied-to message
2. Check transcript for pending tool_use
3. If pending:
   - If replying to THAT tool's message → permission input
   - Else → block: "⚠️ Ignored: pending permission prompt"
4. If no pending → regular input

## State Management

### State File
`/tmp/claude-telegram-state.json`:
```json
{
  "message_id": {
    "pane": "session:window.pane",
    "type": "permission_prompt",
    "transcript_path": "/path/to/transcript.jsonl",
    "tool_use_id": "toolu_...",
    "tool_name": "Bash",
    "cwd": "/path/to/project"
  }
}
```

### Cleanup
Every 5 minutes:
- Remove entries for dead tmux panes
- Remove watchers for dead panes

### Memory Management
- `notified_tools` set pruned when tool_result seen
- Watchers removed when pane dies
- State entries removed when pane dies

## Config File
`~/telegram.json`:
```json
{
  "bot_token": "123456:ABC...",
  "chat_id": "123456789"
}
```

## File Locations

| File | Purpose |
|------|---------|
| `~/telegram.json` | Bot credentials |
| `/tmp/claude-telegram-state.json` | Message tracking |
| `/tmp/claude-telegram-state.lock` | File lock |
| `/tmp/claude-telegram-daemon.log` | Daemon log |
| `/tmp/claude-telegram-hook.log` | Hook log (legacy) |

## Claude Code TUI Behavior

### Permission Prompt Options
1. **Yes** - Accept and run the tool
2. **Yes, and don't ask again** - Accept and add to allow list
3. **Tell Claude something else** - Reject with custom instructions

### tmux Commands
- `tmux has-session -t {pane}` - check pane exists
- `tmux send-keys -t {pane} {key}` - send keystrokes
- `tmux list-panes -a -F '...'` - discover panes
