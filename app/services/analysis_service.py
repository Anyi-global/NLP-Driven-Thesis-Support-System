from ..extensions import db
from ..models.feedback import ComplianceReport, QualityReport, FeedbackItem
from ..nlp.document_parser import parse_document
from ..nlp.rule_engine import run_compliance_checks
from ..nlp.llm_client import evaluate_thesis
from ..nlp import linguistics
from flask import current_app

SEVERITY_SCORE = {"low": 1, "medium": 2, "high": 3}


def analyze_thesis(thesis):
    """Parse -> compliance checks -> AI evaluation -> triage. Old results are replaced only after
    the new ones are ready, so a failure leaves the previous analysis intact."""
    thesis.set_status("processing")
    db.session.commit()
    linguistics.configure(current_app.config)
    parsed = parse_document(thesis.file_path)
    thesis.extracted_text = parsed.get("text", "")[:2000000]
    low = run_compliance_checks(parsed)
    ai = evaluate_thesis(parsed)

    ComplianceReport.query.filter_by(thesis_id=thesis.id).delete()
    QualityReport.query.filter_by(thesis_id=thesis.id).delete()
    FeedbackItem.query.filter_by(thesis_id=thesis.id).delete()
    for f in low:
        db.session.add(ComplianceReport(thesis_id=thesis.id, **f))
    for f in ai.get("findings", []):
        db.session.add(QualityReport(thesis_id=thesis.id, section=f.get("section"), finding=f["finding"],
                                     rationale=f.get("rationale"), suggestion=f.get("suggestion"),
                                     severity=f.get("severity", "medium"), source=f.get("source", "gemini")))
    db.session.flush()
    for r in ComplianceReport.query.filter_by(thesis_id=thesis.id).all():
        content = "%s: %s" % (r.rule_key, r.location or "Document")
        if r.excerpt:
            content += " \u2014 " + r.excerpt[:300]
        db.session.add(FeedbackItem(thesis_id=thesis.id, source_report_type="compliance", source_report_id=r.id,
                                    level="low", content=content, suggestion=r.suggestion, severity=r.severity,
                                    priority_score=SEVERITY_SCORE.get(r.severity, 2)))
    for r in QualityReport.query.filter_by(thesis_id=thesis.id).all():
        content = ("[%s] %s" % (r.section, r.finding)) if r.section else r.finding
        db.session.add(FeedbackItem(thesis_id=thesis.id, source_report_type="quality", source_report_id=r.id,
                                    level="high", content=content, suggestion=r.suggestion, severity=r.severity,
                                    priority_score=SEVERITY_SCORE.get(r.severity, 2)))
    engines = linguistics.engine_status()
    notes = [ai.get("warning")] if ai.get("warning") else []
    if engines["languagetool_requested"] and not engines["languagetool"]:
        notes.append("LanguageTool was enabled but is not available (needs Java and language_tool_python or LANGUAGETOOL_URL); the British-English grammar check was skipped.")
    thesis.set_status("analyzed", " ".join(notes) or None)
    db.session.commit()
    return {"parsed_summary": {"chapters_detected": len(parsed.get("chapters", [])),
                               "heading_count": len(parsed.get("headings", [])),
                               "citation_count": len(parsed.get("citation_items", [])),
                               "reference_count": len(parsed.get("references", [])),
                               "partial_draft": parsed.get("partial_draft")},
            "low_level_issues": len(low), "high_level_issues": len(ai.get("findings", [])),
            "ai_coverage": ai.get("coverage"), "warning": ai.get("warning"), "language_engines": engines}
