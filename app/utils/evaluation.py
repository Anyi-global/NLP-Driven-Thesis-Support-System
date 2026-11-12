"""Evaluation metrics used in the thesis (Objective 5): precision/recall/F1, Cohen's kappa, quadratic weighted
kappa and time saving. Pure Python so the numbers are reproducible and the code is easy to audit."""
import random
from collections import Counter, defaultdict

SEVERITY_SCALE = {"low": 1, "medium": 2, "high": 3}


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f1 = 2 * p * r / (p + r) if p is not None and r is not None and p + r else None
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": f1}


def score_reviewed(documents, category_of):
    """documents: reviewed annotation file (see docs/EVALUATION.md). Each system finding carries the reviewer's
    verdict ('correct'/'incorrect'); reviewer-found misses are listed under 'missed'.
    Returns per-category and overall (micro) precision/recall/F1. Raises ValueError for unreviewed findings."""
    counts = defaultdict(lambda: Counter())
    for doc in documents:
        for f in doc.get("findings", []):
            verdict = f.get("verdict")
            if verdict not in ("correct", "incorrect"):
                raise ValueError("Unreviewed finding in %s: %s (set verdict to 'correct' or 'incorrect')" % (doc.get("document"), f.get("rule_key")))
            counts[category_of(f["rule_key"])]["tp" if verdict == "correct" else "fp"] += 1
        for m in doc.get("missed", []):
            counts[category_of(m["rule_key"])]["fn"] += 1
    result = {cat: prf(c["tp"], c["fp"], c["fn"]) for cat, c in sorted(counts.items())}
    total = Counter()
    for c in counts.values():
        total.update(c)
    result["OVERALL"] = prf(total["tp"], total["fp"], total["fn"])
    return result


def cohen_kappa(a, b):
    """Unweighted Cohen's kappa for two raters over the same items."""
    _check_pairs(a, b)
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def quadratic_weighted_kappa(a, b, labels=None):
    """QWK for ordinal ratings. `labels` is the ordered list of possible ratings (defaults to the observed range)."""
    _check_pairs(a, b)
    labels = list(labels) if labels else list(range(min(min(a), min(b)), max(max(a), max(b)) + 1))
    idx = {v: i for i, v in enumerate(labels)}
    k, n = len(labels), len(a)
    obs = [[0.0] * k for _ in range(k)]
    for x, y in zip(a, b):
        obs[idx[x]][idx[y]] += 1
    ha, hb = [sum(r) for r in obs], [sum(obs[i][j] for i in range(k)) for j in range(k)]
    num = den = 0.0
    for i in range(k):
        for j in range(k):
            w = ((i - j) ** 2) / ((k - 1) ** 2) if k > 1 else 0
            num += w * obs[i][j]
            den += w * ha[i] * hb[j] / n
    return 1.0 if den == 0 else 1 - num / den


def bootstrap_ci(metric, a, b, n_boot=2000, seed=1, alpha=0.05):
    """Percentile bootstrap confidence interval (seeded, so reruns give identical numbers)."""
    rng, n, vals = random.Random(seed), len(a), []
    for _ in range(n_boot):
        ix = [rng.randrange(n) for _ in range(n)]
        try:
            vals.append(metric([a[i] for i in ix], [b[i] for i in ix]))
        except ZeroDivisionError:
            continue
    vals.sort()
    return (vals[int(alpha / 2 * len(vals))], vals[int((1 - alpha / 2) * len(vals)) - 1]) if vals else (None, None)


def to_ordinal(value):
    if isinstance(value, str) and value.strip().lower() in SEVERITY_SCALE:
        return SEVERITY_SCALE[value.strip().lower()]
    return int(float(value))


def time_saving(pairs):
    """pairs: (manual_minutes, assisted_minutes) per task. Returns aggregate and mean per-task saving (%)."""
    pairs = [(float(m), float(a)) for m, a in pairs]
    if not pairs or any(m <= 0 for m, _ in pairs):
        raise ValueError("Every task needs a positive manual time.")
    per_task = [(m - a) / m * 100 for m, a in pairs]
    return {"tasks": len(pairs), "aggregate_saving_percent": (sum(m for m, _ in pairs) - sum(a for _, a in pairs)) / sum(m for m, _ in pairs) * 100,
            "mean_task_saving_percent": sum(per_task) / len(per_task), "min_task_saving_percent": min(per_task), "max_task_saving_percent": max(per_task)}


def _check_pairs(a, b):
    if len(a) != len(b) or not a:
        raise ValueError("Both raters must rate the same, non-empty set of items.")
