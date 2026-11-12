import os
from datetime import timedelta
from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException
from .config import Config, INSECURE_SECRETS
from .extensions import db, login_manager, migrate


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    if not app.config.get("TESTING") and not app.config.get("DEBUG") and app.config["SECRET_KEY"] in INSECURE_SECRETS:
        raise RuntimeError("SECRET_KEY is unset or a placeholder. Set a long random SECRET_KEY in .env "
                           "(python -c \"import secrets; print(secrets.token_hex(32))\").")
    if app.config.get("TRUSTED_PROXY_COUNT", 0) > 0:
        from werkzeug.middleware.proxy_fix import ProxyFix
        n = app.config["TRUSTED_PROXY_COUNT"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)

    from . import models  # noqa: F401
    from .nlp import linguistics
    linguistics.configure(app.config)
    linguistics.preload_core_engines()
    from .routes.auth_routes import auth_bp
    from .routes.student_routes import student_bp
    from .routes.supervisor_routes import supervisor_bp
    from .routes.admin_routes import admin_bp
    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(student_bp, url_prefix="/api/student")
    app.register_blueprint(supervisor_bp, url_prefix="/api/supervisor")
    app.register_blueprint(admin_bp, url_prefix="/api/admin")

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "service": "UNIZIK NLP Thesis Support API"})

    @app.errorhandler(HTTPException)
    def handle_http_error(error):
        return jsonify({"error": error.name, "message": error.description}), error.code

    @app.errorhandler(Exception)
    def handle_unexpected_error(error):
        app.logger.exception("Unhandled backend error")
        return jsonify({"error": "Internal Server Error", "message": "The request could not be completed."}), 500

    @app.before_request
    def enforce_session_timeout():
        from flask_login import current_user, logout_user
        from flask import session
        from datetime import datetime, timezone
        if current_user.is_authenticated:
            now = datetime.now(timezone.utc).timestamp()
            last = session.get("last_activity", now)
            if now - last > app.config["SESSION_TIMEOUT_MINUTES"] * 60:
                logout_user()
                session.clear()
                if request.path.startswith("/api/"):
                    return jsonify({"error": "Session expired", "message": "Please log in again."}), 401
            session["last_activity"] = now

    @app.before_request
    def require_password_change():
        from flask import jsonify
        from flask_login import current_user
        allowed = {"/api/auth/change-password", "/api/auth/logout", "/api/auth/me", "/api/auth/csrf"}
        if (current_user.is_authenticated and getattr(current_user, "must_change_password", False)
                and request.path.startswith("/api/") and request.path not in allowed):
            return jsonify({"error": "Password change required", "message": "Set a new password before continuing."}), 403

    # Registered after the timeout check so an expired session gets 401, not a CSRF 403.
    from .utils.web_security import init_web_security
    init_web_security(app)

    # Serve the HTML/CSS/JavaScript frontend (./frontend) from the same origin as the API, so cookies and CSRF just work.
    from flask import send_from_directory
    frontend_dir = os.path.join(os.path.dirname(app.root_path), "frontend")

    @app.get("/")
    def frontend_index():
        return send_from_directory(frontend_dir, "index.html")

    @app.get("/app/<path:filename>")
    def frontend_asset(filename):
        return send_from_directory(frontend_dir, filename)
    return app
