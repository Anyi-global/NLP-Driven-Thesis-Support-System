"""Agreement between the system's (LLM) ratings and a human rater: Cohen's kappa and quadratic weighted kappa.

Input CSV with a header row: item_id,system_rating,human_rating
Ratings may be integers (e.g. 1-5 quality scores) or low/medium/high (mapped to 1/2/3).
    python scripts/evaluate_agreement.py ratings.csv
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.utils.evaluation import cohen_kappa, quadratic_weighted_kappa, bootstrap_ci, to_ordinal  # noqa: E402


def main(path):
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    a = [to_ordinal(r["system_rating"]) for r in rows]
    b = [to_ordinal(r["human_rating"]) for r in rows]
    labels = list(range(min(a + b), max(a + b) + 1))
    kappa, qwk = cohen_kappa(a, b), quadratic_weighted_kappa(a, b, labels)
    k_lo, k_hi = bootstrap_ci(cohen_kappa, a, b)
    q_lo, q_hi = bootstrap_ci(lambda x, y: quadratic_weighted_kappa(x, y, labels), a, b)
    print("Items rated: %d   Exact agreement: %.1f%%" % (len(a), 100 * sum(x == y for x, y in zip(a, b)) / len(a)))
    print("Cohen's kappa: %.3f  (95%% bootstrap CI %.3f to %.3f)" % (kappa, k_lo, k_hi))
    print("QWK:           %.3f  (95%% bootstrap CI %.3f to %.3f)" % (qwk, q_lo, q_hi))
    if len(a) < 30:
        print("WARNING: fewer than 30 rated items; these estimates are very unstable.")


if __name__ == "__main__":
    main(sys.argv[1]) if len(sys.argv) == 2 else print(__doc__)
