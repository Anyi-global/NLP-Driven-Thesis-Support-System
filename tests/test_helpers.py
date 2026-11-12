"""Pure helpers behind the new endpoints."""
import unittest

from app.utils.metrics import diff_findings, count_items, metrics_to_csv
from app.utils.payloads import parse_bulk_review, MAX_BULK


class Progress(unittest.TestCase):
    def test_revision_diff(self):
        d = diff_findings(["A | x", "B | y", "C | z"], ["B | y", "D | w"])
        self.assertEqual((d["resolved"], d["new"], d["persisting"]), (["A | x", "C | z"], ["D | w"], 1))

    def test_students_only_count_visible_items(self):
        rows = [("compliance", "low", "low", "pending"), ("quality", "high", "high", "pending"),
                ("quality", "high", "medium", "accepted"), ("quality", "high", "high", "rejected")]
        self.assertEqual(count_items(rows, for_student=True)["total"], 2)       # rule item + accepted AI item
        self.assertEqual(count_items(rows, for_student=False)["total"], 4)
        self.assertEqual(count_items(rows, True)["by_severity"], {"low": 1, "medium": 1})


class BulkPayload(unittest.TestCase):
    def test_twenty_fixes_ok(self):                                              # thesis test T-09
        ids, action, err = parse_bulk_review({"item_ids": list(range(1, 21)), "action": "accept"})
        self.assertEqual((len(ids), action, err), (20, "accept", None))

    def test_rejections(self):
        for bad in ({"item_ids": [1], "action": "edit"}, {"item_ids": [], "action": "accept"}, {"item_ids": ["1"], "action": "accept"},
                    {"item_ids": [True], "action": "accept"}, {"item_ids": list(range(MAX_BULK + 1)), "action": "accept"}, None):
            self.assertIsNotNone(parse_bulk_review(bad)[2], bad)

    def test_duplicates_collapsed(self):
        self.assertEqual(parse_bulk_review({"item_ids": [3, 3, 4], "action": "reject"})[0], [3, 4])


class UsageCsv(unittest.TestCase):
    def test_flattening(self):
        csv_text = metrics_to_csv({"users": {"by_role": {"student": 3}}, "theses": {"total": 5, "avg": None}})
        self.assertEqual(csv_text.splitlines(), ["metric,value", "users.by_role.student,3", "theses.total,5", "theses.avg,"])


if __name__ == "__main__":
    unittest.main()
