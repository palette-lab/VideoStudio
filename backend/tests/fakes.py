"""A scripted LLM: returns a given brief for the 'brief' stage and simple answers for judge/review. Counts calls."""


class FakeLLM:
    def __init__(self, brief, judge_choice=0):
        self.brief, self.judge_choice, self.calls = brief, judge_choice, []

    async def __call__(self, model, messages, **kw):
        stage = kw.get("stage")
        self.calls.append(stage)
        if stage == "brief":
            return dict(self.brief)
        if stage == "brief.recheck":
            return dict(getattr(self, "rechecked", None) or self.brief)
        if stage == "judge":
            return {"choice": self.judge_choice, "why": "judged", "confidence": 0.8}
        if stage == "review":
            return {"matches_intent": True, "concerns": [], "confidence": 0.9}
        raise AssertionError(f"unexpected stage {stage}")
