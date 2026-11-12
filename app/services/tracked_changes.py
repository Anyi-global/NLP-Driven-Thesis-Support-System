"""Real Word tracked changes (<w:ins>/<w:del>) for fixes that are safe to automate, plus supervisor comments.

Auto-fixed (pure text substitutions with one correct answer): '&' -> 'and' in citations (CITE-003), missing comma
before the year in a citation (CITE-002), Latin abbreviations (STY-001), ordinal suffixes on dates / positions and
decade apostrophes (STY-002). Everything else (margins, paper size, spacing, missing sections, grammar, acronyms)
needs human judgement and stays a note. Changes are proposals: the student accepts or rejects each in Word."""
import re
from copy import deepcopy
from datetime import datetime, timezone

from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from ..nlp.citations import PAREN_RE, NARRATIVE_RE, NO_COMMA_RE
from ..nlp.rule_engine import LATIN_RE, DECADE_RE, MONTHS
from ..nlp.structure import analyse_structure, CHAPTER_WORDS

AUTHOR = "UNIZIK Thesis Support System"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
LATIN = {"e.g.": "for example", "i.e.": "that is", "viz.": "namely", "cf.": "compare"}
WORDS = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth"}
DATE_ORD_RE = re.compile(r"\b(\d{1,2})(st|nd|rd|th)(?=\s+(?:of\s+)?(?:%s)\b)" % MONTHS)
POS_ORD_RE = re.compile(r"\b([1-9])(st|nd|rd|th)\b(?!\s+percentile)(?!\s+(?:of\s+)?(?:%s)\b)" % MONTHS)
AMP_RE = re.compile(r"\s&\s")
NAMES_AMP_RE = re.compile(r"([A-Za-z'’\-]+) & ([A-Za-z'’\-]+)")


def plan_fixes(text):
    """Return non-overlapping (start, end, new_text, rule_key, key_text) substitutions for one paragraph's text."""
    fixes = []
    for m in PAREN_RE.finditer(text):
        for pm in re.finditer(r"[^;]+", m.group(1)):
            part, off = pm.group(0), m.start(1) + pm.start()
            core = part.strip()
            lead = len(part) - len(part.lstrip())
            nm = NO_COMMA_RE.match(core)
            if nm:
                fixes.append((off + lead + nm.end(1), off + lead + nm.start(2), ", ", "CITE-002", nm.group(0)))
            for am in AMP_RE.finditer(part):
                names = NAMES_AMP_RE.search(part)
                fixes.append((off + am.start(), off + am.end(), " and ", "CITE-003", names.group(0) if names else "&"))
    for m in NARRATIVE_RE.finditer(text):
        for am in AMP_RE.finditer(m.group(1)):
            fixes.append((m.start(1) + am.start(), m.start(1) + am.end(), " and ", "CITE-003", m.group(1)))
    for m in LATIN_RE.finditer(text):
        tok = m.group(0)
        if tok.lower() in LATIN:
            new = LATIN[tok.lower()]
            fixes.append((m.start(), m.end(), new[0].upper() + new[1:] if tok[0].isupper() else new, "STY-001", tok))
        elif tok.lower().startswith("etc"):
            after = text[m.end():m.end() + 3].lstrip()
            final = tok.endswith(".") and (after == "" or after[0].isupper())
            fixes.append((m.start(), m.end(), "and so on" + ("." if final else ""), "STY-001", tok))
    for m in DATE_ORD_RE.finditer(text):
        fixes.append((m.start(2), m.end(2), "", "STY-002", m.group(0)))
    for m in POS_ORD_RE.finditer(text):
        fixes.append((m.start(), m.end(), WORDS[int(m.group(1))], "STY-002", m.group(0)))
    for m in DECADE_RE.finditer(text):
        fixes.append((m.end() - 2, m.end() - 1, "", "STY-002", m.group(0)))
    fixes.sort()
    out, last = [], -1
    for f in fixes:
        if f[0] >= last:
            out.append(f)
            last = f[1]
    return out


def _runs(p_el):
    """Direct runs of a paragraph as (element, start, end, editable, text). Only plain single-<w:t> runs are editable."""
    out, pos = [], 0
    for r in p_el.findall(qn("w:r")):
        kids = [c for c in r if c.tag != qn("w:rPr")]
        editable = len(kids) == 1 and kids[0].tag == qn("w:t")
        text = (kids[0].text or "") if editable else "\x00"
        out.append((r, pos, pos + len(text), editable, text))
        pos += len(text)
    return out


def _split(r, k):
    t = r.find(qn("w:t"))
    text = t.text or ""
    r2 = deepcopy(r)
    t2 = r2.find(qn("w:t"))
    t.text, t2.text = text[:k], text[k:]
    t.set(XML_SPACE, "preserve")
    t2.set(XML_SPACE, "preserve")
    r.addnext(r2)


def _revision(tag, rid, date):
    el = OxmlElement(tag)
    el.set(qn("w:id"), str(rid))
    el.set(qn("w:author"), AUTHOR)
    el.set(qn("w:date"), date)
    return el


def _apply(p_el, s, e, new, rid, date):
    runs = _runs(p_el)
    for cut in (e, s):          # split the later boundary first so earlier offsets stay valid
        for r, a, b, ed, _ in runs:
            if ed and a < cut < b:
                _split(r, cut - a)
                break
        runs = _runs(p_el)
    cover = [x for x in runs if x[2] > x[1] and x[1] >= s and x[2] <= e]
    if not cover or any(not x[3] for x in cover) or sum(x[2] - x[1] for x in cover) != e - s:
        return False
    d = _revision("w:del", rid, date)
    cover[0][0].addprevious(d)
    for r, *_ in cover:
        for t in r.findall(qn("w:t")):
            t.tag = qn("w:delText")
            t.set(XML_SPACE, "preserve")
        d.append(r)
    if new:
        ins, r2 = _revision("w:ins", rid + 1, date), OxmlElement("w:r")
        rpr = cover[0][0].find(qn("w:rPr"))
        if rpr is not None:
            r2.append(deepcopy(rpr))
        t = OxmlElement("w:t")
        t.text = new
        t.set(XML_SPACE, "preserve")
        r2.append(t)
        ins.append(r2)
        d.addnext(ins)
    return True


def _body_paragraphs(doc):
    paras = [p for p in doc.paragraphs if p.text.strip()]
    st = analyse_structure([p.text.strip() for p in paras])
    first = st["chapters"][0]["start"] + 1 if st["chapters"] else 0
    last = st["references"][0] if st["references"] else len(paras)
    return paras, st, paras[first:last]


def apply_tracked_changes(doc, allowed_rules, keep=lambda rule, key_text: True):
    """Insert tracked changes in the body (chapters only, never the reference list). Returns how many were applied."""
    _, _, body = _body_paragraphs(doc)
    ids = [int(e.get(qn("w:id"))) for e in doc.element.body.iter(qn("w:ins"), qn("w:del")) if (e.get(qn("w:id")) or "").isdigit()]
    rid, date, applied = max(ids, default=0) + 1, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), 0
    for p in body:
        text = "".join(x[4] for x in _runs(p._p))
        todo = [f for f in plan_fixes(text) if f[3] in allowed_rules and keep(f[3], f[4])]
        for s, e, new, rule, _ in reversed(todo):      # last to first: earlier offsets are unaffected
            if _apply(p._p, s, e, new, rid, date):
                rid += 2
                applied += 1
    return applied


def add_supervisor_comments(doc, items, max_comments=100):
    """Supervisor comments / edited suggestions as Word comments anchored on the relevant chapter heading.
    Needs python-docx >= 1.2 (Document.add_comment); returns 0 on older versions."""
    if not hasattr(doc, "add_comment"):
        return 0
    paras, st, _ = _body_paragraphs(doc)
    chapters = {c["number"]: paras[c["start"]] for c in st["chapters"]}
    fallback = paras[st["chapters"][0]["start"]] if st["chapters"] else (paras[0] if paras else None)
    n = 0
    for it in items:
        if it.status == "rejected" or not (it.supervisor_comment or it.status == "edited"):
            continue
        m = re.match(r"\[CHAPTER ([A-Z]+)", it.content or "")
        target = chapters.get(CHAPTER_WORDS.get(m.group(1))) if m else None
        target = target or fallback
        if target is None or not target.runs:
            continue
        text = it.content + ("\nSupervisor: " + it.supervisor_comment if it.supervisor_comment else "") + \
            ("\nSuggested change: " + it.suggestion if it.status == "edited" and it.suggestion else "")
        doc.add_comment(target.runs, text=text, author="Supervisor", initials="SUP")
        n += 1
        if n >= max_comments:
            break
    return n
