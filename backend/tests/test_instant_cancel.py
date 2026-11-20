"""Cancel stops a job at once, even inside a long model call (it used to wait for the job's next save point)."""
import asyncio
import time

import pytest

pytest.importorskip("palette_sdk")  # supplied by `pltt test` (or PYTHONPATH=<cli>/backend-sdk)


class Exec:
    id = "job1"

    def __init__(self, cancel_after: float | None):
        self.t0, self.after = time.monotonic(), cancel_after

    async def cancelled(self):
        return self.after is not None and time.monotonic() - self.t0 >= self.after

    async def progress(self, *a, **k):
        return None


def test_a_long_call_is_stopped_within_a_second_of_cancel(monkeypatch):
    import main
    monkeypatch.setattr(main, "CANCEL_POLL_S", 0.05)
    reached = []

    async def body():
        await asyncio.sleep(30)  # "Understanding the request": one long model call, no save point
        reached.append("end")

    t0 = time.monotonic()
    stopped, out = asyncio.run(main._stoppable(Exec(cancel_after=0.1), body()))
    assert stopped is True and out is None and not reached and time.monotonic() - t0 < 1.0


def test_a_job_nobody_cancels_returns_its_result(monkeypatch):
    import main
    monkeypatch.setattr(main, "CANCEL_POLL_S", 0.01)

    async def body():
        await asyncio.sleep(0.05)
        return {"status": "awaiting_approval"}

    class NoSignal:  # a runtime without the cancel signal: the body simply runs
        async def cancelled(self):
            raise RuntimeError("not available")

    assert asyncio.run(main._stoppable(NoSignal(), body())) == (False, {"status": "awaiting_approval"})


def test_a_stopped_job_leaves_the_run_cancelled(monkeypatch):
    import main
    run = {"id": "r1", "events": [], "assets": {}, "status": "executing", "stage": None, "cancel_requested": True,
           "plan": {"steps": [{"step_id": "s1"}, {"step_id": "s2"}]},
           "execution": {"s1": {"status": "running"}, "s2": {"status": "planned"}}}
    saved = []

    class Db:
        async def rollback(self):
            saved.append("rollback")

    class Ctx:
        db = Db()

    async def load(ctx, rid):
        return run

    async def save(ctx, r):
        saved.append(r["status"])

    monkeypatch.setattr(main, "_load", load)
    monkeypatch.setattr(main, "_save", save)
    monkeypatch.setattr(main.execution, "build_report", lambda r: {"success": False})
    out = asyncio.run(main._finish_cancelled(Ctx(), {"run_id": "r1"}, "execute"))
    assert out == {"run_id": "r1", "status": "cancelled"} and saved == ["rollback", "cancelled"]
    assert run["execution"]["s1"]["status"] == "cancelled" and run["execution"]["s2"]["status"] == "not_run"
    assert run["events"][-1]["msg"] == "Cancelled" and "report" in run

    run.update(status="executing", rerender={"scene": 3}, execution={"s1": {"status": "succeeded"}})
    out = asyncio.run(main._finish_cancelled(Ctx(), {"run_id": "r1", "step_id": "s1", "scene": 3}, "rerender"))
    assert out["status"] == "completed" and "rerender" not in run and run["cancel_requested"] is False
    assert "re-render cancelled; the film is unchanged" in run["execution"]["s1"]["message"]
