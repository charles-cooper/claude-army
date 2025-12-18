#!/usr/bin/env python3
"""Telegram daemon entry point.

This is a thin wrapper around daemon_core.main().
All logic is in daemon_core.py for testability.
"""

import asyncio
import sys

from daemon_core import main

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
