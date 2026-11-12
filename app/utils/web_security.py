"""CSRF protection, CORS and security headers (Flask only; no DB dependency).

CSRF model: the API uses cookie sessions, so every state-changing request must carry the
session's token in the `X-CSRF-Token` header. Clients fetch it from GET /api/auth/csrf
(before login) and receive a fresh one in the login response. A cross-site page can send
the cookie but cannot read the token or set the header. The Origin header is also checked.
"""
import hmac
import secrets
from flask import request, session, jsonify

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = session["csrf_token"] = secrets.token_urlsafe(32)
    return token


def rotate_csrf_token():
    session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def _allowed_origins(app):
    return {o.strip().rstrip("/") for o in (app.config.get("CORS_ORIGINS") or []) if o.strip()}


def init_web_security(app):
    @app.before_request
    def _csrf_guard():
        if not app.config.get("CSRF_ENABLED", True) or request.method in SAFE_METHODS:
            return None
        origin = request.headers.get("Origin")
        if origin and origin.rstrip("/") != request.host_url.rstrip("/") and origin.rstrip("/") not in _allowed_origins(app):
            return jsonify({"error": "Forbidden", "message": "Cross-origin request blocked."}), 403
        expected, sent = session.get("csrf_token"), request.headers.get("X-CSRF-Token", "")
        if not expected or not hmac.compare_digest(expected, sent):
            return jsonify({"error": "Forbidden", "message": "Missing or invalid CSRF token. Fetch /api/auth/csrf and send it in the X-CSRF-Token header."}), 403
        return None

    @app.after_request
    def _headers(resp):
        origin = request.headers.get("Origin", "").rstrip("/")
        if origin and origin in _allowed_origins(app):
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Access-Control-Allow-Credentials"] = "true"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-CSRF-Token"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
            resp.headers.add("Vary", "Origin")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp
