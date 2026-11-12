"""In-text citation and reference-list extraction and matching (author-year style)."""
import re

NAME = r"[A-Z][A-Za-z'’\-]+"
YEAR = r"(?:19|20)\d{2}[a-z]?"
PAREN_RE = re.compile(r"\(([^()]{1,200}?" + YEAR + r"[^()]{0,120}?)\)")
NARRATIVE_RE = re.compile(r"\b(" + NAME + r"(?:\s+(?:and|&)\s+" + NAME + r"|\s+et al\.?)?)\s*\((" + YEAR + r")\)")
NO_COMMA_RE = re.compile(r"^(" + NAME + r"(?:\s+(?:and|&)\s+" + NAME + r"|\s+et al\.?)?)\s+(" + YEAR + r")$")
STOP_AUTHORS = {"figure", "table", "chapter", "section", "see", "eg", "ie", "cf", "p", "pp"}


def extract_citations(text):
    """Return structured citations from body text (exclude the reference list first)."""
    text = re.sub(r"\s+", " ", text)  # PDF lines wrap inside citations
    items = []
    for m in PAREN_RE.finditer(text):
        for part in m.group(1).split(";"):
            part = part.strip()
            part = re.sub(r"^(?:e\.g\.,?|see|cf\.)\s+", "", part, flags=re.I)
            ym = re.findall(YEAR, part)
            am = re.match(NAME, part)
            if not ym or not am or am.group(0).lower() in STOP_AUTHORS:
                continue
            items.append({"raw": "(" + part + ")", "author": am.group(0), "year": ym[-1], "style": "parenthetical",
                          "missing_comma": bool(NO_COMMA_RE.match(part)), "ampersand": "&" in part, "group": m.start()})
    for m in NARRATIVE_RE.finditer(text):
        if m.group(1).split()[0].lower() in STOP_AUTHORS:
            continue
        items.append({"raw": m.group(0), "author": re.match(NAME, m.group(1)).group(0), "year": m.group(2),
                      "style": "narrative", "missing_comma": False, "ampersand": "&" in m.group(1), "group": None})
    return items


def split_reference_entries(lines, joined_lines=False):
    """Group raw lines into entries. DOCX paragraphs are already one entry each; PDF
    lines are wrapped, so a new entry starts only at 'Surname, X.' / 'Surname X' patterns."""
    if not joined_lines:
        return [l.strip() for l in lines if l.strip()]
    start = re.compile(r"^" + NAME + r"(?:,|\s+[A-Z]\.|\s+and\b|\s+&)")
    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if entries and not start.match(line):
            entries[-1] += " " + line
        else:
            entries.append(line)
    return entries


def reference_key(entry):
    a = re.match(NAME, entry.strip())
    y = re.search(YEAR, entry)
    return (a.group(0).lower() if a else None, y.group(0) if y else None)


def match_citations(citations, references):
    """Return (unmatched_citations, uncited_references)."""
    ref_keys = [reference_key(r) for r in references]
    ref_text = [r.lower() for r in references]
    unmatched = []
    for c in citations:
        a, y = c["author"].lower(), c["year"]
        ok = any(a in t and y[:4] in t for t in ref_text)
        if not ok:
            unmatched.append(c)
    cited = {(c["author"].lower(), c["year"][:4]) for c in citations}
    uncited = []
    for entry, (a, y) in zip(references, ref_keys):
        if a and y and (a, y[:4]) not in cited:
            uncited.append(entry)
    return unmatched, uncited


def unsorted_pairs(references):
    keys = [reference_key(r)[0] for r in references]
    out = []
    for i in range(1, len(keys)):
        if keys[i - 1] and keys[i] and keys[i] < keys[i - 1]:
            out.append((references[i - 1][:60], references[i][:60]))
    return out
