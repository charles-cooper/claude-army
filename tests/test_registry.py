"""Tests for registry.py - Registry, Config, and marker file operations."""

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest


class TestRegistry:
    """Test Registry class."""

    def test_registry_add_and_get_task(self, temp_dir):
        """Test adding and getting tasks."""
        from registry import Registry, reset_singletons
        reset_singletons()

        registry_path = Path(temp_dir) / "registry.json"
        with patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)):

            registry = Registry()
            registry.add_task("test_task", {"type": "session", "path": "/tmp/test"})

            task = registry.get_task("test_task")
            assert task is not None
            assert task["type"] == "session"

            unknown = registry.get_task("unknown")
            assert unknown is None

    def test_registry_remove_task(self, temp_dir):
        """Test removing tasks."""
        from registry import Registry, reset_singletons
        reset_singletons()

        registry_path = Path(temp_dir) / "registry.json"
        with patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)):

            registry = Registry()
            registry.add_task("to_remove", {"type": "session"})
            assert registry.get_task("to_remove") is not None

            registry.remove_task("to_remove")
            assert registry.get_task("to_remove") is None

    def test_registry_get_all_tasks(self, temp_dir):
        """Test getting all tasks."""
        from registry import Registry, reset_singletons
        reset_singletons()

        registry_path = Path(temp_dir) / "registry.json"
        with patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)):

            registry = Registry()
            registry.add_task("task1", {"type": "session"})
            registry.add_task("task2", {"type": "worktree"})

            tasks = registry.get_all_tasks()
            assert len(tasks) == 2
            names = [t[0] for t in tasks]
            assert "task1" in names
            assert "task2" in names

    def test_registry_find_task_by_topic(self, temp_dir):
        """Test finding task by topic_id."""
        from registry import Registry, reset_singletons
        reset_singletons()

        registry_path = Path(temp_dir) / "registry.json"
        with patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)):

            registry = Registry()
            registry.add_task("topic_task", {"type": "session", "topic_id": 789})

            result = registry.find_task_by_topic(789)
            assert result is not None
            assert result[0] == "topic_task"

            result = registry.find_task_by_topic(999)
            assert result is None

    def test_registry_find_task_by_path(self, temp_dir):
        """Test finding task by path."""
        from registry import Registry, reset_singletons
        reset_singletons()

        registry_path = Path(temp_dir) / "registry.json"
        with patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)):

            registry = Registry()
            registry.add_task("path_task", {"type": "session", "path": "/home/test"})

            result = registry.find_task_by_path("/home/test")
            assert result is not None
            assert result[0] == "path_task"

    def test_registry_update_session_tracking(self, temp_dir):
        """Test updating session tracking fields."""
        from registry import Registry, reset_singletons
        reset_singletons()

        registry_path = Path(temp_dir) / "registry.json"
        with patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)):

            registry = Registry()
            registry.add_task("tracked_task", {"type": "session"})

            registry.update_task_session_tracking(
                "tracked_task", session_id="new_session_123", pid=9999, status="active"
            )

            task = registry.get_task("tracked_task")
            assert task["session_id"] == "new_session_123"
            assert task["pid"] == 9999
            assert task["status"] == "active"

    def test_registry_clear(self, temp_dir):
        """Test clearing registry."""
        from registry import Registry, reset_singletons
        reset_singletons()

        registry_path = Path(temp_dir) / "registry.json"
        with patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)):

            registry = Registry()
            registry.add_task("clear_test", {"type": "session"})

            registry.clear()
            assert len(registry.get_all_tasks()) == 0


class TestConfig:
    """Test Config class."""

    def test_config_get_set_delete(self, temp_dir):
        """Test config get/set/delete operations."""
        from registry import Config, reset_singletons
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)):

            config = Config()
            config.set("test_key", "test_value")
            assert config.get("test_key") == "test_value"

            config.delete("test_key")
            assert config.get("test_key") is None

    def test_config_group_id_property(self, temp_dir):
        """Test group_id property."""
        from registry import Config, reset_singletons
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)):

            config = Config()
            assert config.group_id is None

            config.group_id = -1001234567890
            assert config.group_id == -1001234567890

    def test_config_is_configured(self, temp_dir):
        """Test is_configured method."""
        from registry import Config, reset_singletons
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)):

            config = Config()
            assert config.is_configured() is False

            config.group_id = -1001234567890
            assert config.is_configured() is True

    def test_config_topic_mapping(self, temp_dir):
        """Test topic mapping storage."""
        from registry import Config, reset_singletons
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)):

            config = Config()
            config.store_topic_mapping(123, "test_topic")
            assert config.get_topic_name(123) == "test_topic"
            assert config.get_topic_name(999) is None

    def test_config_clear(self, temp_dir):
        """Test config clear."""
        from registry import Config, reset_singletons
        reset_singletons()

        config_path = Path(temp_dir) / "config.json"
        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)):

            config = Config()
            config.group_id = -1001234567890

            config.clear()
            assert config.group_id is None


class TestMarkerFiles:
    """Test marker file functions."""

    def test_write_and_read_marker_file(self, temp_dir):
        """Test writing and reading marker files."""
        from registry import write_marker_file, read_marker_file, get_marker_path

        data = {"name": "test_task", "type": "session", "topic_id": 123}
        write_marker_file(temp_dir, data)

        marker_path = get_marker_path(temp_dir)
        assert marker_path.exists()

        read_data = read_marker_file(temp_dir)
        assert read_data == data

    def test_read_marker_file_not_found(self, temp_dir):
        """Test reading non-existent marker file."""
        from registry import read_marker_file

        result = read_marker_file(temp_dir)
        assert result is None

    def test_remove_marker_file(self, temp_dir):
        """Test removing marker file."""
        from registry import write_marker_file, remove_marker_file, get_marker_path

        write_marker_file(temp_dir, {"name": "test"})
        assert get_marker_path(temp_dir).exists()

        result = remove_marker_file(temp_dir)
        assert result is True
        assert not get_marker_path(temp_dir).exists()

        result = remove_marker_file(temp_dir)
        assert result is False

    def test_is_managed_directory(self, temp_dir):
        """Test is_managed_directory check."""
        from registry import write_marker_file, is_managed_directory

        assert is_managed_directory(temp_dir) is False

        write_marker_file(temp_dir, {"name": "test"})
        assert is_managed_directory(temp_dir) is True


class TestReadJsonErrorHandling:
    """Test _read_json error handling."""

    def test_read_json_json_decode_error(self, tmp_path):
        """Test _read_json returns None on JSONDecodeError."""
        from registry import _read_json

        bad_json = tmp_path / "bad.json"
        bad_json.write_text("{ invalid json }")

        result = _read_json(bad_json)
        assert result is None

    def test_read_json_io_error(self, tmp_path):
        """Test _read_json returns None on IOError."""
        from registry import _read_json

        dir_path = tmp_path / "is_dir.json"
        dir_path.mkdir()

        result = _read_json(dir_path)
        assert result is None

    def test_read_json_missing_file(self, tmp_path):
        """Test _read_json returns {} for missing file."""
        from registry import _read_json

        missing = tmp_path / "missing.json"
        result = _read_json(missing)
        assert result == {}

    def test_read_json_valid(self, tmp_path):
        """Test _read_json returns parsed dict for valid JSON."""
        from registry import _read_json

        valid_json = tmp_path / "valid.json"
        valid_json.write_text('{"key": "value"}')

        result = _read_json(valid_json)
        assert result == {"key": "value"}


class TestWriteJsonCleanup:
    """Test _write_json cleanup on failure."""

    def test_write_json_success(self, tmp_path):
        """Test _write_json writes atomically."""
        from registry import _write_json

        output = tmp_path / "output.json"
        with patch("registry.CLAUDE_ARMY_DIR", tmp_path):
            _write_json(output, {"test": "data"})

        assert output.exists()
        content = json.loads(output.read_text())
        assert content == {"test": "data"}

    def test_write_json_cleanup_on_failure(self, tmp_path):
        """Test _write_json cleans up temp file on failure."""
        from registry import _write_json

        output = tmp_path / "output.json"

        with patch("registry.CLAUDE_ARMY_DIR", tmp_path), \
             patch("os.rename", side_effect=OSError("Rename failed")):
            with pytest.raises(OSError):
                _write_json(output, {"test": "data"})

        temp_files = list(tmp_path.glob("*.tmp"))
        assert len(temp_files) == 0

    def test_write_json_cleanup_unlink_fails(self, tmp_path):
        """Test _write_json handles cleanup failure gracefully."""
        from registry import _write_json

        output = tmp_path / "output.json"

        with patch("registry.CLAUDE_ARMY_DIR", tmp_path), \
             patch("os.rename", side_effect=OSError("Rename failed")), \
             patch("os.unlink", side_effect=OSError("Unlink failed")):
            with pytest.raises(OSError, match="Rename failed"):
                _write_json(output, {"test": "data"})


class TestReloadableJSONReload:
    """Test ReloadableJSON._reload return False."""

    def test_reload_returns_false_on_parse_error(self, tmp_path):
        """Test _reload returns False when _read_json returns None."""
        from registry import Config, reset_singletons
        reset_singletons()

        config_path = tmp_path / "config.json"
        config_path.write_text("{ invalid json }")

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.CLAUDE_ARMY_DIR", tmp_path):
            config = Config()
            assert config._cache == {}

    def test_reload_returns_true_on_success(self, tmp_path):
        """Test _reload returns True on successful read."""
        from registry import Config, reset_singletons
        reset_singletons()

        config_path = tmp_path / "config.json"
        config_path.write_text('{"key": "value"}')

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.CLAUDE_ARMY_DIR", tmp_path):
            config = Config()
            assert config._cache == {"key": "value"}


class TestReloadableJSONMaybeReload:
    """Test ReloadableJSON._maybe_reload mtime check."""

    def test_maybe_reload_detects_external_change(self, tmp_path):
        """Test _maybe_reload detects file changes."""
        from registry import Config, reset_singletons
        reset_singletons()

        config_path = tmp_path / "config.json"
        config_path.write_text('{"key": "original"}')

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.CLAUDE_ARMY_DIR", tmp_path):
            config = Config()
            assert config.get("key") == "original"

            time.sleep(0.01)
            config_path.write_text('{"key": "modified"}')

            assert config.get("key") == "modified"

    def test_maybe_reload_handles_oserror(self, tmp_path):
        """Test _maybe_reload handles OSError gracefully."""
        from registry import Config, reset_singletons
        reset_singletons()

        config_path = tmp_path / "config.json"
        config_path.write_text('{"key": "value"}')

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.CLAUDE_ARMY_DIR", tmp_path):
            config = Config()

            with patch("pathlib.Path.stat", side_effect=OSError("stat failed")):
                value = config.get("key")
                assert value == "value"

    def test_maybe_reload_file_deleted(self, tmp_path):
        """Test _maybe_reload when file is deleted."""
        from registry import Config, reset_singletons
        reset_singletons()

        config_path = tmp_path / "config.json"
        config_path.write_text('{"key": "value"}')

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.CLAUDE_ARMY_DIR", tmp_path):
            config = Config()
            assert config.get("key") == "value"

            config_path.unlink()

            value = config.get("key")
            assert value == "value"


class TestConfigSetters:
    """Test Config property setters."""

    def test_flush_handles_oserror_on_stat(self, tmp_path):
        """Test _flush handles OSError when getting mtime after write."""
        from registry import Config, reset_singletons
        reset_singletons()

        config_path = tmp_path / "config.json"

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.CLAUDE_ARMY_DIR", tmp_path):
            config = Config()

            config.set("key", "value")
            assert config.get("key") == "value"

            original_stat = Path.stat

            def stat_only_fail_config(self, **kwargs):
                if self == config_path:
                    raise OSError("stat failed after write")
                return original_stat(self, **kwargs)

            with patch("pathlib.Path.stat", stat_only_fail_config):
                config.set("key2", "value2")
                assert config._cache.get("key2") == "value2"

    def test_general_topic_id_setter(self, tmp_path):
        """Test general_topic_id setter."""
        from registry import Config, reset_singletons
        reset_singletons()

        config_path = tmp_path / "config.json"

        with patch("registry.CONFIG_FILE", config_path), \
             patch("registry.CLAUDE_ARMY_DIR", tmp_path):
            config = Config()
            assert config.general_topic_id is None

            config.general_topic_id = 12345
            assert config.general_topic_id == 12345

            config2 = Config()
            config2._path = config_path
            config2._reload()
            assert config2.get("general_topic_id") == 12345


class TestRegistryTasksFallback:
    """Test Registry.tasks fallback."""

    def test_tasks_property_fallback(self, tmp_path):
        """Test tasks property returns empty dict fallback."""
        from registry import Registry, reset_singletons
        reset_singletons()

        registry_path = tmp_path / "registry.json"
        registry_path.write_text('{"other_key": "value"}')

        with patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", tmp_path):
            registry = Registry()
            tasks = registry.tasks
            assert tasks == {}

    def test_tasks_property_with_tasks(self, tmp_path):
        """Test tasks property returns existing tasks."""
        from registry import Registry, reset_singletons
        reset_singletons()

        registry_path = tmp_path / "registry.json"

        with patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", tmp_path):
            registry = Registry()
            registry.add_task("test", {"type": "session"})

            tasks = registry.tasks
            assert "test" in tasks


class TestRegistryUpdateTaskSessionTrackingEarlyReturn:
    """Test Registry.update_task_session_tracking early return."""

    def test_update_nonexistent_task_returns_early(self, tmp_path):
        """Test update_task_session_tracking returns early for unknown task."""
        from registry import Registry, reset_singletons
        reset_singletons()

        registry_path = tmp_path / "registry.json"

        with patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", tmp_path):
            registry = Registry()

            registry.update_task_session_tracking(
                "nonexistent_task",
                session_id="session123",
                pid=1234,
                status="active"
            )

            assert registry.get_task("nonexistent_task") is None

    def test_update_existing_task_partial_fields(self, tmp_path):
        """Test update_task_session_tracking with partial fields."""
        from registry import Registry, reset_singletons
        reset_singletons()

        registry_path = tmp_path / "registry.json"

        with patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", tmp_path):
            registry = Registry()
            registry.add_task("task1", {"type": "session"})

            registry.update_task_session_tracking("task1", session_id="sid123")
            task = registry.get_task("task1")
            assert task["session_id"] == "sid123"
            assert "pid" not in task
            assert "status" not in task

            registry.update_task_session_tracking("task1", pid=9999)
            task = registry.get_task("task1")
            assert task["pid"] == 9999

            registry.update_task_session_tracking("task1", status="paused")
            task = registry.get_task("task1")
            assert task["status"] == "paused"


class TestPendingMarkerFunctions:
    """Test pending marker functions (crash recovery)."""

    def test_write_marker_file_pending(self, tmp_path):
        """Test write_marker_file_pending creates pending marker."""
        from registry import write_marker_file_pending, read_marker_file

        directory = str(tmp_path / "project")
        Path(directory).mkdir()

        write_marker_file_pending(directory, "my_task")

        marker = read_marker_file(directory)
        assert marker is not None
        assert marker["pending_topic_name"] == "my_task"
        assert "pending_since" in marker

    def test_complete_pending_marker(self, tmp_path):
        """Test complete_pending_marker updates marker with topic_id."""
        from registry import write_marker_file_pending, complete_pending_marker, read_marker_file

        directory = str(tmp_path / "project")
        Path(directory).mkdir()

        write_marker_file_pending(directory, "my_task")

        complete_pending_marker(directory, "my_task", topic_id=12345, task_type="worktree")

        marker = read_marker_file(directory)
        assert marker is not None
        assert marker["name"] == "my_task"
        assert marker["topic_id"] == 12345
        assert marker["type"] == "worktree"
        assert "created_at" in marker
        assert "pending_topic_name" not in marker

    def test_get_pending_markers(self, tmp_path):
        """Test get_pending_markers finds pending markers."""
        from registry import write_marker_file_pending, complete_pending_marker, get_pending_markers

        dir1 = tmp_path / "project1"
        dir2 = tmp_path / "project2"
        dir1.mkdir()
        dir2.mkdir()

        write_marker_file_pending(str(dir1), "pending_task")

        complete_pending_marker(str(dir2), "completed_task", topic_id=999)

        with patch("registry.Path.home", return_value=tmp_path):
            pending = get_pending_markers()

        pending_names = [m.get("pending_topic_name") for m in pending]
        assert "pending_task" in pending_names
        assert "completed_task" not in pending_names

    def test_get_pending_marker_names(self, tmp_path):
        """Test get_pending_marker_names returns list of names."""
        from registry import write_marker_file_pending, get_pending_marker_names

        dir1 = tmp_path / "proj1"
        dir2 = tmp_path / "proj2"
        dir1.mkdir()
        dir2.mkdir()

        write_marker_file_pending(str(dir1), "task1")
        write_marker_file_pending(str(dir2), "task2")

        with patch("registry.Path.home", return_value=tmp_path):
            names = get_pending_marker_names()

        assert "task1" in names
        assert "task2" in names

    def test_find_pending_marker_by_name(self, tmp_path):
        """Test find_pending_marker_by_name finds specific marker."""
        from registry import write_marker_file_pending, find_pending_marker_by_name

        directory = tmp_path / "myproj"
        directory.mkdir()

        write_marker_file_pending(str(directory), "target_task")

        with patch("registry.Path.home", return_value=tmp_path):
            found = find_pending_marker_by_name("target_task")
            not_found = find_pending_marker_by_name("other_task")

        assert found is not None
        assert found["pending_topic_name"] == "target_task"
        assert found["path"] == str(directory)
        assert not_found is None


class TestScanForMarkerFiles:
    """Test scan_for_marker_files function."""

    def test_scan_for_marker_files(self, tmp_path):
        """Test scan_for_marker_files finds all markers."""
        from registry import write_marker_file, scan_for_marker_files

        proj1 = tmp_path / "project1"
        proj2 = tmp_path / "project2"
        proj1.mkdir()
        proj2.mkdir()

        write_marker_file(str(proj1), {"name": "task1", "type": "session", "topic_id": 100})
        write_marker_file(str(proj2), {"name": "task2", "type": "worktree", "topic_id": 200})

        markers = scan_for_marker_files([str(tmp_path)])

        assert len(markers) == 2
        names = [m["name"] for m in markers]
        assert "task1" in names
        assert "task2" in names

        for m in markers:
            assert "path" in m

    def test_scan_for_marker_files_empty(self, tmp_path):
        """Test scan_for_marker_files with no markers."""
        from registry import scan_for_marker_files

        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        markers = scan_for_marker_files([str(empty_dir)])
        assert markers == []

    def test_scan_for_marker_files_timeout(self, tmp_path):
        """Test scan_for_marker_files handles timeout."""
        from registry import scan_for_marker_files
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("find", 30)):
            markers = scan_for_marker_files([str(tmp_path)])
            assert markers == []

    def test_scan_for_marker_files_exception(self, tmp_path):
        """Test scan_for_marker_files handles exceptions."""
        from registry import scan_for_marker_files

        with patch("subprocess.run", side_effect=Exception("Unexpected error")):
            markers = scan_for_marker_files([str(tmp_path)])
            assert markers == []


class TestRebuildRegistryFromMarkers:
    """Test rebuild_registry_from_markers function."""

    def test_rebuild_registry_from_markers(self, tmp_path):
        """Test rebuilding registry from marker files."""
        from registry import (
            write_marker_file, rebuild_registry_from_markers,
            Registry, reset_singletons
        )
        reset_singletons()

        registry_path = tmp_path / "registry.json"

        proj1 = tmp_path / "project1"
        proj2 = tmp_path / "project2"
        proj1.mkdir()
        proj2.mkdir()

        write_marker_file(str(proj1), {
            "name": "recovered_task1",
            "type": "session",
            "topic_id": 111,
        })
        write_marker_file(str(proj2), {
            "name": "recovered_task2",
            "type": "worktree",
            "topic_id": 222,
            "repo": "/home/user/main-repo",
        })

        with patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", tmp_path):
            count = rebuild_registry_from_markers([str(tmp_path)])

            assert count == 2

            registry = Registry()
            task1 = registry.get_task("recovered_task1")
            task2 = registry.get_task("recovered_task2")

            assert task1 is not None
            assert task1["type"] == "session"
            assert task1["topic_id"] == 111

            assert task2 is not None
            assert task2["type"] == "worktree"
            assert task2["repo"] == "/home/user/main-repo"

    def test_rebuild_skips_existing_tasks(self, tmp_path):
        """Test rebuild_registry_from_markers skips already registered tasks."""
        from registry import (
            write_marker_file, rebuild_registry_from_markers,
            Registry, reset_singletons
        )
        reset_singletons()

        registry_path = tmp_path / "registry.json"

        proj = tmp_path / "project"
        proj.mkdir()

        write_marker_file(str(proj), {
            "name": "existing_task",
            "type": "session",
            "topic_id": 333,
        })

        with patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", tmp_path):
            registry = Registry()
            registry.add_task("existing_task", {"type": "session", "topic_id": 999})

            count = rebuild_registry_from_markers([str(tmp_path)])

            assert count == 0

            task = registry.get_task("existing_task")
            assert task["topic_id"] == 999

    def test_rebuild_skips_invalid_markers(self, tmp_path):
        """Test rebuild_registry_from_markers skips markers without name/path."""
        from registry import (
            write_marker_file, rebuild_registry_from_markers,
            Registry, reset_singletons
        )
        reset_singletons()

        registry_path = tmp_path / "registry.json"

        proj1 = tmp_path / "project1"
        proj2 = tmp_path / "project2"
        proj1.mkdir()
        proj2.mkdir()

        write_marker_file(str(proj1), {
            "name": "valid_task",
            "type": "session",
            "topic_id": 444,
        })

        write_marker_file(str(proj2), {
            "type": "session",
            "topic_id": 555,
        })

        with patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", tmp_path):
            count = rebuild_registry_from_markers([str(tmp_path)])

            assert count == 1

            registry = Registry()
            assert registry.get_task("valid_task") is not None


class TestRegistryReloadAfterExternalChange:
    """Test Registry._reload override ensures tasks key exists."""

    def test_registry_reload_ensures_tasks_key(self, tmp_path):
        """Test Registry._reload adds tasks key if missing."""
        from registry import Registry, reset_singletons
        reset_singletons()

        registry_path = tmp_path / "registry.json"
        registry_path.write_text('{"other": "data"}')

        with patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", tmp_path):
            registry = Registry()

            assert "tasks" in registry._cache


class TestRegistryGetTopicForSession:
    """Test Registry.get_topic_for_session method."""

    def test_get_topic_for_session_found(self, tmp_path):
        """Test get_topic_for_session returns topic_id when session found."""
        from registry import Registry, reset_singletons
        reset_singletons()

        registry_path = tmp_path / "registry.json"

        with patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", tmp_path):
            registry = Registry()
            registry.add_task("my_task", {
                "type": "session",
                "topic_id": 12345,
                "session_id": "session-abc-123"
            })

            topic_id = registry.get_topic_for_session("session-abc-123")
            assert topic_id == 12345

    def test_get_topic_for_session_not_found(self, tmp_path):
        """Test get_topic_for_session returns None when session not found."""
        from registry import Registry, reset_singletons
        reset_singletons()

        registry_path = tmp_path / "registry.json"

        with patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", tmp_path):
            registry = Registry()
            registry.add_task("my_task", {
                "type": "session",
                "topic_id": 12345,
                "session_id": "session-abc-123"
            })

            topic_id = registry.get_topic_for_session("unknown-session")
            assert topic_id is None

    def test_get_topic_for_session_no_topic_id(self, tmp_path):
        """Test get_topic_for_session returns None when task has no topic_id."""
        from registry import Registry, reset_singletons
        reset_singletons()

        registry_path = tmp_path / "registry.json"

        with patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", tmp_path):
            registry = Registry()
            registry.add_task("my_task", {
                "type": "session",
                "session_id": "session-no-topic"
                # No topic_id
            })

            topic_id = registry.get_topic_for_session("session-no-topic")
            assert topic_id is None

    def test_get_topic_for_session_empty_registry(self, tmp_path):
        """Test get_topic_for_session returns None with empty registry."""
        from registry import Registry, reset_singletons
        reset_singletons()

        registry_path = tmp_path / "registry.json"

        with patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", tmp_path):
            registry = Registry()

            topic_id = registry.get_topic_for_session("any-session")
            assert topic_id is None

    def test_get_topic_for_session_multiple_tasks(self, tmp_path):
        """Test get_topic_for_session finds correct task among multiple."""
        from registry import Registry, reset_singletons
        reset_singletons()

        registry_path = tmp_path / "registry.json"

        with patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", tmp_path):
            registry = Registry()
            registry.add_task("task1", {
                "type": "session",
                "topic_id": 111,
                "session_id": "session-1"
            })
            registry.add_task("task2", {
                "type": "session",
                "topic_id": 222,
                "session_id": "session-2"
            })
            registry.add_task("task3", {
                "type": "session",
                "topic_id": 333,
                "session_id": "session-3"
            })

            # Find middle task
            topic_id = registry.get_topic_for_session("session-2")
            assert topic_id == 222
