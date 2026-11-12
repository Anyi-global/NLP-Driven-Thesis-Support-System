from flask import Blueprint, request, jsonify, Response
from ..extensions import db
from ..models.user import User
from ..models.feedback import ComplianceRule, AuditLog
from ..utils.security import role_required, log_action
from ..utils.validators import validate_password, generate_temp_password
from ..services.usage_service import usage_metrics
from ..utils.metrics import metrics_to_csv

admin_bp=Blueprint("admin",__name__)

@admin_bp.get("/rules")
@role_required("admin")
def list_rules():
    rules=ComplianceRule.query.order_by(ComplianceRule.rule_key).all()
    return jsonify({"rules":[{"id":r.id,"rule_key":r.rule_key,"category":r.category,"description":r.description,"severity":r.severity,"pattern":r.pattern,"suggestion":r.suggestion,"active":r.active,"manual_reference":r.manual_reference} for r in rules]})

@admin_bp.put("/rules/<rule_key>")
@role_required("admin")
def update_rule(rule_key):
    rule=ComplianceRule.query.filter_by(rule_key=rule_key).first()
    if not rule: return jsonify({"error":"Not found","message":"Rule not found. Seed or create it before updating."}),404
    data=request.get_json(silent=True) or {}
    for field in ("category","description","severity","pattern","suggestion","active","manual_reference"):
        if field in data: setattr(rule,field,data[field])
    if not isinstance(rule.active,bool) or not str(rule.description).strip() or not str(rule.suggestion).strip():
        return jsonify({"error":"Validation error","message":"description and suggestion cannot be empty, and active must be true or false."}),400
    if rule.severity not in {"low","medium","high"}: return jsonify({"error":"Validation error","message":"severity must be low, medium, or high."}),400
    log_action("admin.rule.update",f"rule_key={rule_key}"); db.session.commit()
    return jsonify({"message":"Rule updated.","rule_key":rule.rule_key})

@admin_bp.post("/users")
@role_required("admin")
def create_staff_user():
    import bcrypt
    data=request.get_json(silent=True) or {}; name=(data.get("name") or "").strip(); email=(data.get("email") or "").strip().lower(); password=data.get("password") or ""; role=data.get("role")
    problem=validate_password(password)
    if not name or not email or problem or role not in {"supervisor","admin"}:
        return jsonify({"error":"Validation error","message":problem or "name, email and role (supervisor/admin) are required."}),400
    if User.query.filter_by(email=email).first(): return jsonify({"error":"Conflict","message":"Email already exists."}),409
    user=User(name=name,email=email,password_hash=bcrypt.hashpw(password.encode(),bcrypt.gensalt()).decode(),role=role)
    db.session.add(user); log_action("admin.user.create",f"email={email},role={role}"); db.session.commit()
    return jsonify({"user":user.to_dict()}),201

@admin_bp.get("/logs")
@role_required("admin")
def logs():
    limit=min(max(request.args.get("limit",100,type=int),1),500)
    rows=AuditLog.query.order_by(AuditLog.created_at.desc()).limit(limit).all()
    return jsonify({"logs":[{"id":x.id,"user_id":x.user_id,"action":x.action,"details":x.details,"ip_address":x.ip_address,"created_at":x.created_at.isoformat() if x.created_at else None} for x in rows]})

@admin_bp.get("/users")
@role_required("admin")
def list_users():
    role=request.args.get("role")
    q=User.query
    if role in {"student","supervisor","admin"}: q=q.filter_by(role=role)
    return jsonify({"users":[u.to_dict() for u in q.order_by(User.role,User.name).all()]})

@admin_bp.put("/students/<int:student_id>/supervisor")
@role_required("admin")
def assign_supervisor(student_id):
    """Assign (or with supervisor_id=null, unassign) a student's supervisor. Supervisors only see assigned students."""
    student=db.session.get(User,student_id)
    if not student or student.role!="student": return jsonify({"error":"Not found","message":"Student not found."}),404
    data=request.get_json(silent=True) or {}
    if "supervisor_id" not in data: return jsonify({"error":"Validation error","message":"supervisor_id is required (use null to unassign)."}),400
    sid=data["supervisor_id"]
    if sid is not None:
        sup=db.session.get(User,sid) if isinstance(sid,int) else None
        if not sup or sup.role!="supervisor": return jsonify({"error":"Validation error","message":"supervisor_id must belong to a supervisor account."}),400
    student.supervisor_id=sid
    log_action("admin.supervisor.assign",f"student_id={student.id},supervisor_id={sid}"); db.session.commit()
    return jsonify({"message":"Supervisor assignment updated.","user":student.to_dict()})

@admin_bp.get("/reports/usage")
@role_required("admin")
def usage_report():
    """The admin 'Reports' menu: usage statistics as JSON, or CSV with ?format=csv."""
    metrics=usage_metrics(days=min(max(request.args.get("days",30,type=int),1),365))
    if request.args.get("format")=="csv":
        return Response(metrics_to_csv(metrics),mimetype="text/csv",headers={"Content-Disposition":"attachment; filename=usage_report.csv"})
    return jsonify(metrics)

@admin_bp.post("/users/<int:user_id>/reset-password")
@role_required("admin")
def reset_password(user_id):
    """Issue a one-time temporary password; the user must choose a new one before using anything else."""
    import bcrypt
    user=db.session.get(User,user_id)
    if not user: return jsonify({"error":"Not found","message":"User not found."}),404
    temp=generate_temp_password()
    user.password_hash=bcrypt.hashpw(temp.encode(),bcrypt.gensalt()).decode(); user.must_change_password=True
    log_action("admin.password.reset",f"user_id={user.id}"); db.session.commit()
    return jsonify({"message":"Password reset. Share the temporary password securely; it is shown only once.","temporary_password":temp,"user":user.to_dict()})
