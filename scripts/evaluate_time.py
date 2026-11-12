"""Supervisor time saving: manual review versus system-assisted review of the SAME thesis section.

Input CSV with a header row: task_id,manual_minutes,assisted_minutes
    python scripts/evaluate_time.py timings.csv
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.utils.evaluation import time_saving  # noqa: E402


def main(path):
    with open(path, newline="", encoding="utf-8") as fh:
        pairs = [(r["manual_minutes"], r["assisted_minutes"]) for r in csv.DictReader(fh)]
    r = time_saving(pairs)
    print("Tasks timed: %d" % r["tasks"])
    print("Aggregate time saving: %.1f%%   Mean per-task saving: %.1f%%   Range: %.1f%% to %.1f%%" % (
        r["aggregate_saving_percent"], r["mean_task_saving_percent"], r["min_task_saving_percent"], r["max_task_saving_percent"]))


if __name__ == "__main__":
    main(sys.argv[1]) if len(sys.argv) == 2 else print(__doc__)
