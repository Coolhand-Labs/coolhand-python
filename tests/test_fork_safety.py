"""Fork-safety of CoolhandClient (issue #152)."""

import os
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from coolhand.client import CoolhandClient


def _client() -> CoolhandClient:
    return CoolhandClient(auto_submit=False, api_key="real-api-key-12345", silent=True)


class TestResetAfterFork:
    def test_same_process_is_a_noop(self, mock_request_data, mock_response_data):
        client = _client()
        client.log_interaction(mock_request_data, mock_response_data)
        locks = (client._worker_lock, client._state_lock)
        dispatch_queue = client._dispatch_queue

        assert client._reset_after_fork() is False

        assert len(client._queue) == 1
        assert (client._worker_lock, client._state_lock) == locks
        assert client._dispatch_queue is dispatch_queue

    def test_forked_copy_is_reinitialised(self, mock_request_data, mock_response_data):
        client = _client()
        client.log_interaction(mock_request_data, mock_response_data)
        client._dispatch_queue.put_nowait({"id": "inherited"})
        client._dropped_count = 3
        client._delivery_failure_count = 2
        locks = (client._worker_lock, client._state_lock)
        dispatch_queue = client._dispatch_queue
        session_id = client.session_id
        client._pid = -1  # simulate being a fork-inherited copy

        assert client._reset_after_fork() is True

        assert client._queue == []
        assert client._dispatch_queue is not dispatch_queue
        assert client._dispatch_queue.empty()
        assert client._worker_lock is not locks[0]
        assert client._state_lock is not locks[1]
        assert client._worker_thread is None
        assert client._interaction_count == 0
        assert client._dropped_count == 0
        assert client._delivery_failure_count == 0
        assert client._pid == os.getpid()
        assert client.session_id == session_id
        assert client.config["api_key"] == "real-api-key-12345"

    def test_second_reset_is_a_noop(self, mock_request_data, mock_response_data):
        client = _client()
        client._pid = -1
        assert client._reset_after_fork() is True

        client.log_interaction(mock_request_data, mock_response_data)

        assert client._reset_after_fork() is False
        assert len(client._queue) == 1

    def test_rearms_atexit_hook(self):
        client = _client()
        client._pid = -1

        with (
            patch("coolhand.client.atexit.unregister") as unregister,
            patch("coolhand.client.atexit.register") as register,
        ):
            client._reset_after_fork()

        unregister.assert_called_once_with(client.shutdown)
        register.assert_called_once_with(client.shutdown)

    def test_lock_held_at_fork_does_not_deadlock(
        self, mock_request_data, mock_response_data
    ):
        client = _client()
        # Locks held "by a parent thread that doesn't exist in the child".
        client._worker_lock.acquire()
        client._state_lock.acquire()
        client._dispatch_queue.mutex.acquire()
        client._pid = -1

        delivered = threading.Event()

        def fake_send(interaction):
            delivered.set()
            return True

        def use_client():
            assert client._reset_after_fork() is True
            client.log_interaction(mock_request_data, mock_response_data)
            client.flush()
            delivered.wait(timeout=2)
            client.shutdown()

        with patch.object(client, "_send_one", side_effect=fake_send):
            t = threading.Thread(target=use_client, daemon=True)
            t.start()
            t.join(timeout=5)

        assert not t.is_alive(), "client deadlocked on a fork-inherited lock"
        assert delivered.is_set()

    def test_fresh_worker_starts_after_reset(
        self, mock_request_data, mock_response_data
    ):
        client = _client()
        delivered: list[str] = []

        with patch.object(
            client,
            "_send_one",
            side_effect=lambda i: delivered.append(i["id"]) or True,
        ):
            client.log_interaction(mock_request_data, mock_response_data)
            client.flush()
            client.shutdown()  # drain: first worker has delivered and exited
            assert len(delivered) == 1

            client.log_interaction(mock_request_data, mock_response_data)
            client._pid = -1
            client._reset_after_fork()
            # The pre-reset item was dropped; a fresh one must still flow
            # through a newly started worker.
            client.log_interaction(mock_request_data, mock_response_data)
            client.flush()
            client.shutdown()

        assert len(delivered) == 2


class TestMiddlewareResetsInheritedClient:
    def test_after_process_boot_resets_real_inherited_client(
        self, mock_request_data, mock_response_data
    ):
        pytest.importorskip("dramatiq")
        from coolhand.integrations.dramatiq import CoolhandDramatiqMiddleware

        client = _client()
        client.log_interaction(mock_request_data, mock_response_data)
        old_locks = (client._worker_lock, client._state_lock)
        client._pid = -1

        calls: list[str] = []
        original_reset = client._reset_after_fork

        def tracking_reset():
            calls.append("reset")
            return original_reset()

        middleware = CoolhandDramatiqMiddleware(api_key="real-api-key-12345")
        with (
            patch("coolhand.get_instance", return_value=client),
            patch.object(client, "_reset_after_fork", side_effect=tracking_reset),
            patch(
                "coolhand.start_monitoring",
                side_effect=lambda: calls.append("start_monitoring"),
            ),
        ):
            middleware.after_process_boot(MagicMock())

        assert calls == ["reset", "start_monitoring"]
        assert client._queue == []
        assert client._worker_lock is not old_locks[0]
        assert client._state_lock is not old_locks[1]

    def test_reset_failure_does_not_block_monitoring(self):
        pytest.importorskip("dramatiq")
        from coolhand.integrations.dramatiq import CoolhandDramatiqMiddleware

        client = MagicMock()
        client.config = {"api_key": "k"}
        client._reset_after_fork.side_effect = RuntimeError("boom")
        middleware = CoolhandDramatiqMiddleware(api_key="k")

        with (
            patch("coolhand.get_instance", return_value=client),
            patch("coolhand.start_monitoring") as start,
        ):
            middleware.after_process_boot(MagicMock())

        start.assert_called_once()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires os.fork")
# Forking from a multi-threaded pytest process is exactly the scenario under test.
@pytest.mark.filterwarnings("ignore:.*multi-threaded.*:DeprecationWarning")
def test_real_fork_with_locks_held_in_parent(mock_request_data, mock_response_data):
    client = _client()
    client.log_interaction(mock_request_data, mock_response_data)  # parent's pending
    held = threading.Event()
    release = threading.Event()

    def hold_lock():
        with client._state_lock, client._dispatch_queue.mutex:
            held.set()
            release.wait(timeout=10)

    holder = threading.Thread(target=hold_lock, daemon=True)
    holder.start()
    assert held.wait(timeout=2)

    pid = os.fork()
    if pid == 0:  # child
        code = 1
        try:
            client._reset_after_fork()
            client.log_interaction(mock_request_data, mock_response_data)
            # Only the child's own interaction: the parent's was dropped.
            code = 0 if len(client._queue) == 1 else 2
        finally:
            os._exit(code)

    try:
        end = time.monotonic() + 10.0
        while time.monotonic() < end:
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                break
            time.sleep(0.02)
        else:
            os.kill(pid, 9)
            os.waitpid(pid, 0)
            pytest.fail("forked child deadlocked on the inherited lock")
        assert os.waitstatus_to_exitcode(status) == 0
    finally:
        release.set()
        holder.join(timeout=2)
