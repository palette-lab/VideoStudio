"""One language per video, taken from the user's own words, so text, hook and voice never mix English and Korean.

    1. a language the user names ("in Korean", "한국어로", "in English", "영어로") wins
    2. otherwise the script they typed in: Hangul -> Korean
    3. otherwise the classifier's guess when it names another language the prompt really is in (e.g. Spanish),
       but never Korean for a prompt with no Hangul and no mention of Korean -> English
"""

import re

HANGUL = re.compile(r"[가-힣]")
ASK = {
    "korean": re.compile(r"\b(in|into|to)\s+korean\b|\bkorean\s+(language|voice|narration|text|subtitles?)\b|"
                         r"한국어|한글로", re.I),
    "english": re.compile(r"\b(in|into|to)\s+english\b|\benglish\s+(language|voice|narration|text|subtitles?)\b|"
                          r"영어로|영어\s*(음성|자막|텍스트)", re.I),
}
CODES = {"en": "english", "ko": "korean", "kr": "korean"}


def detect(prompt: str, guess: str | None = None) -> str:
    """"english", "korean", or the classifier's other language, for this prompt."""
    text = prompt or ""
    asked = [name for name, rx in ASK.items() if rx.search(text)]
    if len(asked) == 1:
        return asked[0]
    if HANGUL.search(text):
        return "korean"
    g = str(guess or "").strip().lower()
    g = CODES.get(g[:2], g) if len(g) <= 3 else g
    if not g or "korean" in g or g.startswith("ko") or "english" in g:
        return "english"
    return g


def rule(language: str) -> str:
    """The instruction every writer gets: the whole video in one language."""
    name = (language or "english").strip().capitalize()
    return (f"\n\nLANGUAGE RULE (strict): write EVERY word of this video in {name} only - narration, hook, titles, "
            f"key words, labels, captions and end card. Never mix languages, never add a translation or a second "
            f"language in brackets, even for one word. Proper names and brand names stay as they are.")


ISO = {"english": "en", "korean": "ko", "spanish": "es", "french": "fr", "italian": "it", "portuguese": "pt",
       "hindi": "hi", "japanese": "ja", "chinese": "zh", "german": "de"}


def iso(language: str | None) -> str | None:
    """ISO 639-1 code for the speech-to-text, or None to let it detect."""
    g = str(language or "").strip().lower()
    return g if len(g) == 2 else ISO.get(g)


def matches(text: str, language: str | None) -> bool:
    """False when text is plainly in the other one of English / Korean (a mis-heard or translated transcript)."""
    g = str(language or "").strip().lower()
    hangul = len(HANGUL.findall(text or ""))
    letters = len(re.findall(r"[A-Za-z]", text or ""))
    if g in ("english", "en"):
        return hangul <= letters * 0.1
    if g in ("korean", "ko"):
        return hangul >= letters * 0.5 or hangul > 0 and letters == 0
    return True
