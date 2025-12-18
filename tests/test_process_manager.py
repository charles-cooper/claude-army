"""Tests for process_manager.py - ProcessManager spawning and tracking."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from process_manager import ProcessManager


@pytest.mark.asyncio
class TestProcessManager:
    """Test ProcessManager spawns and tracks processes."""

    async def test_tracks_multiple_processes(self):
        """Test ProcessManager tracks multiple processes by name."""
        manager = ProcessManager()

        mock_proc1 = MagicMock()
        mock_proc1.session_id = "session-1"
        mock_proc1.is_running = True
        mock_proc1.pid = 1001

        mock_proc2 = MagicMock()
        mock_proc2.session_id = "session-2"
        mock_proc2.is_running = True
        mock_proc2.pid = 1002

        manager.processes["task1"] = mock_proc1
        manager.processes["task2"] = mock_proc2

        assert "task1" in manager.processes
        assert "task2" in manager.processes
        assert manager.get_process("task1") == mock_proc1
        assert manager.get_process("task2") == mock_proc2
        assert manager.is_running("task1") is True
        assert manager.get_all_tasks() == ["task1", "task2"]

    async def test_get_process_returns_none_for_unknown(self):
        """Test get_process returns None for unknown task."""
        manager = ProcessManager()

        assert manager.get_process("unknown") is None
        assert manager.is_running("unknown") is False


@pytest.mark.asyncio
class TestProcessManagerAdvanced:
    """Additional ProcessManager tests."""

    async def test_send_to_process_not_found(self):
        """Test send_to_process raises KeyError for unknown task."""
        manager = ProcessManager()
        with pytest.raises(KeyError) as exc_info:
            await manager.send_to_process("unknown_task", "Hello")
        assert "unknown_task" in str(exc_info.value)

    async def test_stop_process_not_found(self):
        """Test stop_process raises KeyError for unknown task."""
        manager = ProcessManager()
        with pytest.raises(KeyError) as exc_info:
            await manager.stop_process("unknown_task")
        assert "unknown_task" in str(exc_info.value)

    async def test_stop_all_empty(self):
        """Test stop_all with no processes."""
        manager = ProcessManager()
        await manager.stop_all()
        assert manager._shutdown is True

    async def test_stop_all_multiple(self):
        """Test stop_all stops multiple processes."""
        manager = ProcessManager()

        mock1 = AsyncMock()
        mock1.stop = AsyncMock()
        mock2 = AsyncMock()
        mock2.stop = AsyncMock()

        manager.processes["task1"] = mock1
        manager.processes["task2"] = mock2

        async def empty_monitor():
            await asyncio.sleep(10)

        manager._event_tasks["task1"] = asyncio.create_task(empty_monitor())
        manager._event_tasks["task2"] = asyncio.create_task(empty_monitor())

        await manager.stop_all()
        assert len(manager.processes) == 0
        assert manager._shutdown is True

    async def test_send_to_process_success(self):
        """Test send_to_process forwards message to correct process."""
        manager = ProcessManager()

        mock_process = AsyncMock()
        mock_process.send_message = AsyncMock()
        manager.processes["task1"] = mock_process

        await manager.send_to_process("task1", "Hello Claude")

        mock_process.send_message.assert_called_once_with("Hello Claude")

    async def test_spawn_process_success(self, temp_dir):
        """Test spawn_process creates and tracks a new process."""
        from registry import reset_singletons
        reset_singletons()

        registry_path = Path(temp_dir) / "registry.json"
        with patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)):

            mock_process = AsyncMock()
            mock_process.start = AsyncMock(return_value="test-session-123")
            mock_process.session_id = "test-session-123"
            mock_process.pid = 9999
            mock_process.is_running = True
            mock_process.send_message = AsyncMock()

            async def mock_events():
                yield {"type": "assistant", "message": {"content": []}}

            mock_process.events = mock_events

            with patch.dict("sys.modules", {"claude_process": MagicMock(ClaudeProcess=lambda **kw: mock_process)}):
                manager = ProcessManager()

                from registry import get_registry
                registry = get_registry()
                registry.add_task("test_task", {"type": "session", "path": temp_dir})

                result = await manager.spawn_process(
                    task_name="test_task",
                    cwd=temp_dir,
                    prompt="Hello Claude",
                    allowed_tools=["Read", "Grep"]
                )

                assert result == mock_process
                assert "test_task" in manager.processes
                mock_process.start.assert_called_once()
                mock_process.send_message.assert_called_once_with("Hello Claude")

    async def test_spawn_process_duplicate_raises(self, temp_dir):
        """Test spawn_process raises ValueError for duplicate task name."""
        manager = ProcessManager()

        mock_process = MagicMock()
        manager.processes["existing_task"] = mock_process

        with pytest.raises(ValueError) as exc_info:
            await manager.spawn_process(
                task_name="existing_task",
                cwd=temp_dir,
                prompt="Hello"
            )
        assert "already exists" in str(exc_info.value)

    async def test_resume_process_success(self, temp_dir):
        """Test resume_process resumes existing session."""
        from registry import reset_singletons
        reset_singletons()

        registry_path = Path(temp_dir) / "registry.json"
        with patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)):

            mock_process = AsyncMock()
            mock_process.start = AsyncMock(return_value="existing-session-456")
            mock_process.session_id = "existing-session-456"
            mock_process.pid = 8888
            mock_process.is_running = True

            async def mock_events():
                yield {"type": "system", "subtype": "init"}

            mock_process.events = mock_events

            with patch.dict("sys.modules", {"claude_process": MagicMock(ClaudeProcess=lambda **kw: mock_process)}):
                manager = ProcessManager()

                from registry import get_registry
                registry = get_registry()
                registry.add_task("resume_task", {"type": "session", "path": temp_dir, "session_id": "existing-session-456"})

                result = await manager.resume_process(
                    task_name="resume_task",
                    cwd=temp_dir,
                    session_id="existing-session-456",
                    allowed_tools=["Read"]
                )

                assert result == mock_process
                assert "resume_task" in manager.processes
                mock_process.start.assert_called_once()

    async def test_resume_process_duplicate_raises(self, temp_dir):
        """Test resume_process raises ValueError for duplicate task name."""
        manager = ProcessManager()

        mock_process = MagicMock()
        manager.processes["existing_task"] = mock_process

        with pytest.raises(ValueError) as exc_info:
            await manager.resume_process(
                task_name="existing_task",
                cwd=temp_dir,
                session_id="session-123"
            )
        assert "already exists" in str(exc_info.value)

    async def test_all_events_yields_from_queue(self):
        """Test all_events yields events from internal queue."""
        manager = ProcessManager()

        await manager._event_queue.put(("task1", {"type": "assistant", "text": "hello"}))
        await manager._event_queue.put(("task2", {"type": "result", "success": True}))

        events = []
        count = 0
        async for task_name, event in manager.all_events():
            events.append((task_name, event))
            count += 1
            if count >= 2:
                manager._shutdown = True

        assert len(events) == 2
        assert events[0][0] == "task1"
        assert events[1][0] == "task2"

    async def test_all_events_shutdown_stops_iteration(self):
        """Test all_events stops on shutdown."""
        manager = ProcessManager()
        manager._shutdown = True

        events = []
        async for task_name, event in manager.all_events():
            events.append((task_name, event))

        assert len(events) == 0

    async def test_all_events_timeout_continues(self):
        """Test all_events continues after timeout with no events."""
        manager = ProcessManager()

        async def delayed_shutdown():
            await asyncio.sleep(0.15)
            manager._shutdown = True

        asyncio.create_task(delayed_shutdown())

        events = []
        async for task_name, event in manager.all_events():
            events.append((task_name, event))

        assert len(events) == 0

    async def test_start_event_task_monitors_process(self):
        """Test _start_event_task creates background task that forwards events."""
        manager = ProcessManager()

        events_to_emit = [
            {"type": "assistant", "text": "first"},
            {"type": "result", "success": True},
        ]

        async def mock_events():
            for event in events_to_emit:
                yield event

        mock_process = MagicMock()
        mock_process.events = mock_events

        manager._start_event_task("test_task", mock_process)

        await asyncio.sleep(0.1)

        queued_events = []
        while not manager._event_queue.empty():
            queued_events.append(await manager._event_queue.get())

        assert len(queued_events) == 2
        assert queued_events[0] == ("test_task", {"type": "assistant", "text": "first"})
        assert queued_events[1] == ("test_task", {"type": "result", "success": True})

    async def test_start_event_task_handles_exception(self):
        """Test _start_event_task handles process exceptions."""
        manager = ProcessManager()

        async def mock_events_error():
            yield {"type": "assistant", "text": "ok"}
            raise RuntimeError("Connection lost")

        mock_process = MagicMock()
        mock_process.events = mock_events_error

        manager.processes["error_task"] = mock_process

        manager._start_event_task("error_task", mock_process)

        await asyncio.sleep(0.1)

        queued_events = []
        while not manager._event_queue.empty():
            queued_events.append(await manager._event_queue.get())

        assert len(queued_events) == 2
        assert queued_events[0][1]["type"] == "assistant"
        assert queued_events[1][1]["type"] == "error"
        assert "Connection lost" in queued_events[1][1]["error"]
        assert "error_task" not in manager.processes

    async def test_cleanup_crashed_processes_removes_stale(self, temp_dir):
        """Test cleanup_crashed_processes removes stale PIDs."""
        from registry import reset_singletons
        reset_singletons()

        registry_path = Path(temp_dir) / "registry.json"
        with patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)):

            from registry import get_registry
            registry = get_registry()

            registry.add_task("crashed_task", {
                "type": "session",
                "path": temp_dir,
                "pid": 99999999,  # Very unlikely to exist
                "session_id": "session-123"
            })

            manager = ProcessManager()
            cleaned = await manager.cleanup_crashed_processes()

            assert "crashed_task" in cleaned

            task_data = registry.get_task("crashed_task")
            assert task_data.get("pid") is None
            assert task_data.get("session_id") == "session-123"

    async def test_cleanup_crashed_processes_skips_active(self, temp_dir):
        """Test cleanup_crashed_processes skips actively managed processes."""
        from registry import reset_singletons
        reset_singletons()

        registry_path = Path(temp_dir) / "registry.json"
        with patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)):

            from registry import get_registry
            registry = get_registry()

            registry.add_task("active_task", {
                "type": "session",
                "path": temp_dir,
                "pid": 99999999,
            })

            manager = ProcessManager()
            manager.processes["active_task"] = MagicMock()

            cleaned = await manager.cleanup_crashed_processes()

            assert "active_task" not in cleaned

    async def test_cleanup_crashed_processes_skips_running(self, temp_dir):
        """Test cleanup_crashed_processes skips processes that are still running."""
        import os
        from registry import reset_singletons
        reset_singletons()

        registry_path = Path(temp_dir) / "registry.json"
        with patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)):

            from registry import get_registry
            registry = get_registry()

            registry.add_task("running_task", {
                "type": "session",
                "path": temp_dir,
                "pid": os.getpid(),
            })

            manager = ProcessManager()
            cleaned = await manager.cleanup_crashed_processes()

            assert "running_task" not in cleaned

    async def test_cleanup_crashed_processes_no_pid(self, temp_dir):
        """Test cleanup_crashed_processes skips tasks without PID."""
        from registry import reset_singletons
        reset_singletons()

        registry_path = Path(temp_dir) / "registry.json"
        with patch("registry.REGISTRY_FILE", registry_path), \
             patch("registry.CLAUDE_ARMY_DIR", Path(temp_dir)):

            from registry import get_registry
            registry = get_registry()

            registry.add_task("no_pid_task", {
                "type": "session",
                "path": temp_dir,
            })

            manager = ProcessManager()
            cleaned = await manager.cleanup_crashed_processes()

            assert "no_pid_task" not in cleaned
