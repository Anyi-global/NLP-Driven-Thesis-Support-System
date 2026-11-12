"""Lightweight, idempotent upgrade for databases created before supervisor assignment,
revision chains and background jobs. (Flask-Migrate is installed if you prefer real migrations.)"""
from sqlalchemy import inspect, text

NEW_COLUMNS = {
    "user": [("supervisor_id", "INTEGER"), ("must_change_password", "BOOLEAN NOT NULL DEFAULT 0")],
    "feedback_item": [("student_status", "VARCHAR(20)")],
    "thesis": [("root_thesis_id", "INTEGER"), ("status_updated_at", "DATETIME"), ("analysis_note", "TEXT"), ("review_requested_at", "DATETIME")],
}


def compute_chains(rows):
    """rows: iterable of (id, parent_id). Returns {id: (root_id, version)} following parent links."""
    parent = {i: p for i, p in rows}
    out = {}
    for i in parent:
        depth, cur, seen = 1, i, set()
        while parent.get(cur) is not None and parent[cur] in parent and cur not in seen:
            seen.add(cur); cur = parent[cur]; depth += 1
        out[i] = (cur, depth)
    return out


def ensure_schema(db):
    insp = inspect(db.engine)
    added = []
    for table, cols in NEW_COLUMNS.items():
        if table not in insp.get_table_names():
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        for name, typ in cols:
            if name not in existing:
                db.session.execute(text('ALTER TABLE "%s" ADD COLUMN %s %s' % (table, name, typ)))
                added.append("%s.%s" % (table, name))
    db.session.commit()
    if "thesis" in insp.get_table_names():
        rows = db.session.execute(text("SELECT id, parent_thesis_id FROM thesis")).fetchall()
        for tid, (root, version) in compute_chains([(r[0], r[1]) for r in rows]).items():
            db.session.execute(text("UPDATE thesis SET root_thesis_id = :r, version = :v WHERE id = :i "
                                    "AND (root_thesis_id IS NULL)"), {"r": root, "v": version, "i": tid})
        db.session.commit()
    return added
