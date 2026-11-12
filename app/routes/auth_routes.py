import bcrypt
from flask import Blueprint, request, jsonify, current_app
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy import func
from ..extensions import db
from ..models.user import User
from ..utils.security import log_action
from ..utils.validators import validate_password
from ..utils.rate_limit import limiter, rate_limited_response
from ..utils.web_security import get_csrf_token, rotate_csrf_token

auth_bp = Blueprint("auth", __name__)
VALID_ROLES = {"student", "supervisor", "admin"}
LOGIN_FAILS, LOGIN_WINDOW = 5, 15 * 60          # per (IP, email)
LOGIN_IP_FAILS = 30                              # per IP, same window
REGISTER_LIMIT, REGISTER_WINDOW = 10, 60 * 60    # per IP
_dummy_hash = None


def _dummy():
    """Verify against a dummy hash when the email is unknown, so response time does not reveal which emails exist."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt()).decode()
    return _dummy_hash


def _check(password, hashed):
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:  # e.g. password longer than bcrypt's 72-byte limit
        return False


@auth_bp.get("/csrf")
def csrf():
    return jsonify({"csrf_token": get_csrf_token()})


@auth_bp.post("/register")
def register():
    cfg = current_app.config
    blocked = rate_limited_response("register", request.remote_addr, REGISTER_LIMIT, REGISTER_WINDOW, cfg)
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}
    name, email, password = (data.get("name") or "").strip(), (data.get("email") or "").strip().lower(), data.get("password") or ""
    # Public registration can only create student accounts. Admins create staff accounts through admin workflows.
    problem = validate_password(password)
    if not name or not email or "@" not in email or problem:
        return jsonify({"error": "Validation error", "message": problem or "Name and a valid email are required."}), 400
    limiter.hit("register:%s" % request.remote_addr, REGISTER_WINDOW)
    if db.session.query(User.id).filter(func.lower(User.email) == email).first():
        return jsonify({"error": "Conflict", "message": "An account with this email already exists."}), 409
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user = User(name=name, email=email, password_hash=hashed, role="student")
    db.session.add(user); log_action("user.register", f"email={email}"); db.session.commit()
    return jsonify({"message": "Account created. You can now log in.", "user": user.to_dict()}), 201


@auth_bp.post("/login")
def login():
    cfg = current_app.config
    data = request.get_json(silent=True) or {}
    email, password = (data.get("email") or "").strip().lower(), data.get("password") or ""
    chosen_role = data.get("role")  # optional: the login page lets the user choose a role (thesis Figure 4.1)
    ip = request.remote_addr
    blocked = rate_limited_response("login", "%s:%s" % (ip, email), LOGIN_FAILS, LOGIN_WINDOW, cfg) \
        or rate_limited_response("login-ip", ip, LOGIN_IP_FAILS, LOGIN_WINDOW, cfg)
    if blocked:
        return blocked
    user = User.query.filter(func.lower(User.email) == email).first()
    wrong_role = chosen_role is not None and (not user or chosen_role != user.role)
    if not _check(password, user.password_hash if user else _dummy()) or not user or wrong_role:
        limiter.hit("login:%s:%s" % (ip, email), LOGIN_WINDOW)
        limiter.hit("login-ip:%s" % ip, LOGIN_WINDOW)
        return jsonify({"error": "Unauthorized", "message": "Invalid email or password."}), 401
    limiter.reset("login:%s:%s" % (ip, email))
    login_user(user, remember=False, fresh=True)
    token = rotate_csrf_token()  # new token for the authenticated session
    log_action("user.login", f"user_id={user.id}"); db.session.commit()
    return jsonify({"message": "Login successful.", "user": user.to_dict(), "csrf_token": token}), 200


@auth_bp.post("/logout")
@login_required
def logout():
    log_action("user.logout", f"user_id={current_user.id}"); db.session.commit(); logout_user()
    return jsonify({"message": "Logged out successfully."})


@auth_bp.get("/me")
@login_required
def me():
    return jsonify({"user": current_user.to_dict()})


@auth_bp.post("/change-password")
@login_required
def change_password():
    window = 15 * 60
    blocked = rate_limited_response("chgpw", current_user.id, 5, window, current_app.config)
    if blocked:
        return blocked
    data = request.get_json(silent=True) or {}
    current, new = data.get("current_password") or "", data.get("new_password") or ""
    key = "chgpw:%s" % current_user.id
    if not _check(current, current_user.password_hash):
        limiter.hit(key, window)   # 400, not 401: the client treats 401 as 'session expired'
        return jsonify({"error": "Validation error", "message": "The current password is incorrect."}), 400
    problem = validate_password(new)
    if problem or new == current:
        return jsonify({"error": "Validation error", "message": problem or "The new password must differ from the current one."}), 400
    current_user.password_hash = bcrypt.hashpw(new.encode(), bcrypt.gensalt()).decode()
    current_user.must_change_password = False
    limiter.reset(key)
    log_action("user.password_change", f"user_id={current_user.id}")
    db.session.commit()
    return jsonify({"message": "Password changed."})
