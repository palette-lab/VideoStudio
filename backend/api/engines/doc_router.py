"""What an uploaded document IS decides which studio makes the video - never its file type.

A .docx can be a report to explain, an ad script, a story, a screenplay, a lesson or a product sheet. One AI read of
the user's words and the document's text picks the use; only a document that is meant to be explained goes to the
Document Explainer. Every other kind becomes the brief of the matching film (an ad, a story or drama, a lesson...),
with the document's own words as its source.
"""

from __future__ import annotations

import logging

from router_core import config, llm

log = logging.getLogger("palette_video.doc_router")

# use -> (label, how the film pipeline should treat the document's text)
USES = {
    "explain": ("Document Explainer",
                "a document to be explained: a report, paper, article, manual, notes, data, contract or policy"),
    "ad": ("Advertisement",
           "an advertisement / marketing copy / product or brand promotion: make an ad video from it - its headline, "
           "selling points and call to action, in its tone"),
    "product": ("Product video",
                "a product sheet, catalogue or spec: show the product and its real features and use"),
    "story": ("Story film",
              "a story, fable or narrative to film: keep its plot, characters and order of events"),
    "drama": ("Short drama",
              "a novel chapter, screenplay or dialogue-driven story for characters who talk: adapt it as a drama"),
    "lesson": ("Educational video",
               "a lesson, tutorial, recipe or how-to: teach it step by step in the real order"),
    "presentation": ("Presenter video",
                     "a speech, pitch or talk to be presented by a person to camera"),
}

ROUTER = """You decide what kind of video an uploaded document should become. Read the user's words and the
document's text (the file type means nothing - a Word file can be an ad, a story, a report or a screenplay).
Uses:
{USES}
The user's own words win: "explain / summarise this document" -> explain; "make an ad from this" -> ad; "turn this
into a drama" -> drama. With no clear words, judge by the content: headlines, slogans, benefits and a call to action
mean an ad; characters and plot mean a story (talking characters, chapters or a screenplay mean a drama); a report,
data, a paper, manual or notes mean explain.
Return ONLY JSON {"use": one of the uses, "kind": "what the document is, in a few words", "why": "one short line"}."""

MAX_CHARS = 6000


async def route(prompt: str, documents: list[dict]) -> dict:
    """{"use", "label", "kind", "why"}; "explain" (the old behaviour) when the read fails."""
    docs = [d for d in documents or [] if str(d.get("text") or "").strip()][:3]
    if not docs:
        return {"use": "explain", "label": USES["explain"][0], "kind": "document", "why": "no readable text"}
    uses = "\n".join(f"- {k}: {v[1]}" for k, v in USES.items())
    body = "\n\n".join(f"=== {d.get('filename') or 'document'} ===\n{str(d['text'])[:MAX_CHARS]}" for d in docs)
    try:
        out = await llm.chat_json(config.REASONING_MODEL,
                                  [{"role": "system", "content": ROUTER.replace("{USES}", uses)},
                                   {"role": "user", "content": f"USER'S WORDS: {prompt.strip() or '(none)'}\n\n{body}"}],
                                  max_tokens=800, reasoning_effort="low", stage="story.doc_route")
    except Exception as e:  # noqa: BLE001 - a failed read keeps the old behaviour (explain the document)
        if type(e).__name__ == "RunCancelled":
            raise
        log.warning("document routing failed: %s", e)
        out = {}
    use = str(out.get("use") or "explain").strip().lower()
    if use not in USES:
        use = "explain"
    return {"use": use, "label": USES[use][0], "kind": str(out.get("kind") or "document")[:80],
            "why": str(out.get("why") or "")[:200]}


def brief(route_: dict | None, documents: list[dict]) -> str:
    """The document as the film's brief (for every use except explain, which reads the file itself)."""
    if not route_ or route_.get("use") == "explain":
        return ""
    docs = [d for d in documents or [] if str(d.get("text") or "").strip()][:3]
    if not docs:
        return ""
    how = USES.get(route_["use"], USES["story"])[1]
    text = "\n\n".join(f"=== {d.get('filename') or 'document'} ===\n{str(d['text'])[:12000]}" for d in docs)
    return (f"\n\nTHE UPLOADED DOCUMENT ({route_.get('kind') or 'document'}) IS THE BRIEF - {how}. Use its own "
            f"words, claims, names and order; do not invent claims it does not make; leave out placeholders such "
            f"as [Your website] and ask nothing about them:\n{text}")
