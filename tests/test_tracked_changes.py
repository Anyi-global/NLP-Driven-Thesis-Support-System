"""Tracked-changes export: verified by simulating Word's 'Accept all' and 'Reject all' on the generated XML."""
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from docx import Document
from docx.oxml.ns import qn

from app.services.report_service import generate_annotated_docx
from app.services.tracked_changes import plan_fixes, apply_tracked_changes, add_supervisor_comments

ALL = {"CITE-002", "CITE-003", "STY-001", "STY-002"}
ORIGINAL = ("As noted (Okeke 2014) and (Smith & Jones, 2020), e.g. rules apply on 15th December, 2005 "
            "and in the 1990's; it ranked 5th, etc. Done.")
CORRECTED = ("As noted (Okeke, 2014) and (Smith and Jones, 2020), for example rules apply on 15 December, 2005 "
             "and in the 1990s; it ranked fifth, and so on. Done.")


def view(p_el, accept):
    """Text of a paragraph as Word shows it after Accept all (accept=True) or Reject all (accept=False)."""
    out = ""
    for el in p_el.iter(qn("w:t"), qn("w:delText")):
        inserted = el.getparent().getparent().tag == qn("w:ins")
        if (accept and el.tag == qn("w:delText")) or (not accept and inserted):
            continue
        out += el.text or ""
    return out


def make_doc(paragraph_runs, refs_text="Ref e.g. (Okeke 2014) must not be touched"):
    doc = Document()
    doc.add_paragraph("CHAPTER ONE")
    doc.add_paragraph("INTRODUCTION")
    p = doc.add_paragraph()
    for text, bold in paragraph_runs:
        p.add_run(text).bold = bold
    doc.add_paragraph("REFERENCES")
    doc.add_paragraph(refs_text)
    return doc


class Planning(unittest.TestCase):
    def apply_to_string(self, text):
        for s, e, new, *_ in reversed(plan_fixes(text)):
            text = text[:s] + new + text[e:]
        return text

    def test_every_fix_type(self):
        self.assertEqual(self.apply_to_string(ORIGINAL), CORRECTED)

    def test_sentence_final_etc_keeps_its_period_and_mid_sentence_does_not(self):
        self.assertEqual(self.apply_to_string("Tools, parsers, etc. are used."), "Tools, parsers, and so on are used.")
        self.assertEqual(self.apply_to_string("Tools, parsers, etc. Next sentence."), "Tools, parsers, and so on. Next sentence.")
        self.assertEqual(self.apply_to_string("(parsers, etc.)"), "(parsers, and so on)")

    def test_things_that_must_not_change(self):
        for text in ("The 50th percentile and 90% were reported.", "Smith et al. (2020) agree.", "Correct (Okeke, 2014) form.",
                     "Use ibid. sparingly.", "Position 10th in the table."):
            self.assertEqual(self.apply_to_string(text), text, text)

    def test_two_authors_without_comma_gets_both_fixes(self):
        self.assertEqual(self.apply_to_string("(Smith & Jones 2020)"), "(Smith and Jones, 2020)")


class Applying(unittest.TestCase):
    def test_accept_all_gives_corrected_text_and_reject_all_restores_the_original(self):
        doc = make_doc([(ORIGINAL, False)])
        n = apply_tracked_changes(doc, ALL)
        p = [x for x in doc.paragraphs if x.text.startswith("As noted") or "As noted" in view(x._p, False)][0]._p
        self.assertEqual(n, 7)   # comma, '&', e.g., 15th, 1990's, 5th, etc.
        self.assertEqual(view(p, accept=True), CORRECTED)
        self.assertEqual(view(p, accept=False), ORIGINAL)
        self.assertTrue(list(p.iter(qn("w:ins"))) and list(p.iter(qn("w:del"))))

    def test_reference_list_is_never_edited(self):
        doc = make_doc([("Body text only.", False)])
        apply_tracked_changes(doc, ALL)
        refs = doc.paragraphs[-1]
        self.assertEqual(refs.text, "Ref e.g. (Okeke 2014) must not be touched")
        self.assertFalse(list(refs._p.iter(qn("w:del"))))

    def test_change_spanning_two_runs_keeps_formatting_and_text(self):
        doc = make_doc([("Prior work (Smith &", False), (" Jones, 2020) is bold here", True)])
        self.assertEqual(apply_tracked_changes(doc, ALL), 1)
        p = doc.paragraphs[2]._p
        self.assertEqual(view(p, True), "Prior work (Smith and Jones, 2020) is bold here")
        self.assertEqual(view(p, False), "Prior work (Smith & Jones, 2020) is bold here")
        bold_text = "".join(t.text for r in p.findall(qn("w:r")) if r.find(qn("w:rPr")) is not None and r.find(qn("w:rPr")).find(qn("w:b")) is not None
                            for t in r.findall(qn("w:t")))
        self.assertTrue(bold_text.endswith("is bold here"))

    def test_rule_filter_and_keep_callback(self):
        doc = make_doc([(ORIGINAL, False)])
        apply_tracked_changes(doc, {"CITE-002"})
        p = doc.paragraphs[2]._p
        self.assertEqual(view(p, True), ORIGINAL.replace("(Okeke 2014)", "(Okeke, 2014)"))
        doc = make_doc([(ORIGINAL, False)])
        apply_tracked_changes(doc, ALL, keep=lambda rule, key: rule != "STY-001")
        self.assertIn("e.g.", view(doc.paragraphs[2]._p, True))
        self.assertIn("15 December", view(doc.paragraphs[2]._p, True))

    def test_runs_with_tabs_or_fields_are_left_alone(self):
        doc = make_doc([("Intro ", False)])
        p = doc.paragraphs[2]
        p.add_run().add_tab()
        p.add_run("(Okeke 2014)")
        before = p.text
        apply_tracked_changes(doc, ALL)
        self.assertEqual(view(p._p, False).replace("\x00", ""), before.replace("\t", ""))

    def test_saved_file_reopens(self):
        doc = make_doc([(ORIGINAL, False)])
        apply_tracked_changes(doc, ALL)
        buf = io.BytesIO()
        doc.save(buf)
        reopened = Document(io.BytesIO(buf.getvalue()))
        self.assertTrue(list(reopened.element.body.iter(qn("w:ins"))))


class Comments(unittest.TestCase):
    def test_supervisor_comment_becomes_a_word_comment_on_the_chapter_heading(self):
        doc = make_doc([("Body text.", False)])
        items = [SimpleNamespace(content="[CHAPTER ONE: INTRODUCTION] Objectives unclear.", supervisor_comment="Please sharpen.", status="commented", suggestion=None),
                 SimpleNamespace(content="[CHAPTER ONE] Rejected one", supervisor_comment="x", status="rejected", suggestion=None),
                 SimpleNamespace(content="Nothing to say", supervisor_comment=None, status="pending", suggestion=None)]
        self.assertEqual(add_supervisor_comments(doc, items), 1)
        buf = io.BytesIO()
        doc.save(buf)
        self.assertEqual(len(list(Document(io.BytesIO(buf.getvalue())).comments)), 1)


class FullExport(unittest.TestCase):
    def item(self, content, status="pending", student=None, source="compliance"):
        return SimpleNamespace(content=content, status=status, student_status=student, source_report_type=source, level="low",
                               severity="low", suggestion="s", supervisor_comment=None)

    def export(self, decisions, scope="all"):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "t.docx"
            make_doc([("Noted (Okeke 2014) and e.g. this.", False)]).save(path)
            thesis = SimpleNamespace(file_type="docx", file_path=str(path))
            buf, stats = generate_annotated_docx(thesis, [i for i in decisions if i.status != "rejected"], decisions, scope, ALL)
            return Document(io.BytesIO(buf.getvalue())), stats

    def test_supervisor_rejected_finding_is_not_applied(self):
        decisions = [self.item("CITE-002: Citation: (Okeke 2014) \u2014 (Okeke 2014)", status="rejected"),
                     self.item("STY-001: Latin abbreviation: e.g. (1 times)")]
        doc, stats = self.export(decisions)
        self.assertEqual(stats["tracked_changes"], 1)
        self.assertIn("(Okeke 2014)", view(doc.paragraphs[2]._p, True))
        self.assertIn("for example", view(doc.paragraphs[2]._p, True))

    def test_accepted_scope_applies_only_what_the_student_accepted(self):
        decisions = [self.item("CITE-002: Citation: (Okeke 2014) \u2014 (Okeke 2014)", student="accepted"),
                     self.item("STY-001: Latin abbreviation: e.g. (1 times)")]
        doc, stats = self.export(decisions, scope="accepted")
        self.assertEqual(stats["tracked_changes"], 1)
        self.assertIn("(Okeke, 2014)", view(doc.paragraphs[2]._p, True))
        self.assertIn("e.g.", view(doc.paragraphs[2]._p, True))

    def test_summary_mentions_count_and_marks_accepted_items(self):
        doc, _ = self.export([self.item("CITE-002: Citation: (Okeke 2014) \u2014 (Okeke 2014)", student="accepted")])
        text = "\n".join(p.text for p in doc.paragraphs)
        self.assertIn("Tracked changes in this document: 2", text)   # comma fix + the e.g. fix (no recorded decision against it)
        self.assertIn("fix accepted by student", text)

    def test_export_works_when_source_has_no_heading_styles(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "t.docx"
            source = make_doc([("Body text.", False)])
            for style in list(source.styles.element):
                if style.get(qn("w:styleId")) in {"Heading1", "Heading2"}:
                    source.styles.element.remove(style)
            source.save(path)
            thesis = SimpleNamespace(file_type="docx", file_path=str(path))

            buf, _ = generate_annotated_docx(thesis, [self.item("A finding")])
            exported = Document(io.BytesIO(buf.getvalue()))

        self.assertTrue(any(p.text == "System and Supervisor Feedback" and p.style.name == "Heading 1"
                            for p in exported.paragraphs))
        self.assertTrue(any(p.text.startswith("LOW") and p.style.name == "Heading 2"
                            for p in exported.paragraphs))


if __name__ == "__main__":
    unittest.main()
