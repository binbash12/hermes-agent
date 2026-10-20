"""Tests for cron post_script (after_agent) support."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def cron_env(tmp_path, monkeypatch):
    """Isolated cron environment with temp HERMES_HOME."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "cron").mkdir()
    (hermes_home / "cron" / "output").mkdir()
    (hermes_home / "scripts").mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    import cron.jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "HERMES_DIR", hermes_home)
    monkeypatch.setattr(jobs_mod, "CRON_DIR", hermes_home / "cron")
    monkeypatch.setattr(jobs_mod, "JOBS_FILE", hermes_home / "cron" / "jobs.json")
    monkeypatch.setattr(jobs_mod, "OUTPUT_DIR", hermes_home / "cron" / "output")
    return hermes_home


def test_should_run_post_script_skips_silent_after_agent(cron_env):
    from cron.scheduler_script import should_run_post_script

    job = {"post_script": "electrum_post_agent.py", "post_script_when": "after_agent"}
    assert should_run_post_script(job, final_response="[SILENT]") is False
    assert should_run_post_script(job, final_response="Calm tick.") is True


def test_should_run_post_script_always_runs_even_on_silent(cron_env):
    from cron.scheduler_script import should_run_post_script

    job = {"post_script": "finalize.py", "post_script_when": "always"}
    assert should_run_post_script(job, final_response="[SILENT]") is True


def test_should_run_post_script_requires_field(cron_env):
    from cron.scheduler_script import should_run_post_script

    assert should_run_post_script({}, final_response="hi") is False


def test_maybe_run_post_script_sets_env_and_clears_error(cron_env):
    from cron.jobs import create_job, get_job, update_job
    from cron.scheduler_script import maybe_run_post_script

    probe = cron_env / "scripts" / "probe_post.py"
    probe.write_text(
        "import os\n"
        "path = os.environ.get(\"HERMES_CRON_OUTPUT_PATH\", \"\")\n"
        "open(path + \".seen\", \"w\").write(path)\n"
        "print(\"ok\")\n",
        encoding="utf-8",
    )
    job = create_job(prompt="x", schedule="every 1h")
    update_job(job["id"], {
        "post_script": "probe_post.py",
        "post_script_when": "after_agent",
        "last_post_error": "stale",
    })
    job = get_job(job["id"])
    out = cron_env / "cron" / "output" / job["id"] / "2026-09-16_23-00-00.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("# wrap\n", encoding="utf-8")

    result = maybe_run_post_script(job, out, final_response="hello")
    assert result is not None
    ok, detail = result
    assert ok is True, detail
    assert Path(str(out) + ".seen").exists()
    refreshed = get_job(job["id"])
    assert refreshed.get("last_post_error") in (None, "")


def test_allow_silent_false_uses_no_silent_hint(cron_env):
    from cron.scheduler_prompt import _build_job_prompt

    job = {"prompt": "Electrum tick", "allow_silent": False}
    result = _build_job_prompt(job)
    assert "Do NOT respond with [SILENT]" in result
    assert "SILENT: If there is genuinely nothing new" not in result
    assert "Electrum tick" in result
    assert "FAILURE:" in result
    assert "RECURSION:" in result


def test_allow_silent_string_false(cron_env):
    from cron.scheduler_prompt import _build_job_prompt

    job = {"prompt": "x", "allow_silent": "false"}
    result = _build_job_prompt(job)
    assert "Do NOT respond with [SILENT]" in result


def test_allow_silent_default_keeps_silent_token(cron_env):
    from cron.scheduler_prompt import _build_job_prompt

    result = _build_job_prompt({"prompt": "Check for updates"})
    assert "[SILENT]" in result
    assert "SILENT: If there is genuinely nothing new" in result
    assert "Check for updates" in result


def test_maybe_run_post_script_records_failure(cron_env):
    from cron.jobs import create_job, get_job, update_job
    from cron.scheduler_script import maybe_run_post_script

    (cron_env / "scripts" / "boom.py").write_text(
        "import sys\nsys.exit(2)\n", encoding="utf-8"
    )
    job = create_job(prompt="x", schedule="every 1h")
    update_job(job["id"], {"post_script": "boom.py"})
    job = get_job(job["id"])
    out = cron_env / "cron" / "output" / "wrap.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("x\n", encoding="utf-8")

    result = maybe_run_post_script(job, out, final_response="hello")
    assert result is not None
    ok, _detail = result
    assert ok is False
    refreshed = get_job(job["id"])
    assert refreshed.get("last_post_error")
