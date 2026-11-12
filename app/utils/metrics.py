"""Pure helpers behind the progress and usage endpoints (no database imports, so they are unit-testable)."""
import csv
import io
from collections import Counter

from .access import student_can_see


def diff_findings(previous, current):
    """Compare two collections of finding keys."""
    prev, cur = set(previous), set(current)
    return {"resolved": sorted(prev - cur), "new": sorted(cur - prev), "persisting": len(prev & cur)}


def count_items(rows, for_student):
    """rows: (source_report_type, level, severity, status). Students only count what they are allowed to see."""
    rows = [r for r in rows if not for_student or student_can_see(r[0], r[3])]
    return {"total": len(rows), "by_level": dict(Counter(r[1] for r in rows)),
            "by_severity": dict(Counter(r[2] for r in rows))}


def metrics_to_csv(metrics):
    """Flatten the nested metrics dict into 'metric,value' rows."""
    rows = []

    def walk(prefix, node):
        if isinstance(node, dict):
            for k, v in node.items():
                walk("%s.%s" % (prefix, k) if prefix else str(k), v)
        else:
            rows.append((prefix, "" if node is None else node))
    walk("", metrics)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["metric", "value"])
    w.writerows(rows)
    return buf.getvalue()
