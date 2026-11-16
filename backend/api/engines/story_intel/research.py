"""TopicResearchAgent sources: Wikipedia (free, no key) for factual topics, plus the text of documents the user
uploaded (PDF / TXT / MD). Wikipedia is used for leads and cross-checking, as the reference method advises
(cinematic-skill, references/story.md, MIT): every fact the film states keeps the source it came from.
"""

import asyncio
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

import httpx

log = logging.getLogger("palette_video.story_intel")

WIKI_API = "https://{lang}.wikipedia.org/w/api.php"
PAGE_CHARS = 7000      # per article
TOTAL_CHARS = 24000    # all sources together, so the research call stays focused
DOC_CHARS = 30000      # an uploaded document


# Wikimedia's robot policy rejects (403) a User-Agent without contact information. RESEARCH_CONTACT (a manifest
# secret) lets the operator give their own URL or email; the default names no person.
def _ua() -> dict:
    contact = os.getenv("RESEARCH_CONTACT") or "https://www.mediawiki.org/wiki/API:Etiquette"
    return {"User-Agent": f"VideoStudio-story-research/1.0 ({contact}) httpx/{httpx.__version__}"}


def _clean(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html or "").replace("&quot;", '"').replace("&amp;", "&")


async def wiki_search(client: httpx.AsyncClient, query: str, lang: str = "en", limit: int = 2) -> list[str]:
    r = await client.get(WIKI_API.format(lang=lang), params={"action": "query", "list": "search", "srsearch": query,
                                                             "srlimit": limit, "format": "json"})
    r.raise_for_status()
    return [h["title"] for h in (r.json().get("query") or {}).get("search") or []]


async def wiki_page(client: httpx.AsyncClient, title: str, lang: str = "en") -> dict | None:
    """One article's plain text (the API returns a full-text extract for only one page per request)."""
    r = await client.get(WIKI_API.format(lang=lang), params={
        "action": "query", "prop": "extracts|info", "inprop": "url", "explaintext": 1, "redirects": 1,
        "titles": title, "format": "json"})
    r.raise_for_status()
    for p in ((r.json().get("query") or {}).get("pages") or {}).values():
        text = (p.get("extract") or "").strip()
        if text:
            return {"title": p.get("title"), "url": p.get("fullurl") or "", "text": text[:PAGE_CHARS],
                    "kind": "wikipedia"}
    return None


async def wiki_pages(client: httpx.AsyncClient, titles: list[str], lang: str = "en") -> list[dict]:
    found = await asyncio.gather(*(wiki_page(client, t, lang) for t in titles[:6]), return_exceptions=True)
    for f in found:
        if isinstance(f, Exception):
            log.warning("wikipedia page failed: %s", f)
    return [f for f in found if isinstance(f, dict)]


async def gather(queries: list[str], lang: str = "en") -> list[dict]:
    """Articles for each research query (top results, de-duplicated). Network problems return what was found."""
    queries = [q for q in (queries or []) if str(q).strip()][:4]
    if not queries:
        return []
    try:
        async with httpx.AsyncClient(timeout=20, headers=_ua(), follow_redirects=True) as client:
            found = await asyncio.gather(*(wiki_search(client, q, lang) for q in queries), return_exceptions=True)
            titles: list[str] = []
            for f in found:
                if isinstance(f, Exception):
                    log.warning("wikipedia search failed: %s", f)
                for t in (f if isinstance(f, list) else []):
                    if t not in titles:
                        titles.append(t)
            pages = await wiki_pages(client, titles[:6], lang)
    except Exception as e:  # research is best effort: the story is still written, its facts marked unverified
        log.warning("wikipedia research failed: %s", e)
        return []
    total, keep = 0, []
    for p in pages:
        if total >= TOTAL_CHARS:
            break
        p["text"] = p["text"][: TOTAL_CHARS - total]
        total += len(p["text"])
        keep.append(p)
    return keep


def document_text(path: str) -> str:
    """Plain text of an uploaded document: PDF through poppler's pdftotext (like ffmpeg, a system tool), or a text
    file as is."""
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        exe = shutil.which("pdftotext")
        if not exe:
            raise RuntimeError("pdftotext (poppler) is not installed, so PDFs cannot be read")
        r = subprocess.run([exe, "-layout", "-enc", "UTF-8", str(p), "-"], capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"could not read the PDF: {(r.stderr or '').strip()[:200]}")
        text = r.stdout
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:DOC_CHARS]
