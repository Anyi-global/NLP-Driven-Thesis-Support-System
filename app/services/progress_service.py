"""Revision-to-revision progress (thesis Objective 5: revision cycles and compliance improvement)."""
from ..models.thesis import Thesis
from ..models.feedback import ComplianceReport, FeedbackItem
from ..utils.metrics import diff_findings, count_items  # noqa: F401  (re-exported)


def _rows(thesis_id):
    return [(i.source_report_type, i.level, i.severity, i.status)
            for i in FeedbackItem.query.filter_by(thesis_id=thesis_id).all()]


def _compliance_keys(thesis_id):
    return ["%s | %s" % (c.rule_key, c.location) for c in ComplianceReport.query.filter_by(thesis_id=thesis_id).all()]


def progress_for_student(student_id, for_student):
    theses = Thesis.query.filter_by(student_id=student_id).order_by(Thesis.version).all()
    chains = {}
    for t in theses:
        chains.setdefault(t.root_thesis_id or t.id, []).append(t)
    out = []
    for root, versions in sorted(chains.items()):
        prev_keys, entries = None, []
        for t in sorted(versions, key=lambda x: x.version):
            keys = _compliance_keys(t.id) if t.status == "analyzed" else []
            entry = {"thesis_id": t.id, "version": t.version, "status": t.status,
                     "submitted_at": t.created_at.isoformat() if t.created_at else None,
                     "issues": count_items(_rows(t.id), for_student), "compliance_findings": len(keys)}
            if prev_keys is not None and t.status == "analyzed":
                d = diff_findings(prev_keys, keys)
                entry["since_previous"] = {"resolved": len(d["resolved"]), "new": len(d["new"]), "persisting": d["persisting"]}
            if t.status == "analyzed":
                prev_keys = keys
            entries.append(entry)
        analysed = [e for e in entries if e["status"] == "analyzed"]
        change = (analysed[-1]["compliance_findings"] - analysed[0]["compliance_findings"]) if len(analysed) > 1 else None
        out.append({"root_thesis_id": root, "title": versions[0].title, "revision_cycles": len(versions) - 1,
                    "compliance_change_first_to_latest": change, "versions": entries})
    return {"student_id": student_id, "chains": out}
