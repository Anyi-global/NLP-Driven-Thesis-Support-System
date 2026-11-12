"""Who may see what. The decision functions are pure so they can be unit-tested without a database."""

# Supervisor decisions that release an AI-generated (high-level) finding to the student.
RELEASED_STATUSES = frozenset({"accepted", "edited", "commented"})


def can_access_thesis(role, user_id, thesis_student_id, student_supervisor_id):
    if role == "admin":
        return True
    if role == "student":
        return thesis_student_id == user_id
    if role == "supervisor":
        return student_supervisor_id is not None and student_supervisor_id == user_id
    return False


def student_can_see(source_report_type, status):
    """Compliance findings are rule-based and shown immediately. AI findings (quality) are
    shown only after a supervisor accepts/edits/comments (thesis Section 3.2.2.3: the
    supervisor must review LLM feedback before it reaches the student). Rejected = hidden."""
    if status == "rejected":
        return False
    return source_report_type == "compliance" or status in RELEASED_STATUSES


def next_version(existing_versions):
    return max(existing_versions, default=0) + 1


# ------------------------------------------------------------------ DB-aware helpers
def visible_thesis_ids(user):
    """A SELECT of the thesis ids this user may see (portable for use inside .in_())."""
    from sqlalchemy import select
    from ..models.thesis import Thesis
    from ..models.user import User
    stmt = select(Thesis.id)
    if user.role == "student":
        stmt = stmt.where(Thesis.student_id == user.id)
    elif user.role == "supervisor":
        stmt = stmt.where(Thesis.student_id.in_(select(User.id).where(User.supervisor_id == user.id)))
    return stmt


def visible_theses_query(user):
    from ..models.thesis import Thesis
    return Thesis.query.filter(Thesis.id.in_(visible_thesis_ids(user)))


def get_accessible_thesis(user, thesis_id):
    from ..extensions import db
    from ..models.thesis import Thesis
    t = db.session.get(Thesis, thesis_id)
    if t and can_access_thesis(user.role, user.id, t.student_id, t.student.supervisor_id):
        return t
    return None


def apply_feedback_visibility(query, user):
    """Restrict a FeedbackItem query to what this user may see."""
    from sqlalchemy import or_
    from ..models.feedback import FeedbackItem
    if user.role == "student":
        query = query.filter(FeedbackItem.status != "rejected").filter(
            or_(FeedbackItem.source_report_type == "compliance", FeedbackItem.status.in_(RELEASED_STATUSES)))
    return query
