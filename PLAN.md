# Claude Code Telegram Integration - Feature Plan

## Overview
Telegram bot for Claude Code notifications and remote control.

---

## 1. TODO: Smarter Message Deletion

### Problem
Currently we delete all notifications when tool_result arrives. But there's a difference:
- **False positive**: Tool was auto-approved, notification was unnecessary → delete
- **TUI-handled**: User responded in terminal, notification was valid but stale → keep/mark expired

### Solution
Track timing between notification sent and tool_result received:
- If tool_result arrives quickly (< ~1s after notification), likely auto-approved → delete
- If tool_result arrives later, user probably handled in TUI → mark expired instead

Alternative: Use hooks to confirm permission prompts (6s latency but definitive).

---

## 2. TODO: Handle Messages Before Idling

### Problem
When Claude finishes and is waiting for user input, we detect `end_turn` with text and send an idle notification. But if there was assistant text before a tool_use (which then got handled), the user never sees that text.

### Solution
Track assistant text that precedes tool calls. When tool completes (result arrives), if there was preceding text that wasn't shown, include it in the idle notification or send separately.

---

## 3. Claude Operator / Orchestration (Future)

### Vision
A Claude orchestration system controlled via Telegram:
- Spawn multiple Claude instances, each in its own tmux session
- Tear down instances when done
- Control them remotely from Telegram
- Eventually: a "manager" Claude that coordinates worker instances

### Telegram Commands

| Command | Action |
|---------|--------|
| `/spawn <project> [prompt]` | Create new tmux session, start Claude in project dir, optionally send initial prompt |
| `/list` | List active Claude sessions with status |
| `/kill <session>` | Send Ctrl+C, then kill tmux session |
| `/send <session> <text>` | Send text to specific session |
| `/status <session>` | Show recent transcript activity |
| `/todo <session> <item>` | Add item to Claude's internal todo stack |

### Future: Manager Claude

A supervisory Claude instance that:
1. Receives high-level tasks from user
2. Spawns worker Claudes for subtasks
3. Monitors their progress via transcripts
4. Handles permission prompts on their behalf (or escalates to human)
5. Aggregates results
