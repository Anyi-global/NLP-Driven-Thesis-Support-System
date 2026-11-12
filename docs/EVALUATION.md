# Evaluating the system (thesis Objective 5)

Any figure reported in Chapter 4 or 5 (precision, recall, F1, QWK, Cohen's kappa, time saving, "all tests passed")
should come from a documented run of the tools below, with the counts and sample size reported next to the ratios.
Nothing in this repository ships pre-computed results.

## 1. Compliance checker: precision, recall, F1
1. Collect a set of real theses (with consent, anonymised). Aim for enough that each rule category has a meaningful
   number of findings; very small samples give unstable ratios.
2. `python scripts/evaluate_compliance.py make-sheet THESES_FOLDER review.json`
3. Annotators set `"verdict": "correct"` or `"incorrect"` on every finding and list real problems the checker missed under
   `"missed"`. **For an unbiased recall, annotate each thesis blind (before reading the system output) and add those
   problems to `missed` afterwards.** Reviewing only the system's output understates the number of false negatives.
4. `python scripts/evaluate_compliance.py score review.json report.csv` prints TP, FP, FN, precision, recall and F1 per
   rule category and overall (micro-averaged). Ratios with no denominator are shown as n/a, not 0.
Use two annotators on a subset and report their agreement (below) so the reference labels are credible.

## 2. AI evaluator: agreement with a human rater (QWK, kappa)
CSV `item_id,system_rating,human_rating` (integers, or low/medium/high). Then:
`python scripts/evaluate_agreement.py ratings.csv` prints exact agreement, Cohen's kappa and quadratic weighted kappa
with seeded 95% bootstrap intervals. It warns below 30 items. Metric code is checked against scikit-learn in
`tests/test_evaluation.py`. Run the model for real (set `GEMINI_API_KEY` and `GEMINI_MODEL`); stub runs say nothing about quality.

## 3. Time saving
CSV `task_id,manual_minutes,assisted_minutes` for the SAME thesis section reviewed both ways (counterbalance the order).
`python scripts/evaluate_time.py timings.csv` prints aggregate and per-task saving.

## 4. The twelve functional tests (Chapter 4 table T-01 to T-12)
`tests/test_chapter4_scenarios.py` (no database) and `tests/test_api.py` (end to end) implement them.
T-08 (misaligned objectives flagged) depends on the language model: the stub test only proves the plumbing; the real check is
`LiveGemini`, which runs when `GEMINI_API_KEY` and `GEMINI_MODEL` are set. Model output varies between runs, so record the
actual findings rather than only pass/fail.
