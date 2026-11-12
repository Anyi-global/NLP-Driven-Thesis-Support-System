from functools import wraps
from flask import jsonify
from flask_login import current_user, login_required
from ..extensions import db
from ..models.feedback import AuditLog

def role_required(*roles):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role not in roles:
                return jsonify({"error": "Forbidden", "message": "Your account role cannot access this resource."}), 403
            return view(*args, **kwargs)
        return wrapped
    return decorator

def log_action(action, details=None):
    from flask import request
    from flask_login import current_user
    user_id = current_user.id if current_user.is_authenticated else None
    db.session.add(AuditLog(user_id=user_id, action=action, details=details, ip_address=request.remote_addr))
