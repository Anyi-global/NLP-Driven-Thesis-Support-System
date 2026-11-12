"""Measure compliance-checker precision/recall/F1 (thesis Table 4.x, Objective 5).

Step 1 - generate review sheets from real theses:
    python scripts/evaluate_compliance.py make-sheet THESES_FOLDER review.json
Step 2 - an annotator opens review.json and, for every finding, sets "verdict" to "correct" or "incorrect".
         Real problems the checker did NOT report go in the document's "missed" list ({"rule_key": ..., "location": ...}).
         For an unbiased recall, annotate each thesis blind BEFORE looking at the system output.
Step 3 - compute the metrics:
    python scripts/evaluate_compliance.py score review.json [report.csv]
"""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.nlp.document_parser import parse_document  # noqa: E402
from app.nlp.rule_engine import run_compliance_checks, load_rules  # noqa: E402
from app.utils.evaluation import score_reviewed  # noqa: E402


def category_map():
    return {r["id"]: r.get("category", "general") for r in load_rules()}


def make_sheet(folder, out):
    docs = []
    for path in sorted(Path(folder).glob("*")):
        if path.suffix.lower() not in (".docx", ".pdf"):
            continue
        findings = run_compliance_checks(parse_document(str(path)))
        docs.append({"document": path.name,
                     "findings": [{"rule_key": f["rule_key"], "location": f["location"], "excerpt": f["excerpt"], "verdict": None} for f in findings],
                     "missed": []})
    Path(out).write_text(json.dumps({"documents": docs}, indent=2, ensure_ascii=False), encoding="utf-8")
    print("Wrote %s for %d documents. Annotators: fill every 'verdict' and list misses under 'missed'." % (out, len(docs)))


def score(sheet, report=None):
    cats = category_map()
    docs = json.loads(Path(sheet).read_text(encoding="utf-8"))["documents"]
    result = score_reviewed(docs, lambda k: cats.get(k, "general"))
    fmt = lambda v: "n/a" if v is None else "%.3f" % v
    print("%-18s %5s %5s %5s %9s %7s %6s" % ("Category", "TP", "FP", "FN", "Precision", "Recall", "F1"))
    for cat, m in result.items():
        print("%-18s %5d %5d %5d %9s %7s %6s" % (cat, m["tp"], m["fp"], m["fn"], fmt(m["precision"]), fmt(m["recall"]), fmt(m["f1"])))
    print("\nDocuments scored: %d. Report these counts alongside the ratios." % len(docs))
    if report:
        with open(report, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["category", "tp", "fp", "fn", "precision", "recall", "f1"])
            for cat, m in result.items():
                w.writerow([cat, m["tp"], m["fp"], m["fn"], m["precision"], m["recall"], m["f1"]])


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) >= 3 and args[0] == "make-sheet":
        make_sheet(args[1], args[2])
    elif len(args) >= 2 and args[0] == "score":
        score(args[1], args[2] if len(args) > 2 else None)
    else:
        print(__doc__)
