"""Thesis structure detection shared by the parser, rule engine and LLM evaluator.

Works on a list of text lines (DOCX paragraphs or PDF lines). It locates chapter
starts, front matter and the reference list, and copes with table-of-contents
entries that repeat chapter headings.
"""
import re

CHAPTER_WORDS = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6, "SEVEN": 7,
                 "EIGHT": 8, "NINE": 9, "TEN": 10}
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}
CHAPTER_RE = re.compile(r"^\s*CHAPTER\s+(ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN|\d{1,2}|[IVX]{1,4})\b[\s:.\-–—]*(.*)$", re.I)
REFERENCES_RE = re.compile(r"^\s*(REFERENCES|BIBLIOGRAPHY)\s*$", re.I)
APPENDIX_RE = re.compile(r"^\s*APPENDI(?:X|CES)\b", re.I)
NUMBER_PREFIX_RE = re.compile(r"^\s*\d+(?:\.\d+)*[.)]?\s+")


def chapter_number(token):
    t = token.upper()
    if t.isdigit():
        return int(t)
    return CHAPTER_WORDS.get(t) or ROMAN.get(t)


def strip_numbering(text):
    return NUMBER_PREFIX_RE.sub("", text or "").strip()


def normalize_title(text):
    text = strip_numbering(text).lower()
    text = re.sub(r"[^a-z0-9/ ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def analyse_structure(lines):
    """Return line-index ranges for front matter, chapters, references, appendices."""
    candidates = []
    for i, line in enumerate(lines):
        m = CHAPTER_RE.match(line)
        if m and len(line) < 140:
            num = chapter_number(m.group(1))
            if num:
                candidates.append((i, num, m.group(2).strip()))
    # A chapter number may appear several times (TOC entry + real heading). Keep the
    # occurrence followed by the longest stretch of text before the next candidate.
    spans = []
    for k, (i, num, title) in enumerate(candidates):
        nxt = candidates[k + 1][0] if k + 1 < len(candidates) else len(lines)
        spans.append((nxt - i, i, num, title))
    best = {}
    for span, i, num, title in spans:
        if num not in best or span > best[num][0]:
            best[num] = (span, i, num, title)
    starts = sorted((v[1], v[2], v[3]) for v in best.values())

    refs_idx = None
    last_start = starts[-1][0] if starts else 0
    for i in range(len(lines) - 1, last_start, -1):
        if REFERENCES_RE.match(lines[i]):
            refs_idx = i
            break
    appendix_idx = None
    if refs_idx is not None:
        for i in range(refs_idx + 1, len(lines)):
            if APPENDIX_RE.match(lines[i]):
                appendix_idx = i
                break

    chapters = []
    body_end = refs_idx if refs_idx is not None else len(lines)
    for k, (i, num, title) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else body_end
        if not title and i + 1 < end and len(lines[i + 1]) < 100 and lines[i + 1].isupper():
            title = lines[i + 1].strip()
        chapters.append({"number": num, "label": "CHAPTER " + _word(num), "title": title,
                         "start": i, "end": max(end, i + 1)})
    return {"front_end": starts[0][0] if starts else 0, "chapters": chapters,
            "references": (refs_idx, appendix_idx or len(lines)) if refs_idx is not None else None,
            "has_chapters": bool(starts)}


def _word(n):
    for w, v in CHAPTER_WORDS.items():
        if v == n:
            return w
    return str(n)
