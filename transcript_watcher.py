"""Transcript watcher - monitors Claude transcripts for permission prompts."""

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from telegram_utils import pane_exists


@dataclass
class PendingTool:
    """A tool_use waiting for permission."""
    tool_id: str
    tool_name: str
    tool_input: dict
    assistant_text: str
    transcript_path: str
    pane: str
    cwd: str


@dataclass
class TranscriptWatcher:
    """Watches a single transcript file for new tool_use entries."""
    path: str
    pane: str
    position: int = 0
    notified_tools: set = field(default_factory=set)
    tool_results: set = field(default_factory=set)
    last_check: float = 0

    def check(self) -> list[PendingTool]:
        """Check for new pending tools. Returns list of tools needing notification."""
        pending = []
        try:
            with open(self.path, 'r') as f:
                f.seek(self.position)
                for line in f:
                    self._process_line(line, pending)
                self.position = f.tell()
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"Error reading {self.path}: {e}", flush=True)
        self.last_check = time.time()
        return pending

    def _process_line(self, line: str, pending: list[PendingTool]):
        """Process a single transcript line."""
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            return  # Partial line

        # Track tool_results
        if entry.get("type") == "user":
            for c in entry.get("message", {}).get("content", []):
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    tool_use_id = c.get("tool_use_id")
                    if tool_use_id:
                        self.tool_results.add(tool_use_id)
                        # Prune from notified set
                        self.notified_tools.discard(tool_use_id)

        # Check for new tool_use
        if entry.get("type") != "assistant":
            return

        assistant_text = ""
        tool_call = None

        for c in entry.get("message", {}).get("content", []):
            if isinstance(c, dict):
                if c.get("type") == "text":
                    assistant_text = c.get("text", "")
                elif c.get("type") == "tool_use":
                    tool_call = c

        if not tool_call:
            return

        tool_id = tool_call.get("id", "")
        tool_name = tool_call.get("name", "")

        # Skip if already notified or already has result
        if tool_id in self.notified_tools or tool_id in self.tool_results:
            return

        self.notified_tools.add(tool_id)

        # Get cwd from transcript path
        # Path format: ~/.claude/projects/{encoded-path}/{session}.jsonl
        cwd = ""
        parts = self.path.split("/")
        for i, p in enumerate(parts):
            if p == "projects" and i + 1 < len(parts):
                encoded = parts[i + 1]
                cwd = "/" + encoded.replace("-", "/")
                break

        pending.append(PendingTool(
            tool_id=tool_id,
            tool_name=tool_name,
            tool_input=tool_call.get("input", {}),
            assistant_text=assistant_text,
            transcript_path=self.path,
            pane=self.pane,
            cwd=cwd
        ))


class TranscriptManager:
    """Manages multiple transcript watchers."""

    def __init__(self):
        self.watchers: dict[str, TranscriptWatcher] = {}  # path -> watcher
        self.pane_to_transcript: dict[str, str] = {}  # pane -> transcript path

    def discover_transcripts(self):
        """Find active transcripts from tmux panes running claude."""
        try:
            result = os.popen("tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index} #{pane_current_path}'").read()
        except:
            return

        for line in result.strip().split("\n"):
            if not line:
                continue
            parts = line.split(" ", 1)
            if len(parts) != 2:
                continue
            pane, cwd = parts

            # Find transcript for this cwd
            encoded = cwd.replace("/", "-").lstrip("-")
            pattern = str(Path.home() / f".claude/projects/{encoded}/*.jsonl")

            import glob as glob_module
            transcripts = sorted(
                [Path(p) for p in glob_module.glob(pattern)],
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )

            if not transcripts:
                continue

            # Use most recently modified transcript
            transcript_path = str(transcripts[0])

            if transcript_path not in self.watchers:
                # Start watching from end of file
                try:
                    size = os.path.getsize(transcript_path)
                except:
                    size = 0
                self.watchers[transcript_path] = TranscriptWatcher(
                    path=transcript_path,
                    pane=pane,
                    position=size
                )
                print(f"Watching transcript: {transcript_path} (pane {pane})", flush=True)

            self.pane_to_transcript[pane] = transcript_path

    def add_from_state(self, state: dict):
        """Add watchers for transcripts mentioned in state file."""
        for msg_id, entry in state.items():
            transcript_path = entry.get("transcript_path")
            pane = entry.get("pane")
            if not transcript_path or not pane:
                continue
            if transcript_path in self.watchers:
                continue
            if not Path(transcript_path).exists():
                continue

            # Start watching from end (we already notified for earlier entries)
            try:
                size = os.path.getsize(transcript_path)
            except:
                size = 0
            self.watchers[transcript_path] = TranscriptWatcher(
                path=transcript_path,
                pane=pane,
                position=size
            )
            self.pane_to_transcript[pane] = transcript_path
            print(f"Watching transcript (from state): {transcript_path} (pane {pane})", flush=True)

    def cleanup_dead(self):
        """Remove watchers for dead panes."""
        dead = []
        for path, watcher in self.watchers.items():
            if not pane_exists(watcher.pane):
                dead.append(path)
        for path in dead:
            pane = self.watchers[path].pane
            del self.watchers[path]
            if pane in self.pane_to_transcript:
                del self.pane_to_transcript[pane]
            print(f"Stopped watching (pane dead): {path}", flush=True)

    def check_all(self) -> list[PendingTool]:
        """Check all watchers for pending tools."""
        all_pending = []
        for watcher in self.watchers.values():
            all_pending.extend(watcher.check())
        return all_pending
