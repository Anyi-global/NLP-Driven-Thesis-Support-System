"""Usage statistics for the admin 'Reports' menu (thesis Section 4.3)."""
from collections import Counter
from datetime import datetime, timedelta, timezone

from ..models.user import User
from ..models.thesis import Thesis
from ..models.feedback import FeedbackItem
from ..utils.metrics import metrics_to_csv  # noqa: F401  (re-exported)


def usage_metrics(days=30):
    users = User.query.all()
    theses = Thesis.query.all()
    items = FeedbackItem.query.all()
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    students = [u for u in users if u.role == "student"]
    quality = [i for i in items if i.source_report_type == "quality"]
    analysed = [t for t in theses if t.status == "analyzed"]
    compliance_per_thesis = Counter(i.thesis_id for i in items if i.source_report_type == "compliance")
    return {
        "users": {"by_role": dict(Counter(u.role for u in users)),
                  "students_without_supervisor": sum(1 for u in students if u.supervisor_id is None)},
        "theses": {"total": len(theses), "by_status": dict(Counter(t.status for t in theses)),
                   "resubmissions": sum(1 for t in theses if t.version > 1),
                   "awaiting_supervisor_review": sum(1 for t in theses if t.review_requested_at is not None),
                   "uploads_per_day_last_%d_days" % days: dict(sorted(Counter(
                       t.created_at.date().isoformat() for t in theses if t.created_at and t.created_at >= cutoff).items()))},
        "feedback": {"by_level": dict(Counter(i.level for i in items)), "by_status": dict(Counter(i.status for i in items)),
                     "ai_items_reviewed_by_supervisor": sum(1 for i in quality if i.status != "pending"),
                     "ai_items_total": len(quality),
                     "avg_compliance_issues_per_analyzed_thesis":
                         round(sum(compliance_per_thesis.get(t.id, 0) for t in analysed) / len(analysed), 2) if analysed else None},
    }
