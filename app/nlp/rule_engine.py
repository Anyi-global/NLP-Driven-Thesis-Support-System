import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from .structure import normalize_title, CHAPTER_WORDS
from .citations import match_citations, unsorted_pairs

RULES_PATH = Path(__file__).resolve().parents[2] / "data" / "rules" / "unizik_rules.json"
MAX_ITEMS = 30  # cap per-rule findings so one repeated mistake cannot flood the dashboard


def _json_rules():
    try:
        data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def load_rules():
    """JSON holds executable check metadata; DB rows may override human-editable fields."""
    specs = {r["id"]: r for r in _json_rules() if isinstance(r, dict) and r.get("id")}
    try:
        from ..models.feedback import ComplianceRule
        for row in ComplianceRule.query.all():
            if row.rule_key not in specs and not row.pattern:
                continue  # obsolete row left from an older rule file: nothing executable to run
            merged = dict(specs.get(row.rule_key, {"id": row.rule_key, "check": "required_text"}))
            merged.update({"category": row.category, "description": row.description, "severity": row.severity,
                           "pattern": row.pattern, "suggestion": row.suggestion, "active": row.active,
                           "manual_reference": row.manual_reference})
            specs[row.rule_key] = merged
    except Exception:
        pass
    return list(specs.values())


def _finding(rule, location="Document-wide", excerpt=None, suggestion=None, severity=None):
    return {"rule_key": rule["id"], "category": rule.get("category", "general"), "location": location[:300],
            "excerpt": excerpt, "status": "open",
            "suggestion": suggestion or rule.get("suggestion", "Review this item against the approved guide."),
            "severity": severity or rule.get("severity", "medium"), "manual_reference": rule.get("manual_reference")}


def _top(counter, total, n=3):
    items = sorted(counter.items(), key=lambda kv: -kv[1])[:n]
    return ", ".join("%s (%d%%)" % (k, round(100 * v / total)) for k, v in items)


# ---------------------------------------------------------------- structure checks
def _check_required_text(rule, parsed):
    try:
        matched = re.search(rule.get("pattern") or "(?!)", parsed.get("text", "")) is not None
    except re.error:
        matched = True
    if matched:
        return []
    sev = rule.get("partial_draft_severity") if parsed.get("partial_draft") else None
    note = " (This draft does not yet contain all five chapters, so this may be expected.)" if sev else ""
    return [_finding(rule, suggestion=rule.get("suggestion", "") + note, severity=sev)]


def _check_required_headings(rule, parsed):
    word = re.sub(r"(?i)^chapter\s+", "", rule.get("chapter", "")).strip().upper()
    number = CHAPTER_WORDS.get(word)
    chapter = next((c for c in parsed.get("chapters", []) if c["number"] == number), None)
    if chapter is None:
        if parsed.get("partial_draft"):
            return [_finding(rule, rule.get("chapter", "Chapter structure"),
                             suggestion="%s was not found in this draft. This is expected for a partial draft; it must be present in the final submission." % rule.get("chapter"),
                             severity="low")]
        return [_finding(rule, rule.get("chapter", "Chapter structure"), severity="high",
                         suggestion="%s was not found. Check that the chapter heading is typed as 'CHAPTER ...' on its own line." % rule.get("chapter"))]
    lines = parsed["lines"][chapter["start"] + 1:chapter["end"]]
    candidates = [normalize_title(l) for l in lines if len(l) < 140]
    missing, near, satisfied = [], [], set()
    for req in rule.get("required", []):
        alts = [a.strip() for a in req.split("||")]       # 'A || B' = either heading satisfies the requirement
        best = (0, "")
        for alt in alts:
            want = normalize_title(alt)
            best = max(best, max(((SequenceMatcher(None, want, c).ratio(), c) for c in candidates if c), default=(0, "")))
        if best[0] >= 0.88:
            satisfied.add(best[1])
        else:
            missing.append(alts[0])
            near.append((alts[0], best))
    # Only suggest a 'closest heading' that is not already the match for another requirement.
    hints = ["%s (closest heading found: '%s')" % (req, b[1]) for req, b in near if b[0] >= 0.6 and b[1] not in satisfied]
    if not missing:
        return []
    detail = "; ".join(hints + [m for m in missing if not any(m in h for h in hints)])
    return [_finding(rule, rule.get("chapter", "Chapter structure"), "Missing or differently titled: " + detail,
                     "Add or rename these sections to match the departmental format: " + "; ".join(missing))]


def _check_preliminary_order(rule, parsed):
    seq = rule.get("sequence") or []
    front = parsed.get("front_matter") or []
    if not front:
        if parsed.get("partial_draft"):
            return [_finding(rule, "Preliminary pages", suggestion="No preliminary pages were found before Chapter One. Expected for a chapters-only draft; they are required in the final submission.", severity="low")]
        front = parsed.get("lines", [])
    found = _prelim_positions(front, seq)
    out = []
    missing = [it["key"] for it, pos in found if pos is None and it.get("required")]
    if missing:
        out.append(_finding(rule, "Preliminary pages", "Missing: " + ", ".join(missing),
                            "Add the missing preliminary page(s): " + ", ".join(missing) + ". Pages must carry the exact headings (e.g. CERTIFICATION) on their own line to be detected."))
    present = [(it["key"], pos) for it, pos in found if pos is not None]
    bad = [(a[0], b[0]) for a, b in zip(present, present[1:]) if b[1] < a[1]]
    if bad:
        out.append(_finding(rule, "Preliminary pages", "; ".join("'%s' appears before '%s'" % (b, a) for a, b in bad),
                            "Reorder the preliminary pages: " + " > ".join(it["key"] for it in seq)))
    return out


# ---------------------------------------------------------------- formatting checks
def _body(parsed):
    return parsed.get("formatting", {}).get("body") or {}


def _check_font_name(rule, parsed):
    b = _body(parsed)
    total = b.get("body_chars", 0)
    if not total:
        return []
    expected = str(rule.get("expected", "Times New Roman")).lower()
    wrong = {k: v for k, v in b.get("fonts", {}).items() if k.lower() != expected}
    out = []
    if b.get("unresolved_font_chars", 0) / total > 0.5:
        out.append(_finding(rule, "Body text", "Font could not be determined for most body text.",
                            "The font could not be resolved from the document's styles. Verify manually.", "low"))
    share = sum(wrong.values()) / total
    if share > float(rule.get("tolerance_share", 0.05)):
        out.append(_finding(rule, "Body text (%d%% of characters)" % round(100 * share), _top(wrong, total)))
    return out


def _check_font_size(rule, parsed):
    b = _body(parsed)
    total = b.get("body_chars", 0)
    if not total:
        return []
    exp, tol = float(rule.get("expected", 12)), float(rule.get("tolerance", .5))
    wrong = {k + " pt": v for k, v in b.get("sizes", {}).items() if abs(float(k) - exp) > tol}
    share = sum(wrong.values()) / total
    if share > float(rule.get("tolerance_share", 0.05)):
        return [_finding(rule, "Body text (%d%% of characters)" % round(100 * share), _top(wrong, total))]
    return []


def _check_line_spacing(rule, parsed):
    b = _body(parsed)
    sp = b.get("spacing") or {}
    total = sum(sp.values())
    if not total:
        return []
    accepted = [float(x) for x in rule.get("expected_any", [rule.get("expected", 1.5)])]
    tol = float(rule.get("tolerance", .05))
    def ok(k):
        try:
            return any(abs(float(k) - e) <= tol for e in accepted)
        except ValueError:
            return False  # fixed-point spacing ('exact:18pt') cannot be compared reliably
    wrong = {k: v for k, v in sp.items() if not ok(k)}
    share = sum(wrong.values()) / total
    if share > float(rule.get("tolerance_share", 0.10)):
        return [_finding(rule, "Body text (%d%% of characters)" % round(100 * share),
                         _top(wrong, total) + " line spacing found; accepted: " + " or ".join("%.1f" % e for e in accepted))]
    return []


def _check_margins(rule, parsed):
    if parsed.get("file_type") == "pdf":
        return [_finding(rule, "PDF layout", suggestion="PDF margins, line spacing and page-number style are not measured. Verify manually or submit DOCX.", severity="low")]
    tol = float(rule.get("tolerance", .05))
    profiles = rule.get("profiles") or {"Expected": {k: float(rule.get("expected", 1)) for k in ("top", "bottom", "left", "right")}}
    bad = []
    secs = parsed.get("formatting", {}).get("sections", [])
    for s in secs:
        m = s.get("margins_inches") or {}
        if any(all(abs(float(m.get(side, 0)) - want) <= tol for side, want in prof.items()) for prof in profiles.values()):
            continue
        bad.append("Section %d: %s" % (s["index"] + 1, ", ".join("%s=%s in" % kv for kv in m.items())))
    if bad:
        return [_finding(rule, "Page layout (%d of %d sections)" % (len(bad), len(secs)),
                         "; ".join(bad[:10]) + ". Accepted: " + " or ".join(profiles))]
    return []


def _check_paper_size(rule, parsed):
    want = sorted(rule.get("expected_inches", [8.27, 11.69]))
    tol = float(rule.get("tolerance", .1))
    bad = []
    for s in parsed.get("formatting", {}).get("sections", []):
        p = s.get("paper_size_inches") or {}
        dims = sorted([p.get("width", 0), p.get("height", 0)])  # sorted: landscape sections are fine
        if any(abs(a - b) > tol for a, b in zip(dims, want)):
            label = "US Letter" if abs(dims[0] - 8.5) < tol and abs(dims[1] - 11) < tol else "not A4"
            bad.append("Section %d: %.2f x %.2f in (%s)" % (s["index"] + 1, dims[0], dims[1], label))
    if bad:
        return [_finding(rule, "Paper size", "; ".join(bad[:10]))]
    return []


def _check_page_numbering(rule, parsed):
    if parsed.get("file_type") != "docx":
        return []
    secs = parsed.get("formatting", {}).get("sections", [])
    if not secs:
        return []
    if not any(s.get("has_page_field") for s in secs):
        return [_finding(rule, "Headers/footers", "No page-number field detected.",
                         "No page-number field was found in any header or footer. Insert page numbers at the bottom right.")]
    out = []
    in_header = [s["index"] + 1 for s in secs if s.get("page_field_where") == "header"]
    off_side = [s["index"] + 1 for s in secs if s.get("page_field_where") == "footer"
                and s.get("page_field_align") not in ("right",) and not s.get("page_field_tabbed")]
    if in_header:
        out.append(_finding(rule, "Page numbers", "Page number is in the header of section(s) %s." % ", ".join(map(str, in_header)),
                            "Move page numbers to the footer, bottom right."))
    if off_side:
        out.append(_finding(rule, "Page numbers", "Footer page number is %s-aligned in section(s) %s." % (
            next(s["page_field_align"] for s in secs if s["index"] + 1 == off_side[0]), ", ".join(map(str, off_side))),
            "Right-align the footer page number (bottom right)."))
    if parsed.get("front_matter"):
        if not any(s.get("page_number_format") == "lowerRoman" for s in secs):
            out.append(_finding(rule, "Preliminary pages", "No section uses lower-case Roman numerals."))
        elif len(secs) == 1:
            out.append(_finding(rule, "Page numbering", "Only one section.", "The document has a single section, so preliminary pages and the main body cannot have separate numbering styles."))
    return out


# ---------------------------------------------------------------- citation checks
def _dedupe(items, key):
    seen, out = set(), []
    for it in items:
        k = key(it)
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


def _capped(rule, items, build):
    findings = [build(i) for i in items[:MAX_ITEMS]]
    if len(items) > MAX_ITEMS:
        findings.append(_finding(rule, "Document-wide", "%d further instances not listed." % (len(items) - MAX_ITEMS),
                                 "Fix the listed instances, then resubmit to see the remainder.", "low"))
    return findings


def _check_citations_present(rule, parsed):
    if parsed.get("citation_items"):
        return []
    return [_finding(rule, suggestion="No author-year citations were detected automatically. Review manually; this may be a parser limitation.")]


def _check_citation_format(rule, parsed):
    items = _dedupe([c for c in parsed.get("citation_items", []) if c["missing_comma"]], lambda c: c["raw"])
    def build(c):
        fixed = re.sub(r"\s+(\S+\))$", r", \1", c["raw"])
        return _finding(rule, "Citation: " + c["raw"], c["raw"], "Write %s instead of %s." % (fixed, c["raw"]))
    return _capped(rule, items, build)


def _check_citation_ampersand(rule, parsed):
    items = _dedupe([c for c in parsed.get("citation_items", []) if c["ampersand"]], lambda c: c["raw"])
    return _capped(rule, items, lambda c: _finding(rule, "Citation: " + c["raw"], c["raw"],
                                                   "Write %s instead." % c["raw"].replace("&", "and")))


def _check_reference_match(rule, parsed):
    refs = parsed.get("references", [])
    if not refs:
        return []
    unmatched, _ = match_citations(parsed.get("citation_items", []), refs)
    unmatched = _dedupe(unmatched, lambda c: (c["author"].lower(), c["year"]))
    return _capped(rule, unmatched, lambda c: _finding(rule, "Detected citation: " + c["raw"][:120], c["raw"][:300]))


def _check_uncited(rule, parsed):
    refs = parsed.get("references", [])
    if not refs or not parsed.get("citation_items"):
        return []
    _, uncited = match_citations(parsed["citation_items"], refs)
    return _capped(rule, uncited, lambda r: _finding(rule, "Reference entry: " + r[:100], r[:300]))


def _check_reference_order(rule, parsed):
    refs = parsed.get("references", [])
    if len(refs) < 3:
        return []
    pairs = unsorted_pairs(refs)
    if not pairs:
        return []
    return [_finding(rule, "Reference list (%d out-of-order entries)" % len(pairs),
                     "; ".join("'%s' follows '%s'" % (b, a) for a, b in pairs[:5]))]


def _prelim_positions(front, seq):
    found = []
    for item in seq:
        rx = re.compile(r"^\s*(?:%s)\s*$" % item["pattern"], re.I)
        found.append((item, next((i for i, l in enumerate(front) if rx.match(l)), None)))
    return found


def _check_abstract_length(rule, parsed):
    front = parsed.get("front_matter") or []
    seq = (next((r for r in _json_rules() if r.get("id") == "PRE-001"), {}) or {}).get("sequence", [])
    found = dict((it["key"], pos) for it, pos in _prelim_positions(front, seq))
    start = found.get("Abstract")
    if start is None:
        return []
    later = [pos for pos in found.values() if pos is not None and pos > start]
    block = front[start + 1:(min(later) if later else len(front))]
    block = [l for l in block if not re.match(r"(?i)^\s*key\s*words?\b", l)]
    from .linguistics import word_tokens
    words = len(word_tokens("\n".join(block)))
    limits = rule.get("limits", {"msc": 300, "phd": 500})
    out = []
    if words > limits["phd"]:
        out.append(_finding(rule, "Abstract", "%d words (maximum 500, even for a doctoral dissertation)." % words))
    elif words > limits["msc"]:
        out.append(_finding(rule, "Abstract", "%d words: acceptable for a PhD (500) but over the 300-word Master's limit." % words,
                            "If this is a Master's thesis, shorten the abstract to 300 words.", "low"))
    if len(block) > 1:
        out.append(_finding(rule, "Abstract", "%d separate paragraphs." % len(block),
                            "The Manual requires the abstract to be written as one block paragraph.", "low"))
    return out


NUM_HEAD_RE = re.compile(r"^(\d+(?:\.\d+)+)\s+[A-Z]")


def _check_heading_numbering(rule, parsed):
    out = []
    for c in parsed.get("chapters", []):
        last, prev_label = {}, None
        for line in parsed["lines"][c["start"] + 1:c["end"]]:
            m = NUM_HEAD_RE.match(line)
            if not m or len(line) > 120 or line.rstrip().endswith("."):
                continue
            parts = m.group(1).split(".")
            if int(parts[0]) != c["number"] or len(parts) > 5:
                continue
            parent, idx = ".".join(parts[:-1]), int(parts[-1])
            expected = last.get(parent, 0) + 1
            if idx != expected:
                kind = "skips %s" % ".".join(parts[:-1] + [str(expected)]) if idx > expected else "repeats or goes backwards"
                out.append(_finding(rule, "%s: heading %s" % (c["label"], m.group(1)),
                                    "'%s' follows %s and %s." % (line[:70], prev_label or "the chapter heading", kind)))
            last[parent] = idx
            prev_label = m.group(1)
    return out[:MAX_ITEMS]


def _check_citation_group_order(rule, parsed):
    groups = {}
    for c in parsed.get("citation_items", []):
        if c.get("group") is not None:
            groups.setdefault(c["group"], []).append(c)
    bad, seen = [], set()
    for items in groups.values():
        names = [i["author"].lower() for i in items]
        key = tuple((i["author"].lower(), i["year"]) for i in items)
        if len(items) > 1 and names != sorted(names) and key not in seen:
            seen.add(key)
            bad.append(items)
    return _capped(rule, bad, lambda items: _finding(
        rule, "Citation group: " + "; ".join("%s, %s" % (i["author"], i["year"]) for i in items)[:200],
        "; ".join(i["raw"] for i in items)[:300],
        "Reorder alphabetically: " + "; ".join("%s, %s" % (i["author"], i["year"]) for i in sorted(items, key=lambda i: i["author"].lower()))))


# ---------------------------------------------------------------- heading format, style, language
SMALL_WORDS = {"a", "an", "and", "or", "but", "nor", "for", "of", "the", "in", "on", "at", "to", "by", "with", "from",
               "as", "vs", "per", "via"}
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’\-]*")


def _words(title):
    return [w for w in WORD_RE.findall(title) if not (w.isupper() and len(w) > 1)]   # drop acronyms


def title_case_violations(title):
    """Words that should be capitalised in Title Case (first word always; small words excepted)."""
    ws = _words(title)
    return [w for i, w in enumerate(ws) if w[0].islower() and (i == 0 or w.lower() not in SMALL_WORDS)]


def looks_title_case(title):
    rest = [w for w in _words(title)[1:] if w.lower() not in SMALL_WORDS]
    return len(rest) >= 2 and sum(w[0].isupper() for w in rest) / len(rest) >= 0.6


def _check_heading_format(rule, parsed):
    meta = parsed.get("line_meta") or []
    if parsed.get("file_type") != "docx" or not meta:
        return []
    found = []

    def add(where, text, why):
        found.append(_finding(rule, where, text[:120], why))
    for c in parsed.get("chapters", []):
        lines = parsed["lines"]
        label_idx = c["start"]
        parts = [(label_idx, "chapter number")]
        nxt = label_idx + 1
        if nxt < c["end"] and len(lines[nxt]) < 100 and not NUM_HEAD_RE.match(lines[nxt]) and not lines[nxt].rstrip().endswith("."):
            parts.append((nxt, "chapter title"))
        for idx, what in parts:
            m, text = meta[idx], lines[idx]
            problems = [x for x, bad in (("not centred", m.get("align") != "center"), ("not bold", not m.get("bold")),
                                         ("not in UPPERCASE", not text.isupper())) if bad]
            if problems:
                add("%s: %s" % (c["label"], what), text, "The %s is %s." % (what, " and ".join(problems)))
        for i in range(c["start"] + 1, c["end"]):
            text = lines[i]
            mm = NUM_HEAD_RE.match(text)
            if not mm or len(text) > 120 or text.rstrip().endswith("."):
                continue
            nums = mm.group(1).split(".")
            if int(nums[0]) != c["number"]:
                continue
            depth, m = len(nums), meta[i]
            title = text[len(mm.group(1)):].strip()
            problems = []
            if depth > 4:
                problems.append("is a level-%d heading; at most four levels are allowed" % depth)
            if not m.get("bold"):
                problems.append("is not bold")
            if m.get("align") in ("center", "right"):
                problems.append("is not flush left")
            if depth == 2 and title_case_violations(title):
                problems.append("should use Title Case (capitalise: %s)" % ", ".join(title_case_violations(title)[:4]))
            if depth >= 3 and looks_title_case(title):
                problems.append("should capitalise only the first word")
            if depth == 4 and not m.get("italic"):
                problems.append("should be italic (level four)")
            if problems:
                add("%s: heading %s" % (c["label"], mm.group(1)), text, "Heading %s %s." % (mm.group(1), "; ".join(problems)))
    return found[:MAX_ITEMS]


def _body_text(parsed, numbers=None):
    lines = parsed.get("lines", [])
    chunks = []
    for c in parsed.get("chapters", []):
        if numbers is None or c["number"] in numbers:
            chunks += [l for l in lines[c["start"] + 1:c["end"]]]
    return "\n".join(chunks)


def _sentence(text, m):
    from .linguistics import sentence_containing
    return sentence_containing(text, m.start(), m.end())


LATIN_RE = re.compile(r"(?<![\w.])(?:e\.g\.|i\.e\.|viz\.|cf\.|ibid\.?|op\.\s?cit\.?|n\.b\.)|\betc\b\.?", re.I)
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
# Under 10 -> must be words ('fifth'); 10 and over only matters in dates ('15th December'). '50th percentile' is neither.
ORDINAL_RE = re.compile(r"\b[1-9](?:st|nd|rd|th)\b(?!\s+percentile)|\b\d{1,2}(?:st|nd|rd|th)\s+(?:of\s+)?(?:%s)\b" % MONTHS)
DECADE_RE = re.compile(r"\b(?:1[89]|20)\d0['’]s\b")
PCT_RE = re.compile(r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)\s*%", re.I)
FIRST_PERSON_RE = re.compile(r"\b(?:we|We|our|Our|ours|us|my|My|me|mine)\b|\bI\b(?![/.\-])")
ACR_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,7}\b")
ROMAN_RE = re.compile(r"[IVXLCDM]+")


def _distinct_matches(regex, text, key=lambda m: m.group(0).lower()):
    seen = {}
    for m in regex.finditer(text):
        k = key(m)
        seen.setdefault(k, [m, 0])[1] += 1
    return list(seen.values())


def _check_latin(rule, parsed):
    text = _body_text(parsed)
    return _capped(rule, _distinct_matches(LATIN_RE, text), lambda mc: _finding(
        rule, "Latin abbreviation: %s (%d times)" % (mc[0].group(0), mc[1]), _sentence(text, mc[0])))


def _check_numerals(rule, parsed):
    text = _body_text(parsed)
    items = []
    for regex, why in ((ORDINAL_RE, "ordinal suffix"), (DECADE_RE, "apostrophe in decade"), (PCT_RE, "spelled number with %")):
        items += [(mc, why) for mc in _distinct_matches(regex, text)]
    return _capped(rule, items, lambda it: _finding(
        rule, "%s: %s (%d times)" % (it[1].capitalize(), it[0][0].group(0), it[0][1]), _sentence(text, it[0][0])))


def _check_third_person(rule, parsed):
    text = _body_text(parsed, {int(rule.get("chapter", 4))})
    # 'My Reports' / 'Our Team' are interface labels or names, not first-person narration
    hits = [m for m in FIRST_PERSON_RE.finditer(text)
            if not (m.group(0) in ("My", "Our") and re.match(r"\s+[A-Z]", text[m.end():m.end() + 3]))]
    if not hits:
        return []
    examples, seen = [], set()
    for m in hits:
        sent = _sentence(text, m)
        if sent not in seen:
            seen.add(sent); examples.append(sent)
        if len(examples) == 3:
            break
    return [_finding(rule, "Chapter %s (%d first-person words)" % (rule.get("chapter", 4), len(hits)), " | ".join(examples)[:600])]


def _acronym_defined(text, start, token):
    end = start + len(token)
    if text[max(0, start - 1):start] == "(" and text[end:end + 1] == ")":
        return True
    m = re.match(r"\s*\(([A-Za-z][^()]{3,90})\)", text[end:end + 120])      # reversed form: NLP (Natural Language Processing)
    if m:
        words = [w for w in re.findall(r"[A-Za-z]+", m.group(1)) if w.lower() not in SMALL_WORDS]
        return len(words) >= 2 and words[0][0].upper() == token[0]
    return False


def _check_acronyms(rule, parsed):
    lines = [l for l in _body_text(parsed).splitlines()]
    text = "\n".join("" if (l.isupper() and len(l) > 2) else l for l in lines)   # skip all-caps heading lines
    first = {}
    for m in ACR_RE.finditer(text):
        tok = m.group(0)
        if sum(ch.isupper() for ch in tok) < 2 or ROMAN_RE.fullmatch(tok) or tok in first:
            continue
        first[tok] = m
    bad = [(tok, m) for tok, m in first.items() if not _acronym_defined(text, m.start(), tok)]
    return _capped(rule, bad, lambda tm: _finding(rule, "Acronym: %s" % tm[0], _sentence(text, tm[1]),
                   "'%s' is used without being spelled out on first use. Write the full term followed by (%s)." % (tm[0], tm[0])))


def _custom_dictionary():
    path = RULES_PATH.parent / "custom_dictionary.txt"
    try:
        return {w.strip().lower() for w in path.read_text(encoding="utf-8").splitlines() if w.strip() and not w.startswith("#")}
    except OSError:
        return set()


GRAMMAR_CATEGORIES = {"GRAMMAR", "CONFUSED_WORDS", "TYPOS", "SEMANTICS"}


def summarise_languagetool(matches, text):
    """Group LanguageTool matches into findings: one per distinct misspelt word, one per grammar match."""
    ignore, spell, grammar = _custom_dictionary(), {}, []
    for m in matches:
        word = text[m["offset"]:m["offset"] + m["length"]]
        rid, cat = (m.get("rule_id") or "").upper(), str(m.get("category") or "").upper()
        if "SPELL" in rid or "MORFOLOGIK" in rid:
            if len(word) <= 2 or word[0].isupper() or any(ch.isdigit() for ch in word) or word.lower() in ignore:
                continue   # names, acronyms, identifiers and project vocabulary are not spelling errors
            spell.setdefault(word.lower(), [m, 0])[1] += 1
        elif cat in GRAMMAR_CATEGORIES:
            grammar.append(m)
    return spell, grammar


def _check_languagetool(rule, parsed):
    from .linguistics import languagetool_matches
    text = _body_text(parsed)
    matches = languagetool_matches(text)
    if matches is None:
        return []   # engine unavailable: the analysis note reports this, so absence is never silent
    spell, grammar = summarise_languagetool(matches, text)
    out = []
    for word, (m, n) in list(spell.items())[:MAX_ITEMS]:
        sug = ", ".join(m["replacements"]) or "check the spelling"
        out.append(_finding(rule, "Spelling (British English): '%s' (%d times)" % (word, n), m["context"][:300],
                            "Possible misspelling or non-British spelling. Suggested: %s." % sug))
    for m in grammar[:MAX_ITEMS]:
        out.append(_finding(rule, "Grammar: " + (m["message"] or "")[:120], m["context"][:300],
                            (m["message"] or "") + (" Suggested: " + ", ".join(m["replacements"]) if m["replacements"] else "")))
    return out


CHECKS = {"required_text": _check_required_text, "required_headings": _check_required_headings,
          "preliminary_order": _check_preliminary_order, "margins_inches": _check_margins,
          "font_name": _check_font_name, "font_size_pt": _check_font_size, "line_spacing": _check_line_spacing,
          "paper_size": _check_paper_size, "page_numbering": _check_page_numbering,
          "citations_present": _check_citations_present, "citation_format": _check_citation_format,
          "citation_ampersand": _check_citation_ampersand, "citation_reference_match": _check_reference_match,
          "uncited_references": _check_uncited, "reference_order": _check_reference_order,
          "abstract_length": _check_abstract_length, "heading_numbering": _check_heading_numbering,
          "citation_group_order": _check_citation_group_order,
          "heading_format": _check_heading_format, "latin_abbreviations": _check_latin, "numeral_style": _check_numerals,
          "third_person": _check_third_person, "acronym_definitions": _check_acronyms, "languagetool": _check_languagetool}


def run_compliance_checks(parsed, rules=None):
    findings = []
    for rule in (rules if rules is not None else load_rules()):
        if not rule.get("active", True):
            continue
        fn = CHECKS.get(rule.get("check"))
        if fn:
            findings.extend(fn(rule, parsed))
    return findings
