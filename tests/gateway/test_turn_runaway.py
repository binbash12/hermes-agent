"""A gateway turn that keeps streaming still has to end.

Inactivity only measures silence. These tests cover the wall-clock cap, the
turn-wide output budget, and stopping an unattended turn when its submitter
disconnects.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import gateway.run as gateway_run
from gateway.run import _watch_gateway_turn_inactivity
from gateway.turn_runaway import (
    interrupt_unwatched_unattended,
    output_token_budget,
    output_tokens,
    turn_is_unattended,
    wall_timeout_s,
)


class _Busy:
    def __init__(self, *, tokens=0, stop_after=None, done=None):
        self.session_output_tokens = tokens
        self.session_completion_tokens = 0
        self.session_reasoning_tokens = 0
        self.reads = 0
        self._stop_after = stop_after
        self._done = done

    def get_activity_summary(self):
        self.reads += 1
        if self._done is not None and self._stop_after is not None and self.reads >= self._stop_after:
            self._done.set()
        return {"seconds_since_activity": 0.0}


def _watch(agent, **kwargs):
    fired = []
    kwargs.setdefault("timeout", 10_000.0)
    kwargs.setdefault("poll_interval", 0.01)
    _watch_gateway_turn_inactivity(
        agent_holder=[agent],
        task_id="t",
        process_baseline=None,
        worker_done=kwargs.pop("worker_done", threading.Event()),
        timeout_fired=threading.Event(),
        cleanup_lock=threading.Lock(),
        is_still_current=None,
        **kwargs,
    )
    return fired


def test_streaming_turn_ends_at_the_wall_clock(monkeypatch):
    reasons = []

    def _abandon(**kwargs):
        reasons.append(kwargs.get("reason"))
        return True

    monkeypatch.setattr(gateway_run, "_abandon_timed_out_gateway_turn", _abandon)
    agent = _Busy()
    _watch(agent, wall_timeout=0.05)
    assert reasons == ["Execution timed out (wall clock)"]
    assert agent.reads >= 1


def test_fresh_stream_under_the_wall_keeps_running(monkeypatch):
    reasons = []
    monkeypatch.setattr(
        gateway_run, "_abandon_timed_out_gateway_turn", lambda **kwargs: reasons.append(kwargs) or True)
    done = threading.Event()
    agent = _Busy(stop_after=3, done=done)
    _watch(agent, wall_timeout=10_000.0, worker_done=done)
    assert reasons == []
    assert agent.reads >= 3


def test_output_budget_ends_a_turn_that_is_not_idle(monkeypatch):
    reasons = []

    def _abandon(**kwargs):
        reasons.append(kwargs.get("tool_reason"))
        return True

    monkeypatch.setattr(gateway_run, "_abandon_timed_out_gateway_turn", _abandon)
    agent = _Busy(tokens=24_000)
    _watch(agent, output_token_budget=24_000)
    assert reasons == ["gateway output token budget"]


def test_reasoning_tokens_count_when_output_does_not_include_them():
    agent = SimpleNamespace(
        session_output_tokens=800,
        session_completion_tokens=800,
        session_reasoning_tokens=4_000,
    )
    assert output_tokens(agent) == 4_800


def test_output_already_including_reasoning_is_not_double_counted():
    agent = SimpleNamespace(
        session_output_tokens=4_800,
        session_completion_tokens=800,
        session_reasoning_tokens=4_000,
    )
    assert output_tokens(agent) == 4_800


def test_unattended_budgets_are_the_short_ones(monkeypatch):
    monkeypatch.delenv("HERMES_GATEWAY_TURN_WALL_S", raising=False)
    monkeypatch.delenv("HERMES_GATEWAY_TURN_WALL_UNATTENDED_S", raising=False)
    monkeypatch.delenv("HERMES_GATEWAY_TURN_OUTPUT_TOKENS", raising=False)
    monkeypatch.delenv("HERMES_GATEWAY_TURN_OUTPUT_TOKENS_UNATTENDED", raising=False)
    assert wall_timeout_s(unattended=False) == 3600
    assert wall_timeout_s(unattended=True) == 720
    assert output_token_budget(unattended=False) is None
    assert output_token_budget(unattended=True) == 24_000
    monkeypatch.setenv("HERMES_GATEWAY_TURN_WALL_UNATTENDED_S", "0")
    monkeypatch.setenv("HERMES_GATEWAY_TURN_OUTPUT_TOKENS_UNATTENDED", "0")
    assert wall_timeout_s(unattended=True) is None
    assert output_token_budget(unattended=True) is None


def test_cron_and_oneshot_use_the_short_budget():
    assert turn_is_unattended(unattended_flag=True) is True
    assert turn_is_unattended(SimpleNamespace(user_id="cron-owner")) is True
    assert turn_is_unattended(SimpleNamespace(platform=SimpleNamespace(value="cron"))) is True
    assert turn_is_unattended(SimpleNamespace(user_id="human", platform=SimpleNamespace(value="local"))) is False


def test_dropped_submitter_stops_only_an_unwatched_unattended_turn(monkeypatch):
    stopped = {}

    class _Agent:
        def interrupt(self, message=None, tool_reason=None):
            stopped["message"] = message
            stopped["tool_reason"] = tool_reason

    live = SimpleNamespace(
        subscribers={},
        event_stream=SimpleNamespace(execution={"admission_id": "adm-1"}),
    )
    authority = SimpleNamespace(
        sessions={"s": live},
        db=object(),
        adopted={"s": (4, _Agent())},
        pending_stops={},
    )
    monkeypatch.setattr(
        "hermes_state_runtime.get_session_admission",
        lambda _db, *, admission_id: {"admission_id": admission_id, "payload": {"unattended": True}},
    )
    assert interrupt_unwatched_unattended(authority, "s") is True
    assert stopped == {
        "message": "Submitter disconnected",
        "tool_reason": "submitter disconnected",
    }
    assert authority.pending_stops["s"] == 4

    stopped.clear()
    authority.pending_stops.clear()
    live.subscribers = {"sub": object()}
    assert interrupt_unwatched_unattended(authority, "s") is False
    assert stopped == {}
    assert authority.pending_stops == {}

    live.subscribers = {}
    monkeypatch.setattr(
        "hermes_state_runtime.get_session_admission",
        lambda _db, *, admission_id: {
            "admission_id": admission_id, "payload": {}, "principal_id": "human",
        },
    )
    assert interrupt_unwatched_unattended(authority, "s") is False
    assert stopped == {}
    assert authority.pending_stops == {}
