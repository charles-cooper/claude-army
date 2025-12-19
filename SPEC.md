# Claude Army Specification

## Overview

Claude Army is a multi-instance task management system with Telegram integration. It manages multiple Claude instances working on separate tasks in isolated git worktrees, with an Operator Claude interpreting user instructions and managing Worker Claudes.

**Architecture:**
```
┌─────────────────────────────────────────────────────────────┐
│                 Telegram Forum Group                        │
├─────────────────────────────────────────────────────────────┤
│  General Topic              │  Task Topics                  │
│  ────────────────           │  ────────────                 │
│  User ↔ Operator Claude     │  feature-x: Worker Claude A   │
│  Natural language commands  │  fix-bug-123: Worker Claude B │
│  Setup, status, management  │  refactor-api: Worker Claude C│
└──────────────┬──────────────┴───────────────┬───────────────┘
               │                              │
               ▼                              ▼
       ┌───────────────┐            ┌─────────────────┐
       │   Operator    │            │     Workers     │
       │  subprocess   │            │  (subprocesses) │
       │ ~/claude-army │            │ repo/trees/X    │
       └───────────────┘            └─────────────────┘
               │                              │
               └──────────────┬───────────────┘
                              ▼
        ┌─────────────────────────────────────────┐
        │               Daemon                    │
        │  - ProcessManager: Claude subprocesses  │
        │  - PermissionManager: tool approvals    │
        │  - TelegramAdapter: Telegram polling    │
        │  - HTTP server: permission hooks        │
        └─────────────────────────────────────────┘
```

**Components:**
- `telegram-daemon.py` - Entry point, signal handlers, calls daemon_core.main()
- `daemon_core.py` - Main daemon orchestrating all components
- `process_manager.py` - Manages pool of ClaudeProcess instances
- `claude_process.py` - Single Claude subprocess with stream-json I/O
- `permission_server.py` - HTTP server + PermissionManager for tool approvals
- `permission_hook.py` - Hook script called by Claude CLI for permission decisions
- `telegram_adapter.py` - Telegram API frontend (polls updates, sends messages)
- `telegram_utils.py` - Shared utilities (formatting, API calls)
- `registry.py` - Task registry and configuration management
- `bot_commands.py` - Bot command handlers

## Core Concepts

### Task Types

| Type | Directory | Topic | Cleanup |
|------|-----------|-------|---------|
| **Worktree Task** | Created by us (`repo/trees/{name}/`) | Long-lived (PR/branch work) | Delete worktree + close topic |
| **Session** | Any existing directory | Ephemeral (focused work) | Remove marker + close topic (preserve dir) |

Both types:
- Have `.claude/army.json` marker file
- Have a dedicated Telegram topic
- Are recoverable from filesystem scan

### Source of Truth Hierarchy

1. **Marker files** - `.claude/army.json` defines tasks (inside Claude's own state dir)
2. **Registry** - Cache at `~/claude-army/operator/registry.json` (rebuildable from markers)
3. **Subprocesses** - Ephemeral, resurrected as needed from session_id

## Directory Structure

```
~/claude-army/                    # Project root (daemon runs here)
  telegram-daemon.py              # Daemon entry point
  daemon_core.py                  # Daemon logic
  operator/                       # Operator Claude's working directory (gitignored)
    registry.json                 # Cache (rebuildable from .claude/army.json files)
    config.json                   # Group ID, topic IDs, etc.
    .claude/                      # Operator's Claude state (conversations, settings)
  ...

~/projects/myrepo/                # User's repository
  trees/
    feature-x/                    # Worktree for task
      .claude/
        army.json                 # Marker file (inside Claude's state dir)
        ...                       # Claude's conversation state

~/projects/other-project/         # Existing directory (session, not worktree)
  .claude/
    army.json                     # Session marker
```

### Marker File Format

```json
// .claude/army.json (same format for worktree and session)
{
  "name": "feature-x",
  "type": "worktree",             // or "session"
  "repo": "/home/user/myrepo",    // only for worktrees
  "description": "Add dark mode support",
  "topic_id": 123456,
  "created_at": "2025-01-01T00:00:00Z"
}
```

### Task Directory Files

Created automatically when a task is spawned:

**CLAUDE.local.md** - Instructions for the Claude instance:
- Task name and description
- Instructions to update file with learnings (persists across sessions)
- Instructions to check TODO.local.md periodically

**TODO.local.md** - Async todo queue:
- Created when first `/todo` is sent to a task topic
- Format: markdown checkboxes (`- [ ] item`)
- Claude is instructed to add items to its todo stack and mark done when complete

Example workflow:
1. User sends `/todo fix the failing test` in task topic
2. System appends `- [ ] fix the failing test` to TODO.local.md
3. Claude periodically checks file, adds to its TodoWrite stack
4. Claude works on item, marks it `- [x]` when done
5. Claude periodically cleans up completed items from the file

### Registry Cache Format

```json
// operator/registry.json (rebuildable from .claude/army.json scans)
{
  "tasks": {
    "feature-x": {
      "type": "worktree",
      "path": "/home/user/myrepo/trees/feature-x",
      "repo": "/home/user/myrepo",
      "topic_id": 123,
      "status": "active",
      "session_id": "abc123...",
      "pid": 12345
    }
  }
}
```

Note: `group_id`, `general_topic_id`, and `telegram_offset` are in `config.json`, not registry.

## Telegram Setup

### Initial Setup Flow

1. User creates Telegram group, adds bot as admin
2. User enables Topics in group settings
3. User sends `/setup` in group
4. Bot stores `group_id` in config
5. Daemon starts Operator Claude subprocess

### Bot Commands

| Command | Description |
|---------|-------------|
| `/spawn <desc>` | Create a new task (routes to operator) |
| `/status` | Show all tasks and status |
| `/cleanup [task]` | Clean up a task (routes to operator) |
| `/help` | Show available commands |
| `/todo <item>` | Add todo (writes to TODO.local.md in task topics, routes to Operator in General topic) |
| `/setup` | Initialize group as control center |
| `/summarize` | Have operator summarize all tasks and priorities |
| `/operator [msg]` | Request operator intervention for current task |
| `/rebuild-registry` | Rebuild registry from marker files (maintenance) |
| `/debug` | Debug a message (reply to it) |

Commands are registered via `setMyCommands` API at startup.

### Topic Structure

- **General** - Operator Claude, management commands, fallback notifications
- **task-name** - One per task, Worker Claude notifications

## Operator Claude

### Role

- Runs in `~/claude-army/operator` directory (or `~` by default)
- Receives all messages from General topic
- Interprets user intent (natural language)
- Manages tasks (spawn, status, cleanup)
- Manages todo queue - receives todos from any topic, decides routing/priority
- Goes through permission prompts for actions

### Example Interactions

```
User: "Can you look into the memory leak in vyper?"
Operator: [identifies repo ~/vyper, creates task description]
  → Permission prompt: "Create worktree vyper/trees/fix-memory-leak?"
  → User approves
Operator: "Created task 'fix-memory-leak' in vyper. Worker Claude is investigating."

User: "What's the status of all tasks?"
Operator: [scans worktrees, checks sessions]
Operator: "3 active tasks:
  - vyper/fix-memory-leak: Running, last activity 2m ago
  - myrepo/feature-x: Idle, waiting for input
  - myrepo/refactor-api: Paused"
```

### Available Actions

| Action | Triggers | Permission Required |
|--------|----------|---------------------|
| Spawn worktree task | User request | Yes (creates worktree) |
| Spawn session | User request | Yes (creates topic) |
| List tasks | User request | No |
| Task status | User request | No |
| Pause task | User request | No |
| Resume task | User request | No |
| Cleanup worktree task | User request | Yes (deletes worktree) |
| Cleanup session | User request | Yes (closes topic) |

## Worker Claude Sessions

### Lifecycle

```
Spawn Worktree Task:
  1. Create git worktree from master
  2. Create Telegram topic
  3. Write .claude/army.json marker
  4. Start ClaudeProcess subprocess
  5. Send initial prompt with task description

Spawn Session (existing directory):
  1. Verify directory exists
  2. Create Telegram topic
  3. Write .claude/army.json marker
  4. Start ClaudeProcess subprocess
  5. Send initial prompt with task description

Running:
  - AssistantMessage events → task topic
  - User replies → subprocess stdin (stream-json)
  - Permission prompts → task topic buttons

Death (crash, reboot):
  - ProcessManager detects missing subprocess
  - Resurrects: `claude --resume <session_id>` in directory
  - Session continues working

Cleanup (worktree):
  - Terminate subprocess
  - Delete worktree (removes directory + .claude/army.json)
  - Close topic

Cleanup (session):
  - Terminate subprocess
  - Remove .claude/army.json (preserve directory)
  - Close topic
```

### Claude Startup

- **Subprocess command**: `claude -p --verbose --output-format stream-json --input-format stream-json`
- **Resume**: `claude ... --resume <session_id>`
- **Environment**: `CLAUDE_ARMY_MANAGED=1` (enables permission hooks)

### Post-Worktree Setup Hook

If a repo contains `.claude-army-setup.sh` (in repo root), it runs after worktree creation:

```bash
#!/bin/bash
# .claude-army-setup.sh - runs in new worktree directory
ln -sf ~/shared/.env .env
ln -sf ../main/node_modules node_modules
```

Environment variables: `TASK_NAME`, `REPO_PATH`, `WORKTREE_PATH`

## Permission System

### Architecture

```
┌───────────────────┐     HTTP POST      ┌────────────────────┐
│ permission_hook.py│ ──────────────────→│ PermissionServer   │
│ (Claude CLI hook) │     /permission/   │ (localhost:9000)   │
└───────────────────┘       request      └─────────┬──────────┘
                                                   │
                                                   ▼
                                         ┌────────────────────┐
                                         │ PermissionManager  │
                                         │ - Stores pending   │
                                         │ - Signals asyncio  │
                                         └─────────┬──────────┘
                                                   │
                                                   ▼
                                         ┌────────────────────┐
                                         │ Daemon async loop  │
                                         │ - Sends Telegram   │
                                         │   notification     │
                                         │ - Waits for user   │
                                         │   callback/reply   │
                                         └────────────────────┘
```

### Flow

1. Claude subprocess calls tool
2. `permission_hook.py` intercepts (if `CLAUDE_ARMY_MANAGED=1`)
3. Hook POSTs to `localhost:9000/permission/request`
4. PermissionManager stores pending request, signals daemon
5. Daemon sends Telegram notification with Allow/Deny buttons
6. User clicks button or replies
7. PermissionManager releases blocked hook with decision
8. Hook returns decision to Claude CLI

### Auto-allowed Tools

These tools are auto-allowed (no Telegram prompt):
- `Read`
- `Grep`
- `Glob`
- `TodoRead`
- `TodoWrite`

### Permission Notification Format

```
Claude is asking permission to run:
```bash
command here
```
_description_
```

Buttons: `✓ Allow` | `✗ Deny`

After user action, button updates to show decision (✓ Allowed, ✗ Denied).

### Reply Context

When users reply to messages, the daemon includes context metadata:
```
[Replying to msg_id=123 topic=456 from=John]
Original message text

[msg_id=789]
User's reply text
```

This helps Claude understand the conversation context.

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

Permission prompts get two buttons:
- `✓ Allow` (callback_data: "allow:tool_use_id")
- `✗ Deny` (callback_data: "deny:tool_use_id")

### Tool Formatting

| Tool | Format |
|------|--------|
| Bash | Code block with command + description |
| Edit | Unified diff |
| Write | File path + content in code block |
| Read | File path |
| AskUserQuestion | Questions with options |
| Other | JSON of input |

### Markdown Handling

All messages use MarkdownV2 for consistency. Escaping approach:
- Text outside code blocks: escape all special chars
- Code blocks: preserve as-is, only replace ``` with ''' inside
- Inline code: preserve as-is

### Notification Routing

```python
def route_notification(session_id, notification):
    topic_id = registry.get_topic_for_session(session_id)

    if topic_id:
        send_to_topic(topic_id, notification)
    else:
        send_to_general_topic(notification, prefix="[unknown session]")
```

## Telegram Polling

### Update Types

#### Callback Query (button click)
```json
{
  "callback_query": {
    "id": "...",
    "data": "allow:tool_use_id" | "deny:tool_use_id",
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
    "message_thread_id": 789,
    "reply_to_message": {"message_id": 123}
  }
}
```

### Response Handling

| Action | Condition | Effect |
|--------|-----------|--------|
| Allow | callback_data="allow:..." | PermissionManager.respond("allow") |
| Deny | callback_data="deny:..." | PermissionManager.respond("deny") |
| Text reply | Any text message | Route to subprocess via stream-json |

### Button Updates

After action:
- Allow → "✓ Allowed"
- Deny → "✗ Denied"

## State Management

### Threading Model

- **Main event loop**: asyncio (handles Claude events, Telegram polling, permission checks)
- **Permission HTTP server**: separate daemon thread (threading.Thread)
- **Telegram polling**: uses asyncio.to_thread() for blocking HTTP calls
- **Claude subprocesses**: managed via asyncio.create_subprocess_exec()

### Registry

`operator/registry.json` stores:
- Task metadata (name, type, path, topic_id)
- Session tracking (session_id, pid)
- O(1) indexes for topic/session/path lookups

### Config

`operator/config.json` stores:
- `group_id` - Telegram group ID
- `general_topic_id` - General topic ID
- `telegram_offset` - Poll offset for crash recovery
- `topic_mappings` - topic_id → name mappings for recovery

### Cleanup

On daemon shutdown:
- Signal handlers call os._exit(0) immediately
- PID file cleaned up via atexit

## Registry Recovery

If `operator/registry.json` is corrupted/lost:

1. Scan for marker files: `find ~ -name "army.json" -path "*/.claude/*"`
2. For each `.claude/army.json` found:
   - Read task metadata (name, type, topic_id)
   - Add to registry
3. All tasks (worktree and session) are recovered

Use `/rebuild-registry` command to trigger this manually.

## Crash-Safe Topic Creation

Topic creation uses a pending marker pattern to handle daemon crashes.

### Problem

If daemon crashes between creating a Telegram topic and persisting the topic_id, the topic becomes orphaned. The Bot API cannot enumerate existing topics.

### Solution: Pending Marker Pattern

```
1. Write pending marker: {pending_topic_name: "task-foo", pending_since: "..."}
2. Create topic → API returns topic_id
3. Send setup message: "Setup in progress for task-foo..."
4. Complete marker: {name: "task-foo", topic_id: 123, ...}
5. Send completion: "Setup complete"
```

### Data Structures

**Pending marker** (in `.claude/army.json`):
```json
{
  "pending_topic_name": "task-foo",
  "pending_since": "2024-01-01T12:00:00Z"
}
```

**Topic mapping** (in `config.json`):
```json
{
  "topic_mappings": {
    "12345": "task-foo",
    "12346": "task-bar"
  }
}
```

**Persisted offset** (in `config.json`):
```json
{
  "telegram_offset": 123456789
}
```

### Recovery Mechanisms

1. **Automatic via forum_topic_created**: When polling sees a `forum_topic_created` event, store `topic_id → name` mapping in config.

2. **Message from unknown topic**: When a message arrives from a topic_id not in registry:
   - Try stored mapping → complete pending marker
   - Check if message text matches pending marker name → complete
   - Prompt user with list of pending tasks

3. **Offset persistence**: Store `telegram_offset` in config so we don't miss `forum_topic_created` events after restart.

### Crash Scenarios

| Crash Point | State | Recovery |
|-------------|-------|----------|
| Before step 2 | Pending marker, no topic | Clean up marker on next attempt |
| Between 2-3 | Pending marker, topic exists | `forum_topic_created` mapping → auto-recover |
| Between 3-4 | Pending marker, topic + setup msg | Reply to setup msg OR mapping lookup |
| After 4 | Complete marker | Just missing completion msg (cosmetic) |

### Guarantees

- **No duplicate topics**: Pending marker prevents new creation while uncertain
- **No orphaned topics**: Multi-tier recovery ensures we can always link topic_id to marker
- **No data loss**: Offset persistence prevents update replay

## Config Files

| File | Purpose |
|------|---------|
| `~/telegram.json` | Bot credentials (`bot_token`, `chat_id`) |
| `operator/config.json` | Group ID, topic IDs, telegram_offset |
| `operator/registry.json` | Task cache |
| `/tmp/claude-army-daemon.pid` | Daemon PID |

## Implementation Status

### Phase 1: Foundation ✓
- [x] Telegram Forum setup (`/setup` command)
- [x] Topic creation API integration
- [x] Registry cache implementation
- [x] Config management with auto-reload

### Phase 2: Operator Claude ✓
- [x] Operator subprocess management
- [x] Message routing to Operator process
- [x] Operator response capture and send to Telegram

### Phase 3: Task Management ✓
- [x] Spawn worktree task (create worktree, topic, marker, subprocess)
- [x] Spawn session (create topic, marker for existing directory)
- [x] Notification routing by task (lookup in registry)
- [x] Cleanup (worktree vs session behavior)
- [x] Permission warning when bot lacks Manage Topics rights

### Phase 4: Session Lifecycle ✓
- [x] Worker session resurrection on death
- [x] Pause/resume functionality
- [ ] Status indicators in topic names (stub exists, not implemented)

### Phase 5: Recovery & Polish
- [x] Registry recovery from `.claude/army.json` scans (`/rebuild-registry`)
- [x] Natural language command interpretation (via Operator)
- [ ] Cleanup after PR merge (not automated)

### Phase 6: JSON Headless Mode (Current) ✓
- [x] ClaudeProcess with stream-json I/O
- [x] ProcessManager for subprocess pool
- [x] PermissionManager + HTTP server
- [x] permission_hook.py for Claude CLI integration
- [x] TelegramAdapter for message routing
- [x] Session ID tracking in registry
- [x] Process resurrection via --resume
