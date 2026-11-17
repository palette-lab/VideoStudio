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


# Archive photos (cinematic-skill references/archive.md and scripts/fetch_commons.py, MIT): Wikimedia Commons files
# with their licence and credit. Only clearly free files are used - public domain, CC0, CC BY, CC BY-SA; anything
# NonCommercial, NoDerivatives, fair use or "no known restrictions" (needs a human check) is refused.
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
ARCHIVE_MAX = 8
ARCHIVE_MIN_PX = 500
_FREE = re.compile(r"^(public domain|pd\b|pd-|cc0|cc[- ]?by(?:[- ]sa)?\b)", re.I)
_NOT_FREE = re.compile(r"\b(nc|nd)\b|non-?commercial|no ?deriv|fair use|non-?free|all rights reserved|"
                       r"no known (copyright )?restrictions", re.I)


def free_licence(short: str) -> bool:
    s = (short or "").strip()
    return bool(s) and not _NOT_FREE.search(s) and bool(_FREE.match(s))


def _meta(meta: dict, key: str, n: int = 300) -> str:
    return re.sub(r"\s+", " ", _clean(str((meta.get(key) or {}).get("value") or ""))).strip()[:n]


async def commons_search(client: httpx.AsyncClient, query: str, limit: int = 8) -> list[dict]:
    """Free-licensed still images on Commons for one query, each with its licence, credit and source page."""
    r = await client.get(COMMONS_API, params={
        "action": "query", "generator": "search", "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": 6,
        "gsrlimit": limit, "prop": "imageinfo", "iiprop": "url|extmetadata|mime|size", "iiurlwidth": 1600,
        "format": "json"})
    r.raise_for_status()
    out = []
    pages = sorted(((r.json().get("query") or {}).get("pages") or {}).values(), key=lambda p: p.get("index", 0))
    for p in pages:
        ii = (p.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata") or {}
        lic = _meta(meta, "LicenseShortName", 80)
        if ii.get("mime") not in ("image/jpeg", "image/png") or not free_licence(lic):
            continue
        if min(int(ii.get("width") or 0), int(ii.get("height") or 0)) < ARCHIVE_MIN_PX:
            continue
        title = str(p.get("title") or "")
        out.append({"title": title, "what": _meta(meta, "ImageDescription") or title.removeprefix("File:"),
                    "date": _meta(meta, "DateTimeOriginal", 40), "url": ii.get("thumburl") or ii.get("url"),
                    "source": ii.get("descriptionurl") or "", "license": lic,
                    "credit": _meta(meta, "Artist", 200) or _meta(meta, "Credit", 200) or "unknown"})
    return out


_GENERIC = {"the", "a", "an", "of", "and", "or", "in", "on", "to", "for", "with", "its", "their", "how", "why", "what",
            "effects", "effect", "causes", "cause", "consequences", "impact", "history", "story", "facts", "overview",
            "explained", "timeline", "role", "about"}


def archive_queries(cls: dict, prompt: str = "") -> list[str]:
    """Commons matches short keyword searches, not encyclopedia questions: the classifier's archive_queries, else
    each research query cut to its first three meaningful words."""
    own = [str(q).strip() for q in cls.get("archive_queries") or [] if str(q).strip()]
    if own:
        return own[:4]
    out = []
    for q in (cls.get("research_queries") or []) + [cls.get("subject") or prompt[:80]]:
        words = [w for w in re.findall(r"[\w'-]+", str(q or "")) if w.lower() not in _GENERIC][:3]
        short = " ".join(words)
        if short and short.lower() not in (o.lower() for o in out):
            out.append(short)
    return out[:4]


async def archive_photos(queries: list[str], limit: int = ARCHIVE_MAX) -> list[dict]:
    """Up to `limit` free archive photos for the film's research queries, ids A1, A2, ... Best effort: [] offline."""
    queries = [q for q in (queries or []) if str(q).strip()][:4]
    if not queries:
        return []
    try:
        async with httpx.AsyncClient(timeout=20, headers=_ua(), follow_redirects=True) as client:
            found = await asyncio.gather(*(commons_search(client, q) for q in queries), return_exceptions=True)
    except Exception as e:
        log.warning("commons search failed: %s", e)
        return []
    seen, out = set(), []
    for k in range(ARCHIVE_MAX):  # round robin, so every query gets its best photos in
        for f in found:
            if isinstance(f, list) and k < len(f) and f[k]["title"] not in seen and len(out) < limit:
                seen.add(f[k]["title"])
                out.append(f[k])
    for f in found:
        if isinstance(f, Exception):
            log.warning("commons search failed: %s", f)
    return [{"id": f"A{n}", **p} for n, p in enumerate(out, start=1)]


def credits_md(photos: list[dict]) -> str:
    """credits.md for the film: every archive photo used, with its author, licence and source page."""
    if not photos:
        return ""
    rows = [f"- {p['what'][:120]} - {p['credit']} - {p['license']} - {p['source']}" for p in photos]
    return "# Credits\n\nArchive photos (Wikimedia Commons):\n" + "\n".join(rows) + "\n"


def docx_text(path: Path) -> str:
    """The text of a Word document (.docx is a zip of XML): paragraphs, headings and table cells in order."""
    import zipfile
    from xml.etree import ElementTree as ET
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as z:
        if "word/document.xml" not in z.namelist():
            raise RuntimeError("this is not a Word document (.docx)")
        root = ET.fromstring(z.read("word/document.xml"))
    lines = []
    for para in root.iter(f"{w}p"):
        bits = []
        for node in para.iter():
            if node.tag == f"{w}t" and node.text:
                bits.append(node.text)
            elif node.tag in (f"{w}tab",):
                bits.append("\t")
            elif node.tag in (f"{w}br", f"{w}cr"):
                bits.append("\n")
        line = "".join(bits).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def document_text(path: str) -> str:
    """Plain text of an uploaded document: PDF through poppler's pdftotext (like ffmpeg, a system tool), a Word
    .docx from its XML, or a text file as is."""
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        exe = shutil.which("pdftotext")
        if not exe:
            raise RuntimeError("pdftotext (poppler) is not installed, so PDFs cannot be read")
        r = subprocess.run([exe, "-layout", "-enc", "UTF-8", str(p), "-"], capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"could not read the PDF: {(r.stderr or '').strip()[:200]}")
        text = r.stdout
    elif p.suffix.lower() == ".docx":
        text = docx_text(p)
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:DOC_CHARS]
