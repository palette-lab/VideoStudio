"""Busy runs whose job died are marked failed so the chat stops waiting (main._reap)."""
import importlib.util
import time
from pathlib import Path

import pytest

pytest.importorskip("palette_sdk")
from fastapi import HTTPException  # noqa: E402

_spec = importlib.util.spec_from_file_location("plugin_main_reap", Path(__file__).resolve().parents[1] / "api" / "main.py")
main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(main)


class Jobs:
    def __init__(self, state=None, missing=False):
        self.state, self.missing = state, missing

    async def get(self, job_id):
        if self.missing:
            raise HTTPException(404, "Local job not found")
        return {"job_id": job_id, "status": self.state}


class Ctx:
    def __init__(self, jobs):
        self.jobs = jobs


def _run(status="executing", job=True, idle=60):
    t = time.time() - idle
    return {"id": "r1", "status": status, "created_at": t, "events": [{"t": t, "msg": "scene 2/5 clip"}],
            "job": {"id": "j1", "name": "palette_video.execute", "started_at": t} if job else None,
            "plan": {"steps": [{"step_id": "s1"}]}, "execution": {"s1": {"status": "running", "message": "scene 2/5"}}}


@pytest.fixture(autouse=True)
def no_db(monkeypatch):
    saved = []

    async def _save(ctx, run):
        saved.append(run["status"])
    monkeypatch.setattr(main, "_save", _save)
    return saved


async def test_live_job_is_left_alone(no_db):
    run = await main._reap(Ctx(Jobs("running")), _run())
    assert run["status"] == "executing" and not no_db


async def test_lost_job_fails_the_run_with_a_retryable_step(no_db):
    run = await main._reap(Ctx(Jobs(missing=True)), _run())
    assert run["status"] == "failed" and run["execution"]["s1"]["status"] == "failed"
    assert "Retry" in run["execution"]["s1"]["message"] and no_db == ["failed"]


async def test_job_that_ended_without_saving_fails_the_run():
    run = await main._reap(Ctx(Jobs("failed")), _run())
    assert run["status"] == "failed"


async def test_lost_analysis_becomes_an_error():
    run = await main._reap(Ctx(Jobs(missing=True)), _run(status="analyzing"))
    assert run["status"] == "error" and "again" in run["error"]


async def test_run_without_job_id_waits_for_the_job_timeout():
    recent = await main._reap(Ctx(Jobs(missing=True)), _run(job=False, idle=600))
    assert recent["status"] == "executing"
    old = await main._reap(Ctx(Jobs(missing=True)), _run(job=False, idle=7200 + 700))
    assert old["status"] == "failed"


async def test_finished_runs_are_not_touched(no_db):
    run = _run(status="completed")
    assert (await main._reap(Ctx(Jobs(missing=True)), run))["status"] == "completed" and not no_db
