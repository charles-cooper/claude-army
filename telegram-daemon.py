#!/usr/bin/env python3
"""Telegram daemon entry point.

This is a thin wrapper around daemon_core.main().
All logic is in daemon_core.py for testability.
"""

import asyncio
import os
import signal
import sys

from daemon_core import main


def _exit_immediately(signum, frame):
    """Exit immediately on SIGINT/SIGTERM. PID file cleanup happens on next start."""
    try:
        print("\nShutting down...", flush=True)
    except:
        pass
    os._exit(0)


if __name__ == "__main__":
    # Register signal handlers BEFORE asyncio.run() to avoid racing with asyncio's handlers
    signal.signal(signal.SIGINT, _exit_immediately)
    signal.signal(signal.SIGTERM, _exit_immediately)
    sys.exit(asyncio.run(main()))
