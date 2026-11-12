"""Pure-logic tests (no database, no network). Run: python -m unittest tests.test_nlp  (or pytest)."""
import json
import tempfile
import unittest
from pathlib import Path

from docx import Document
from docx.shared import Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import parse_xml

from app.nlp.document_parser import parse_document
from app.nlp.rule_engine import run_compliance_checks, load_rules
from app.nlp.structure import analyse_structure, normalize_title
from app.nlp.citations import extract_citations
from app.nlp.rag_retriever import bm25_search
from app.nlp import llm_client

LONG = "This is a body sentence that is long enough to be measured as body text by the inspector. "


W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def make_docx(path, blocks, font="Times New Roman", size=12, spacing=2.0, odd_runs=None, margins_cm=None,
              page_number=None):
    """margins_cm=(top,bottom,left,right); page_number=("footer"|"header", WD_ALIGN_PARAGRAPH.X)."""
    doc = Document()
    if margins_cm:
        sec = doc.sections[0]
        sec.top_margin, sec.bottom_margin, sec.left_margin, sec.right_margin = [Cm(x) for x in margins_cm]
    if page_number:
        where, align = page_number
        hf = doc.sections[0].footer if where == "footer" else doc.sections[0].header
        hf.is_linked_to_previous = False
        hp = hf.paragraphs[0]
        hp.alignment = align
        hp._p.append(parse_xml('<w:fldSimple %s w:instr="PAGE"><w:r><w:t>1</w:t></w:r></w:fldSimple>' % W_NS))
    st = doc.styles["Normal"]
    st.font.name, st.font.size = font, Pt(size)
    st.paragraph_format.line_spacing = spacing
    for b in blocks:
        opts = {}
        if isinstance(b, tuple):          # (text, {"bold":..., "italic":..., "center":...}) for heading-format tests
            b, opts = b
        p = doc.add_paragraph()
        r = p.add_run(b)
        if opts:
            r.bold, r.italic = opts.get("bold", False), opts.get("italic", False)
            if opts.get("center"):
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        elif b.upper().startswith("CHAPTER") or b.isupper():
            r.bold = True
    for text, sz in (odd_runs or []):
        p = doc.add_paragraph(); r = p.add_run(text); r.font.size = Pt(sz)
    doc.save(path)


def chapter_blocks(extra=()):
    out = []
    for n, (label, title) in enumerate([("CHAPTER ONE", "INTRODUCTION"), ("CHAPTER TWO", "LITERATURE REVIEW")], 1):
        out += [label, title, "%d.1 Background of the Study" % n, LONG * 6]
    return out + list(extra)


class StructureTests(unittest.TestCase):
    def test_toc_entries_do_not_hide_real_chapters(self):
        lines = ["TABLE OF CONTENTS", "CHAPTER ONE\tINTRODUCTION\t1", "CHAPTER TWO\tREVIEW\t9",
                 "CHAPTER ONE", "INTRODUCTION"] + ["text " * 20] * 30 + ["CHAPTER TWO", "REVIEW"] + ["text " * 20] * 30
        st = analyse_structure(lines)
        self.assertEqual([c["number"] for c in st["chapters"]], [1, 2])
        self.assertEqual(st["chapters"][0]["start"], 3)
        self.assertEqual(st["chapters"][0]["title"], "INTRODUCTION")

    def test_numbering_is_ignored_when_matching_titles(self):
        self.assertEqual(normalize_title("1.3 Aim and Objectives of the Study"), normalize_title("Aim and objectives of the study"))


class DocxChecks(unittest.TestCase):
    def run_rules(self, **kw):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "t.docx"
            make_docx(path, kw.pop("blocks", chapter_blocks()), **kw)
            parsed = parse_document(str(path))
            return parsed, run_compliance_checks(parsed, load_rules())

    def keys(self, findings):
        return {f["rule_key"] for f in findings}

    def test_compliant_font_spacing_passes(self):
        _, f = self.run_rules()
        self.assertNotIn("FMT-002", self.keys(f)); self.assertNotIn("FMT-003", self.keys(f)); self.assertNotIn("FMT-004", self.keys(f))

    def test_single_small_run_does_not_trigger_size_finding(self):
        _, f = self.run_rules(odd_runs=[("Source: survey, 2024 (footnote style text of ten point)", 10)])
        self.assertNotIn("FMT-003", self.keys(f))

    def test_style_inherited_font_is_detected(self):
        _, f = self.run_rules(font="Calibri")  # set on the style only; runs carry no explicit font
        self.assertIn("FMT-002", self.keys(f))

    def test_style_inherited_spacing_detected(self):
        _, f = self.run_rules(spacing=1.0)   # single spacing is in neither the Manual nor the department format
        self.assertIn("FMT-004", self.keys(f))

    def test_default_python_docx_page_is_letter_not_a4(self):
        _, f = self.run_rules()
        self.assertIn("FMT-005", self.keys(f))

    def test_missing_headings_reported_and_partial_draft_downgraded(self):
        _, f = self.run_rules()
        ch4 = [x for x in f if x["rule_key"] == "CS-CH4-001"][0]
        self.assertEqual(ch4["severity"], "low")
        ch1 = [x for x in f if x["rule_key"] == "MS-CH1-001"][0]
        self.assertIn("Statement of the Problem", ch1["suggestion"])
        self.assertNotIn("Background to the Study", ch1["suggestion"])   # '1.1 Background of the Study' counts

    def test_preliminary_order_and_missing(self):
        front = ["CERTIFICATION", "ABSTRACT", "ACKNOWLEDGEMENTS", "TABLE OF CONTENTS"]
        _, f = self.run_rules(blocks=front + chapter_blocks())
        pre = [x for x in f if x["rule_key"] == "PRE-001"]
        text = " ".join((x["excerpt"] or "") for x in pre)
        self.assertIn("Missing: Approval page", text)
        self.assertIn("'Abstract' appears before 'Acknowledgements'", text)

    def test_citation_checks_and_reference_matching(self):
        body = chapter_blocks(["Prior work (Okeke 2014) and (Smith & Jones, 2020) agree; see Obi (2019)."])
        refs = ["REFERENCES", "Zed, A. (2001). Zed paper.", "Obi, C. (2019). Obi paper.", "Okeke, N. (2014). Okeke paper."]
        parsed, f = self.run_rules(blocks=body + refs)
        keys = self.keys(f)
        self.assertIn("CITE-002", keys); self.assertIn("CITE-003", keys)
        ref_findings = [x["location"] for x in f if x["rule_key"] == "REF-001"]
        self.assertTrue(any("Smith" in l for l in ref_findings))
        self.assertFalse(any("Obi" in l for l in ref_findings))
        self.assertIn("REF-002", keys)   # Zed is never cited
        self.assertIn("REF-003", keys)   # Zed before Obi
        suggestion = [x["suggestion"] for x in f if x["rule_key"] == "CITE-002"][0]
        self.assertIn("(Okeke, 2014)", suggestion)


class ManualAlignedChecks(unittest.TestCase):
    """Rules checked against the real UNIZIK Manual (2019) and the CS department format."""
    def rules(self, blocks=None, **kw):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "t.docx"
            make_docx(path, blocks or chapter_blocks(), **kw)
            parsed = parse_document(str(path))
            return parsed, run_compliance_checks(parsed, load_rules())

    def keys(self, f):
        return {x["rule_key"] for x in f}

    def test_only_double_spacing_accepted(self):
        self.assertNotIn("FMT-004", self.keys(self.rules(spacing=2.0)[1]))
        self.assertIn("FMT-004", self.keys(self.rules(spacing=1.5)[1]))   # the department's 1.5 does NOT apply
        self.assertIn("FMT-004", self.keys(self.rules(spacing=1.0)[1]))

    def test_manual_margins_only(self):
        self.assertNotIn("FMT-001", self.keys(self.rules(margins_cm=(2.0, 2.0, 2.5, 2.0))[1]))       # Manual: L 2.5, others 2.0
        self.assertIn("FMT-001", self.keys(self.rules(margins_cm=(2.54, 2.54, 2.54, 2.54))[1]))      # 1 inch all round does NOT apply
        self.assertIn("FMT-001", self.keys(self.rules(margins_cm=(2.0, 2.0, 4.0, 2.0))[1]))

    def test_chapter_one_and_two_follow_the_manual_not_the_department(self):
        # Manual segments only; Limitations / Definition of Terms (department list) are not required.
        c1 = ["CHAPTER ONE", "INTRODUCTION", "Background to the Study", LONG * 2, "Statement of the Problem", LONG * 2,
              "Aim of the Study", LONG * 2, "Research Objectives", LONG * 2, "Significance of the Study", LONG * 2,
              "Scope of the Study", LONG * 2]
        c2 = ["CHAPTER TWO", "LITERATURE REVIEW", "Empirical Studies", LONG * 2, "Summary of Literature Review", LONG * 2]
        f = self.rules(blocks=c1 + c2)[1]
        self.assertNotIn("MS-CH1-001", self.keys(f))
        self.assertNotIn("MS-CH2-001", self.keys(f))
        self.assertNotIn("CS-CH1-001", self.keys(f))

    def test_combined_aim_and_objectives_heading_is_accepted(self):
        c1 = ["CHAPTER ONE", "INTRODUCTION", "Background to the Study", LONG * 2, "Statement of the Problem", LONG * 2,
              "Aim and Objectives of the Study", LONG * 2, "Significance of the Study", LONG * 2, "Scope of the Study", LONG * 2]
        f = self.rules(blocks=c1 + ["CHAPTER TWO", "LIT", "Empirical Studies", LONG])[1]
        self.assertNotIn("MS-CH1-001", self.keys(f))

    def test_chapter_one_still_flags_when_neither_form_present(self):
        c1 = ["CHAPTER ONE", "INTRODUCTION", "Background to the Study", LONG * 2, "Statement of the Problem", LONG * 2,
              "Significance of the Study", LONG * 2, "Scope of the Study", LONG * 2]
        ch1 = [x for x in self.rules(blocks=c1 + ["CHAPTER TWO", "LIT", "Empirical Studies", LONG])[1] if x["rule_key"] == "MS-CH1-001"]
        self.assertEqual(len(ch1), 1)
        self.assertIn("Aim/Purpose of the Study", ch1[0]["excerpt"])
        self.assertIn("Research Objectives", ch1[0]["excerpt"])

    def test_abbreviations_and_definition_of_terms_are_required_preliminaries(self):
        front = ["CERTIFICATION", "APPROVAL PAGE", "ACKNOWLEDGEMENTS", "ABSTRACT", "short abstract text", "TABLE OF CONTENTS"]
        pre = [x for x in self.rules(blocks=front + chapter_blocks())[1] if x["rule_key"] == "PRE-001"]
        self.assertIn("List of Abbreviations", pre[0]["excerpt"])
        self.assertIn("Operational Definition of Terms", pre[0]["excerpt"])
        self.assertNotIn("Dedication", pre[0]["excerpt"])      # marked '(if any)' in the Manual

    def test_page_number_placement(self):
        right = self.rules(page_number=("footer", WD_ALIGN_PARAGRAPH.RIGHT))[1]
        self.assertNotIn("FMT-006", self.keys(right))
        centre = [x for x in self.rules(page_number=("footer", WD_ALIGN_PARAGRAPH.CENTER))[1] if x["rule_key"] == "FMT-006"]
        self.assertIn("center-aligned", centre[0]["excerpt"])
        header = [x for x in self.rules(page_number=("header", WD_ALIGN_PARAGRAPH.RIGHT))[1] if x["rule_key"] == "FMT-006"]
        self.assertIn("header", header[0]["excerpt"])
        none = [x for x in self.rules()[1] if x["rule_key"] == "FMT-006"]
        self.assertIn("No page-number field", none[0]["excerpt"])

    def chapter4(self, drop=()):
        req = ["4.1 Objectives of the Design", "4.2 Control Centre/Main Menu", "4.3 The Submenus/Subsystems", "4.4 System Specifications",
               "4.7 System Implementation", "4.7.1 Proposed System Requirements", "4.7.2 Program Development", "4.7.3 System Testing",
               "4.7.4 System Security", "4.7.5 Training", "4.7.6 Documentation", "4.7.7 System Conversion"]
        blocks = chapter_blocks() + ["CHAPTER FOUR", "SYSTEM DESIGN AND IMPLEMENTATION"]
        for h in req:
            if h not in drop:
                blocks += [h, LONG * 2]
        return blocks

    def test_chapter_four_optional_sections_not_required(self):
        f = self.rules(blocks=self.chapter4())[1]          # no 4.4.x, 4.5, 4.6 present
        self.assertNotIn("CS-CH4-001", self.keys(f))
        f = self.rules(blocks=self.chapter4(drop=("4.7.5 Training",)))[1]
        ch4 = [x for x in f if x["rule_key"] == "CS-CH4-001"][0]
        self.assertIn("4.7.5 Training", ch4["suggestion"])

    def test_heading_number_gap_detected(self):
        blocks = chapter_blocks(["2.2 Second", LONG * 2, "2.4 Fourth", LONG * 2])
        f = [x for x in self.rules(blocks=blocks)[1] if x["rule_key"] == "STR-002"]
        self.assertEqual(len(f), 1)
        self.assertIn("skips 2.3", f[0]["excerpt"])
        self.assertEqual([x for x in self.rules(blocks=chapter_blocks(["2.2 Second", LONG * 2, "2.3 Third", LONG * 2]))[1] if x["rule_key"] == "STR-002"], [])

    def front(self, abstract_words, paragraphs=1):
        per = abstract_words // paragraphs
        abstract = ["word " * per] * paragraphs
        return ["CERTIFICATION", "APPROVAL PAGE", "ACKNOWLEDGEMENTS", "ABSTRACT"] + abstract + ["TABLE OF CONTENTS"] + chapter_blocks()

    def test_abstract_limits(self):
        def found(n, paras=1):
            return [x for x in self.rules(blocks=self.front(n, paras))[1] if x["rule_key"] == "PRE-002"]
        self.assertEqual(found(250), [])
        mid = found(400)
        self.assertEqual((len(mid), mid[0]["severity"]), (1, "low"))
        self.assertEqual(found(600)[0]["severity"], "medium")
        self.assertIn("separate paragraphs", found(250, paras=2)[0]["excerpt"])

    def test_citations_cited_together_must_be_alphabetical(self):
        bad = chapter_blocks(["Evidence exists (Okoye, 2005; Nkamigbo, 2018)."])
        good = chapter_blocks(["Evidence exists (Nkamigbo, 2018; Okoye, 2005)."])
        self.assertIn("CITE-004", self.keys(self.rules(blocks=bad)[1]))
        self.assertNotIn("CITE-004", self.keys(self.rules(blocks=good)[1]))


class CitationUnit(unittest.TestCase):
    def test_multi_citation_split_and_wrapped_lines(self):
        items = extract_citations("as shown (Ding et al., 2025; Louise\net al., 2025) here")
        self.assertEqual([(i["author"], i["year"]) for i in items], [("Ding", "2025"), ("Louise", "2025")])

    def test_figure_references_not_citations(self):
        self.assertEqual(extract_citations("see (Figure 2023) and Table (2019)"), [])


class Retrieval(unittest.TestCase):
    def test_bm25_ranks_relevant_passage_first(self):
        corpus = [{"section": "Citation", "text": "cite author and year with a comma"},
                  {"section": "Margins", "text": "margins of one inch and A4 paper"}]
        self.assertEqual(bm25_search("how to cite author year", corpus, 1)[0]["section"], "Citation")
        self.assertEqual(bm25_search("zzz unrelated", corpus, 1), [])


class LLMEvaluator(unittest.TestCase):
    def parsed(self, long_chapter=False):
        body = "\n".join([LONG] * (400 if long_chapter else 3))
        lines = ["CHAPTER ONE", "INTRODUCTION", "1.3 Aim and Objectives of the Study", "The objective is to build a thing.",
                 body, "CHAPTER TWO", "LITERATURE REVIEW", "Literature is reviewed here in a short paragraph.",
                 "CHAPTER THREE", "METHODOLOGY", "We use qualitative interviews only."]
        from app.nlp.document_parser import _assemble
        return _assemble("docx", lines, [], {}, False)

    cfg = {"GEMINI_API_KEY": "x", "GEMINI_MODEL": "m"}

    def test_complete_document_is_sent_in_one_model_call(self):
        parsed = self.parsed(long_chapter=True)
        parsed["text"] += "\nFINAL-DOCUMENT-TAIL-MARKER"
        seen = []
        def gen(prompt, s):
            seen.append(prompt); return '{"findings":[]}'
        r = llm_client.evaluate_thesis(parsed, {**self.cfg, "LLM_MAX_CALLS": 1, "LLM_CHUNK_CHARS": 100}, gen)
        self.assertEqual(len(seen), 1)
        self.assertIn(parsed["text"], seen[0])
        self.assertEqual(r["coverage"]["calls"], 1)
        self.assertEqual(len(r["coverage"]["chapters_evaluated"]), 3)
        self.assertEqual(r["coverage"]["chapters_skipped"], [])

    def test_unverifiable_evidence_is_dropped_and_verified_kept(self):
        def gen(prompt, s):
            return json.dumps({"findings": [
                {"section": "CHAPTER THREE", "finding": "Real", "evidence": "We use qualitative interviews only.", "severity": "high"},
                {"finding": "Hallucinated", "evidence": "We surveyed 4000 farmers", "severity": "high"},
                {"finding": "Absence concern", "evidence": "", "severity": "low"}]})
        r = llm_client.evaluate_thesis(self.parsed(), self.cfg, gen)
        names = {f["finding"] for f in r["findings"]}
        self.assertEqual(names, {"Real", "Absence concern"})
        self.assertEqual(next(f for f in r["findings"] if f["finding"] == "Real")["section"], "CHAPTER THREE")
        self.assertEqual(r["coverage"]["dropped_unverified"], 1)

    def test_model_request_is_not_retried(self):
        state = {"n": 0}
        def gen(prompt, s):
            state["n"] += 1
            raise RuntimeError("503 UNAVAILABLE")
        r = llm_client.evaluate_thesis(self.parsed(), self.cfg, gen)
        self.assertEqual(state["n"], 1)
        self.assertEqual(r["coverage"]["calls"], 1)
        self.assertEqual(r["coverage"]["failed_calls"], 1)
        self.assertIn("failed", r["warning"])

    def test_failed_whole_document_call_reports_all_chapters_skipped(self):
        state = {"n": 0}
        def gen(prompt, s):
            state["n"] += 1
            raise RuntimeError("503 UNAVAILABLE: temporary high demand")

        r = llm_client.evaluate_thesis(self.parsed(), self.cfg, gen)

        self.assertEqual(state["n"], 1)
        self.assertEqual(r["coverage"]["calls"], 1)
        self.assertEqual(r["coverage"]["failed_calls"], 1)
        self.assertEqual(r["coverage"]["chapters_skipped"], [
            "CHAPTER ONE: INTRODUCTION",
            "CHAPTER TWO: LITERATURE REVIEW",
            "CHAPTER THREE: METHODOLOGY",
        ])
        self.assertIn("whole-document AI evaluation call failed", r["warning"])

    def test_provider_failure_reason_is_reported_without_api_key(self):
        secret = "test-api-key-secret"
        def gen(prompt, s):
            raise RuntimeError("503 UNAVAILABLE; key=%s; try again later" % secret)

        r = llm_client.evaluate_thesis(self.parsed(), {**self.cfg, "GEMINI_API_KEY": secret}, gen)

        self.assertIn("503 UNAVAILABLE", r["warning"])
        self.assertIn("try again later", r["warning"])
        self.assertNotIn(secret, r["warning"])

    def test_model_gone_stops_cleanly(self):
        from app.nlp.gemini import LLMUnavailable
        def gen(prompt, s): raise LLMUnavailable("Model 'm' is not available. Update GEMINI_MODEL in .env.")
        r = llm_client.evaluate_thesis(self.parsed(), self.cfg, gen)
        self.assertIn("GEMINI_MODEL", r["warning"]); self.assertEqual(r["findings"], [])

    def test_not_configured(self):
        r = llm_client.evaluate_thesis(self.parsed(), {"GEMINI_API_KEY": ""})
        self.assertIn("not configured", r["warning"])

if __name__ == "__main__":
    unittest.main()
