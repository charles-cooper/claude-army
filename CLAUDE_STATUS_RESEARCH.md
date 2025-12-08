# Claude Code Activity Detection Research

Research into what signals are available to detect Claude Code's activity state.

## Summary

**No heartbeat or "thinking" signal exists.** Claude Code only writes to the transcript when it produces output (messages, tool calls, tool results). We cannot distinguish between "Claude is thinking" and "Claude is stuck/frozen".

## Available Signals

### Transcript-Based (Primary)

Transcript files at `~/.claude/projects/{encoded-cwd}/*.jsonl` contain:

| Entry Type | Fields | Meaning |
|------------|--------|---------|
| `assistant` message | `timestamp`, `message.id`, `stop_reason`, `usage` | Claude generated a response |
| `tool_use` | `id`, `name`, `input` (in `message.content[]`) | Claude called a tool, waiting for execution |
| `tool_result` | `tool_use_id`, `content`, `is_error` | Tool finished, Claude can resume |
| `compact_boundary` | `compactMetadata.trigger`, `preTokens` | Context compaction occurred |

### Idle Detection

A text-only assistant message (no `tool_use` in `message.content`) indicates Claude finished and is waiting for user input.

### Timestamp-Based

Each entry has ISO 8601 `timestamp`. Can detect:
- **Stale**: No new entries for N seconds
- **Response latency**: Time between user message and assistant response
- **Tool duration**: Time between tool_use and tool_result

## What We Cannot Detect

| Scenario | Why |
|----------|-----|
| "Claude is thinking" | No intermediate writes during reasoning |
| "Claude is stuck" vs "slow" | Silence looks identical |
| API timeout/failure | No error signal written |
| Hung process | No heartbeat to detect |

## Practical Status Indicators

Given limitations, coarse-grained status only:

| Status | Detection Method |
|--------|------------------|
| Idle | Last entry is text-only assistant message |
| Pending | tool_use exists without corresponding tool_result |
| Working | tool_result just arrived (brief window) |
| Stale | No transcript writes for >N seconds |
| Dead | Tmux pane doesn't exist |

## Alternative Detection Methods

### File Modification Time

```python
mtime = os.path.getmtime(transcript_path)
if time.time() - mtime > 60:
    status = "stale"  # Could be thinking OR stuck
```

### Process Monitoring

```bash
# Check if Claude process is alive
ps aux | grep claude

# Check CPU usage (high = probably thinking)
top -p <pid>
```

### Tmux Pane Content

Could scrape tmux pane for spinner animation, but fragile and platform-dependent.

## Recommendations

1. **Use coarse status**: idle/pending/stale/dead
2. **Don't promise "thinking" indicator**: Can't reliably detect
3. **Typing indicator**: Send on transcript activity, let it expire naturally
4. **Stale timeout**: Alert if no activity for extended period, but caveat it could be normal thinking

## Files Reference

- `~/.claude/projects/{encoded-cwd}/*.jsonl` - Transcript files
- `~/.claude/debug/{sessionId}.txt` - Debug logs (async, not real-time)
- `/tmp/claude-telegram-state.json` - Our state tracking

## Future Improvements

Would require Claude Code changes:
- Add explicit heartbeat entries to transcript
- Add processing status field to entries
- Add timeout metadata to tool_use entries
- Expose status via API or status file
