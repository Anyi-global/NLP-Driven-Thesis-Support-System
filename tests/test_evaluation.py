"""Evaluation metrics: hand-computed cases plus a cross-check against scikit-learn when it is installed."""
import random
import unittest

from app.utils.evaluation import (prf, score_reviewed, cohen_kappa, quadratic_weighted_kappa, bootstrap_ci, to_ordinal, time_saving)


class PrecisionRecall(unittest.TestCase):
    def test_hand_computed(self):
        m = prf(tp=85, fp=15, fn=22)
        self.assertAlmostEqual(m["precision"], 0.85)
        self.assertAlmostEqual(m["recall"], 85 / 107)
        self.assertAlmostEqual(m["f1"], 2 * 0.85 * (85 / 107) / (0.85 + 85 / 107))

    def test_undefined_ratios_are_none_not_zero(self):
        self.assertIsNone(prf(0, 0, 5)["precision"])
        self.assertIsNone(prf(0, 5, 0)["recall"])

    def test_scoring_reviewed_sheet(self):
        docs = [{"document": "a", "findings": [{"rule_key": "CITE-002", "verdict": "correct"}, {"rule_key": "CITE-002", "verdict": "incorrect"},
                                              {"rule_key": "FMT-001", "verdict": "correct"}],
                 "missed": [{"rule_key": "FMT-001"}, {"rule_key": "CITE-002"}]},
                {"document": "b", "findings": [{"rule_key": "CITE-002", "verdict": "correct"}], "missed": []}]
        cat = {"CITE-002": "citations", "FMT-001": "formatting"}.get
        r = score_reviewed(docs, cat)
        self.assertEqual((r["citations"]["tp"], r["citations"]["fp"], r["citations"]["fn"]), (2, 1, 1))
        self.assertEqual((r["formatting"]["tp"], r["formatting"]["fp"], r["formatting"]["fn"]), (1, 0, 1))
        self.assertEqual((r["OVERALL"]["tp"], r["OVERALL"]["fp"], r["OVERALL"]["fn"]), (3, 1, 2))

    def test_unreviewed_findings_are_refused(self):
        with self.assertRaises(ValueError):
            score_reviewed([{"document": "a", "findings": [{"rule_key": "X", "verdict": None}]}], lambda k: "c")


class Agreement(unittest.TestCase):
    def test_perfect_and_chance_level(self):
        a = [1, 2, 3, 1, 2, 3]
        self.assertEqual(cohen_kappa(a, a), 1.0)
        self.assertEqual(quadratic_weighted_kappa(a, a), 1.0)
        self.assertAlmostEqual(cohen_kappa([1, 1, 2, 2], [1, 2, 1, 2]), 0.0)

    def test_qwk_penalises_distant_disagreement_more(self):
        base = [1, 2, 3, 4, 5, 1, 2, 3, 4, 5]
        near = base[:-1] + [4]     # one rating off by 1
        far = base[:-1] + [1]      # one rating off by 4
        self.assertGreater(quadratic_weighted_kappa(base, near, [1, 2, 3, 4, 5]), quadratic_weighted_kappa(base, far, [1, 2, 3, 4, 5]))

    def test_matches_scikit_learn(self):
        try:
            from sklearn.metrics import cohen_kappa_score
        except ImportError:
            self.skipTest("scikit-learn not installed")
        rng = random.Random(7)
        for _ in range(25):
            n = rng.randint(15, 80)
            a = [rng.randint(1, 5) for _ in range(n)]
            b = [min(5, max(1, x + rng.choice([-1, 0, 0, 0, 1, 2]))) for x in a]
            labels = [1, 2, 3, 4, 5]
            self.assertAlmostEqual(cohen_kappa(a, b), cohen_kappa_score(a, b), places=9)
            self.assertAlmostEqual(quadratic_weighted_kappa(a, b, labels), cohen_kappa_score(a, b, weights="quadratic", labels=labels), places=9)

    def test_bootstrap_is_reproducible_and_brackets_estimate(self):
        a = [1, 2, 3, 2, 1, 3, 2, 2, 3, 1, 2, 3, 1, 1, 2, 3, 3, 2, 1, 2]
        b = [1, 2, 3, 2, 2, 3, 2, 1, 3, 1, 2, 3, 1, 2, 2, 3, 3, 2, 1, 3]
        lo, hi = bootstrap_ci(cohen_kappa, a, b)
        self.assertEqual((lo, hi), bootstrap_ci(cohen_kappa, a, b))
        self.assertLessEqual(lo, cohen_kappa(a, b)); self.assertGreaterEqual(hi, cohen_kappa(a, b))

    def test_severity_words_map_to_scale(self):
        self.assertEqual([to_ordinal(x) for x in ("low", "Medium", "HIGH", "4", 2.0)], [1, 2, 3, 4, 2])

    def test_mismatched_lengths_refused(self):
        with self.assertRaises(ValueError):
            cohen_kappa([1, 2], [1])


class TimeSaving(unittest.TestCase):
    def test_numbers(self):
        r = time_saving([(60, 30), (40, 30), (20, 10)])
        self.assertAlmostEqual(r["aggregate_saving_percent"], 50 / 120 * 100)
        self.assertAlmostEqual(r["mean_task_saving_percent"], (50 + 25 + 50) / 3)

    def test_rejects_zero_manual_time(self):
        with self.assertRaises(ValueError):
            time_saving([(0, 5)])


if __name__ == "__main__":
    unittest.main()
