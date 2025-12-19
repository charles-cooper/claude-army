"""Tests for permission_hook.py - CLAUDE_ARMY_MANAGED behavior."""

import json
import io
import os
import pytest
import requests
from unittest.mock import patch, MagicMock

import permission_hook
from permission_hook import (
    is_managed_session,
    passthrough_response,
    permission_response,
    request_permission,
    main
)


class TestIsManagedSession:
    """Test is_managed_session() detection."""

    def test_not_managed_when_env_not_set(self, monkeypatch):
        """Not managed when CLAUDE_ARMY_MANAGED is not set."""
        monkeypatch.delenv("CLAUDE_ARMY_MANAGED", raising=False)
        assert is_managed_session() is False

    def test_not_managed_when_env_empty(self, monkeypatch):
        """Not managed when CLAUDE_ARMY_MANAGED is empty."""
        monkeypatch.setenv("CLAUDE_ARMY_MANAGED", "")
        assert is_managed_session() is False

    def test_not_managed_when_env_zero(self, monkeypatch):
        """Not managed when CLAUDE_ARMY_MANAGED is '0'."""
        monkeypatch.setenv("CLAUDE_ARMY_MANAGED", "0")
        assert is_managed_session() is False

    def test_managed_when_env_is_one(self, monkeypatch):
        """Managed when CLAUDE_ARMY_MANAGED is '1'."""
        monkeypatch.setenv("CLAUDE_ARMY_MANAGED", "1")
        assert is_managed_session() is True


class TestPassthroughResponse:
    """Test passthrough_response() output."""

    def test_passthrough_has_no_decision(self):
        """Passthrough response has no permissionDecision field."""
        resp = passthrough_response()
        assert "hookSpecificOutput" in resp
        assert resp["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert "permissionDecision" not in resp["hookSpecificOutput"]


class TestPermissionResponse:
    """Test permission_response() output."""

    def test_allow_response(self):
        """Allow response has correct structure."""
        resp = permission_response("allow", "User approved")
        assert resp["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert resp["hookSpecificOutput"]["permissionDecisionReason"] == "User approved"

    def test_deny_response(self):
        """Deny response has correct structure."""
        resp = permission_response("deny", "User rejected")
        assert resp["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert resp["hookSpecificOutput"]["permissionDecisionReason"] == "User rejected"


class TestRequestPermission:
    """Test request_permission() behavior."""

    def test_success_allow(self):
        """Server returns allow -> returns allow."""
        with patch('permission_hook.requests.post') as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {"decision": "allow", "reason": "User approved"}
            mock_post.return_value = mock_resp

            decision, reason = request_permission(
                "Bash", {"command": "ls"}, "toolu_123", "session_456", "/tmp"
            )

            assert decision == "allow"
            assert reason == "User approved"

    def test_success_deny(self):
        """Server returns deny -> returns deny."""
        with patch('permission_hook.requests.post') as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {"decision": "deny", "reason": "User rejected"}
            mock_post.return_value = mock_resp

            decision, reason = request_permission(
                "Bash", {"command": "rm -rf /"}, "toolu_123", "session_456", "/tmp"
            )

            assert decision == "deny"
            assert reason == "User rejected"

    def test_connection_error_raises_runtime_error(self):
        """Connection refused -> raises RuntimeError."""
        with patch('permission_hook.requests.post') as mock_post:
            mock_post.side_effect = requests.ConnectionError("Connection refused")

            with pytest.raises(RuntimeError) as exc_info:
                request_permission(
                    "Bash", {"command": "ls"}, "toolu_123", "session_456", "/tmp"
                )

            assert "Permission server not running" in str(exc_info.value)

    def test_timeout_returns_deny(self):
        """Request timeout -> returns deny."""
        with patch('permission_hook.requests.post') as mock_post:
            mock_post.side_effect = requests.Timeout("Read timed out")

            decision, reason = request_permission(
                "Bash", {"command": "ls"}, "toolu_123", "session_456", "/tmp"
            )

            assert decision == "deny"
            assert "timed out" in reason.lower()

    def test_http_error_raises_runtime_error(self):
        """HTTP 500 -> raises RuntimeError."""
        with patch('permission_hook.requests.post') as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_resp.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
            mock_post.return_value = mock_resp

            with pytest.raises(RuntimeError) as exc_info:
                request_permission(
                    "Bash", {"command": "ls"}, "toolu_123", "session_456", "/tmp"
                )

            assert "Permission server error" in str(exc_info.value)

    def test_invalid_decision_raises_runtime_error(self):
        """Invalid decision value from server -> raises RuntimeError."""
        with patch('permission_hook.requests.post') as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {"decision": "maybe", "reason": "idk"}
            mock_post.return_value = mock_resp

            with pytest.raises(RuntimeError) as exc_info:
                request_permission(
                    "Bash", {"command": "ls"}, "toolu_123", "session_456", "/tmp"
                )

            assert "Invalid decision" in str(exc_info.value)


class TestMainNonManaged:
    """Test main() behavior for non-managed sessions."""

    def test_non_managed_returns_passthrough(self, monkeypatch):
        """Non-managed session returns passthrough immediately."""
        monkeypatch.delenv("CLAUDE_ARMY_MANAGED", raising=False)

        stdout_mock = io.StringIO()
        monkeypatch.setattr("sys.stdout", stdout_mock)

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0
        output = json.loads(stdout_mock.getvalue())
        assert "hookSpecificOutput" in output
        assert "permissionDecision" not in output["hookSpecificOutput"]


class TestMainManaged:
    """Test main() behavior for managed sessions."""

    def test_managed_invalid_json_allows(self, monkeypatch):
        """Managed session with invalid JSON input -> allows (fail-open)."""
        monkeypatch.setenv("CLAUDE_ARMY_MANAGED", "1")

        stdin_mock = io.StringIO("not valid json")
        stdout_mock = io.StringIO()
        stderr_mock = io.StringIO()

        monkeypatch.setattr("sys.stdin", stdin_mock)
        monkeypatch.setattr("sys.stdout", stdout_mock)
        monkeypatch.setattr("sys.stderr", stderr_mock)

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0
        output = json.loads(stdout_mock.getvalue())
        assert output["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert "Invalid hook input" in output["hookSpecificOutput"]["permissionDecisionReason"]

    def test_managed_missing_fields_denies(self, monkeypatch):
        """Managed session with missing required fields -> denies."""
        monkeypatch.setenv("CLAUDE_ARMY_MANAGED", "1")

        hook_input = json.dumps({"tool_name": "Bash"})  # missing tool_use_id, session_id
        stdin_mock = io.StringIO(hook_input)
        stdout_mock = io.StringIO()
        stderr_mock = io.StringIO()

        monkeypatch.setattr("sys.stdin", stdin_mock)
        monkeypatch.setattr("sys.stdout", stdout_mock)
        monkeypatch.setattr("sys.stderr", stderr_mock)

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0
        output = json.loads(stdout_mock.getvalue())
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "Missing required fields" in output["hookSpecificOutput"]["permissionDecisionReason"]

    def test_managed_server_down_allows(self, monkeypatch):
        """Managed session with server down -> allows (fail-open)."""
        monkeypatch.setenv("CLAUDE_ARMY_MANAGED", "1")

        hook_input = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "tool_use_id": "toolu_123",
            "session_id": "session_456",
            "cwd": "/tmp"
        })
        stdin_mock = io.StringIO(hook_input)
        stdout_mock = io.StringIO()
        stderr_mock = io.StringIO()

        monkeypatch.setattr("sys.stdin", stdin_mock)
        monkeypatch.setattr("sys.stdout", stdout_mock)
        monkeypatch.setattr("sys.stderr", stderr_mock)

        with patch('permission_hook.requests.post') as mock_post:
            mock_post.side_effect = requests.ConnectionError("Connection refused")

            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(stdout_mock.getvalue())
        assert output["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert "Permission server not running" in output["hookSpecificOutput"]["permissionDecisionReason"]

        # Verify error was logged to stderr
        assert "Permission server not running" in stderr_mock.getvalue()

    def test_managed_timeout_denies(self, monkeypatch):
        """Managed session with timeout -> denies."""
        monkeypatch.setenv("CLAUDE_ARMY_MANAGED", "1")

        hook_input = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "tool_use_id": "toolu_123",
            "session_id": "session_456",
            "cwd": "/tmp"
        })
        stdin_mock = io.StringIO(hook_input)
        stdout_mock = io.StringIO()
        stderr_mock = io.StringIO()

        monkeypatch.setattr("sys.stdin", stdin_mock)
        monkeypatch.setattr("sys.stdout", stdout_mock)
        monkeypatch.setattr("sys.stderr", stderr_mock)

        with patch('permission_hook.requests.post') as mock_post:
            mock_post.side_effect = requests.Timeout("Read timed out")

            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(stdout_mock.getvalue())
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "timed out" in output["hookSpecificOutput"]["permissionDecisionReason"].lower()

    def test_managed_success_allow(self, monkeypatch):
        """Managed session with successful allow -> returns allow."""
        monkeypatch.setenv("CLAUDE_ARMY_MANAGED", "1")

        hook_input = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "tool_use_id": "toolu_123",
            "session_id": "session_456",
            "cwd": "/tmp"
        })
        stdin_mock = io.StringIO(hook_input)
        stdout_mock = io.StringIO()

        monkeypatch.setattr("sys.stdin", stdin_mock)
        monkeypatch.setattr("sys.stdout", stdout_mock)

        with patch('permission_hook.requests.post') as mock_post:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {"decision": "allow", "reason": "User approved"}
            mock_post.return_value = mock_resp

            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(stdout_mock.getvalue())
        assert output["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert output["hookSpecificOutput"]["permissionDecisionReason"] == "User approved"

    def test_managed_success_deny(self, monkeypatch):
        """Managed session with successful deny -> returns deny."""
        monkeypatch.setenv("CLAUDE_ARMY_MANAGED", "1")

        hook_input = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /"},
            "tool_use_id": "toolu_123",
            "session_id": "session_456",
            "cwd": "/tmp"
        })
        stdin_mock = io.StringIO(hook_input)
        stdout_mock = io.StringIO()

        monkeypatch.setattr("sys.stdin", stdin_mock)
        monkeypatch.setattr("sys.stdout", stdout_mock)

        with patch('permission_hook.requests.post') as mock_post:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {"decision": "deny", "reason": "User rejected"}
            mock_post.return_value = mock_resp

            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(stdout_mock.getvalue())
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert output["hookSpecificOutput"]["permissionDecisionReason"] == "User rejected"

    def test_managed_uses_default_cwd(self, monkeypatch):
        """Managed session uses os.getcwd() when cwd not provided."""
        monkeypatch.setenv("CLAUDE_ARMY_MANAGED", "1")

        hook_input = json.dumps({
            "tool_name": "Read",
            "tool_input": {},
            "tool_use_id": "toolu_cwd_test",
            "session_id": "session-cwd-test",
            # no cwd field
        })
        stdin_mock = io.StringIO(hook_input)
        stdout_mock = io.StringIO()

        monkeypatch.setattr("sys.stdin", stdin_mock)
        monkeypatch.setattr("sys.stdout", stdout_mock)

        captured_cwd = []

        def capture_request_permission(tool_name, tool_input, tool_use_id, session_id, cwd):
            captured_cwd.append(cwd)
            return ("allow", "Auto-allowed")

        with patch.object(permission_hook, "request_permission", capture_request_permission):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        assert captured_cwd[0] == os.getcwd()

    def test_managed_empty_tool_input(self, monkeypatch):
        """Managed session handles missing tool_input field."""
        monkeypatch.setenv("CLAUDE_ARMY_MANAGED", "1")

        hook_input = json.dumps({
            "tool_name": "TodoRead",
            "tool_use_id": "toolu_empty_input",
            "session_id": "session-empty",
            # no tool_input field
        })
        stdin_mock = io.StringIO(hook_input)
        stdout_mock = io.StringIO()

        monkeypatch.setattr("sys.stdin", stdin_mock)
        monkeypatch.setattr("sys.stdout", stdout_mock)

        captured_input = []

        def capture_request_permission(tool_name, tool_input, tool_use_id, session_id, cwd):
            captured_input.append(tool_input)
            return ("allow", "Auto-allowed")

        with patch.object(permission_hook, "request_permission", capture_request_permission):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        assert captured_input[0] == {}
