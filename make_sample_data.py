"""
Generate synthetic sample input CSVs for both script variants.

    python3 make_sample_data.py

Writes g1_5/CSV/sample_grades.csv and g6/CSV/sample_grades.csv in the exact
input format the scripts expect (반명, 원생명, 시험일, <subjects...>).
Deterministic (fixed seed).  Every student, teacher tag and score is
invented; nothing here comes from a real export.

The data deliberately exercises the edge cases the scripts handle:
  * students enrolled in one track or both (주1 / 주2)
  * name suffixes parsed by get_level: "L3", "(P)", "E2K3", "K3E2", "E3", "K2", "주1"
  * a couple of same-week duplicate rows (so dedup and duplicate_entries.md trigger)
  * a student with fewer than MIN_TESTS dates (omitted from the summary)
  * a student whose first score is late in the term (for START_CUTOFF)
  * an occasional blank score cell
"""

import csv
import os
import random
from datetime import date, timedelta

SEED = 20260101
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WEEKS = 8
TERM_START = date(2026, 1, 5)  # a Monday

G15_SUBJECTS = ["Particip", "Vocabulary", "Grammar HW", "Reading", "Listening", "Speaking", "Writing", "Novel"]
G6_SUBJECTS = ["Particip", "Vocabulary", "Reading", "Listening", "Speaking", "Writing", "Science HW", "Science Test", "Novel"]

# g1_5 classes: G<grade>-<level><section><DAY>(<teacher>) ; M/TU/W = Track A, TH/F/SA = Track B
G15_CLASSES = {
    "A": {2: "G3-21M(TeacherA)", 3: "G3-32W(TeacherC)", 4: "G3-43TU(TeacherE)"},
    "B": {2: "G3-21TH(TeacherB)", 3: "G3-32F(TeacherD)", 4: "G3-43SA(TeacherF)"},
}
# g6 classes: "[E]" prefix or ASCII teacher tag = Track 1; non-ASCII teacher tag = Track 2
G6_CLASSES = {
    "1": {2: "[E]G6-21M(TeacherA)", 3: "G6-32W(TeacherB)", 4: "[E]G6-43TU(TeacherC)"},
    "2": {2: "G6-21TH(교사A)", 3: "G6-32F(교사B)", 4: "G6-43SA(교사C)"},
}
# Weekday offset (0 = Monday) on which each track's weekly test falls
TEST_DAY = {"A": 2, "B": 5, "1": 1, "2": 4}

# (name as it appears in the export, {track: class level}, ability 0-1, weeks attended or None for all)
G15_STUDENTS = [
    ("학생A", {"A": 2, "B": 2}, 0.92, None),
    ("학생B", {"A": 2, "B": 2}, 0.80, None),
    ("학생C L3", {"A": 2, "B": 2}, 0.74, None),        # L3 tag overrides the class-code level
    ("학생D", {"A": 3, "B": 3}, 0.70, None),
    ("학생E(P)", {"A": 3, "B": 3}, 0.55, None),        # (P) tag = pre-level
    ("학생F", {"A": 3, "B": 3}, 0.66, None),
    ("학생G 주1", {"A": 2}, 0.85, None),                # once-a-week tag, stripped for display
    ("학생H", {"A": 3}, 0.62, None),
    ("학생I", {"A": 4}, 0.58, None),
    ("학생J", {"B": 2}, 0.88, None),
    ("학생K", {"B": 3}, 0.71, None),
    ("학생L", {"B": 4}, 0.50, None),
    ("학생M*", {"A": 4, "B": 4}, 0.60, None),          # asterisk is stripped for display
    ("학생N", {"A": 3}, 0.75, [0, 1, 2]),                 # only 3 test dates -> omitted (MIN_TESTS)
    ("학생O", {"B": 3}, 0.68, [3, 4, 5, 6, 7]),           # late starter (for START_CUTOFF demos)
]
G15_DUPLICATES = [("학생A", "A", 1), ("학생D", "B", 3)]  # (name, track, week) extra same-week row

G6_STUDENTS = [
    ("학생A", {"1": 2, "2": 2}, 0.93, None),
    ("학생B", {"1": 2, "2": 2}, 0.79, None),
    ("학생C E2K3", {"1": 2, "2": 3}, 0.76, None),          # separate level per track
    ("학생D K3E2", {"1": 2, "2": 3}, 0.72, None),
    ("학생E L3", {"1": 3, "2": 3}, 0.70, None),
    ("학생F(P)", {"1": 3, "2": 3}, 0.54, None),
    ("학생G E3", {"1": 3}, 0.67, None),                    # standalone Track 1 (E-tag) level
    ("학생H K2", {"2": 2}, 0.86, None),                    # standalone Track 2 (K-tag) level
    ("학생I 주1", {"1": 2}, 0.84, None),
    ("학생J", {"1": 4}, 0.57, None),
    ("학생K", {"2": 3}, 0.69, None),
    ("학생L", {"2": 4}, 0.52, None),
    ("학생M", {"1": 4, "2": 4}, 0.61, None),
    ("학생N", {"1": 3}, 0.74, [0, 1, 2, 3]),                # only 4 test dates -> omitted (MIN_TESTS)
    ("학생O", {"2": 3}, 0.66, [3, 4, 5, 6, 7]),             # late starter (for START_CUTOFF demos)
]
G6_DUPLICATES = [("학생B", "1", 2), ("학생K", "2", 5)]


def score_for(rng, ability, subject):
    """A plausible integer score 40-100 around the student's ability."""
    if subject == "Particip":
        base = 70 + ability * 30
        return int(max(50, min(100, rng.gauss(base, 5))))
    base = 45 + ability * 55
    return int(max(40, min(100, rng.gauss(base, 7))))


def build_rows(rng, students, classes, subjects, duplicates):
    rows = []
    for name, tracks, ability, weeks in students:
        week_list = weeks if weeks is not None else list(range(WEEKS))
        for track, level in tracks.items():
            class_name = classes[track][level]
            for wk in week_list:
                test_date = TERM_START + timedelta(weeks=wk, days=TEST_DAY[track])
                row = {"반명": class_name, "원생명": name, "시험일": test_date.isoformat()}
                for subj in subjects:
                    # ~6 % blank cells, never blank participation
                    if subj != "Particip" and rng.random() < 0.06:
                        row[subj] = ""
                    else:
                        row[subj] = score_for(rng, ability, subj)
                rows.append(row)
    # Deliberate duplicates: a second row for the same student+class earlier in the same ISO week
    by_name = {s[0]: s for s in students}
    for name, track, wk in duplicates:
        _, tracks, ability, _ = by_name[name]
        class_name = classes[track][tracks[track]]
        test_date = TERM_START + timedelta(weeks=wk, days=max(0, TEST_DAY[track] - 1))
        row = {"반명": class_name, "원생명": name, "시험일": test_date.isoformat()}
        for subj in subjects:
            row[subj] = score_for(rng, ability, subj)
        rows.append(row)
    rows.sort(key=lambda r: (r["시험일"], r["반명"], r["원생명"]))
    return rows


def write_csv(path, subjects, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["반명", "원생명", "시험일"] + subjects)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {os.path.relpath(path, SCRIPT_DIR)}")


def main():
    rng = random.Random(SEED)
    write_csv(os.path.join(SCRIPT_DIR, "g1_5", "CSV", "sample_grades.csv"), G15_SUBJECTS,
              build_rows(rng, G15_STUDENTS, G15_CLASSES, G15_SUBJECTS, G15_DUPLICATES))
    write_csv(os.path.join(SCRIPT_DIR, "g6", "CSV", "sample_grades.csv"), G6_SUBJECTS,
              build_rows(rng, G6_STUDENTS, G6_CLASSES, G6_SUBJECTS, G6_DUPLICATES))


if __name__ == "__main__":
    main()
