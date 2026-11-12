"""Create tables, upgrade older databases, and import the starter rules.

Usage:  python scripts/initialize_db.py [--refresh-rules]
--refresh-rules overwrites description/severity/suggestion of existing rules from the JSON file
(otherwise rows already in the DB, including stale text from older versions, are kept).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models.feedback import ComplianceRule  # noqa: E402
from app.utils.schema import ensure_schema  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
refresh = "--refresh-rules" in sys.argv
app = create_app()
with app.app_context():
    db.create_all()
    added = ensure_schema(db)
    if added:
        print("Upgraded schema, added columns: " + ", ".join(added))
    rules = json.loads((ROOT / "data" / "rules" / "unizik_rules.json").read_text(encoding="utf-8"))
    created = updated = 0
    for item in rules:
        rule = ComplianceRule.query.filter_by(rule_key=item["id"]).first()
        fields = dict(category=item["category"], description=item["description"], severity=item.get("severity", "medium"),
                      pattern=item.get("pattern"), suggestion=item.get("suggestion", "Review against the official manual."),
                      manual_reference=item.get("manual_reference"))
        if not rule:
            db.session.add(ComplianceRule(rule_key=item["id"], active=item.get("active", True), **fields)); created += 1
        elif refresh:
            for k, v in fields.items():
                setattr(rule, k, v)
            updated += 1
    if refresh:
        known = {i["id"] for i in rules}
        for rule in ComplianceRule.query.all():
            if rule.rule_key not in known and rule.active:
                rule.active = False
                print("Deactivated obsolete rule: " + rule.rule_key)
    db.session.commit()
    print("Database ready. Rules created: %d, refreshed: %d." % (created, updated))
