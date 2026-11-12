"""Feedback exports, generated in memory (no shared files on disk, so one audience's export can
never overwrite another's). Callers pass the items the requesting user is allowed to see."""
import io
from xml.sax.saxutils import escape

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.shared import Pt
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

from .tracked_changes import apply_tracked_changes, add_supervisor_comments


def _p(text):
    # ReportLab Paragraph parses a mini-HTML markup; user/LLM text must be escaped.
    return escape(str(text or ""))


def generate_feedback_pdf(thesis, items):
    buf = io.BytesIO()
    styles = getSampleStyleSheet()
    story = [Paragraph("NLP-Driven Thesis Support System", styles["Title"]),
             Paragraph(_p(thesis.title), styles["Heading2"]),
             Paragraph("Version %s" % thesis.version, styles["Normal"]), Spacer(1, 5 * mm),
             Paragraph("Feedback items: %d" % len(items), styles["Normal"]), Spacer(1, 4 * mm)]
    for i, item in enumerate(items, 1):
        story.append(Paragraph("%d. [%s / %s] %s%s" % (i, _p(item.level).upper(), _p(item.severity).upper(), _p(item.content), " (fix accepted by student)" if item.student_status else ""), styles["Heading4"]))
        if item.suggestion:
            story.append(Paragraph("Suggestion: " + _p(item.suggestion), styles["BodyText"]))
        if item.supervisor_comment:
            story.append(Paragraph("Supervisor comment: " + _p(item.supervisor_comment), styles["BodyText"]))
        story.append(Spacer(1, 3 * mm))
    SimpleDocTemplate(buf, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm).build(story)
    buf.seek(0)
    return buf


def _ensure_heading_styles(doc):
    for level, size in ((1, 16), (2, 13)):
        name = "Heading %d" % level
        if name not in doc.styles:
            style = doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
            style.font.bold = True
            style.font.size = Pt(size)


def generate_annotated_docx(thesis, items, decisions=(), scope="all", allowed_rules=None):
    """Copy of the student's DOCX with real tracked changes for safe automatic fixes, supervisor comments as Word
    comments, and a feedback summary at the end. `decisions` = ALL compliance items of the thesis (including ones the
    supervisor rejected, which students cannot see), used to decide which fixes to apply. scope='accepted' applies
    only fixes the student accepted. Returns (BytesIO, {'tracked_changes': n, 'comments': m})."""
    if thesis.file_type != "docx":
        raise ValueError("Annotated DOCX export requires an original DOCX submission.")
    doc = Document(thesis.file_path)

    def keep(rule, key_text):
        hits = [d for d in decisions if d.content.startswith(rule + ":") and key_text.lower() in d.content.lower()]
        if hits and all(d.status == "rejected" for d in hits):
            return False                      # the supervisor rejected this finding
        return any(d.student_status == "accepted" for d in hits) if scope == "accepted" else True
    stats = {"tracked_changes": apply_tracked_changes(doc, allowed_rules or {"CITE-002", "CITE-003", "STY-001", "STY-002"}, keep),
             "comments": add_supervisor_comments(doc, items)}
    _ensure_heading_styles(doc)
    doc.add_page_break()
    doc.add_heading("System and Supervisor Feedback", level=1)
    doc.add_paragraph("Tracked changes in this document: %d. Review each in Word (Review > Accept / Reject). Other issues are "
                      "listed below and need manual correction." % stats["tracked_changes"])
    for item in items:
        doc.add_heading("%s \u2014 %s%s" % (item.level.upper(), item.severity.upper(), " (fix accepted by student)" if item.student_status else ""), level=2)
        doc.add_paragraph(item.content)
        if item.suggestion:
            doc.add_paragraph("Suggested action: " + item.suggestion)
        if item.supervisor_comment:
            doc.add_paragraph("Supervisor comment: " + item.supervisor_comment)
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf, stats
