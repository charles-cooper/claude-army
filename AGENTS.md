# Claude Telegram Notifier

Sends Telegram notifications when Claude Code needs input, and allows replying via Telegram.

## Current State

**Working:**
- Permission prompt notifications (Bash, Edit, Write, Read)
- Stop event notifications
- Idle prompt notifications (60+ seconds)
- Reply injection via tmux send-keys

## Files

- `telegram-hook.py` - Claude Code hook that sends notifications
- `telegram-daemon.py` - Polls Telegram for replies, injects into Claude via tmux
- `install.sh` - Automated setup
- `uninstall.sh` - Clean removal

## Architecture

```
Claude Code -> Hook (Stop/Notification) -> telegram-hook.py -> Telegram Bot -> User
User Reply -> Telegram Bot -> telegram-daemon.py -> tmux send-keys -> Claude Code
```

## Key Findings

### tmux send-keys quirk

When sending text + Enter to Claude Code, they MUST be separate commands:

```bash
# Works
tmux send-keys -t pane "text" && tmux send-keys -t pane Enter

# Does NOT work
tmux send-keys -t pane "text" Enter
```

The single-command version puts text in the input buffer but Enter doesn't submit. Separating into two commands fixes this.

### Notification hook matchers

| Matcher | Triggers |
|---------|----------|
| `permission_prompt` | Claude needs permission for a tool |
| `idle_prompt` | Claude idle 60+ seconds |
| Stop hook | Claude finishes responding |

Note: `elicitation_dialog` is for MCP tools only, not built-in tools like AskUserQuestion.

### Permission prompt payload

The `permission_prompt` notification only contains a generic message like "Claude needs your permission to use Bash". To get the actual command/diff, read the transcript file and extract the pending `tool_use` from the last assistant message.

## State Files

- `/tmp/claude-telegram-state.json` - Maps Telegram message IDs to sessions for reply routing (auto-cleaned on reboot)
- `/tmp/claude-telegram-hook.log` - Debug log for hook invocations

## Known Limitations

- **Diff green coloring**: Telegram Android doesn't render green for `+` lines in diff syntax highlighting - only red `-` lines show color. Desktop works fine. Tested alternatives (`patch`, `udiff`, full git headers) - none work. This is a Telegram Android client limitation (tested v12.2.9).
- **Raw PTY injection**: TIOCSTI is disabled on modern Linux (`legacy_tiocsti=0`). Would need `sudo sysctl dev.tty.legacy_tiocsti=1` to enable. tmux send-keys is the workaround.

## Notification Features

- Permission prompts include assistant's context message before tool details
- AskUserQuestion shows the question and answer options
- Bash shows command + description
- Edit shows unified diff

## TODO

- [x] Handle multiple Claude sessions (pane-based routing)
