"""A cron model call that keeps streaming is cut on the wall clock."""

from concurrent.futures import Future
from unittest.mock import MagicMock, patch

import pytest


def test_fresh_stream_is_cut_when_the_wall_clock_expires(monkeypatch):
    from cron import scheduler

    clock = {"t": 0.0}

    def monotonic():
        return clock["t"]

    def wait(_futures, timeout=None):
        clock["t"] += 1000.0
        return set(), set()

    monkeypatch.setattr(scheduler.time, "monotonic", monotonic)
    monkeypatch.setattr(scheduler.concurrent.futures, "wait", wait)
    monkeypatch.setattr(scheduler, "_cron_inactivity_seconds", lambda: 10_000.0)
    monkeypatch.setattr(scheduler, "_cron_wall_seconds", lambda: 900.0)

    agent = MagicMock()
    agent.get_activity_summary.return_value = {
        "seconds_since_activity": 0.0,
        "last_activity_desc": "receiving stream response",
        "api_call_count": 0,
        "max_iterations": 2,
        "current_tool": None,
    }
    fake_future = Future()
    fake_pool = MagicMock()
    fake_pool.submit.return_value = fake_future
    with patch("cron.scheduler.concurrent.futures.ThreadPoolExecutor", return_value=fake_pool):
        with pytest.raises(TimeoutError, match="wall-clock limit"):
            scheduler._run_agent_with_watchdog(
                agent,
                "probe",
                {"schedule": {"kind": "cron", "expr": "*/15 * * * *"}},
                "job",
                "electrum-sweep",
                "task",
                None,
            )
    agent.interrupt.assert_called()


def test_wall_clock_and_output_cap_defaults(monkeypatch):
    from cron.scheduler import _cron_max_output_tokens, _cron_wall_seconds

    monkeypatch.delenv("HERMES_CRON_WALL_TIMEOUT", raising=False)
    monkeypatch.delenv("HERMES_CRON_MAX_TOKENS", raising=False)
    assert _cron_wall_seconds() == 900.0
    assert _cron_max_output_tokens({}) == 10240
    assert _cron_max_output_tokens({"max_tokens": 2048}) == 2048
    assert _cron_max_output_tokens({"max_tokens": 0}) is None
    monkeypatch.setenv("HERMES_CRON_WALL_TIMEOUT", "0")
    assert _cron_wall_seconds() == 0.0
