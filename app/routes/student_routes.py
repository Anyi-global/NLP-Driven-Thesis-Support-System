import uuid
from pathlib import Path
from flask import Blueprint, request, jsonify, current_app, send_file
from flask_login import current_user, login_required
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from ..extensions import db
from ..models.thesis import Thesis
from ..models.feedback import FeedbackItem, QualityReport
from ..utils.validators import allowed_file, safe_extension, inspect_upload
from ..utils.security import log_action
from ..utils.access import (get_accessible_thesis, visible_theses_query, visible_thesis_ids,
                            apply_feedback_visibility, next_version)
from ..utils.rate_limit import limiter, rate_limited_response
from ..services.job_runner import enqueue_analysis, is_stale
from ..services.progress_service import progress_for_student
from ..utils.metrics import count_items
from ..nlp.rule_engine import load_rules
from datetime import datetime, timezone
from ..services.report_service import generate_feedback_pdf, generate_annotated_docx

student_bp = Blueprint("student", __name__)
UPLOAD_LIMIT, UPLOAD_WINDOW = 15, 60 * 60  # per user


def _error(error, message, code):
    return jsonify({"error": error, "message": message}), code


def _read_upload():
    """Return (data, ext, error_response). Validates extension AND file contents."""
    file = request.files.get("file")
    if not file or not file.filename:
        return None, None, _error("Validation error", "Upload a DOCX or PDF file in the 'file' field.", 400)
    if not allowed_file(file.filename):
        return None, None, _error("Unsupported file", "Only DOCX and PDF files are accepted.", 415)
    ext, data = safe_extension(file.filename), file.read()
    ok, message = inspect_upload(data, ext)
    if not ok:
        return None, None, _error("Unsupported file", message, 415)
    return data, ext, None


def _store(data, ext):
    dest = Path(current_app.config["UPLOAD_FOLDER"]) / f"{uuid.uuid4().hex}.{ext}"
    dest.write_bytes(data)
    return dest


def _start_analysis(thesis, success_message):
    mode, summary = enqueue_analysis(current_app._get_current_object(), thesis.id)
    db.session.refresh(thesis)
    body = {"thesis": thesis.to_dict(), "status_url": f"/api/student/theses/{thesis.id}/status"}
    if mode == "queued":
        body["message"] = "Upload received. Analysis is running in the background; poll status_url for progress."
        return jsonify(body), 202
    body["analysis"] = summary
    if thesis.status == "analyzed":
        body["message"] = success_message
        return jsonify(body), 201
    body["message"] = "Saved, but analysis failed: %s" % (thesis.analysis_note or "see server logs")
    return jsonify(body), 202


@student_bp.post("/upload")
@login_required
def upload():
    if current_user.role != "student":
        return _error("Forbidden", "Only student accounts can upload thesis drafts.", 403)
    blocked = rate_limited_response("upload", current_user.id, UPLOAD_LIMIT, UPLOAD_WINDOW, current_app.config)
    if blocked:
        return blocked
    title = (request.form.get("title") or "Untitled thesis").strip()
    data, ext, err = _read_upload()
    if err:
        return err
    limiter.hit("upload:%s" % current_user.id, UPLOAD_WINDOW)
    dest = _store(data, ext)
    thesis = Thesis(student_id=current_user.id, title=title[:300], original_filename=Path(request.files["file"].filename).name[:255],
                    file_path=str(dest), file_type=ext, version=1)
    db.session.add(thesis)
    db.session.flush()
    thesis.root_thesis_id = thesis.id
    thesis.set_status("queued")
    log_action("thesis.upload", f"thesis_title={title[:120]}")
    db.session.commit()
    return _start_analysis(thesis, "Thesis uploaded and analyzed.")


@student_bp.post("/resubmit")
@login_required
def resubmit():
    if current_user.role != "student":
        return _error("Forbidden", "Only students can resubmit drafts.", 403)
    blocked = rate_limited_response("upload", current_user.id, UPLOAD_LIMIT, UPLOAD_WINDOW, current_app.config)
    if blocked:
        return blocked
    parent_id = request.form.get("thesis_id", type=int)
    parent = db.session.get(Thesis, parent_id) if parent_id else None
    if not parent or parent.student_id != current_user.id:
        return _error("Not found", "Original thesis not found.", 404)
    root_id = parent.root_thesis_id or parent.id
    latest = db.session.query(func.max(Thesis.version)).filter(Thesis.root_thesis_id == root_id).scalar() or parent.version
    if parent.version != latest:
        return _error("Conflict", "Resubmit from the latest version (v%d) of this thesis." % latest, 409)
    data, ext, err = _read_upload()
    if err:
        return err
    limiter.hit("upload:%s" % current_user.id, UPLOAD_WINDOW)
    dest = _store(data, ext)
    thesis = Thesis(student_id=current_user.id, title=parent.title, original_filename=Path(request.files["file"].filename).name[:255],
                    file_path=str(dest), file_type=ext, version=next_version([latest]), parent_thesis_id=parent.id,
                    root_thesis_id=root_id)
    thesis.set_status("queued")
    db.session.add(thesis)
    log_action("thesis.resubmit", f"parent_id={parent.id}")
    try:
        db.session.commit()
    except IntegrityError:  # two simultaneous resubmissions claimed the same version number
        db.session.rollback()
        dest.unlink(missing_ok=True)
        return _error("Conflict", "Another resubmission was saved at the same moment. Reload and try again.", 409)
    return _start_analysis(thesis, "Revised draft analyzed.")


@student_bp.get("/theses")
@login_required
def list_theses():
    theses = visible_theses_query(current_user).order_by(Thesis.created_at.desc()).all()
    quality_counts = {}
    thesis_ids = [t.id for t in theses]
    if thesis_ids:
        quality_counts = dict(db.session.query(QualityReport.thesis_id, func.count(QualityReport.id))
                             .filter(QualityReport.thesis_id.in_(thesis_ids))
                             .group_by(QualityReport.thesis_id).all())

    results = []
    for thesis in theses:
        data = thesis.to_dict()
        evaluation_status = _quality_evaluation_status(thesis) if thesis.status == "analyzed" else "pending"
        data["research_quality_evaluation"] = evaluation_status
        data["research_quality_issue_count"] = quality_counts.get(thesis.id, 0) \
            if evaluation_status == "complete" else None
        results.append(data)
    return jsonify({"theses": results})


def _quality_evaluation_status(thesis):
    note = (thesis.analysis_note or "").lower()
    if "gemini is not configured" in note or "gemini_model is not set" in note:
        return "unavailable"
    if ("ai evaluation stopped:" in note or "whole-document ai evaluation call failed" in note
            or "ai calls failed and were skipped" in note):
        return "failed"
    if "not evaluated (call budget reached)" in note or "only the first part was evaluated" in note:
        return "incomplete"
    return "complete"


@student_bp.get("/theses/<int:thesis_id>/status")
@login_required
def status(thesis_id):
    thesis = get_accessible_thesis(current_user, thesis_id)
    if not thesis:
        return _error("Not found", "Thesis not found.", 404)
    stale = is_stale(thesis, current_app.config.get("STALE_JOB_MINUTES", 20))
    return jsonify({"thesis_id": thesis.id, "status": thesis.status, "analysis_note": thesis.analysis_note,
                    "stale": stale, "done": thesis.status in {"analyzed", "analysis_failed"}})


@student_bp.post("/theses/<int:thesis_id>/reanalyze")
@login_required
def reanalyze(thesis_id):
    """Retry a failed or stuck analysis. Refused for completed analyses because re-running
    replaces feedback items and would erase supervisor review decisions."""
    thesis = get_accessible_thesis(current_user, thesis_id)
    if not thesis or current_user.role == "supervisor":
        return _error("Not found", "Thesis not found.", 404)
    stale = is_stale(thesis, current_app.config.get("STALE_JOB_MINUTES", 20))
    if thesis.status != "analysis_failed" and not stale:
        return _error("Conflict", "Only failed or stuck analyses can be retried.", 409)
    thesis.set_status("queued")
    log_action("thesis.reanalyze", f"thesis_id={thesis.id}")
    db.session.commit()
    return _start_analysis(thesis, "Thesis analyzed.")


@student_bp.get("/reports")
@login_required
def reports():
    thesis_id = request.args.get("thesis_id", type=int)
    query = FeedbackItem.query.filter(FeedbackItem.thesis_id.in_(visible_thesis_ids(current_user)))
    if thesis_id:
        query = query.filter(FeedbackItem.thesis_id == thesis_id)
    query = apply_feedback_visibility(query, current_user)
    items = query.order_by(FeedbackItem.priority_score.desc(), FeedbackItem.id.asc()).all()
    return jsonify({"feedback": [i.to_dict() for i in items]})


def _exportable_items(thesis):
    q = apply_feedback_visibility(FeedbackItem.query.filter_by(thesis_id=thesis.id), current_user)
    return q.order_by(FeedbackItem.priority_score.desc(), FeedbackItem.id.asc()).all()


@student_bp.get("/theses/<int:thesis_id>/export/pdf")
@login_required
def export_pdf(thesis_id):
    thesis = get_accessible_thesis(current_user, thesis_id)
    if not thesis:
        return _error("Not found", "Thesis not found.", 404)
    if thesis.status != "analyzed":
        return _error("Conflict", "Analysis is not complete yet.", 409)
    return send_file(generate_feedback_pdf(thesis, _exportable_items(thesis)), mimetype="application/pdf",
                     as_attachment=True, download_name=f"thesis_{thesis.id}_feedback.pdf")


@student_bp.get("/theses/<int:thesis_id>/export/docx")
@login_required
def export_docx(thesis_id):
    thesis = get_accessible_thesis(current_user, thesis_id)
    if not thesis:
        return _error("Not found", "Thesis not found.", 404)
    if thesis.status != "analyzed":
        return _error("Conflict", "Analysis is not complete yet.", 409)
    scope = request.args.get("scope", "all")
    if scope not in {"all", "accepted"}:
        return _error("Validation error", "scope must be 'all' or 'accepted'.", 400)
    decisions = FeedbackItem.query.filter_by(thesis_id=thesis.id, source_report_type="compliance").all()
    allowed = {r["id"] for r in load_rules() if r.get("active", True)}
    try:
        buf, stats = generate_annotated_docx(thesis, _exportable_items(thesis), decisions, scope, allowed)
    except ValueError as e:
        return _error("Unsupported export", str(e), 400)
    resp = send_file(buf, mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                     as_attachment=True, download_name=f"thesis_{thesis.id}_annotated.docx")
    resp.headers["X-Tracked-Changes"], resp.headers["X-Comments"] = str(stats["tracked_changes"]), str(stats["comments"])
    return resp


@student_bp.get("/summary")
@login_required
def summary():
    """Summary cards for My Reports (thesis Figure 4.4): submissions and the issues in the latest version."""
    if current_user.role != "student":
        return _error("Forbidden", "Only students have a personal summary.", 403)
    theses = Thesis.query.filter_by(student_id=current_user.id).order_by(Thesis.created_at.desc(), Thesis.id.desc()).all()
    latest = theses[0] if theses else None
    rows = [(i.source_report_type, i.level, i.severity, i.status) for i in
            FeedbackItem.query.filter_by(thesis_id=latest.id).all()] if latest else []
    return jsonify({"submissions": len(theses), "analyzed": sum(1 for t in theses if t.status == "analyzed"),
                    "latest": latest.to_dict() if latest else None, "issues_in_latest": count_items(rows, True)})


@student_bp.get("/progress")
@login_required
def progress():
    """Revision cycles and compliance improvement across the student's submissions."""
    if current_user.role != "student":
        return _error("Forbidden", "Only students have a personal progress view.", 403)
    return jsonify(progress_for_student(current_user.id, for_student=True))


@student_bp.get("/feedback")
@login_required
def supervisor_feedback():
    """The 'Feedback' menu: items the supervisor has accepted, edited or commented on."""
    if current_user.role != "student":
        return _error("Forbidden", "Only students have a feedback inbox.", 403)
    q = FeedbackItem.query.filter(FeedbackItem.thesis_id.in_(visible_thesis_ids(current_user)))
    thesis_id = request.args.get("thesis_id", type=int)
    if thesis_id:
        q = q.filter(FeedbackItem.thesis_id == thesis_id)
    items = [i for i in q.order_by(FeedbackItem.reviewed_at.desc()).all()
             if i.status in {"accepted", "edited", "commented"} or i.supervisor_comment]
    return jsonify({"feedback": [i.to_dict() for i in items if i.status != "rejected"]})


@student_bp.post("/theses/<int:thesis_id>/submit-for-review")
@login_required
def submit_for_review(thesis_id):
    """'Send the report to the supervisor for review' (thesis Figure 4.6)."""
    thesis = get_accessible_thesis(current_user, thesis_id)
    if not thesis or current_user.role != "student":
        return _error("Not found", "Thesis not found.", 404)
    if thesis.status != "analyzed":
        return _error("Conflict", "Analysis is not complete yet.", 409)
    if current_user.supervisor_id is None:
        return _error("Conflict", "No supervisor is assigned to your account yet. Ask the administrator.", 409)
    thesis.review_requested_at = datetime.now(timezone.utc).replace(tzinfo=None)
    log_action("thesis.submit_for_review", f"thesis_id={thesis.id}")
    db.session.commit()
    return jsonify({"message": "Sent to your supervisor for review.", "thesis": thesis.to_dict()})


@student_bp.post("/theses/<int:thesis_id>/accept-fixes")
@login_required
def accept_fixes(thesis_id):
    """'Accept all fixes' (thesis Figure 4.5). Marks rule-based suggestions as accepted by the student; with item_ids it
    accepts only those. This is the student's own marker and never changes the supervisor's review status."""
    thesis = get_accessible_thesis(current_user, thesis_id)
    if not thesis or current_user.role != "student":
        return _error("Not found", "Thesis not found.", 404)
    ids = (request.get_json(silent=True) or {}).get("item_ids")
    q = FeedbackItem.query.filter_by(thesis_id=thesis.id, source_report_type="compliance").filter(
        FeedbackItem.status != "rejected", FeedbackItem.student_status.is_(None))
    if ids is not None:
        if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
            return _error("Validation error", "item_ids must be a list of integers.", 400)
        q = q.filter(FeedbackItem.id.in_(ids))
    items = q.all()
    for item in items:
        item.student_status = "accepted"
    log_action("feedback.student_accept", f"thesis_id={thesis.id},count={len(items)}")
    db.session.commit()
    return jsonify({"message": "%d fixes accepted." % len(items), "accepted": len(items)})
