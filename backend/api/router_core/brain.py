"""LLM judgement used only where deterministic code cannot decide (AI Analyze mode).

- judge(): choose between several VIABLE routes that already passed the hard filter (cannot invent routes).
- review(): check a validated plan against the user's goal.
"""

import json

from . import config, llm, router

JUDGE_SYSTEM = """You compare candidate production routes for a video request. Every candidate already passed hard
capability and input checks; you may ONLY pick one of them by index. Consider what the user actually wants (single
shot vs scenes, realism vs precise animation, consistency, inputs), the deterministic score, and the unconfirmed
facts. Prefer fewer applications when they fully cover the request. For real-world subjects (products, vehicles,
machines, people, places) strongly prefer an application that films them with AI image/video models (graphics and
effects can be added on top) over one that draws them with code shapes; code-drawn animation suits charts, data,
kinetic text and abstract diagrams, or when the user explicitly asks for a 2D / motion-graphics look. Return JSON:
{"choice": 0, "why": "2-3 sentences for the user", "comparison": [{"index": 0, "verdict": "short"}], "confidence": 0.0}"""

REVIEW_SYSTEM = """You review a validated plan of a video-application router against the user's goal.
Check: does it deliver what the user asked for, are uploaded files used for the right purpose, is anything the user
asked for missing? Be concise and concrete. Return JSON: {"matches_intent": true, "concerns": [".."], "confidence": 0.0}"""


def _describe(route: dict) -> dict:
    return {"applications": [s["name"] for s in route["steps"]], "kind": route["kind"], "score": route["score"],
            "unconfirmed": route["unconfirmed"][:6], "scoring": [router.explain(s) for s in route["steps"]]}


async def judge(prompt: str, brief: dict, candidates: list[dict], chat=llm.chat_json) -> dict:
    user = {"prompt": prompt, "brief": brief, "candidates": [{"index": i, **_describe(c)} for i, c in enumerate(candidates)]}
    out = await chat(config.REASONING_MODEL, [{"role": "system", "content": JUDGE_SYSTEM},
                                              {"role": "user", "content": json.dumps(user, ensure_ascii=False)}],
                     stage="judge")
    idx = out.get("choice")
    if not isinstance(idx, int) or not 0 <= idx < len(candidates):
        idx, out["why"] = 0, "The AI's choice was invalid; the highest deterministic score was used."
    out["choice"] = idx
    return out


async def review(prompt: str, brief: dict, the_plan: dict, chat=llm.chat_json) -> dict:
    user = {"prompt": prompt, "brief": brief, "plan": the_plan}
    return await chat(config.REASONING_MODEL, [{"role": "system", "content": REVIEW_SYSTEM},
                                               {"role": "user", "content": json.dumps(user, ensure_ascii=False)}],
                      stage="review")
