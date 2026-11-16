"""Regression: with the oracle brief, every one of the 60 evaluation cases routes correctly (no LLM)."""

from eval import run_eval
from eval.cases import cases


def test_60_cases_exist_with_three_paraphrases_per_group():
    cs = cases()
    assert len(cs) == 60 and len({c["group"] for c in cs}) == 20
    for g in {c["group"] for c in cs}:
        prompts = [c["prompt"] for c in cs if c["group"] == g]
        assert len(prompts) == 3


async def test_offline_eval_all_pass(monkeypatch, tmp_path):
    monkeypatch.setattr(run_eval.config, "STORAGE", tmp_path)
    r = await run_eval.main(["--quiet"])
    assert r["errors"] == 0 and r["passed_cases"] == 60, [x for x in r["rows"] if not x["ok"]]
    assert r["metrics_pct"]["unnecessary_hybrid_rate"] == 0.0
    assert r["usage"]["calls"] == 0
