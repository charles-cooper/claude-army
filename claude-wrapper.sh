#!/bin/bash
# Wrapper to launch Claude with injectable stdin
# Usage: claude-wrapper.sh [claude args...]

FIFO_DIR="$HOME/.claude-fifos"
FIFO="$FIFO_DIR/$(pwd | tr '/' '_')"

mkdir -p "$FIFO_DIR"
rm -f "$FIFO"
mkfifo "$FIFO"

cleanup() {
    rm -f "$FIFO"
}
trap cleanup EXIT

# Merge fifo input with terminal - fifo takes priority when data available
(tail -f "$FIFO" 2>/dev/null &) | claude "$@"
