# Claude Code Telegram Integration - Feature Plan

## Overview
Enhance the Telegram bot to provide faster notifications and more control options.

---

## 1. Transcript Watcher (Priority: High)

### Problem
Claude Code hooks fire ~7 seconds after tool_use appears in transcript. This delay is in Claude Code's internal processing, not our notification code.

### Solution
Watch the transcript file directly instead of (or in addition to) relying on hooks.

### Transcript Behavior Analysis
- **Format**: JSONL (one JSON object per line)
- **Update pattern**: APPEND-ONLY (confirmed: 980 tool_use entries, 979 tool_result entries as separate lines)
- **Key fields**:
  - `type`: "assistant" | "user" | "system"
  - `timestamp`: ISO timestamp
  - `message.content[]`: Array containing `{type: "tool_use", id, name, input}` or `{type: "tool_result", tool_use_id}`

### Implementation
Add to daemon (polling approach, simpler than inotify):

```python
class TranscriptWatcher:
    def __init__(self, path):
        self.path = path
        self.position = os.path.getsize(path)  # Start at end
        self.notified_tools = set()  # Track sent notifications

    def check_new_entries(self):
        with open(self.path, 'r') as f:
            f.seek(self.position)
            for line in f:
                self.process_line(line)
            self.position = f.tell()

    def process_line(self, line):
        try:
            entry = json.loads(line)
            if entry.get("type") != "assistant":
                return
            for content in entry.get("message", {}).get("content", []):
                if content.get("type") == "tool_use":
                    tool_id = content.get("id")
                    tool_name = content.get("name")
                    if tool_id not in self.notified_tools:
                        if tool_name in ("Bash", "Edit", "Write", "Read"):
                            self.send_notification(tool_name, content.get("input", {}), tool_id)
                            self.notified_tools.add(tool_id)
        except json.JSONDecodeError:
            pass  # Partial line, will get it next time
```

### Integration with Daemon
- Poll transcripts every ~1 second (alongside Telegram polling)
- Get transcript paths from state file (already has pane→transcript mapping)
- Reuse `format_tool_permission()` from hook for consistent formatting

### Architecture Decision: Merge Hook into Daemon

**Decision:** Delete hook, merge notification logic into daemon.

**Tradeoffs analyzed:**

| Aspect | Keep Hook + Daemon | Daemon-only |
|--------|-------------------|-------------|
| Latency | ~7 sec (Claude Code delay) | ~1 sec (polling) |
| Code | 2 files, ~600 lines | 1 file, ~400 lines |
| Setup | Hook config in settings.json | Just run daemon |
| Robustness | Hook works if daemon dead | Single point of failure |

**Why merge wins:**
- 7x faster notifications
- Simpler deployment (no hook config)
- One process to monitor/restart
- Hook can also fail silently; two components = two failure modes

**Session Discovery:**
- Watch `~/.claude/projects/*/*.jsonl` for active transcripts
- Track by modification time (ignore stale files)
- Or: parse tmux sessions to find Claude instances

### Memory/Resource Leak Considerations

1. **notified_tools set grows unbounded**
   - Fix: Prune tool IDs older than session (check tool_result exists)
   - Or: Use LRU cache with max size

2. **TranscriptWatcher instances accumulate**
   - Fix: Remove watchers for dead sessions (tmux pane gone)
   - Periodic cleanup every 5 minutes

3. **File handles**
   - Don't keep files open; open/seek/read/close each poll cycle
   - Avoids issues with file rotation/deletion

4. **State file growth**
   - Already have cleanup_dead_panes() - runs every 5 min
   - Add max age cleanup (remove entries > 24h old)

### Implementation Notes

- Polling interval: 1 second for transcripts, 30 second long-poll for Telegram
- Use `select()` or threading to handle both without blocking
- Alternative: Single loop with short Telegram timeout (5s) + transcript check

### Files to Modify
- `telegram-daemon.py`: Add TranscriptWatcher, merge notification formatting from hook
- `telegram-hook.py`: DELETE (move format_tool_permission to daemon)
- `install.sh` / `uninstall.sh`: Remove hook configuration
- `SPEC.md`: Update architecture docs

---

## 2. "Yes, and don't ask again" Button (Priority: Medium) ✅ IMPLEMENTED

### Problem
Currently only "Allow" and "Deny" buttons. User may want to permanently allow a tool pattern.

### Solution
Add third button that accepts AND adds to Claude's allow list.

### Implementation (DONE)

- Hook: Added "✓ Always" button with `callback_data: "a"`
- Daemon: Handle "a" → Down Enter (option 2 in TUI)
- State: Now stores `tool_name` for display in button label
- Button label: "✓ Always: {tool_name}" (e.g., "✓ Always: Bash")

### TUI Navigation
Permission prompt options:
1. Yes (Enter)
2. Yes, and don't ask again (Down, Enter)
3. Tell Claude something else (Down, Down, Enter)

### Files Modified
- `telegram-hook.py`: Added "Always" button, store tool_name in state
- `telegram-daemon.py`: Handle "a" callback with tool_name in label
- `SPEC.md`: Updated button documentation

---

## 3. Claude Operator / Orchestration (Priority: Future)

### Vision
A Claude orchestration system controlled via Telegram:
- Spawn multiple Claude instances, each in its own tmux session
- Tear down instances when done
- Control them remotely from Telegram
- Eventually: a "manager" Claude that coordinates worker instances

### Architecture

```
┌─────────────────┐
│    Telegram     │
│   (commands)    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Daemon/Bot     │
│  - /spawn       │
│  - /list        │
│  - /kill        │
│  - /send        │
└────────┬────────┘
         │
    ┌────┴────┬─────────┐
    ▼         ▼         ▼
┌───────┐ ┌───────┐ ┌───────┐
│ tmux  │ │ tmux  │ │ tmux  │
│ sess1 │ │ sess2 │ │ sess3 │
│Claude │ │Claude │ │Claude │
└───────┘ └───────┘ └───────┘
```

### Telegram Commands

| Command | Action |
|---------|--------|
| `/spawn <project> [prompt]` | Create new tmux session, start Claude in project dir, optionally send initial prompt |
| `/list` | List active Claude sessions with status |
| `/kill <session>` | Send Ctrl+C, then kill tmux session |
| `/send <session> <text>` | Send text to specific session |
| `/status <session>` | Show recent transcript activity |

### Implementation

#### Session Spawning
```python
def spawn_claude(project_path: str, initial_prompt: str = None) -> str:
    session_name = f"claude-{uuid.uuid4().hex[:8]}"
    # Create tmux session
    subprocess.run(["tmux", "new-session", "-d", "-s", session_name, "-c", project_path])
    # Start claude in it
    subprocess.run(["tmux", "send-keys", "-t", session_name, "claude", "Enter"])
    time.sleep(2)  # Wait for Claude to start
    if initial_prompt:
        subprocess.run(["tmux", "send-keys", "-t", session_name, "-l", initial_prompt])
        subprocess.run(["tmux", "send-keys", "-t", session_name, "Enter"])
    return session_name
```

#### Session Registry
```python
# Store in /tmp/claude-sessions.json
{
    "claude-a1b2c3d4": {
        "project": "/home/ubuntu/myproject",
        "created": "2025-12-06T21:00:00Z",
        "transcript": "/home/ubuntu/.claude/projects/.../transcript.jsonl",
        "status": "running"  # running, idle, waiting_permission
    }
}
```

### Future: Manager Claude

A supervisory Claude instance that:
1. Receives high-level tasks from user
2. Spawns worker Claudes for subtasks
3. Monitors their progress via transcripts
4. Handles permission prompts on their behalf (or escalates to human)
5. Aggregates results

This would require:
- Worker Claude instances with reduced permissions
- Manager Claude with orchestration tools (spawn, monitor, kill)
- Protocol for manager to communicate with workers
- Human escalation for sensitive operations

### Files to Add
- `claude-operator.py` - New script for session management commands
- Or extend `telegram-daemon.py` with command handling

---

## 4. Implementation Order

1. **"Yes, and don't ask again" button** - Quick win, ~30 min
2. **Transcript watcher** - Biggest impact, ~2-3 hours
3. **Operator features** - Design needed, scope TBD

---

## 5. Files Summary

| File | Changes |
|------|---------|
| `telegram-daemon.py` | Add TranscriptWatcher, handle "a" button |
| `telegram-hook.py` | Add "Always" button |
| `SPEC.md` | Document new features |
| `README.md` | Update documentation |
