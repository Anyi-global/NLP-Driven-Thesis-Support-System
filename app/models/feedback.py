from ..extensions import db

class ComplianceRule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    rule_key = db.Column(db.String(100), unique=True, nullable=False)
    category = db.Column(db.String(80), nullable=False)
    description = db.Column(db.Text, nullable=False)
    severity = db.Column(db.String(20), nullable=False, default="medium")
    pattern = db.Column(db.Text, nullable=True)
    suggestion = db.Column(db.Text, nullable=False, default="Review this item against the UNIZIK Manual of Style.")
    active = db.Column(db.Boolean, nullable=False, default=True)
    manual_reference = db.Column(db.String(300), nullable=True)

class ComplianceReport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    thesis_id = db.Column(db.Integer, db.ForeignKey("thesis.id"), nullable=False, index=True)
    rule_key = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(80), nullable=False)
    location = db.Column(db.String(300), nullable=True)
    excerpt = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(30), nullable=False, default="open")
    suggestion = db.Column(db.Text, nullable=False)
    severity = db.Column(db.String(20), nullable=False, default="medium")
    manual_reference = db.Column(db.String(300), nullable=True)
    thesis = db.relationship("Thesis", back_populates="compliance_reports")

class QualityReport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    thesis_id = db.Column(db.Integer, db.ForeignKey("thesis.id"), nullable=False, index=True)
    section = db.Column(db.String(120), nullable=True)
    finding = db.Column(db.Text, nullable=False)
    rationale = db.Column(db.Text, nullable=True)
    suggestion = db.Column(db.Text, nullable=True)
    severity = db.Column(db.String(20), nullable=False, default="medium")
    source = db.Column(db.String(40), nullable=False, default="llm")
    thesis = db.relationship("Thesis", back_populates="quality_reports")

class FeedbackItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    thesis_id = db.Column(db.Integer, db.ForeignKey("thesis.id"), nullable=False, index=True)
    source_report_type = db.Column(db.String(30), nullable=False)
    source_report_id = db.Column(db.Integer, nullable=False)
    level = db.Column(db.String(20), nullable=False)  # low or high
    content = db.Column(db.Text, nullable=False)
    suggestion = db.Column(db.Text, nullable=True)
    severity = db.Column(db.String(20), nullable=False, default="medium")
    priority_score = db.Column(db.Integer, nullable=False, default=2)
    status = db.Column(db.String(30), nullable=False, default="pending")
    supervisor_comment = db.Column(db.Text, nullable=True)
    student_status = db.Column(db.String(20), nullable=True)   # student's own 'accepted fix' marker; never the supervisor's decision
    reviewed_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    thesis = db.relationship("Thesis", back_populates="feedback_items")

    def to_dict(self):
        return {"id": self.id, "thesis_id": self.thesis_id, "source_report_type": self.source_report_type,
                "source_report_id": self.source_report_id, "level": self.level, "content": self.content,
                "suggestion": self.suggestion, "severity": self.severity, "priority_score": self.priority_score,
                "status": self.status, "supervisor_comment": self.supervisor_comment, "student_status": self.student_status,
                "reviewed_by": self.reviewed_by, "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None}

class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    action = db.Column(db.String(120), nullable=False)
    details = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(80), nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)
