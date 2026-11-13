"""Caps that stop a gateway turn while it is still producing tokens.

The inactivity watchdog only measures silence. A local model that streams
reasoning, or a tool loop that never pauses, keeps that clock at zero.
"""

from __future__ import annotations

import os
from typing import Any

_UNATTENDED_WALL_S = 720.0
_INTERACTIVE_WALL_S = 3600.0
_UNATTENDED_OUTPUT_TOKENS = 24000
_CRON_PRINCIPAL = "cron-owner"


def _positive(raw: str | None, default: float) -> float | None:
    if raw is None or raw == "":
        value = default
    else:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = default
    return value if value > 0 else None


def turn_is_unattended(source: Any = None, *, unattended_flag: bool = False) -> bool:
    """True for hermes -z, cron, and other turns nobody is waiting at a keyboard for."""
    if unattended_flag:
        return True
    user_id = str(getattr(source, "user_id", "") or "")
    if user_id == _CRON_PRINCIPAL:
        return True
    platform = getattr(source, "platform", None)
    name = getattr(platform, "value", platform)
    return str(name or "").lower() == "cron"


def wall_timeout_s(*, unattended: bool) -> float | None:
    if unattended:
        return _positive(
            os.environ.get("HERMES_GATEWAY_TURN_WALL_UNATTENDED_S"),
            _UNATTENDED_WALL_S,
        )
    return _positive(os.environ.get("HERMES_GATEWAY_TURN_WALL_S"), _INTERACTIVE_WALL_S)


def output_token_budget(*, unattended: bool) -> int | None:
    if unattended:
        value = _positive(
            os.environ.get("HERMES_GATEWAY_TURN_OUTPUT_TOKENS_UNATTENDED"),
            float(_UNATTENDED_OUTPUT_TOKENS),
        )
    else:
        value = _positive(os.environ.get("HERMES_GATEWAY_TURN_OUTPUT_TOKENS"), 0.0)
    return int(value) if value is not None else None


def output_tokens(agent: Any) -> int:
    """Model output spent on this turn.

    ``session_output_tokens`` is the canonical billed output. Completion and
    reasoning are added only when they exceed that total, so a thinking stream
    counts once whether or not the provider folded it into output.
    """
    def _count(name: str) -> int:
        try:
            return max(0, int(getattr(agent, name, 0) or 0))
        except (TypeError, ValueError):
            return 0

    output = _count("session_output_tokens")
    completion = _count("session_completion_tokens")
    reasoning = _count("session_reasoning_tokens")
    return max(output, completion + reasoning)


def admission_is_unattended(row: dict | None) -> bool:
    if not row:
        return False
    payload = row.get("payload") or {}
    if payload.get("unattended") is True:
        return True
    from gateway.session_unattended_unknown import is_unattended

    return is_unattended(row)


def interrupt_unwatched_unattended(authority: Any, session_id: str) -> bool:
    """Stop a running unattended turn whose last submitter just disconnected.

    A person who drops a chat can reconnect; their turn is left running.
    ``hermes -z`` and cron have nobody to reconnect, and the work has no
    consumer left. Returns True when a stop was requested.
    """
    live = authority.sessions.get(session_id)
    if live is None or live.subscribers:
        return False
    execution = getattr(getattr(live, "event_stream", None), "execution", None) or {}
    admission_id = execution.get("admission_id") if isinstance(execution, dict) else None
    if not admission_id:
        return False
    from hermes_state_runtime import get_session_admission

    try:
        row = get_session_admission(authority.db, admission_id=admission_id)
    except Exception:
        return False
    if not admission_is_unattended(row):
        return False
    generation, agent = authority.adopted.get(session_id, (None, None))
    if generation is not None:
        authority.pending_stops[session_id] = generation
    if agent is None:
        return generation is not None
    from agent.interrupt_compat import request_hard_interrupt

    request_hard_interrupt(agent, "Submitter disconnected", tool_reason="submitter disconnected")
    return True
