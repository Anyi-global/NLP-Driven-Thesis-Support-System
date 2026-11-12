"""End-to-end API tests (need the full requirements installed). Run: pytest -q"""
import io
import time

import bcrypt
import pytest
from docx import Document

from app import create_app
from app.extensions import db
from app.models.user import User
from app.utils.rate_limit import limiter

PASSWORD = "StrongPass123"


def docx_bytes(text="Body text. " * 20):
    d = Document()
    d.add_paragraph("CHAPTER ONE")
    d.add_paragraph("INTRODUCTION")
    d.add_paragraph(text)
    buf = io.BytesIO(); d.save(buf)
    return buf.getvalue()


class Api:
    """Test client wrapper that handles the CSRF token like a real frontend must."""
    def __init__(self, app):
        self.c = app.test_client(); self.token = self.c.get("/api/auth/csrf").json["csrf_token"]

    def _h(self):
        return {"X-CSRF-Token": self.token}

    def post(self, url, **kw):
        return self.c.post(url, headers=self._h(), **kw)

    def put(self, url, **kw):
        return self.c.put(url, headers=self._h(), **kw)

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def login(self, email, password=PASSWORD):
        r = self.post("/api/auth/login", json={"email": email, "password": password})
        if r.status_code == 200:
            self.token = r.json["csrf_token"]
        return r

    def upload(self, path="/api/student/upload", data=None, name="t.docx", **form):
        return self.post(path, data={"file": (io.BytesIO(data or docx_bytes()), name), **form},
                         content_type="multipart/form-data")


@pytest.fixture()
def app(tmp_path):
    limiter._hits.clear()
    app = create_app({"TESTING": True, "SECRET_KEY": "test-secret", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path/'test.db'}",
                      "UPLOAD_FOLDER": str(tmp_path / "uploads"), "ANALYSIS_ASYNC": False, "GEMINI_API_KEY": ""})
    with app.app_context():
        db.create_all()
    yield app
    with app.app_context():
        db.drop_all()


def make_user(app, email, role, supervisor_id=None):
    with app.app_context():
        u = User(name=email.split("@")[0], email=email, role=role, supervisor_id=supervisor_id,
                 password_hash=bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode())
        db.session.add(u); db.session.commit()
        return u.id


@pytest.fixture()
def world(app):
    sup_a, sup_b = make_user(app, "a@x.edu", "supervisor"), make_user(app, "b@x.edu", "supervisor")
    stu = make_user(app, "s@x.edu", "student", supervisor_id=sup_a)
    make_user(app, "admin@x.edu", "admin")
    return {"sup_a": sup_a, "sup_b": sup_b, "stu": stu}


@pytest.fixture()
def fake_ai(monkeypatch):
    finding = {"section": "CHAPTER ONE", "finding": "SECRET-AI-FINDING", "rationale": "r", "suggestion": "s", "severity": "high", "source": "gemini"}
    monkeypatch.setattr("app.services.analysis_service.evaluate_thesis",
                        lambda parsed: {"findings": [dict(finding)], "warning": None, "coverage": {}})


def as_user(app, email):
    api = Api(app); assert api.login(email).status_code == 200
    return api


def test_health(app):
    assert app.test_client().get("/api/health").json["status"] == "ok"


def test_csrf_required_for_state_changes(app):
    c = app.test_client()
    assert c.post("/api/auth/register", json={"name": "A", "email": "a@a.com", "password": PASSWORD}).status_code == 403


def test_register_login_and_no_role_escalation(app):
    api = Api(app)
    r = api.post("/api/auth/register", json={"name": "Bad", "email": "bad@x.com", "password": PASSWORD, "role": "admin"})
    assert r.status_code == 201 and r.json["user"]["role"] == "student"
    assert api.login("bad@x.com").status_code == 200


def test_login_lockout_after_repeated_failures(app, world):
    api = Api(app)
    for _ in range(5):
        assert api.login("s@x.edu", "wrong-password").status_code == 401
    r = api.login("s@x.edu", PASSWORD)
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_upload_rejects_disguised_file(app, world):
    stu = as_user(app, "s@x.edu")
    assert stu.upload(data=b"MZ not a document", name="evil.docx").status_code == 415


def test_supervisor_isolation_and_ai_release_gate(app, world, fake_ai):
    stu = as_user(app, "s@x.edu")
    r = stu.upload(title="My thesis")
    assert r.status_code == 201, r.json
    tid = r.json["thesis"]["id"]

    # Student sees rule-based feedback but NOT unreviewed AI feedback.
    fb = stu.get(f"/api/student/reports?thesis_id={tid}").json["feedback"]
    assert fb and all(i["source_report_type"] == "compliance" for i in fb)

    sup_a, sup_b = as_user(app, "a@x.edu"), as_user(app, "b@x.edu")
    dash_a = sup_a.get("/api/supervisor/dashboard").json
    assert [t["id"] for t in dash_a["theses"]] == [tid]
    ai_item = next(i for i in dash_a["feedback"] if i["source_report_type"] == "quality")
    assert sup_b.get("/api/supervisor/dashboard").json["theses"] == []
    assert sup_b.post(f"/api/supervisor/feedback/{ai_item['id']}", json={"action": "accept"}).status_code == 404
    assert sup_b.get(f"/api/student/theses/{tid}/export/pdf").status_code == 404

    # Once the assigned supervisor accepts it, the student can see it; rejected items disappear.
    assert sup_a.post(f"/api/supervisor/feedback/{ai_item['id']}", json={"action": "accept"}).status_code == 200
    fb = stu.get(f"/api/student/reports?thesis_id={tid}").json["feedback"]
    assert any(i["source_report_type"] == "quality" for i in fb)
    assert sup_a.post(f"/api/supervisor/feedback/{ai_item['id']}", json={"action": "reject"}).status_code == 200
    fb = stu.get(f"/api/student/reports?thesis_id={tid}").json["feedback"]
    assert not any(i["source_report_type"] == "quality" for i in fb)


def test_student_export_excludes_unreleased_ai_feedback(app, world, fake_ai):
    import pdfplumber
    stu = as_user(app, "s@x.edu")
    tid = stu.upload().json["thesis"]["id"]
    r = stu.get(f"/api/student/theses/{tid}/export/pdf")
    assert r.status_code == 200
    with pdfplumber.open(io.BytesIO(r.data)) as pdf:
        assert "SECRET-AI-FINDING" not in " ".join(p.extract_text() or "" for p in pdf.pages)


@pytest.mark.parametrize("findings, expected_count", [
    ([], 0),
    ([{"section": "CHAPTER ONE", "finding": "Quality finding one"},
      {"section": "CHAPTER THREE", "finding": "Quality finding two"}], 2),
])
def test_student_dashboard_reports_successful_research_quality_count(app, world, monkeypatch, findings, expected_count):
    monkeypatch.setattr("app.services.analysis_service.evaluate_thesis", lambda parsed: {
        "findings": findings, "warning": None, "coverage": {"calls": 1, "failed_calls": 0}
    })
    stu = as_user(app, "s@x.edu")
    thesis_id = stu.upload().json["thesis"]["id"]

    thesis = next(t for t in stu.get("/api/student/theses").json["theses"] if t["id"] == thesis_id)
    assert thesis["research_quality_evaluation"] == "complete"
    assert thesis["research_quality_issue_count"] == expected_count
    feedback = stu.get(f"/api/student/reports?thesis_id={thesis_id}").json["feedback"]
    assert all(item["source_report_type"] != "quality" for item in feedback)


@pytest.mark.parametrize("warning, expected_status", [
    ("Gemini is not configured. Set GEMINI_API_KEY to enable evaluation.", "unavailable"),
    ("The whole-document AI evaluation call failed; no research-quality findings were produced.", "failed"),
    ("Not evaluated (call budget reached): CHAPTER FIVE.", "incomplete"),
])
def test_student_dashboard_does_not_report_zero_when_quality_evaluation_unavailable(
        app, world, monkeypatch, warning, expected_status):
    monkeypatch.setattr("app.services.analysis_service.evaluate_thesis", lambda parsed: {
        "findings": [], "warning": warning, "coverage": {"calls": 0, "failed_calls": 1}
    })
    stu = as_user(app, "s@x.edu")
    thesis_id = stu.upload().json["thesis"]["id"]

    thesis = next(t for t in stu.get("/api/student/theses").json["theses"] if t["id"] == thesis_id)
    assert thesis["research_quality_evaluation"] == expected_status
    assert thesis["research_quality_issue_count"] is None


def test_unassigned_student_ai_feedback_stays_hidden(app, fake_ai):
    make_user(app, "lone@x.edu", "student")
    stu = as_user(app, "lone@x.edu")
    tid = stu.upload().json["thesis"]["id"]
    assert all(i["source_report_type"] == "compliance" for i in stu.get(f"/api/student/reports?thesis_id={tid}").json["feedback"])


def test_resubmit_versions_follow_the_chain(app, world):
    stu = as_user(app, "s@x.edu")
    v1 = stu.upload(title="Same title").json["thesis"]
    stu.upload(title="Same title")  # an unrelated second thesis with an identical title
    v2 = stu.upload("/api/student/resubmit", thesis_id=str(v1["id"])).json["thesis"]
    assert (v2["version"], v2["root_thesis_id"], v2["parent_thesis_id"]) == (2, v1["id"], v1["id"])
    assert stu.upload("/api/student/resubmit", thesis_id=str(v1["id"])).status_code == 409  # not the latest
    v3 = stu.upload("/api/student/resubmit", thesis_id=str(v2["id"])).json["thesis"]
    assert v3["version"] == 3


def test_failed_analysis_can_be_retried_but_completed_cannot(app, world, monkeypatch):
    stu = as_user(app, "s@x.edu")
    import app.services.analysis_service as svc
    real = svc.parse_document
    monkeypatch.setattr(svc, "parse_document", lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
    r = stu.upload()
    assert r.status_code == 202 and r.json["thesis"]["status"] == "analysis_failed"
    tid = r.json["thesis"]["id"]
    monkeypatch.setattr(svc, "parse_document", real)
    assert stu.post(f"/api/student/theses/{tid}/reanalyze").status_code == 201
    assert stu.get(f"/api/student/theses/{tid}/status").json["status"] == "analyzed"
    assert stu.post(f"/api/student/theses/{tid}/reanalyze").status_code == 409  # would wipe supervisor reviews


def test_background_analysis_returns_immediately_and_completes(tmp_path):
    app = create_app({"TESTING": True, "SECRET_KEY": "t", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path/'a.db'}",
                      "UPLOAD_FOLDER": str(tmp_path / "u"), "ANALYSIS_ASYNC": True,
                      "GEMINI_API_KEY": "", "GEMINI_MODEL": ""})
    with app.app_context():
        db.create_all()
    make_user(app, "s@x.edu", "student")
    stu = as_user(app, "s@x.edu")
    r = stu.upload()
    assert r.status_code == 202 and r.json["thesis"]["status"] in {"queued", "processing", "analyzed"}
    tid = r.json["thesis"]["id"]
    for _ in range(60):
        if stu.get(f"/api/student/theses/{tid}/status").json["done"]:
            break
        time.sleep(0.5)
    assert stu.get(f"/api/student/theses/{tid}/status").json["status"] == "analyzed"


def test_admin_assigns_supervisor(app, world):
    admin = as_user(app, "admin@x.edu")
    r = admin.put(f"/api/admin/students/{world['stu']}/supervisor", json={"supervisor_id": world["sup_b"]})
    assert r.status_code == 200 and r.json["user"]["supervisor_id"] == world["sup_b"]
    assert admin.put(f"/api/admin/students/{world['stu']}/supervisor", json={"supervisor_id": world["stu"]}).status_code == 400
    assert as_user(app, "s@x.edu").put(f"/api/admin/students/{world['stu']}/supervisor", json={"supervisor_id": None}).status_code == 403


# ---------------------------------------------------------------------------------------------------------------
# Thesis Chapter 4 test cases T-01 ... T-12 and the endpoints added for the thesis menus.
# ---------------------------------------------------------------------------------------------------------------
from app.models.feedback import FeedbackItem  # noqa: E402


def add_items(app, thesis_id, n, level="low", source="compliance"):
    with app.app_context():
        ids = []
        for i in range(n):
            item = FeedbackItem(thesis_id=thesis_id, source_report_type=source, source_report_id=1000 + i, level=level,
                                content="bulk item %d" % i, severity="low", priority_score=1)
            db.session.add(item)
            db.session.flush()
            ids.append(item.id)
        db.session.commit()
        return ids


def test_T01_T02_valid_docx_accepted_corrupt_pdf_reports_error(app, world):
    stu = as_user(app, "s@x.edu")
    assert stu.upload().status_code == 201                                           # T-01
    r = stu.upload(data=b"%PDF-1.4\nnot a real pdf body", name="broken.pdf")         # T-02: right header, unreadable body
    assert r.status_code == 202 and r.json["thesis"]["status"] == "analysis_failed"
    assert r.json["thesis"]["analysis_note"]                                         # the student is shown an error message
    assert stu.upload(data=b"just text", name="fake.pdf").status_code == 415


def test_T09_bulk_accept_twenty_items(app, world):
    stu = as_user(app, "s@x.edu")
    tid = stu.upload().json["thesis"]["id"]
    ids = add_items(app, tid, 20)
    sup = as_user(app, "a@x.edu")
    r = sup.post("/api/supervisor/feedback/bulk", json={"item_ids": ids, "action": "accept"})
    assert r.status_code == 200 and r.json["updated"] == 20
    with app.app_context():
        assert all(db.session.get(FeedbackItem, i).status == "accepted" for i in ids)
    assert len(sup.get("/api/supervisor/feedback/history").json["feedback"]) >= 20


def test_bulk_is_all_or_nothing_and_respects_assignment(app, world):
    stu = as_user(app, "s@x.edu")
    tid = stu.upload().json["thesis"]["id"]
    ids = add_items(app, tid, 3)
    other = as_user(app, "b@x.edu")                                                 # supervisor of nobody
    assert other.post("/api/supervisor/feedback/bulk", json={"item_ids": ids, "action": "accept"}).status_code == 404
    sup = as_user(app, "a@x.edu")
    assert sup.post("/api/supervisor/feedback/bulk", json={"item_ids": ids + [999999], "action": "accept"}).status_code == 404
    with app.app_context():
        assert all(db.session.get(FeedbackItem, i).status == "pending" for i in ids)    # nothing changed
    assert sup.post("/api/supervisor/feedback/bulk", json={"item_ids": ids, "action": "edit"}).status_code == 400


def test_T10_export_annotated_docx_and_pdf_download(app, world):
    stu = as_user(app, "s@x.edu")
    tid = stu.upload().json["thesis"]["id"]
    r = stu.get(f"/api/student/theses/{tid}/export/docx")
    assert r.status_code == 200 and r.data[:2] == b"PK" and "wordprocessingml" in r.mimetype
    assert stu.get(f"/api/student/theses/{tid}/export/pdf").data[:5] == b"%PDF-"


def test_T11_unauthorised_access_blocked(app, world):
    anon = Api(app)
    assert anon.get("/api/student/theses").status_code == 401
    stu = as_user(app, "s@x.edu")
    tid = stu.upload().json["thesis"]["id"]
    assert stu.get("/api/supervisor/dashboard").status_code == 403
    assert stu.get("/api/admin/reports/usage").status_code == 403
    make_user(app, "s2@x.edu", "student")
    other = as_user(app, "s2@x.edu")
    assert other.get(f"/api/student/theses/{tid}/status").status_code == 404
    assert other.get(f"/api/student/theses/{tid}/export/pdf").status_code == 404


def test_T12_resubmit_saves_a_new_version(app, world):
    stu = as_user(app, "s@x.edu")
    v1 = stu.upload(title="My thesis").json["thesis"]
    v2 = stu.upload("/api/student/resubmit", thesis_id=str(v1["id"])).json["thesis"]
    assert (v2["version"], v2["parent_thesis_id"]) == (2, v1["id"])


def test_password_rule_letters_and_numbers(app):
    api = Api(app)
    for bad in ("short1", "onlyletters", "12345678"):
        assert api.post("/api/auth/register", json={"name": "A", "email": "a@a.com", "password": bad}).status_code == 400
    assert api.post("/api/auth/register", json={"name": "A", "email": "a@a.com", "password": "Letters123"}).status_code == 201


def test_login_role_choice(app, world):
    api = Api(app)
    assert api.login("s@x.edu").status_code == 200
    ok = Api(app).post("/api/auth/login", json={"email": "s@x.edu", "password": PASSWORD, "role": "student"})
    wrong = Api(app).post("/api/auth/login", json={"email": "s@x.edu", "password": PASSWORD, "role": "admin"})
    assert ok.status_code == 200 and wrong.status_code == 401


def test_submit_for_review_progress_summary_and_assigned_theses(app, world):
    stu = as_user(app, "s@x.edu")
    v1 = stu.upload().json["thesis"]
    assert stu.post(f"/api/student/theses/{v1['id']}/submit-for-review").status_code == 200
    v2 = stu.upload("/api/student/resubmit", thesis_id=str(v1["id"]), data=docx_bytes("Different body text. " * 30)).json["thesis"]

    summary = stu.get("/api/student/summary").json
    assert summary["submissions"] == 2 and summary["latest"]["id"] == v2["id"]

    chain = stu.get("/api/student/progress").json["chains"][0]
    assert chain["revision_cycles"] == 1 and [v["version"] for v in chain["versions"]] == [1, 2]
    assert "since_previous" in chain["versions"][1]

    sup = as_user(app, "a@x.edu")
    assigned = sup.get("/api/supervisor/assigned-theses").json["students"]
    assert [len(s["theses"]) for s in assigned] == [2]
    assert sup.get(f"/api/supervisor/progress?student_id={world['stu']}").json["students"][0]["chains"]
    assert as_user(app, "b@x.edu").get(f"/api/supervisor/progress?student_id={world['stu']}").status_code == 404

    flagged = [t for t in sup.get("/api/supervisor/dashboard").json["theses"] if t["review_requested_at"]]
    assert [t["id"] for t in flagged] == [v1["id"]]


def test_submit_for_review_needs_a_supervisor(app):
    make_user(app, "lone@x.edu", "student")
    stu = as_user(app, "lone@x.edu")
    tid = stu.upload().json["thesis"]["id"]
    assert stu.post(f"/api/student/theses/{tid}/submit-for-review").status_code == 409


def test_student_feedback_inbox_only_shows_supervisor_reviewed_items(app, world, fake_ai):
    stu = as_user(app, "s@x.edu")
    tid = stu.upload().json["thesis"]["id"]
    assert stu.get("/api/student/feedback").json["feedback"] == []
    sup = as_user(app, "a@x.edu")
    ai = next(i for i in sup.get("/api/supervisor/dashboard").json["feedback"] if i["source_report_type"] == "quality")
    sup.post(f"/api/supervisor/feedback/{ai['id']}", json={"action": "comment", "comment": "Please clarify."})
    inbox = stu.get("/api/student/feedback").json["feedback"]
    assert [i["supervisor_comment"] for i in inbox] == ["Please clarify."]


def test_admin_usage_report_json_and_csv(app, world):
    as_user(app, "s@x.edu").upload()
    admin = as_user(app, "admin@x.edu")
    data = admin.get("/api/admin/reports/usage").json
    assert data["theses"]["total"] == 1 and data["users"]["by_role"]["supervisor"] == 2
    csv_resp = admin.get("/api/admin/reports/usage?format=csv")
    assert csv_resp.mimetype == "text/csv" and csv_resp.data.decode().startswith("metric,value")


# ---------------------------------------------------------------------------------------------------------------
# Password change/reset, forced change, accept-fixes
# ---------------------------------------------------------------------------------------------------------------
def test_change_password(app, world):
    stu = as_user(app, "s@x.edu")
    assert stu.post("/api/auth/change-password", json={"current_password": "wrong", "new_password": "NewPass123"}).status_code == 400
    assert stu.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": "weakpass"}).status_code == 400
    assert stu.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": "NewPass123"}).status_code == 200
    assert Api(app).login("s@x.edu", PASSWORD).status_code == 401
    assert Api(app).login("s@x.edu", "NewPass123").status_code == 200


def test_admin_reset_forces_a_new_password(app, world):
    admin = as_user(app, "admin@x.edu")
    r = admin.post(f"/api/admin/users/{world['sup_a']}/reset-password")
    temp = r.json["temporary_password"]
    assert r.status_code == 200 and len(temp) == 12
    sup = Api(app)
    login = sup.login("a@x.edu", temp)
    assert login.status_code == 200 and login.json["user"]["must_change_password"] is True
    blocked = sup.get("/api/supervisor/dashboard")
    assert blocked.status_code == 403 and blocked.json["error"] == "Password change required"
    assert sup.post("/api/auth/change-password", json={"current_password": temp, "new_password": "Fresh1234x"}).status_code == 200
    assert sup.get("/api/supervisor/dashboard").status_code == 200
    assert as_user(app, "s@x.edu").post(f"/api/admin/users/{world['sup_a']}/reset-password").status_code == 403


def test_student_accepts_fixes_without_touching_supervisor_status(app, world):
    stu = as_user(app, "s@x.edu")
    tid = stu.upload().json["thesis"]["id"]
    r = stu.post(f"/api/student/theses/{tid}/accept-fixes", json={})
    assert r.status_code == 200 and r.json["accepted"] > 0
    items = stu.get(f"/api/student/reports?thesis_id={tid}").json["feedback"]
    assert all(i["student_status"] == "accepted" and i["status"] == "pending" for i in items)
    assert stu.post(f"/api/student/theses/{tid}/accept-fixes", json={}).json["accepted"] == 0
    make_user(app, "s2@x.edu", "student")
    assert as_user(app, "s2@x.edu").post(f"/api/student/theses/{tid}/accept-fixes", json={}).status_code == 404
