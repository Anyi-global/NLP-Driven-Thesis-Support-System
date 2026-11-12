"""Optional language engines: spaCy, NLTK and LanguageTool.

Every function works without the libraries (regex fallbacks), so the app never depends on them;
`engine_status()` reports which engines are actually active so results are never silently weaker.

  spaCy         sentence segmentation and tokenisation (blank English pipeline + sentencizer: no model download)
  NLTK          Porter stemming for retrieval (no corpus download needed)
  LanguageTool  British-English spelling/grammar (the Manual requires British English). Needs Java and either
                the language_tool_python download (first use) or a LANGUAGETOOL_URL server. Off unless enabled.
"""
import logging
import re

log = logging.getLogger(__name__)
_S = {"nlp": None, "nlp_tried": False, "stem": None, "stem_tried": False, "lt": None, "lt_tried": False, "cfg": {}}
_SENT_FALLBACK = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(\[])")
_WORD_FALLBACK = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-]*")


def configure(cfg):
    """Called by the analysis service with app config; resets lazily-built engines when the settings change."""
    keys = ("LANGUAGETOOL_ENABLED", "LANGUAGETOOL_URL", "LANGUAGETOOL_MAX_CHARS")
    new = {k: cfg.get(k) for k in keys}
    if new != _S["cfg"]:
        _S["cfg"], _S["lt"], _S["lt_tried"] = new, None, False


def preload_core_engines():
    """Load optional, local text engines before the first thesis is queued."""
    _spacy()
    _stemmer()


def _spacy():
    if not _S["nlp_tried"]:
        _S["nlp_tried"] = True
        try:
            import spacy
            nlp = spacy.blank("en")
            nlp.add_pipe("sentencizer")
            nlp.max_length = 2_000_000
            _S["nlp"] = nlp
        except Exception:
            _S["nlp"] = None
    return _S["nlp"]


def _stemmer():
    if not _S["stem_tried"]:
        _S["stem_tried"] = True
        try:
            from nltk.stem import PorterStemmer
            _S["stem"] = PorterStemmer()
        except Exception:
            _S["stem"] = None
    return _S["stem"]


def sentences(text):
    nlp = _spacy()
    if nlp is not None and text:
        return [s.text.strip() for s in nlp(text[:500_000]).sents if s.text.strip()]
    return [s.strip() for s in _SENT_FALLBACK.split(text or "") if s.strip()]


def sentence_containing(text, start, end, window=500):
    """The sentence around text[start:end]; segmentation is done on a small window, not the whole thesis."""
    lo, hi = max(0, start - window), min(len(text), end + window)
    snippet, needle = text[lo:hi], text[start:end]
    for s in sentences(snippet):
        if needle in s:
            return s[:400]
    return text[max(0, start - 80):end + 80].strip()


def word_tokens(text):
    nlp = _spacy()
    if nlp is not None and text:
        return [t.text for t in nlp.make_doc(text[:500_000]) if t.is_alpha or t.like_num]
    return _WORD_FALLBACK.findall(text or "")


def stem(token):
    st = _stemmer()
    return st.stem(token) if st is not None else token


def _languagetool():
    if not _S["cfg"].get("LANGUAGETOOL_ENABLED"):
        return None
    if not _S["lt_tried"]:
        _S["lt_tried"] = True
        try:
            import language_tool_python as lt
            url = _S["cfg"].get("LANGUAGETOOL_URL")
            _S["lt"] = lt.LanguageTool("en-GB", remote_server=url) if url else lt.LanguageTool("en-GB")
        except Exception as exc:
            log.warning("LanguageTool unavailable: %s", exc)
            _S["lt"] = None
    return _S["lt"]


def languagetool_matches(text):
    """Return a list of plain dicts, or None if LanguageTool is not available/enabled."""
    tool = _languagetool()
    if tool is None:
        return None
    limit = int(_S["cfg"].get("LANGUAGETOOL_MAX_CHARS") or 100_000)
    out = []
    try:
        for m in tool.check(text[:limit]):
            out.append({"rule_id": getattr(m, "ruleId", "") or getattr(m, "rule_id", ""),
                        "category": getattr(m, "category", "") or "",
                        "message": getattr(m, "message", "") or "",
                        "offset": getattr(m, "offset", 0), "length": getattr(m, "errorLength", 0) or getattr(m, "error_length", 0),
                        "replacements": list(getattr(m, "replacements", []) or [])[:3],
                        "context": getattr(m, "context", "") or ""})
    except Exception as exc:
        log.warning("LanguageTool check failed: %s", exc)
        return None
    return out


def engine_status():
    return {"spacy": _spacy() is not None, "nltk": _stemmer() is not None,
            "languagetool": _languagetool() is not None,
            "languagetool_requested": bool(_S["cfg"].get("LANGUAGETOOL_ENABLED"))}
