"""
Propose cutlines from an existing summary CSV.

Usage:
    python3 analyze_cutlines.py path/to/<name>_summary.csv [--out cutline_report.md]

For each current Level it computes min / Q1 / median / Q3 / max / IQR of the
"Total Average" column, then proposes a cutline between every pair of
adjacent levels as

    0.7 * median(upper level) + 0.3 * median(lower level)

and lists who would move UP / DOWN / STAY under those proposed cutlines.
This is a descriptive statistic, not a model: nothing is fitted or learned.
The report is written next to the input file unless --out is given.

Works on the output of either generate_summary.py variant.  Standard
library only.  Python 3.9+.
"""

import argparse
import csv
import os
import sys
from collections import defaultdict

UPPER_WEIGHT = 0.7   # weight of the upper level's median
LOWER_WEIGHT = 0.3   # weight of the lower level's median
LEVEL_ORDER = ["2", "3", "4", "P"]  # best to lowest


def percentile(sorted_data, p):
    """Linear-interpolation percentile (same convention as numpy's default)."""
    n = len(sorted_data)
    k = (n - 1) * p / 100
    f = int(k)
    c = f + 1
    if c >= n:
        return sorted_data[-1]
    return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])


def read_summary(path):
    """Read a summary CSV, skipping the '=== ... ===' section header rows."""
    rows_by_level = defaultdict(list)
    students = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["반명"].startswith("==="):
                continue
            level = row["Level"].strip()
            total = row["Total Average"].strip()
            if level and total:
                total_f = float(total)
                rows_by_level[level].append(total_f)
                students.append({
                    "name": row["원생명"].strip(),
                    "class": row["반명"].strip(),
                    "level": level,
                    "total": total_f,
                    "test_count": row.get("Test Count", "").strip(),
                })
    return rows_by_level, students


def main(argv=None):
    parser = argparse.ArgumentParser(description="Propose level cutlines from a summary CSV.")
    parser.add_argument("summary_csv", help="a *_summary.csv produced by generate_summary.py")
    parser.add_argument("--out", help="report path (default: cutline_report.md next to the input)")
    args = parser.parse_args(argv)

    input_file = args.summary_csv
    report_file = args.out or os.path.join(os.path.dirname(os.path.abspath(input_file)), "cutline_report.md")

    rows_by_level, students = read_summary(input_file)
    levels_present = [lv for lv in LEVEL_ORDER if lv in rows_by_level]
    level_rank = {lv: i for i, lv in enumerate(LEVEL_ORDER)}
    if len(levels_present) < 2:
        print("Need at least two levels with data to propose cutlines.")
        return 1

    stats = {}
    for lv in levels_present:
        data = sorted(rows_by_level[lv])
        q1, med, q3 = percentile(data, 25), percentile(data, 50), percentile(data, 75)
        stats[lv] = {"n": len(data), "min": data[0], "max": data[-1],
                     "q1": q1, "median": med, "q3": q3, "iqr": q3 - q1}

    # cutlines[lv] = minimum total average to be placed in that level
    cutlines = {}
    for upper_lv, lower_lv in zip(levels_present, levels_present[1:]):
        cutlines[upper_lv] = stats[upper_lv]["median"] * UPPER_WEIGHT + stats[lower_lv]["median"] * LOWER_WEIGHT

    def target_level(score):
        for lv in levels_present:
            if lv in cutlines and score >= cutlines[lv]:
                return lv
        return levels_present[-1]

    def status(current_lv, target_lv):
        curr, tgt = level_rank.get(current_lv, 99), level_rank.get(target_lv, 99)
        return "UP" if tgt < curr else "STAY" if tgt == curr else "DOWN"

    groups = {"UP": [], "STAY": [], "DOWN": []}
    for s in students:
        s["target"] = target_level(s["total"])
        s["status"] = status(s["level"], s["target"])
        groups[s["status"]].append(s)
    groups["UP"].sort(key=lambda s: -s["total"])
    groups["DOWN"].sort(key=lambda s: s["total"])
    groups["STAY"].sort(key=lambda s: (level_rank.get(s["level"], 99), -s["total"]))

    # Console summary
    print(f"{'Level':>5} | {'Count':>5} | {'Min':>7} | {'Q1':>7} | {'Median':>7} | {'Q3':>7} | {'Max':>7} | {'IQR':>7}")
    print("-" * 72)
    for lv in levels_present:
        s = stats[lv]
        print(f"{lv:>5} | {s['n']:>5} | {s['min']:>7.2f} | {s['q1']:>7.2f} | {s['median']:>7.2f} | {s['q3']:>7.2f} | {s['max']:>7.2f} | {s['iqr']:>7.2f}")
    print(f"\nSuggested cutlines ({int(UPPER_WEIGHT*100)}% upper median + {int(LOWER_WEIGHT*100)}% lower median):")
    for lv in levels_present:
        if lv in cutlines:
            print(f"  Lv{lv}: >= {cutlines[lv]:.2f}")
    print(f"\nUP: {len(groups['UP'])} | STAY: {len(groups['STAY'])} | DOWN: {len(groups['DOWN'])}")

    # Markdown report
    lines = ["# Cutline Analysis Report", f"**Source:** `{os.path.basename(input_file)}`\n",
             "## Level Statistics (IQR)\n",
             "| Level | Count | Min | Q1 | Median | Q3 | Max | IQR |",
             "|-------|-------|-----|----|--------|----|-----|-----|"]
    for lv in levels_present:
        s = stats[lv]
        lines.append(f"| {lv} | {s['n']} | {s['min']:.2f} | {s['q1']:.2f} | {s['median']:.2f} | {s['q3']:.2f} | {s['max']:.2f} | {s['iqr']:.2f} |")
    lines += ["", f"## Suggested Cutlines ({int(UPPER_WEIGHT*100)}% upper median + {int(LOWER_WEIGHT*100)}% lower median)\n"]
    for upper_lv, lower_lv in zip(levels_present, levels_present[1:]):
        lines.append(f"- **Lv{upper_lv}**: >= {cutlines[upper_lv]:.2f}  "
                     f"(Lv{upper_lv} median={stats[upper_lv]['median']:.2f}, Lv{lower_lv} median={stats[lower_lv]['median']:.2f})")
    lines += ["", f"## Leveling Summary: UP {len(groups['UP'])} / STAY {len(groups['STAY'])} / DOWN {len(groups['DOWN'])}\n"]
    for label in ("UP", "DOWN"):
        if groups[label]:
            lines += [f"### {label}\n", "| Name | Class | Current | Target | Total Avg | Tests |",
                      "|------|-------|---------|--------|-----------|-------|"]
            lines += [f"| {s['name']} | {s['class']} | Lv{s['level']} | Lv{s['target']} | {s['total']:.2f} | {s['test_count']} |"
                      for s in groups[label]]
            lines.append("")
    if groups["STAY"]:
        lines += ["### STAY\n", "| Name | Class | Level | Total Avg | Tests |", "|------|-------|-------|-----------|-------|"]
        lines += [f"| {s['name']} | {s['class']} | Lv{s['level']} | {s['total']:.2f} | {s['test_count']} |" for s in groups["STAY"]]

    with open(report_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nReport written to {report_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
