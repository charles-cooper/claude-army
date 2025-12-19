#!/usr/bin/env python3
"""Permission hook for Claude tool calls.

Only activates for daemon-managed sessions (CLAUDE_ARMY_MANAGED=1).
Non-managed sessions get clean passthrough.
"""

import json
import os
import sys
from typing import Literal

import requests


def is_managed_session() -> bool:
    """Check if this session is managed by claude-army daemon."""
    return os.environ.get("CLAUDE_ARMY_MANAGED") == "1"


def passthrough_response():
    """Return passthrough - let Claude handle normally."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            # No permissionDecision = passthrough
        }
    }


def permission_response(decision: Literal["allow", "deny"], reason: str = ""):
    """Return a permission decision response."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason
        }
    }


def request_permission(tool_name: str, tool_input: dict, tool_use_id: str,
                       session_id: str, cwd: str) -> tuple[Literal["allow", "deny"], str]:
    """Request permission from server with specific exception handling."""
    server_url = os.environ.get("PERMISSION_SERVER", "http://localhost:9000")
    endpoint = f"{server_url}/permission/request"

    payload = {
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_use_id": tool_use_id,
        "session_id": session_id,
        "cwd": cwd
    }

    try:
        resp = requests.post(endpoint, json=payload, timeout=300)
        resp.raise_for_status()
        result = resp.json()
        decision = result.get("decision")
        reason = result.get("reason", "")

        if decision not in ("allow", "deny"):
            raise ValueError(f"Invalid decision: {decision}")

        return (decision, reason)

    except requests.ConnectionError:
        # Server not running - config error for managed sessions
        raise RuntimeError("Permission server not running")

    except requests.Timeout:
        # User didn't respond in 5 min
        return ("deny", "Permission request timed out")

    except requests.HTTPError as e:
        raise RuntimeError(f"Permission server error: {e}")

    except ValueError as e:
        raise RuntimeError(str(e))


def main():
    # Check if managed session FIRST
    if not is_managed_session():
        json.dump(passthrough_response(), sys.stdout)
        sys.exit(0)

    # Parse hook input
    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"Hook: Invalid JSON input: {e}\n")
        json.dump(permission_response("allow", "Invalid hook input"), sys.stdout)
        sys.exit(0)

    # Extract required fields
    tool_name = hook_input.get("tool_name")
    tool_input = hook_input.get("tool_input", {})
    tool_use_id = hook_input.get("tool_use_id")
    session_id = hook_input.get("session_id")
    cwd = hook_input.get("cwd", os.getcwd())

    if not all([tool_name, tool_use_id, session_id]):
        sys.stderr.write("Hook: Missing required fields\n")
        json.dump(permission_response("deny", "Missing required fields"), sys.stdout)
        sys.exit(0)

    # Request permission
    try:
        decision, reason = request_permission(
            tool_name, tool_input, tool_use_id, session_id, cwd
        )
        json.dump(permission_response(decision, reason), sys.stdout)
        sys.exit(0)

    except RuntimeError as e:
        # Server/config errors - log and fail-open
        sys.stderr.write(f"Hook: {e}\n")
        json.dump(permission_response("allow", str(e)), sys.stdout)
        sys.exit(0)


if __name__ == "__main__":
    main()
