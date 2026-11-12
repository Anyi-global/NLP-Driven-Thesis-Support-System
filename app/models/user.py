from flask_login import UserMixin
from ..extensions import db, login_manager

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="student")
    created_at = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)
    # For students: the supervisor assigned to review their work. Supervisors only see assigned students.
    must_change_password = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    supervisor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True, index=True)
    supervisor = db.relationship("User", remote_side=[id], backref="assigned_students")
    theses = db.relationship("Thesis", back_populates="student", cascade="all, delete-orphan")

    def to_dict(self):
        return {"id": self.id, "name": self.name, "email": self.email, "role": self.role,
                "supervisor_id": self.supervisor_id, "must_change_password": bool(self.must_change_password)}

@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None
