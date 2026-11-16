"""Execution: separate states, honest mock reporting, failure, runtime handoff failure, retry."""

import copy

from router_core import execution
from tests.helpers import step, und


def _run(steps, simulate=None):
    return {"id": "r1", "mode": "hybrid", "status": "approved", "assets": {}, "understanding": und(),
            "plan": {"steps": steps}, "execution": {s["step_id"]: {"status": "queued"} for s in steps},
            "events": [], "simulate": simulate or {}}


TWO = [step("s1", "storyboard-studio", ["video.from_storyboard"], [("prompt", "text")]),
       step("s2", "programmatic-motion-studio", ["motion.overlay_graphics"], [("step:s1", "video_file")])]


async def test_mock_success_is_reported_as_mock_not_success():
    run = _run(copy.deepcopy(TWO))
    await execution.run_plan(run, lambda r: None)
    assert run["status"] == "completed"
    assert run["report"]["mock"] is True and run["report"]["success"] is False
    assert "MOCK" in run["report"]["headline"]


async def test_failure_marks_downstream_not_run_and_offers_recovery():
    run = _run(copy.deepcopy(TWO), {"s1": "fail"})
    await execution.run_plan(run, lambda r: None)
    assert run["status"] == "failed"
    assert [run["execution"][s]["status"] for s in ("s1", "s2")] == ["failed", "not_run"]
    assert run["execution"]["s1"]["recovery"]["retry"] is True
    for e in run["execution"].values():
        if e["status"] in ("failed", "not_run"):
            e["status"] = "queued"
    run["status"] = "approved"
    await execution.run_plan(run, lambda r: None)
    assert run["status"] == "completed" and run["execution"]["s1"]["attempts"] == 2


async def test_runtime_handoff_failure_detected():
    run = _run(copy.deepcopy(TWO), {"s1": "bad_handoff"})
    await execution.run_plan(run, lambda r: None)
    assert run["execution"]["s2"]["status"] == "failed"
    assert "Handoff failed" in run["execution"]["s2"]["message"]


async def test_unverified_broker_app_never_reports_success(monkeypatch):
    from router_core import registry
    apps = {k: copy.deepcopy(v) for k, v in registry.apps().items()}
    apps["normal-video-generator"]["execution"]["adapter"] = "broker"
    monkeypatch.setattr(registry, "apps", lambda: apps)
    run = _run([step("s1", "normal-video-generator", ["video.text_to_video"], [("prompt", "text")])])
    await execution.run_plan(run, lambda r: None)
    assert run["status"] == "failed"
    assert "no verified Palette broker service" in run["execution"]["s1"]["message"]


async def test_original_file_references_are_passed_unchanged():
    run = _run([step("s1", "normal-video-generator", ["video.image_to_video"], [("asset:a1", "image_file")])])
    run["assets"] = {"a1": {"id": "a1", "sha256": "abc123", "modality": "image"}}
    await execution.run_plan(run, lambda r: None)
    assert run["execution"]["s1"]["inputs"][0]["sha256"] == "abc123"


def test_parallel_progress_saves_never_overlap():
    import asyncio as _a
    from router_core import execution as ex_mod

    active = {"n": 0, "max": 0}

    async def save(run):
        active["n"] += 1
        active["max"] = max(active["max"], active["n"])
        await _a.sleep(0.01)
        active["n"] -= 1

    class Parallel:
        wants_context = True

        async def launch(self, app, step, inputs, request_id, simulate, *, progress=None, run=None):
            await _a.gather(*(progress(f"scene {i}") for i in range(5)))
            return {"status": "succeeded", "mock": True, "message": "ok", "outputs": [{"format": "video_file", "ref": "mock://x"}]}

    ex_mod.ADAPTERS["parallel"] = Parallel()
    try:
        from router_core import registry
        app_id = next(iter(registry.apps()))
        orig = registry.apps()[app_id]["execution"]["adapter"]
        registry.apps()[app_id]["execution"]["adapter"] = "parallel"
        run = {"id": "r", "status": "approved", "events": [], "simulate": {},
               "plan": {"steps": [{"step_id": "s1", "app_id": app_id, "inputs": [], "outputs": ["video_file"]}]},
               "execution": {"s1": {"status": "queued"}}, "assets": {}}
        _a.run(ex_mod.run_plan(run, save))
        assert run["status"] == "completed" and active["max"] == 1
    finally:
        registry.apps()[app_id]["execution"]["adapter"] = orig
        ex_mod.ADAPTERS.pop("parallel", None)


async def test_run_deleted_while_running_stops_without_running_later_steps():
    run = _run(copy.deepcopy(TWO))
    saves = []

    def save(r):
        saves.append(r["status"])
        if any(e["msg"].startswith("Launching") and "s2" in e["msg"] for e in r["events"]):
            raise execution.RunDeleted(r["id"])  # the user deleted the chat before step s2 saved

    try:
        await execution.run_plan(run, save)
        raise AssertionError("run_plan should stop when the run is deleted")
    except execution.RunDeleted:
        pass
    assert run["execution"]["s2"].get("adapter") and "outputs" not in run["execution"]["s2"]
    assert run.get("report") is None


async def test_run_deleted_inside_an_engine_progress_report_is_not_swallowed():
    class Engine:
        wants_context = True

        async def launch(self, app, step, inputs, request_id, simulate, *, progress=None, run=None):
            await progress("working")
            return {"status": "succeeded", "outputs": []}

    run = _run([step("s1", "storyboard-studio", ["video.from_storyboard"], [("prompt", "text")])])

    def save(r):
        if r["execution"]["s1"].get("message") == "working":
            raise execution.RunDeleted(r["id"])

    old = dict(execution.ADAPTERS)
    execution.ADAPTERS.clear()
    execution.ADAPTERS.update({k: Engine() for k in old})
    try:
        try:
            await execution.run_plan(run, save)
            raise AssertionError("RunDeleted should reach the job")
        except execution.RunDeleted:
            pass
    finally:
        execution.ADAPTERS.clear()
        execution.ADAPTERS.update(old)
