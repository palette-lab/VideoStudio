"""Routing evaluation.

  offline (default): every case uses its group's oracle brief -> measures the deterministic layers (no LLM calls)
  --live           : the real Palette LLM writes the brief    -> measures understanding + routing end to end

Small runs during development:   --live --per-group 1      (18 cases)   or   --live --groups G05,G12
Full run before a release:       --live                     (54 cases)
Identical LLM calls are cached in storage/eval_cache (disable with --no-cache).
Reports: storage/eval_reports/<timestamp>-<offline|live>.json and .md

Perception is NOT exercised here: assets carry pre-written perception reports (labelled synthetic).
"""

import argparse
import asyncio
import copy
import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "api"), str(Path(__file__).resolve().parents[1])]
os.environ.setdefault("MOCK_STEP_SECONDS", "0")

from router_core import config, execution, fallback, llm, pipeline, validator  # noqa: E402
from eval.cases import cases  # noqa: E402


class OracleLLM:
    def __init__(self, brief):
        self.brief = brief

    async def __call__(self, model, messages, **kw):
        stage = kw.get("stage")
        if stage in ("brief", "brief.recheck"):
            return copy.deepcopy(self.brief)
        if stage == "judge":
            return {"choice": 0, "why": "highest deterministic score (offline oracle)"}
        return {"matches_intent": True, "concerns": []}


def _assets(case):
    out, perception = {}, {}
    for aid, a in case["assets"].items():
        out[aid] = {"id": aid, "filename": a["filename"], "modality": a["modality"], "tags": list(a["tags"]),
                    "sha256": hashlib.sha256(f"{case['id']}:{aid}".encode()).hexdigest()}
        perception[aid] = {**a["perception"], "synthetic_fixture": True}
    return out, perception


async def run_case(case, live: bool) -> dict:
    assets, perception = _assets(case)
    chat = llm.chat_json if live else OracleLLM(case["oracle"])
    started = time.monotonic()
    try:
        res = await pipeline.analyze(prompt=case["prompt"], mode=case["mode"], assets=assets, perception=perception,
                                     answers={}, chat=chat)
    except Exception as e:  # recorded as a failure, never hidden
        return {"case": case, "error": str(e), "latency_s": round(time.monotonic() - started, 2)}
    out = {"case": case, "res": res, "assets": assets, "latency_s": round(time.monotonic() - started, 2)}
    plan = res.get("plan")
    if res["status"] == "awaiting_approval" and plan:
        run = {"id": case["id"], "mode": case["mode"], "status": "approved", "assets": assets, "plan": copy.deepcopy(plan),
               "understanding": res["understanding"], "execution": {s["step_id"]: {"status": "queued"} for s in plan["steps"]},
               "events": [], "simulate": {}}
        await execution.run_plan(run, lambda r: None)
        out["execution"] = {"status": run["status"], "report": run["report"]}
        offered = []
        for s in plan["steps"]:
            for f in fallback.fallback_options(run, s["step_id"]):
                if f["classification"] != "invalid":
                    trial = copy.deepcopy(run)
                    fallback.substitute(trial, s["step_id"], f["app_id"])
                    still = validator.errors(validator.validate(trial["plan"], res["understanding"], case["mode"], assets))
                    offered.append({"step": s["step_id"], "app": f["app_id"], "class": f["classification"],
                                    "valid_after_substitution": not still and trial["plan"]["steps"] != plan["steps"]})
        out["fallbacks"] = offered
    return out


def score(results: list[dict]) -> dict:
    m = {k: [0, 0] for k in ["outcome", "intent", "app_selection", "capabilities", "handoff_validity",
                             "honest_execution", "valid_fallbacks"]}
    hybrid_bad = [0, 0]
    asked_tp = asked_fp = asked_fn = 0
    errors, rows = 0, []
    for r in results:
        c = r["case"]
        e = c["expect"]
        if "error" in r:
            errors += 1
            rows.append({"id": c["id"], "ok": False, "status": "ERROR", "note": r["error"][:120]})
            continue
        res = r["res"]
        und = res["understanding"]
        apps = [s["app_id"] for s in (res.get("plan") or {}).get("steps", [])] if res["status"] == "awaiting_approval" else None
        caps = {x["capability"] for x in und["requirements"]}
        checks = {}
        checks["outcome"] = res["status"] in e["status"]
        intent_ok = und["detected_intent"] in e["intent"]
        if e["types"]:
            intent_ok = intent_ok and bool(set(und["production_type"]) & e["types"])
        checks["intent"] = intent_ok
        if e["apps"] is not None:
            checks["app_selection"] = apps == e["apps"]
        if e["caps_in"] or e["caps_out"]:
            checks["capabilities"] = set(e["caps_in"]) <= caps and not (set(e["caps_out"]) & caps)
        if apps and len(apps) > 1:
            checks["handoff_validity"] = all(h["validated"] for h in res["decision"]["handoff_requirements"])
        if "execution" in r:
            rep = r["execution"]["report"]
            checks["honest_execution"] = rep["mock"] and not rep["success"] and "MOCK" in rep["headline"]
        for f in r.get("fallbacks", []):
            m["valid_fallbacks"][0] += f["valid_after_substitution"]
            m["valid_fallbacks"][1] += 1
        for k, v in checks.items():
            m[k][0] += bool(v)
            m[k][1] += 1
        if e["apps"] is not None and len(e["apps"]) == 1 and apps is not None:
            hybrid_bad[1] += 1
            hybrid_bad[0] += len(apps) > 1
        asked = res["status"] == "needs_clarification"
        if e["ask"] is True:
            asked_tp += asked
            asked_fn += not asked
        elif e["ask"] is False:
            asked_fp += asked
        rows.append({"id": c["id"], "ok": all(checks.values()), "status": res["status"], "apps": apps,
                     "intent": und["detected_intent"], "types": und["production_type"],
                     "failed_checks": [k for k, v in checks.items() if not v],
                     "questions": [q["question"] for q in res.get("questions") or []],
                     "suggestion": res.get("suggestion"), "confidence": res["decision"]["confidence"],
                     "latency_s": r["latency_s"]})
    pct = lambda a: round(100 * a[0] / a[1], 1) if a[1] else None  # noqa: E731
    return {
        "cases": len(results), "errors": errors,
        "passed_cases": sum(1 for x in rows if x["ok"]),
        "metrics_pct": {
            "outcome_accuracy": pct(m["outcome"]),
            "intent_accuracy": pct(m["intent"]),
            "application_selection_accuracy": pct(m["app_selection"]),
            "capability_validation_accuracy": pct(m["capabilities"]),
            "handoff_validity": pct(m["handoff_validity"]),
            "honest_execution_reporting": pct(m["honest_execution"]),
            "unnecessary_hybrid_rate": pct(hybrid_bad),
            "invalid_fallback_rate": round(100 - pct(m["valid_fallbacks"]), 1) if m["valid_fallbacks"][1] else None,
            "clarification_precision": round(100 * asked_tp / (asked_tp + asked_fp), 1) if asked_tp + asked_fp else None,
            "clarification_recall": round(100 * asked_tp / (asked_tp + asked_fn), 1) if asked_tp + asked_fn else None,
        },
        "counts": {k: v for k, v in m.items()} | {"unnecessary_hybrid": hybrid_bad,
                                                 "clarification_tp_fp_fn": [asked_tp, asked_fp, asked_fn]},
        "execution_success_note": "All executions are MOCK; real execution success cannot be measured until apps are connected.",
        "rows": rows,
    }


async def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--groups", default="")
    ap.add_argument("--per-group", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    selected = [c for c in cases() if (not args.groups or c["group"] in args.groups.split(","))
                and int(c["id"].split(".")[1]) <= args.per_group]
    if args.limit:
        selected = selected[: args.limit]
    usage: list = []
    llm.usage_log.set(usage)
    if args.live and not args.no_cache:
        llm.cache_dir.set(config.STORAGE / "eval_cache")
    sem = asyncio.Semaphore(args.concurrency)

    async def guarded(c):
        async with sem:
            return await run_case(c, args.live)

    started = time.monotonic()
    results = await asyncio.gather(*(guarded(c) for c in selected))
    report = score(results)
    report.update(mode="live" if args.live else "offline", wall_time_s=round(time.monotonic() - started, 1),
                  usage=llm.summarize(usage), models={"reasoning": config.REASONING_MODEL},
                  perception="synthetic pre-written asset descriptions (vision not exercised)")
    out_dir = config.STORAGE / "eval_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (out_dir / f"{stamp}-{report['mode']}.json").write_text(json.dumps(report, indent=1, default=str))
    (out_dir / f"{stamp}-{report['mode']}.md").write_text(to_markdown(report))
    if not args.quiet:
        print(to_markdown(report))
        print(f"\nSaved: {out_dir / (stamp + '-' + report['mode'])}.json/.md")
    return report


def to_markdown(r: dict) -> str:
    lines = [f"# Routing evaluation ({r['mode']})", "",
             f"Cases: {r['cases']} · passed: {r['passed_cases']} · errors: {r['errors']} · wall time: {r['wall_time_s']}s",
             f"Perception: {r['perception']}", "", "| Metric | Value |", "|---|---|"]
    for k, v in r["metrics_pct"].items():
        lines.append(f"| {k.replace('_', ' ')} | {'n/a' if v is None else str(v) + '%'} |")
    u = r["usage"]
    lines += ["", f"LLM calls: {u['calls']} (cached {u['cached_calls']}, failed {u['failed_calls']}) · tokens in/out: "
              f"{u['prompt_tokens']}/{u['completion_tokens']} · summed latency: {u['latency_s']}s · est. cost: "
              f"{u['estimated_cost_usd'] if u['estimated_cost_usd'] is not None else 'not configured'}",
              "", r["execution_success_note"], "", "| Case | OK | Status | Apps | Intent | Failed checks |", "|---|---|---|---|---|---|"]
    for x in r["rows"]:
        lines.append(f"| {x['id']} | {'✔' if x['ok'] else '✘'} | {x['status']} | {', '.join(x.get('apps') or []) or '-'} | "
                     f"{x.get('intent', '-')} | {', '.join(x.get('failed_checks') or []) or (x.get('note') or '')} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    asyncio.run(main())
