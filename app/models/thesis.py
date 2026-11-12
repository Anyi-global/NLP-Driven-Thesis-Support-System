from datetime import datetime, timezone
from ..extensions import db

STATUSES = ("uploaded", "queued", "processing", "analyzed", "analysis_failed")

class Thesis(db.Model):
    __table_args__ = (db.UniqueConstraint("root_thesis_id", "version", name="uq_thesis_chain_version"),)
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    title = db.Column(db.String(300), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(600), nullable=False)
    file_type = db.Column(db.String(10), nullable=False)
    version = db.Column(db.Integer, nullable=False, default=1)
    parent_thesis_id = db.Column(db.Integer, db.ForeignKey("thesis.id"), nullable=True)
    # First upload in a revision chain (points to itself for version 1). Versions are numbered within the chain.
    root_thesis_id = db.Column(db.Integer, db.ForeignKey("thesis.id"), nullable=True, index=True)
    status = db.Column(db.String(30), nullable=False, default="uploaded")
    status_updated_at = db.Column(db.DateTime, nullable=True)
    analysis_note = db.Column(db.Text, nullable=True)
    review_requested_at = db.Column(db.DateTime, nullable=True)   # student pressed 'send to supervisor'
    created_at = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)
    extracted_text = db.Column(db.Text, nullable=True)
    student = db.relationship("User", back_populates="theses", foreign_keys=[student_id])
    compliance_reports = db.relationship("ComplianceReport", back_populates="thesis", cascade="all, delete-orphan")
    quality_reports = db.relationship("QualityReport", back_populates="thesis", cascade="all, delete-orphan")
    feedback_items = db.relationship("FeedbackItem", back_populates="thesis", cascade="all, delete-orphan")

    def set_status(self, status, note=None):
        self.status = status
        self.analysis_note = note[:1000] if note else None
        self.status_updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    def to_dict(self):
        return {"id": self.id, "student_id": self.student_id, "title": self.title,
                "original_filename": self.original_filename, "file_type": self.file_type,
                "version": self.version, "parent_thesis_id": self.parent_thesis_id,
                "root_thesis_id": self.root_thesis_id, "status": self.status, "analysis_note": self.analysis_note,
                "review_requested_at": self.review_requested_at.isoformat() if self.review_requested_at else None, "created_at": self.created_at.isoformat() if self.created_at else None}
