#!/usr/bin/env python3
"""Permission hook for Claude tool calls.

Invoked by Claude SDK as a PreToolUse hook. Reads tool info from stdin,
POSTs to permission server, blocks until user responds, outputs decision.
"""

import json
import os
import sys
import requests
from typing import Literal


def request_permission(
    tool_name: str,
    tool_input: dict,
    tool_use_id: str,
    session_id: str,
    cwd: str
) -> tuple[Literal["allow", "deny"], str]:
    """Request permission from server. Returns (decision, reason).

    Blocks until user responds (5 min timeout).
    On timeout or error, returns ("deny", reason).
    """
    server_url = os.environ.get("PERMISSION_SERVER", "http://localhost:9000")
    endpoint = f"{server_url}/permission/request"

    payload = {
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_use_id": tool_use_id,
        "session_id": session_id,
        "cwd": cwd
    }

    timeout = 300  # 5 minutes

    try:
        resp = requests.post(endpoint, json=payload, timeout=timeout)

        if not resp.ok:
            return ("deny", f"Permission server error: {resp.status_code}")

        result = resp.json()
        decision = result.get("decision", "deny")
        reason = result.get("reason", "")

        if decision not in ("allow", "deny"):
            return ("deny", f"Invalid decision from server: {decision}")

        return (decision, reason)

    except requests.Timeout:
        return ("deny", "Permission request timed out (5 min)")

    except Exception as e:
        return ("deny", f"Permission request failed: {e}")


def main():
    """Read hook input from stdin, request permission, output decision."""
    try:
        # Read hook input from stdin
        hook_input = json.load(sys.stdin)

        tool_name = hook_input.get("tool_name")
        tool_input = hook_input.get("tool_input", {})
        tool_use_id = hook_input.get("tool_use_id")
        session_id = hook_input.get("session_id")
        cwd = hook_input.get("cwd", os.getcwd())

        if not all([tool_name, tool_use_id, session_id]):
            # Missing required fields - deny
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Missing required fields in hook input"
                }
            }
            json.dump(output, sys.stdout)
            sys.exit(0)

        # Request permission from server
        decision, reason = request_permission(
            tool_name, tool_input, tool_use_id, session_id, cwd
        )

        # Output decision
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
                "permissionDecisionReason": reason
            }
        }

        json.dump(output, sys.stdout)
        sys.exit(0)

    except Exception as e:
        # On any error, deny with reason
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"Hook error: {e}"
            }
        }
        json.dump(output, sys.stdout)
        sys.exit(1)


if __name__ == "__main__":
    main()
