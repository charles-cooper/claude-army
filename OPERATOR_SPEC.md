# Claude Army - Multi-Instance Task Management

## Overview

Manage multiple Claude instances, each working on a separate task/feature/PR in isolated git worktrees. An Operator Claude interprets user instructions and manages Worker Claudes.

## Architecture

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
       │ Operator      │            │ Worker Sessions │
       │ tmux session  │            │ (per worktree)  │
       │ ~/claude-army │            │ repo/trees/X    │
       └───────────────┘            └─────────────────┘
               │                              │
               └──────────────┬───────────────┘
                              ▼
                    ┌─────────────────┐
                    │     Daemon      │
                    │ - Watches all   │
                    │   transcripts   │
                    │ - Routes to     │
                    │   correct topic │
                    │ - Handles input │
                    └─────────────────┘
```

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

### Entities

| Entity | Description | Lifetime |
|--------|-------------|----------|
| **Worktree Task** | Branch work in isolated git worktree | Until cleanup (deletes directory) |
| **Session** | Focused work in any directory | Until cleanup (preserves directory) |
| **Worker Claude** | Claude instance in task directory | Ephemeral, resurrected as needed |
| **Topic** | Telegram forum topic for task | Tied to task lifecycle |
| **Operator Claude** | Management Claude in ~/claude-army | Always running |

### Source of Truth Hierarchy

1. **Marker files** - `.claude/army.json` defines tasks (inside Claude's own state dir)
2. **Pinned messages** - Store task metadata (recoverable from Telegram)
3. **Registry** - Cache at `~/claude-army/operator/registry.json` (rebuildable from markers)
4. **tmux sessions** - Ephemeral, resurrected as needed

## Directory Structure

```
~/claude-army/                    # Project root (daemon runs here)
  telegram-daemon.py              # Daemon process
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
    fix-bug-123/
      .claude/
        army.json
        ...

~/projects/other-project/         # Existing directory (session, not worktree)
  .claude/
    army.json                     # Session marker
    ...
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

### Post-Worktree Setup Hook

If a repo contains `.claude-army-setup.sh` (in repo root), it runs after worktree creation:

```bash
#!/bin/bash
# .claude-army-setup.sh - runs in new worktree directory
# Example: create symlinks, copy .env, etc.

ln -sf ~/shared/.env .env
ln -sf ../main/node_modules node_modules
```

The script receives environment variables:
- `TASK_NAME` - name of the task
- `REPO_PATH` - path to main repo
- `WORKTREE_PATH` - path to new worktree

### Registry Cache Format

```json
// operator/registry.json (rebuildable from .claude/army.json scans)
{
  "tasks": {
    "feature-x": {
      "type": "worktree",
      "path": "/home/user/myrepo/trees/feature-x",
      "repo": "/home/user/myrepo",
      "topic_id": 123
    },
    "investigate-bug": {
      "type": "session",
      "path": "/home/user/other-project",
      "topic_id": 456
    }
  }
}
```

Note: `group_id`, `general_topic_id`, and `operator_pane` are in `config.json`, not registry.

## Telegram Setup

### Initial Setup Flow

1. User creates Telegram group, adds bot
2. User sends `/setup` in group
3. Bot checks: not already configured elsewhere
4. Bot converts group to Forum (supergroup with topics)
5. Bot creates "General" topic for Operator Claude
6. Bot stores `group_id` in config
7. Daemon starts Operator Claude session

### Multi-Group Protection

```
/setup in Group A → Success
/setup in Group B → "Already configured for Group A. Run /reset first."
```

### Topic Structure

- **General** - Operator Claude, management commands, fallback notifications
- **task-name** - One per task, Worker Claude notifications

### Pinned Message Metadata

Each task topic has a pinned message:
```json
{
  "task": "feature-x",
  "repo": "/home/user/myrepo",
  "branch": "feature-x",
  "worktree": "trees/feature-x",
  "description": "Add dark mode support",
  "created_at": "2025-01-01T00:00:00Z",
  "status": "active"
}
```

## Operator Claude

### Role

- Runs in `~/claude-army` directory
- Receives all messages from General topic
- Interprets user intent (natural language)
- Manages tasks (spawn, status, cleanup)
- **Manages todo queue** - receives todos from any topic, decides routing/priority
- **Updates AGENTS.md** - observes repeated difficulties across workers, updates project AGENTS.md with learnings
- **Spawn assistance** - learns from previous spawns, asks clarifying questions if task seems ambiguous, enriches initial prompts with context from past worker struggles
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

User: "Clean up the refactor-api task"
Operator: → Permission prompt: "Delete worktree and close topic?"
  → User approves
Operator: "Cleaned up refactor-api task."
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
  4. Pin metadata message in topic
  5. Create tmux session
  6. Start Claude with task description

Spawn Session (existing directory):
  1. Verify directory exists
  2. Create Telegram topic
  3. Write .claude/army.json marker
  4. Create tmux session
  5. Start Claude with task description

Auto-register (daemon discovers Claude):
  1. Daemon sees new transcript
  2. Create Telegram topic
  3. Write .claude/army.json marker
  4. Task is now tracked and routable

Running:
  - Notifications → task topic
  - User replies → Worker Claude
  - Permission prompts → task topic buttons

Death (crash, reboot):
  - Daemon detects missing session
  - Resurrects: `claude --resume` in directory
  - Topic continues working

Cleanup (worktree):
  - Kill session
  - Delete worktree (removes directory + .claude/army.json)
  - Close topic

Cleanup (session):
  - Kill session
  - Remove .claude/army.json (preserve directory)
  - Close topic
```

### tmux Session Naming

Short names for easy mobile access:
```
Operator: ca-op
Workers: ca-{task_name}
```

The `ca-` prefix (claude-army) avoids collisions with user sessions.
Task names must be unique across all repos.

### Session Working Directories

Each Claude session runs in its own directory for conversation isolation:
- **Daemon**: Runs from wherever invoked
- **Operator**: `<claude-army-dir>/operator/` (script-relative, not pwd-relative)
- **Workers**: `<worktree_path>`

Script-relative paths ensure the operator directory is always consistent regardless of where the daemon is invoked from. Each directory has its own `.claude/` conversation state.

### Claude Startup

- **Operator**: `claude --resume || claude` (fall back to fresh if no conversation)
- **New worker task**: `claude "<task description>"`
- **Worker resume after death**: `claude --resume || claude "<task description>"` (description stored in marker file)

## Daemon Changes

### Notification Routing

```python
def route_notification(pane, notification):
    worktree_path = get_worktree_for_pane(pane)

    if worktree_path and is_managed_worktree(worktree_path):
        task = load_marker_file(worktree_path)
        send_to_topic(task["topic_id"], notification)
    elif is_operator_pane(pane):
        send_to_general_topic(notification)
    else:
        # Fallback: non-managed Claude session
        send_to_general_topic(notification, prefix="[unmanaged]")
```

### Session Resurrection

```python
def check_and_resurrect():
    for repo in registry["repos"]:
        for task_name in repo["tasks"]:
            worktree = get_worktree_path(repo, task_name)
            marker = load_marker_file(worktree)

            if marker["status"] == "paused":
                continue

            session_name = f"claude-{repo_name}-{task_name}"
            if not tmux_session_exists(session_name):
                resurrect_session(worktree, session_name)
                log(f"Resurrected session for {task_name}")
```

### Message Routing to Operator

```python
def handle_general_topic_message(message):
    if is_command(message):  # /setup, /reset
        handle_command(message)
    else:
        # Forward to Operator Claude
        send_to_operator_pane(message.text)
```

## Commands

### Bot Commands (any topic)

| Command | Description |
|---------|-------------|
| `/setup` | Initialize group as Claude Army control center |
| `/reset` | Remove configuration (allows setup elsewhere) |
| `/help` | Show available commands |
| `/todo <item>` | Add todo to Operator's queue (from any topic) |
| `/debug` | Debug a notification (reply to it) |

### Natural Language (via Operator Claude)

- "Create a task to fix bug #123 in vyper"
- "What's the status of all tasks?"
- "Pause the feature-x task"
- "Clean up completed tasks"
- "List all repos"

**Routing rules:**
- `/todo` and `/debug` always route to the Operator, even from task topics
- When user replies to a message, the replied-to message (with Telegram metadata like msg_id, topic, timestamp) is included as context
- The Operator manages the todo queue and decides which worker (if any) should handle each item

## Registry Recovery

If `operator/registry.json` is corrupted/lost:

1. Scan for marker files: `find ~ -name "army.json" -path "*/.claude/*"`
2. For each `.claude/army.json` found:
   - Read task metadata (name, type, topic_id)
   - Verify topic exists (Telegram API)
   - Add to registry
3. All tasks (worktree and session) are recovered

Since marker files live in `.claude/` which Claude creates, all registered tasks are recoverable.

## Implementation Phases

### Phase 1: Foundation ✓
- [x] Telegram Forum setup (`/setup` command)
- [x] Topic creation API integration
- [x] Registry cache implementation
- [x] Config management with auto-reload

### Phase 2: Operator Claude ✓
- [x] Operator tmux session management
- [x] Message routing to Operator pane
- [x] Operator response capture and send to Telegram

### Phase 3: Task Management ✓
- [x] Spawn worktree task (create worktree, topic, marker, session)
- [x] Spawn session (create topic, marker for existing directory)
- [x] Auto-register discovered sessions (daemon writes marker)
- [x] Notification routing by task (lookup in registry)
- [x] Cleanup (worktree vs session behavior)
- [x] Permission warning when bot lacks Manage Topics rights

### Phase 4: Session Lifecycle
- [ ] Worker session resurrection on death
- [ ] Pause/resume functionality
- [ ] Status indicators in topic names

### Phase 5: Recovery & Polish
- [ ] Registry recovery from `.claude/army.json` scans
- [ ] Topic metadata recovery from Telegram
- [ ] Natural language command interpretation
- [ ] Cleanup after PR merge
