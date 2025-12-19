"""Tests for install.sh - Claude Code hook installation script."""

import json
import os
import subprocess
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).parent.parent / "install.sh"


@pytest.fixture
def isolated_home(tmp_path):
    """Create isolated HOME with telegram.json pre-configured to skip prompts."""
    home = tmp_path / "home"
    home.mkdir()

    # Create telegram.json to skip interactive prompts
    telegram_config = home / "telegram.json"
    telegram_config.write_text(json.dumps({
        "bot_token": "test_token_123",
        "chat_id": "123456789"
    }))

    # Create .claude directory
    claude_dir = home / ".claude"
    claude_dir.mkdir()

    return home


def run_install_script(home_dir: Path, script_dir: Path = None, stdin_input: str = "n\n") -> subprocess.CompletedProcess:
    """Run install.sh with custom HOME and SCRIPT_DIR.

    Args:
        home_dir: Directory to use as HOME
        script_dir: Directory containing the script (for SCRIPT_DIR)
        stdin_input: Input to provide to stdin (default "n\n" to skip overwriting telegram.json)
    """
    env = os.environ.copy()
    env["HOME"] = str(home_dir)

    # Get the parent directory of install.sh for SCRIPT_DIR
    if script_dir is None:
        script_dir = SCRIPT_PATH.parent

    # Run the script with stdin input to handle interactive prompts
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        env=env,
        capture_output=True,
        text=True,
        input=stdin_input,
        cwd=str(script_dir),  # Run from script directory
        timeout=10
    )
    return result


def get_hook_command(script_dir: Path) -> str:
    """Get the expected hook command for a given script directory."""
    return f"python3 {script_dir}/telegram-hook.py"


class TestFreshInstall:
    """Test install.sh on fresh system with no settings.json."""

    def test_creates_settings_json(self, isolated_home):
        """Test that install.sh creates settings.json when none exists."""
        settings_path = isolated_home / ".claude" / "settings.json"
        assert not settings_path.exists()

        result = run_install_script(isolated_home)

        assert result.returncode == 0, f"Script failed: {result.stderr}"
        assert settings_path.exists()

    def test_creates_notification_hooks(self, isolated_home):
        """Test that Notification hooks are created."""
        settings_path = isolated_home / ".claude" / "settings.json"

        run_install_script(isolated_home)

        settings = json.loads(settings_path.read_text())
        assert "hooks" in settings
        assert "Notification" in settings["hooks"]

        notif_hooks = settings["hooks"]["Notification"]
        matchers = [h["matcher"] for h in notif_hooks]
        assert "permission_prompt" in matchers

    def test_creates_precompact_hooks(self, isolated_home):
        """Test that PreCompact hooks are created for auto and manual."""
        settings_path = isolated_home / ".claude" / "settings.json"

        run_install_script(isolated_home)

        settings = json.loads(settings_path.read_text())
        precompact_hooks = settings["hooks"]["PreCompact"]
        matchers = [h["matcher"] for h in precompact_hooks]
        assert "auto" in matchers
        assert "manual" in matchers

    def test_creates_postcompact_hooks(self, isolated_home):
        """Test that PostCompact hooks are created for auto and manual."""
        settings_path = isolated_home / ".claude" / "settings.json"

        run_install_script(isolated_home)

        settings = json.loads(settings_path.read_text())
        postcompact_hooks = settings["hooks"]["PostCompact"]
        matchers = [h["matcher"] for h in postcompact_hooks]
        assert "auto" in matchers
        assert "manual" in matchers

    def test_hook_command_format(self, isolated_home):
        """Test that hook commands have correct format."""
        settings_path = isolated_home / ".claude" / "settings.json"

        run_install_script(isolated_home)

        settings = json.loads(settings_path.read_text())

        # Check a hook command
        notif_hook = settings["hooks"]["Notification"][0]
        cmd = notif_hook["hooks"][0]["command"]
        assert "python3" in cmd
        assert "telegram-hook.py" in cmd


class TestIdempotent:
    """Test that running install.sh twice doesn't duplicate hooks."""

    def test_no_duplicate_hooks_on_rerun(self, isolated_home):
        """Test running install.sh twice doesn't duplicate hooks."""
        settings_path = isolated_home / ".claude" / "settings.json"

        # Run install twice
        run_install_script(isolated_home)
        run_install_script(isolated_home)

        settings = json.loads(settings_path.read_text())

        # Count permission_prompt hooks
        notif_hooks = settings["hooks"]["Notification"]
        permission_prompts = [h for h in notif_hooks if h["matcher"] == "permission_prompt"]
        assert len(permission_prompts) == 1, f"Expected 1 permission_prompt hook, got {len(permission_prompts)}"

        # Count PreCompact auto hooks
        precompact_hooks = settings["hooks"]["PreCompact"]
        auto_hooks = [h for h in precompact_hooks if h["matcher"] == "auto"]
        assert len(auto_hooks) == 1, f"Expected 1 auto hook, got {len(auto_hooks)}"

    def test_stable_file_content_on_rerun(self, isolated_home):
        """Test that running install.sh twice produces same settings."""
        settings_path = isolated_home / ".claude" / "settings.json"

        run_install_script(isolated_home)
        first_content = settings_path.read_text()

        run_install_script(isolated_home)
        second_content = settings_path.read_text()

        # Parse and compare as JSON (formatting might differ)
        assert json.loads(first_content) == json.loads(second_content)


class TestPreservesExistingSettings:
    """Test that install.sh preserves existing settings."""

    def test_preserves_other_settings(self, isolated_home):
        """Test that non-hook settings are preserved."""
        settings_path = isolated_home / ".claude" / "settings.json"

        # Create settings with other keys
        existing_settings = {
            "someOtherSetting": True,
            "preferences": {
                "theme": "dark",
                "fontSize": 14
            }
        }
        settings_path.write_text(json.dumps(existing_settings, indent=2))

        run_install_script(isolated_home)

        settings = json.loads(settings_path.read_text())
        assert settings["someOtherSetting"] is True
        assert settings["preferences"]["theme"] == "dark"
        assert settings["preferences"]["fontSize"] == 14

    def test_preserves_existing_hooks(self, isolated_home):
        """Test that existing hooks from other sources are preserved."""
        settings_path = isolated_home / ".claude" / "settings.json"

        # Create settings with existing hooks
        existing_settings = {
            "hooks": {
                "Notification": [
                    {
                        "matcher": "some_other_event",
                        "hooks": [{"type": "command", "command": "echo other"}]
                    }
                ],
                "SomeOtherHook": [
                    {"matcher": "*", "hooks": [{"type": "command", "command": "echo test"}]}
                ]
            }
        }
        settings_path.write_text(json.dumps(existing_settings, indent=2))

        run_install_script(isolated_home)

        settings = json.loads(settings_path.read_text())

        # Check original hooks are preserved
        notif_hooks = settings["hooks"]["Notification"]
        other_event_hooks = [h for h in notif_hooks if h["matcher"] == "some_other_event"]
        assert len(other_event_hooks) == 1

        # Check SomeOtherHook is preserved
        assert "SomeOtherHook" in settings["hooks"]

    def test_merges_with_existing_notification_hooks(self, isolated_home):
        """Test that new hooks are added to existing Notification hooks."""
        settings_path = isolated_home / ".claude" / "settings.json"

        # Create settings with existing Notification hooks
        existing_settings = {
            "hooks": {
                "Notification": [
                    {"matcher": "existing_matcher", "hooks": [{"type": "command", "command": "echo existing"}]}
                ]
            }
        }
        settings_path.write_text(json.dumps(existing_settings, indent=2))

        run_install_script(isolated_home)

        settings = json.loads(settings_path.read_text())

        notif_hooks = settings["hooks"]["Notification"]
        matchers = [h["matcher"] for h in notif_hooks]

        # Both original and new hooks should exist
        assert "existing_matcher" in matchers
        assert "permission_prompt" in matchers


class TestUninstall:
    """Test uninstall functionality.

    Note: The current install.sh does not have explicit uninstall support.
    These tests document the expected behavior for manual removal.
    """

    def test_manual_hook_removal(self, isolated_home):
        """Test that hooks can be manually removed from settings.json."""
        settings_path = isolated_home / ".claude" / "settings.json"

        # Install hooks first
        run_install_script(isolated_home)

        settings = json.loads(settings_path.read_text())
        assert "hooks" in settings

        # Manually remove hooks (simulating uninstall)
        hook_cmd = get_hook_command(SCRIPT_PATH.parent)

        # Remove telegram-hook.py hooks from Notification
        if "Notification" in settings["hooks"]:
            settings["hooks"]["Notification"] = [
                h for h in settings["hooks"]["Notification"]
                if not any(hk.get("command", "").endswith("telegram-hook.py") for hk in h.get("hooks", []))
            ]

        # Remove telegram-hook.py hooks from PreCompact
        if "PreCompact" in settings["hooks"]:
            settings["hooks"]["PreCompact"] = [
                h for h in settings["hooks"]["PreCompact"]
                if not any(hk.get("command", "").endswith("telegram-hook.py") for hk in h.get("hooks", []))
            ]

        # Remove telegram-hook.py hooks from PostCompact
        if "PostCompact" in settings["hooks"]:
            settings["hooks"]["PostCompact"] = [
                h for h in settings["hooks"]["PostCompact"]
                if not any(hk.get("command", "").endswith("telegram-hook.py") for hk in h.get("hooks", []))
            ]

        settings_path.write_text(json.dumps(settings, indent=2))

        # Verify hooks are removed
        settings = json.loads(settings_path.read_text())
        for hook_type in ["Notification", "PreCompact", "PostCompact"]:
            for hook in settings["hooks"].get(hook_type, []):
                for h in hook.get("hooks", []):
                    assert "telegram-hook.py" not in h.get("command", "")


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_handles_empty_settings_file(self, isolated_home):
        """Test handling of empty settings.json file."""
        settings_path = isolated_home / ".claude" / "settings.json"
        settings_path.write_text("{}")

        result = run_install_script(isolated_home)

        assert result.returncode == 0
        settings = json.loads(settings_path.read_text())
        assert "hooks" in settings

    def test_creates_claude_directory_if_missing(self, isolated_home):
        """Test that .claude directory is created if missing."""
        claude_dir = isolated_home / ".claude"
        claude_dir.rmdir()  # Remove directory created by fixture

        assert not claude_dir.exists()

        run_install_script(isolated_home)

        assert claude_dir.exists()
        assert (claude_dir / "settings.json").exists()

    def test_handles_malformed_hooks_array(self, isolated_home):
        """Test handling of settings with empty hooks object."""
        settings_path = isolated_home / ".claude" / "settings.json"
        settings_path.write_text(json.dumps({"hooks": {}}))

        result = run_install_script(isolated_home)

        assert result.returncode == 0
        settings = json.loads(settings_path.read_text())
        assert "Notification" in settings["hooks"]
