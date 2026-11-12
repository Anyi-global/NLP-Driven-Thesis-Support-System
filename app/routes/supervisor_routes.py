from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from flask_login import current_user
from ..extensions import db
from ..models.thesis import Thesis
from ..models.user import User
from ..models.feedback import FeedbackItem
from ..utils.security import role_required, log_action
from ..utils.access import visible_theses_query, visible_thesis_ids, get_accessible_thesis
from ..utils.payloads import parse_bulk_review
from ..services.progress_service import progress_for_student

supervisor_bp = Blueprint("supervisor", __name__)


@supervisor_bp.get("/dashboard")
@role_required("supervisor", "admin")
def dashboard():
    thesis_id = request.args.get("thesis_id", type=int)
    theses_q = visible_theses_query(current_user)
    q = FeedbackItem.query.filter(FeedbackItem.thesis_id.in_(visible_thesis_ids(current_user)))
    if thesis_id:
        q = q.filter(FeedbackItem.thesis_id == thesis_id)
    items = q.order_by(FeedbackItem.priority_score.desc(), FeedbackItem.id.asc()).all()
    theses = theses_q.order_by(Thesis.created_at.desc()).all()
    students = User.query.filter_by(supervisor_id=current_user.id).order_by(User.name).all() if current_user.role == "supervisor" else []
    return jsonify({"students": [s.to_dict() for s in students], "theses": [t.to_dict() for t in theses],
                    "feedback": [i.to_dict() for i in items]})


@supervisor_bp.post("/feedback/<int:item_id>")
@role_required("supervisor", "admin")
def review_feedback(item_id):
    item = db.session.get(FeedbackItem, item_id)
    # Same 404 whether the item is missing or belongs to a student you do not supervise.
    if not item or not get_accessible_thesis(current_user, item.thesis_id):
        return jsonify({"error": "Not found", "message": "Feedback item not found."}), 404
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    if action not in {"accept", "edit", "reject", "comment", "reclassify"}:
        return jsonify({"error": "Validation error", "message": "action must be accept, edit, reject, comment, or reclassify."}), 400
    if action == "edit":
        if not isinstance(data.get("suggestion"), str) or not data["suggestion"].strip():
            return jsonify({"error": "Validation error", "message": "A non-empty suggestion is required for edit."}), 400
        item.suggestion = data["suggestion"].strip()
    elif action in {"comment", "accept", "reject"}:
        if action == "comment" or data.get("comment") is not None:
            item.supervisor_comment = (data.get("comment") or "").strip()
    elif action == "reclassify":
        level = data.get("level")
        if level not in {"low", "high"}:
            return jsonify({"error": "Validation error", "message": "level must be low or high."}), 400
        item.level = level
    item.status = {"accept": "accepted", "edit": "edited", "reject": "rejected", "comment": "commented", "reclassify": "pending"}[action]
    item.reviewed_by = current_user.id
    item.reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    log_action("feedback." + action, f"feedback_id={item.id}")
    db.session.commit()
    return jsonify({"message": "Feedback updated.", "feedback": item.to_dict()})


def _review_stamp(item, status):
    item.status = status
    item.reviewed_by = current_user.id
    item.reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)


@supervisor_bp.post("/feedback/bulk")
@role_required("supervisor", "admin")
def bulk_review():
    """Accept or reject many items at once (thesis test T-09: 'bulk accept 20 fixes'). All-or-nothing."""
    ids, action, error = parse_bulk_review(request.get_json(silent=True))
    if error:
        return jsonify({"error": "Validation error", "message": error}), 400
    items = FeedbackItem.query.filter(FeedbackItem.id.in_(ids)).all()
    ok = {}   # thesis_id -> may this user review it (checked once per thesis)
    for i in items:
        if i.thesis_id not in ok:
            ok[i.thesis_id] = get_accessible_thesis(current_user, i.thesis_id) is not None
    if len(items) != len(ids) or not all(ok.values()):
        return jsonify({"error": "Not found", "message": "One or more feedback items were not found."}), 404
    for item in items:
        _review_stamp(item, "accepted" if action == "accept" else "rejected")
    log_action("feedback.bulk." + action, f"count={len(items)}")
    db.session.commit()
    return jsonify({"message": "%d items updated." % len(items), "updated": len(items)})


@supervisor_bp.get("/assigned-theses")
@role_required("supervisor", "admin")
def assigned_theses():
    """The 'Assigned Theses' menu: each assigned student with their submissions."""
    sq = User.query.filter_by(role="student")
    if current_user.role == "supervisor":
        sq = sq.filter_by(supervisor_id=current_user.id)
    students = sq.order_by(User.name).all()
    theses = visible_theses_query(current_user).order_by(Thesis.root_thesis_id, Thesis.version).all()
    by_student = {}
    for t in theses:
        by_student.setdefault(t.student_id, []).append(t.to_dict())
    return jsonify({"students": [{"student": u.to_dict(), "theses": by_student.get(u.id, [])} for u in students]})


@supervisor_bp.get("/progress")
@role_required("supervisor", "admin")
def student_progress():
    """The 'Progress' menu: revision cycles and compliance change for one student, or all assigned students."""
    student_id = request.args.get("student_id", type=int)
    sq = User.query.filter_by(role="student")
    if current_user.role == "supervisor":
        sq = sq.filter_by(supervisor_id=current_user.id)
    if student_id:
        sq = sq.filter_by(id=student_id)
    students = sq.order_by(User.name).limit(50).all()
    if student_id and not students:
        return jsonify({"error": "Not found", "message": "Student not found."}), 404
    return jsonify({"students": [dict(progress_for_student(u.id, for_student=False), name=u.name) for u in students]})


@supervisor_bp.get("/feedback/history")
@role_required("supervisor", "admin")
def feedback_history():
    """The 'Feedback History' menu: items that have already been reviewed."""
    q = FeedbackItem.query.filter(FeedbackItem.thesis_id.in_(visible_thesis_ids(current_user)), FeedbackItem.status != "pending")
    if current_user.role == "supervisor":
        q = q.filter(FeedbackItem.reviewed_by == current_user.id)
    thesis_id = request.args.get("thesis_id", type=int)
    student_id = request.args.get("student_id", type=int)
    if thesis_id:
        q = q.filter(FeedbackItem.thesis_id == thesis_id)
    if student_id:
        q = q.filter(FeedbackItem.thesis_id.in_(db.session.query(Thesis.id).filter(Thesis.student_id == student_id)))
    limit = min(max(request.args.get("limit", 100, type=int), 1), 500)
    items = q.order_by(FeedbackItem.reviewed_at.desc()).limit(limit).all()
    return jsonify({"feedback": [i.to_dict() for i in items]})
