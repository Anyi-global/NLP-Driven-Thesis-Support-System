"""Pure tests for access policy, CSRF/CORS, rate limiting, upload validation (no database)."""
import io
import unittest
import zipfile

from flask import Flask, jsonify

from app.utils.access import can_access_thesis, student_can_see, next_version
from app.utils.rate_limit import RateLimiter
from app.utils.validators import inspect_upload, validate_password
from app.utils.web_security import init_web_security, get_csrf_token
from app.services.report_service import _p


class AccessPolicy(unittest.TestCase):
    def test_supervisor_only_sees_assigned_students(self):
        self.assertTrue(can_access_thesis("supervisor", 7, thesis_student_id=1, student_supervisor_id=7))
        self.assertFalse(can_access_thesis("supervisor", 8, thesis_student_id=1, student_supervisor_id=7))
        self.assertFalse(can_access_thesis("supervisor", 8, thesis_student_id=1, student_supervisor_id=None))

    def test_student_only_sees_own(self):
        self.assertTrue(can_access_thesis("student", 1, 1, 7))
        self.assertFalse(can_access_thesis("student", 2, 1, 7))

    def test_admin_and_unknown_roles(self):
        self.assertTrue(can_access_thesis("admin", 99, 1, None))
        self.assertFalse(can_access_thesis("guest", 1, 1, 1))

    def test_ai_feedback_hidden_until_supervisor_releases_it(self):
        self.assertTrue(student_can_see("compliance", "pending"))
        self.assertFalse(student_can_see("quality", "pending"))
        for s in ("accepted", "edited", "commented"):
            self.assertTrue(student_can_see("quality", s))
        self.assertFalse(student_can_see("quality", "rejected"))
        self.assertFalse(student_can_see("compliance", "rejected"))

    def test_versions(self):
        self.assertEqual(next_version([]), 1)
        self.assertEqual(next_version([3]), 4)


class RateLimiting(unittest.TestCase):
    def test_window_slides_and_reset(self):
        t = [0.0]
        rl = RateLimiter(clock=lambda: t[0])
        for _ in range(3):
            rl.hit("k", 60)
        self.assertTrue(rl.is_limited("k", 3, 60))
        self.assertGreaterEqual(rl.retry_after("k", 60), 1)
        t[0] = 61
        self.assertFalse(rl.is_limited("k", 3, 60))
        rl.hit("k", 60); rl.reset("k")
        self.assertFalse(rl.is_limited("k", 1, 60))


def make_docx_bytes(valid=True):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<x/>")
        if valid:
            z.writestr("word/document.xml", "<x/>")
    return buf.getvalue()


class UploadValidation(unittest.TestCase):
    def test_pdf(self):
        self.assertTrue(inspect_upload(b"%PDF-1.7\n...", "pdf")[0])
        self.assertFalse(inspect_upload(b"MZ\x90\x00 executable", "pdf")[0])

    def test_docx(self):
        self.assertTrue(inspect_upload(make_docx_bytes(), "docx")[0])
        self.assertFalse(inspect_upload(make_docx_bytes(valid=False), "docx")[0])
        self.assertFalse(inspect_upload(b"%PDF-1.7 renamed", "docx")[0])
        self.assertFalse(inspect_upload(b"", "docx")[0])

    def test_password_rules(self):
        self.assertIsNone(validate_password("longenough1"))
        self.assertIn("8 characters", validate_password("short1"))
        self.assertIn("letters and numbers", validate_password("onlyletters"))     # thesis: must include numbers
        self.assertIn("letters and numbers", validate_password("12345678"))        # ... and letters
        self.assertIn("72 bytes", validate_password("a1" * 40))


class TempPassword(unittest.TestCase):
    def test_generated_passwords_meet_the_rule_and_differ(self):
        from app.utils.validators import generate_temp_password
        pw = {generate_temp_password() for _ in range(50)}
        self.assertEqual(len(pw), 50)
        self.assertTrue(all(validate_password(p) is None and len(p) == 12 for p in pw))


class PdfEscaping(unittest.TestCase):
    def test_markup_is_escaped(self):
        self.assertEqual(_p('<img src="file:///etc/passwd"/> & more'), '&lt;img src="file:///etc/passwd"/&gt; &amp; more')


def make_app(**cfg):
    app = Flask(__name__)
    app.config.update(SECRET_KEY="t", CORS_ORIGINS=["https://front.example"], **cfg)

    @app.get("/api/csrf")
    def csrf():
        return jsonify(token=get_csrf_token())

    @app.post("/api/act")
    def act():
        return jsonify(ok=True)

    init_web_security(app)
    return app


class CsrfAndCors(unittest.TestCase):
    def setUp(self):
        self.c = make_app().test_client()

    def token(self):
        return self.c.get("/api/csrf").json["token"]

    def test_post_without_token_rejected(self):
        self.assertEqual(self.c.post("/api/act").status_code, 403)

    def test_post_with_wrong_token_rejected(self):
        self.token()
        self.assertEqual(self.c.post("/api/act", headers={"X-CSRF-Token": "nope"}).status_code, 403)

    def test_post_with_token_ok(self):
        t = self.token()
        self.assertEqual(self.c.post("/api/act", headers={"X-CSRF-Token": t}).status_code, 200)

    def test_get_needs_no_token(self):
        self.assertEqual(self.c.get("/api/csrf").status_code, 200)

    def test_foreign_origin_blocked_even_with_token(self):
        t = self.token()
        r = self.c.post("/api/act", headers={"X-CSRF-Token": t, "Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)

    def test_allowed_origin_gets_cors_headers_and_preflight(self):
        r = self.c.options("/api/act", headers={"Origin": "https://front.example"})
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], "https://front.example")
        self.assertEqual(r.headers["Access-Control-Allow-Credentials"], "true")
        r = self.c.get("/api/csrf", headers={"Origin": "https://evil.example"})
        self.assertNotIn("Access-Control-Allow-Origin", r.headers)

    def test_security_headers(self):
        r = self.c.get("/api/csrf")
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(r.headers["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
