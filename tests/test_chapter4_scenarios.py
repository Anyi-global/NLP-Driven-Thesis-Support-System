"""The twelve test cases listed in thesis Chapter 4 (Table: T-01 ... T-12), as automated tests.

Cases that need no database run here (T-01..T-08 logic). T-01, T-02, T-09..T-12 are also exercised end-to-end through
the API in tests/test_api.py. T-08 depends on the language model: the plumbing is tested with a stub, and
`LiveGemini` runs the real model only when GEMINI_API_KEY and GEMINI_MODEL are set in the environment."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from app.nlp import llm_client
from app.nlp.document_parser import parse_document, _assemble
from app.nlp.rule_engine import run_compliance_checks, load_rules
from app.utils.validators import inspect_upload
from tests.test_nlp import make_docx, chapter_blocks, LONG
from tests.test_language_rules import chapter, BOLD


def findings_for(blocks, **kw):
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "t.docx"
        make_docx(path, blocks, **kw)
        return run_compliance_checks(parse_document(str(path)), load_rules())


def keys(f):
    return {x["rule_key"] for x in f}


class T01_T02_Uploads(unittest.TestCase):
    def test_T01_valid_docx_accepted_and_parsed(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "ok.docx"
            make_docx(path, chapter_blocks())
            self.assertTrue(inspect_upload(path.read_bytes(), "docx")[0])
            self.assertTrue(parse_document(str(path))["chapters"])

    def test_T02_corrupt_pdf_gives_an_error(self):
        self.assertFalse(inspect_upload(b"this is not a pdf", "pdf")[0])                 # rejected at upload
        with tempfile.TemporaryDirectory() as d:
            broken = Path(d) / "broken.pdf"
            broken.write_bytes(b"%PDF-1.4\n1 0 obj << /Broken true >>\nnot a real pdf body")   # passes the header check ...
            self.assertTrue(inspect_upload(broken.read_bytes(), "pdf")[0])
            with self.assertRaises(Exception):                                          # ... but cannot be parsed
                parse_document(str(broken))


class T03_T06_Citations(unittest.TestCase):
    refs = ["REFERENCES", "Okeke, N. (2014). A study of things. Awka: Press."]

    def test_T03_missing_comma_flagged(self):
        f = findings_for(chapter_blocks(["As noted (Okeke 2014) the result holds."]) + self.refs)
        self.assertIn("CITE-002", keys(f))

    def test_T05_missing_reference_flagged(self):
        f = findings_for(chapter_blocks(["As noted (Obi, 2019) the result holds."]) + self.refs)
        self.assertTrue(any(x["rule_key"] == "REF-001" and "Obi" in x["location"] for x in f))

    def test_T06_correct_citation_not_flagged(self):
        f = findings_for(chapter_blocks(["As noted (Okeke, 2014) the result holds."]) + self.refs)
        self.assertFalse({"CITE-002", "CITE-003", "REF-001", "REF-002"} & keys(f))


class T04_T07_Structure(unittest.TestCase):
    def test_T04_wrong_heading_level_flagged(self):
        blocks = chapter(1, "ONE", "INTRODUCTION", [("1.1 Background to the Study", BOLD), LONG, ("1.1.1 Effect Of Moulding Water", BOLD), LONG])
        self.assertIn("FMT-007", keys(findings_for(blocks)))

    def test_T07_missing_methodology_flagged_high(self):
        blocks = []
        for n, (w, t) in {1: ("ONE", "INTRODUCTION"), 2: ("TWO", "LITERATURE REVIEW"), 4: ("FOUR", "DESIGN"), 5: ("FIVE", "SUMMARY")}.items():
            blocks += chapter(n, w, t)
        ch3 = [x for x in findings_for(blocks) if x["rule_key"] == "CS-CH3-001"]
        self.assertEqual([x["severity"] for x in ch3], ["high"])


MISALIGNED = ["CHAPTER ONE", "INTRODUCTION", "1.3 Aim and Objectives of the Study",
              "The objective of this study is to measure whether a new interface improves user satisfaction among 200 students.",
              LONG, "CHAPTER THREE", "METHODOLOGY",
              "The study applied static code analysis to three open-source repositories. No users or surveys were involved.", LONG,
              "CHAPTER FIVE", "SUMMARY", "5.2 Conclusion", "The study concludes that the new interface significantly improved user satisfaction.", LONG]


class T08_Alignment(unittest.TestCase):
    def test_T08_plumbing_alignment_finding_is_stored_as_high(self):
        """One whole-thesis prompt evaluates cross-chapter alignment and keeps verified findings."""
        parsed = _assemble("docx", MISALIGNED, [], {}, False)
        sent = []
        quote = "No users or surveys were involved."

        def stub(prompt, settings):
            sent.append(prompt)
            return json.dumps({"findings": [{"section": "Cross-chapter alignment",
                                             "finding": "Objective about user satisfaction has no matching method.",
                                             "evidence": quote, "rationale": "r", "suggestion": "s", "severity": "high"}]})
        r = llm_client.evaluate_thesis(parsed, {"GEMINI_API_KEY": "x", "GEMINI_MODEL": "m"}, stub)
        self.assertEqual(len(sent), 1)
        self.assertIn("CHAPTER ONE", sent[0])
        self.assertIn("CHAPTER THREE", sent[0])
        self.assertIn("CHAPTER FIVE", sent[0])
        found = [f for f in r["findings"] if f["section"] == "Cross-chapter alignment"]
        self.assertEqual([f["severity"] for f in found], ["high"])


@unittest.skipUnless(os.getenv("GEMINI_API_KEY") and os.getenv("GEMINI_MODEL"), "set GEMINI_API_KEY and GEMINI_MODEL to run against the real model")
class LiveGemini(unittest.TestCase):
    def test_T08_live_model_flags_misaligned_objectives(self):
        """Real call. A model's output is not deterministic: if this fails, read the findings before concluding anything."""
        parsed = _assemble("docx", MISALIGNED, [], {}, False)
        r = llm_client.evaluate_thesis(parsed, {"GEMINI_API_KEY": os.environ["GEMINI_API_KEY"], "GEMINI_MODEL": os.environ["GEMINI_MODEL"]})
        print("\nLIVE RESULT:", json.dumps(r, indent=2)[:3000])
        self.assertEqual(r["coverage"]["failed_calls"], 0, r["warning"])
        align = [f for f in r["findings"] if f["section"] == "Cross-chapter alignment"]
        self.assertTrue(align, "model reported no alignment problem for an obviously misaligned thesis")
        self.assertTrue(any(f["severity"] in ("medium", "high") for f in align))


if __name__ == "__main__":
    unittest.main()
