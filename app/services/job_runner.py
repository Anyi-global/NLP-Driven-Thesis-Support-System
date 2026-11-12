"""Runs thesis analysis off the request thread so uploads return immediately.

A ThreadPoolExecutor is adequate for a single-process prototype. It is NOT durable (queued jobs
are lost on restart) and not shared between worker processes; use Celery/RQ for production.
Stuck jobs are recoverable through POST /api/student/theses/<id>/reanalyze.
"""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from ..extensions import db
from ..models.thesis import Thesis
from .analysis_service import analyze_thesis

log = logging.getLogger(__name__)
_executor, _lock = None, threading.Lock()


def _get_executor(workers):
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="analysis")
        return _executor


def _friendly(exc):
    if isinstance(exc, ValueError):
        return str(exc)
    return "Analysis failed unexpectedly. Check the file opens normally (not corrupted or password-protected) and retry."


def run_analysis(app, thesis_id):
    with app.app_context():
        try:
            thesis = db.session.get(Thesis, thesis_id)
            if thesis is None:
                return None
            return analyze_thesis(thesis)
        except Exception as exc:
            log.exception("Thesis analysis failed for thesis_id=%s", thesis_id)
            db.session.rollback()
            thesis = db.session.get(Thesis, thesis_id)
            if thesis is not None:
                thesis.set_status("analysis_failed", _friendly(exc))
                db.session.commit()
            return None
        finally:
            db.session.remove()


def enqueue_analysis(app, thesis_id):
    """Returns ('queued', None) in async mode, or ('done', summary_or_None) in synchronous mode."""
    if app.config.get("ANALYSIS_ASYNC", True):
        _get_executor(app.config.get("ANALYSIS_WORKERS", 2)).submit(run_analysis, app, thesis_id)
        return "queued", None
    return "done", run_analysis(app, thesis_id)


def is_stale(thesis, minutes):
    if thesis.status not in {"queued", "processing"} or thesis.status_updated_at is None:
        return False
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return now - thesis.status_updated_at > timedelta(minutes=minutes)
