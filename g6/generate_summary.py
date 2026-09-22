"""
Grade summary generator -- grade 6 variant (Track 1 / Track 2).

Reads every ``*.csv`` in a CSV directory (default: ``CSV/`` next to this
script).  Each file is a grade-management export with the columns
``반명`` (class name), ``원생명`` (student name), ``시험일`` (test date,
YYYY-MM-DD) and one column per subject.  Every other column is treated as
a subject; the subject list is derived from the CSV headers (union across
all input files), so nothing here has to be edited when the curriculum
changes.

All input files are pooled into ONE summary: ``<name>_summary.csv`` when
there is a single input file, ``combined_summary.csv`` otherwise.  Rows are
per student with subject averages, a Track 1 average, a Track 2 average, a
weighted total (Track 1 60 / Track 2 40 by default), the current Level and a
Status verdict (UP / STAY / DOWN / P, or PENDING while ``LEVELING`` is
False).  Same-week duplicate rows are dropped and listed in
``duplicate_entries.md``.

Track detection: a class name starting with ``[E]`` is Track 1; otherwise the
token in parentheses (the teacher tag) decides: ASCII -> Track 1, anything
else -> Track 2.  E.g. ``[E]G6-31M(TeacherA)`` and ``G6-32TH(TeacherB)`` are
Track 1, ``G6-31M(교사A)`` is Track 2.  The digit right after ``G6-`` is the
class level.

Student-name suffix conventions handled by ``get_level`` (the ``E`` tag
refers to Track 1, the ``K`` tag to Track 2): ``E2K3`` / ``K3E2`` (separate
levels per track), ``E3`` or ``K2`` (one track), ``L3`` (both tracks), ``(P)``
(pre-level), trailing ``주1`` (once-a-week) tag.

Standard library only.  Python 3.9+.
"""

import argparse
import csv
import glob
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

# Display names of the two tracks (used in output columns and group labels)
TRACK_1 = "Track 1"   # classes with an [E] prefix or an ASCII teacher tag
TRACK_2 = "Track 2"   # classes with a non-ASCII teacher tag

### ====== SETTINGS ====== ###
# All numbers below are PLACEHOLDERS.  Set them per institution.
LEVELING = False               # True: apply cutlines, False: every Status is "PENDING"
MIN_TESTS = 5                  # Minimum test dates per track to be included (< this = omitted)
START_CUTOFF = None            # e.g. datetime(2026, 1, 26): students whose first score is on or
                               # after this date are excluded.  None disables the filter.
TRACK_WEIGHTS = {TRACK_1: 0.6, TRACK_2: 0.4}  # Track weights for the combined total of two-track students
PARTICIP_WEIGHT = 0.1          # Weight of the participation column in each track total
ACADEMIC_WEIGHT = 0.9          # Weight of the pooled academic scores in each track total
PARTICIP_COL = "Particip"      # Header of the participation column (if present)
# Placeholder cutlines: total average >= value -> that level; below Lv4 -> "P".
# "ju2" (주2) = enrolled in both tracks (twice a week); "ju1" (주1) = one track only.
LV_CUTLINES = {
    "ju2":        {2: 80, 3: 70, 4: 60},  # both tracks, weighted combined total
    "track1_ju1": {2: 80, 3: 70, 4: 60},  # Track 1-only students
    "track2_ju1": {2: 80, 3: 70, 4: 60},  # Track 2-only students
}
### ======================== ###

META_COLS = {"반명", "원생명", "시험일"}  # Non-subject columns in the CSV
# Track -> cutline set for single-track students
TRACK_CUTLINE_KEY = {TRACK_1: "track1_ju1", TRACK_2: "track2_ju1"}


def round2(value):
    """Round to 2 decimal places, round-half-up (matches Excel's ROUND)."""
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def is_track1_class(class_name):
    """Track 1 if the name starts with [E] or the parenthesised teacher tag is ASCII."""
    if class_name.startswith("[E]"):
        return True
    teacher = re.search(r"\(([^)]+)\)", class_name)
    return bool(teacher and teacher.group(1).isascii())


def open_csv(filepath):
    """Open a CSV trying the encodings the export tool is known to produce."""
    for enc in ["utf-8-sig", "utf-8", "cp949", "euc-kr"]:
        try:
            f = open(filepath, encoding=enc, newline="")
            f.read(1024)
            f.seek(0)
            return f
        except (UnicodeDecodeError, UnicodeError):
            f.close()
    raise ValueError(f"Cannot detect encoding for {filepath}")


def dedup_rows(rows):
    """Drop duplicate entries per (student, class) within the same ISO week.

    Keeps the entry with the latest date.  Returns (deduped_rows, duplicates)
    where duplicates is a list of dicts describing what was removed.
    """
    grouped = defaultdict(list)
    for row in rows:
        name = row["원생명"].strip()
        cls = row["반명"].strip()
        date_str = row["시험일"].strip()
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            week_key = dt.isocalendar()[:2]
        except ValueError:
            week_key = date_str
        grouped[(name, cls, week_key)].append((date_str, row))

    result = []
    duplicates = []
    for key, entries in grouped.items():
        entries.sort(key=lambda x: x[0])
        result.append(entries[-1][1])
        if len(entries) > 1:
            name, cls, _ = key
            dates = [e[0] for e in entries]
            duplicates.append({"student": name, "class": cls, "kept": dates[-1], "removed": dates[:-1]})
    if duplicates:
        print(f"  [dedup] Removed {sum(len(d['removed']) for d in duplicates)} duplicate entries (same student+class in same week)")
    return result, duplicates


def get_level(name, class_name, class_type):
    """Current level for one track (TRACK_1 or TRACK_2).

    Override priority: E#K# / K#E# > standalone E# / K# > L# > (P) > class code digit.
    In the name tags, ``E`` refers to Track 1 and ``K`` to Track 2.
    """
    m = re.search(r"G\d+-(\d)", class_name)
    class_level = int(m.group(1)) if m else ""

    ek_match = re.search(r"E(\d)K(\d)", name)
    ke_match = re.search(r"K(\d)E(\d)", name)
    if ek_match:
        return int(ek_match.group(1)) if class_type == TRACK_1 else int(ek_match.group(2))
    if ke_match:
        return int(ke_match.group(2)) if class_type == TRACK_1 else int(ke_match.group(1))

    e_match = re.search(r"E(\d)", name)
    if e_match and class_type == TRACK_1:
        return int(e_match.group(1))

    k_match = re.search(r"K(\d)", name)
    if k_match and class_type == TRACK_2:
        return int(k_match.group(1))

    l_match = re.search(r"L(\d)", name)
    if l_match:
        return int(l_match.group(1))

    if "(P)" in name:
        return "P"

    return class_level


def get_target_level(avg, cutline_key):
    """Fixed threshold rule: compare the total average with the cutlines."""
    cutlines = LV_CUTLINES[cutline_key]
    if avg >= cutlines[2]:
        return 2
    elif avg >= cutlines[3]:
        return 3
    elif avg >= cutlines[4]:
        return 4
    else:
        return "P"


def get_status(current_level, total_avg, cutline_key):
    """UP if the target level is higher than the current one, STAY if equal, DOWN otherwise."""
    if not LEVELING:
        return "PENDING"
    target = get_target_level(total_avg, cutline_key)
    rank = {2: 2, 3: 3, 4: 4, "P": 5}  # lower rank = higher level
    curr = rank.get(current_level)
    tgt = rank.get(target)
    if curr is None:
        return ""
    if tgt < curr:
        return "UP"
    elif tgt == curr:
        return "STAY"
    elif target == "P":
        return "P"
    else:
        return "DOWN"


def calc_avgs(score_dict, subject_cols):
    """Per-subject averages (2 dp) and a participation-weighted track total (2 dp).

    Total = mean(all academic scores pooled) * ACADEMIC_WEIGHT
          + mean(participation scores)       * PARTICIP_WEIGHT
    If there is no participation column the total is just the pooled academic mean.
    """
    subject_avgs = {}
    academic_scores = []
    particip_scores = []

    for col in subject_cols:
        scores = score_dict.get(col, [])
        if scores:
            subject_avgs[col] = round2(sum(scores) / len(scores))
            if col == PARTICIP_COL:
                particip_scores.extend(scores)
            else:
                academic_scores.extend(scores)
        else:
            subject_avgs[col] = ""

    academic_avg = sum(academic_scores) / len(academic_scores) if academic_scores else 0
    particip_avg = sum(particip_scores) / len(particip_scores) if particip_scores else 0

    if academic_scores and particip_scores:
        total_avg = round2(academic_avg * ACADEMIC_WEIGHT + particip_avg * PARTICIP_WEIGHT)
    elif academic_scores:
        total_avg = round2(academic_avg)
    elif particip_scores:
        total_avg = round2(particip_avg)
    else:
        total_avg = 0

    return subject_avgs, total_avg


def cutline_str(cutlines):
    return " / ".join(f"Lv{k}: {v}" for k, v in sorted(cutlines.items()))


def write_duplicate_report(all_duplicates, out_dir):
    report_path = os.path.join(out_dir, "duplicate_entries.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# Duplicate Score Entries\n\n")
        f.write("The following students had multiple score entries for the same class within the same week.\n")
        f.write("The later date was kept; the earlier date(s) should be removed from the source system.\n\n")
        for filename, dups in all_duplicates.items():
            f.write(f"## {filename}\n\n")
            f.write("| Student | Class | Kept | Removed (delete these) |\n")
            f.write("|---------|-------|------|------------------------|\n")
            for d in sorted(dups, key=lambda x: (x["class"], x["student"])):
                f.write(f"| {d['student']} | {d['class']} | {d['kept']} | {', '.join(d['removed'])} |\n")
            f.write("\n")
    print(f"Duplicate report written to {os.path.basename(report_path)}")


def main(argv=None):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description="Summarise grade-export CSVs (grade 6, Track 1 / Track 2).")
    parser.add_argument("--csv-dir", default=os.path.join(script_dir, "CSV"),
                        help="directory containing the input *.csv files (default: CSV/ next to this script)")
    parser.add_argument("--out-dir", default=script_dir,
                        help="directory for the *_summary.csv and duplicate_entries.md outputs (default: next to this script)")
    args = parser.parse_args(argv)

    input_files = sorted(glob.glob(os.path.join(args.csv_dir, "*.csv")))
    if not input_files:
        print(f"No CSV files found in {args.csv_dir}")
        return 1
    os.makedirs(args.out_dir, exist_ok=True)

    # Per student per track: scores, dates, latest class.  key: (name, TRACK_1 | TRACK_2)
    scores_by_type = defaultdict(lambda: defaultdict(list))
    dates_by_type = defaultdict(set)
    latest_class_by_type = {}
    student_scores = defaultdict(lambda: defaultdict(list))
    first_date_by_student = {}
    subject_cols = []  # union of subject headers, in order of first appearance
    all_duplicates = {}

    for input_file in input_files:
        print(f"Reading: {os.path.basename(input_file)}")
        with open_csv(input_file) as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            all_rows = list(reader)

        file_subjects = [col for col in fieldnames if col and col.strip() and col not in META_COLS]
        for col in file_subjects:
            if col not in subject_cols:
                subject_cols.append(col)
        print(f"  Subject columns: {file_subjects}")

        all_rows, duplicates = dedup_rows(all_rows)
        if duplicates:
            all_duplicates[os.path.basename(input_file)] = duplicates

        for row in all_rows:
            name = row["원생명"].strip()
            class_name = row["반명"].strip()
            test_date = row["시험일"].strip()

            if not name:
                continue

            ct = TRACK_1 if is_track1_class(class_name) else TRACK_2
            key = (name, ct)

            has_any_score = False
            for col in file_subjects:
                val = (row.get(col) or "").strip()
                if val:
                    score = round2(float(val))
                    student_scores[name][col].append(score)
                    scores_by_type[key][col].append(score)
                    has_any_score = True

            if not has_any_score:
                continue

            dates_by_type[key].add(test_date)

            try:
                dt = datetime.strptime(test_date, "%Y-%m-%d")
                if name not in first_date_by_student or dt < first_date_by_student[name]:
                    first_date_by_student[name] = dt
            except ValueError:
                pass

            if key not in latest_class_by_type or test_date > latest_class_by_type[key][0]:
                latest_class_by_type[key] = (test_date, class_name)

    # Optional: drop students who started on or after START_CUTOFF
    if START_CUTOFF is not None:
        excluded_late = []
        for name in list(student_scores.keys()):
            start_dt = first_date_by_student.get(name)
            if start_dt and start_dt >= START_CUTOFF:
                excluded_late.append((name, start_dt.strftime("%Y-%m-%d")))
                del student_scores[name]
        if excluded_late:
            print(f"  [date filter] Excluded {len(excluded_late)} students (started on or after {START_CUTOFF.strftime('%Y-%m-%d')}):")
            for ename, edate in sorted(excluded_late):
                print(f"    - {ename} (first score: {edate})")

    # Build summary rows
    summary = []
    for name in student_scores:
        class_types = []
        for ct in [TRACK_1, TRACK_2]:
            key = (name, ct)
            if key in dates_by_type and len(dates_by_type[key]) >= MIN_TESTS:
                class_types.append(ct)

        if not class_types:
            continue

        ju = 2 if len(class_types) == 2 else 1  # 2 = both tracks, 1 = one track

        display_name = re.sub(r"\s*주1", "", name)
        display_name = re.sub(r"\s*\(?L\d\)?\s*", " ", display_name)
        display_name = re.sub(r"\s*E\dK\d\s*", " ", display_name)
        display_name = re.sub(r"\s*K\dE\d\s*", " ", display_name)
        display_name = re.sub(r"\s*K\d\s*", " ", display_name)
        display_name = re.sub(r"\s*E\d\s*", " ", display_name)
        display_name = display_name.replace("*", "").strip()

        if ju == 2:
            # Participation weighting per track first, then the track weights on top
            t1_key, t2_key = (name, TRACK_1), (name, TRACK_2)
            _, t1_class = latest_class_by_type[t1_key]
            _, t2_class = latest_class_by_type[t2_key]
            class_display = f"{t1_class} / {t2_class}"
            t1_dates = len(dates_by_type[t1_key])
            t2_dates = len(dates_by_type[t2_key])
            test_count = f"{TRACK_1}: {t1_dates} / {TRACK_2}: {t2_dates}"

            t1_avgs, t1_total = calc_avgs(scores_by_type[t1_key], subject_cols)
            t2_avgs, t2_total = calc_avgs(scores_by_type[t2_key], subject_cols)

            weighted_total = round2(t1_total * TRACK_WEIGHTS[TRACK_1] + t2_total * TRACK_WEIGHTS[TRACK_2])
            level = get_level(name, t1_class, TRACK_1)
            status = get_status(level, weighted_total, "ju2")

            # Subject averages: simple mean of the two track averages where both exist
            subject_avgs = {}
            for col in subject_cols:
                vals = [a[col] for a in (t1_avgs, t2_avgs) if a.get(col, "") != ""]
                subject_avgs[col] = round2(sum(vals) / len(vals)) if vals else ""

            summary.append({
                "반명": class_display,
                "원생명": display_name,
                "Level": level,
                "Status": status,
                "주": ju,
                **subject_avgs,
                f"{TRACK_1} Average": t1_total,
                f"{TRACK_2} Average": t2_total,
                "Total Average": weighted_total,
                "Test Count": test_count,
            })
        else:
            ct = class_types[0]
            key = (name, ct)
            _, latest_class = latest_class_by_type[key]
            level = get_level(name, latest_class, ct)
            num_dates = len(dates_by_type[key])
            subject_avgs, total_avg = calc_avgs(scores_by_type[key], subject_cols)
            status = get_status(level, total_avg, TRACK_CUTLINE_KEY[ct])

            summary.append({
                "반명": latest_class,
                "원생명": display_name,
                "Level": level,
                "Status": status,
                "주": ju,
                **subject_avgs,
                f"{TRACK_1} Average": "",
                f"{TRACK_2} Average": "",
                "Total Average": total_avg,
                "Test Count": num_dates,
            })

    # Sort: two-track first, then Track 1 before Track 2, then total average descending
    def class_type_key(row):
        if row["주"] == 2:
            return 0
        return 0 if is_track1_class(row["반명"]) else 1

    summary.sort(key=lambda r: (r["주"], class_type_key(r), -r["Total Average"]))

    ju2_rows = [r for r in summary if r["주"] == 2]
    ju1_t1_rows = [r for r in summary if r["주"] == 1 and is_track1_class(r["반명"])]
    ju1_t2_rows = [r for r in summary if r["주"] == 1 and not is_track1_class(r["반명"])]

    if len(input_files) == 1:
        base_name = os.path.splitext(os.path.basename(input_files[0]))[0]
    else:
        base_name = "combined"
    safe_name = re.sub(r'[<>:"/\\|?*]', "_", base_name)
    output_file = os.path.join(args.out_dir, f"{safe_name}_summary.csv")

    output_cols = ["반명", "원생명", "Level", "Status", "주", "Test Count"] + subject_cols + [f"{TRACK_1} Average", f"{TRACK_2} Average", "Total Average"]
    empty_row = {col: "" for col in output_cols}

    with open(output_file, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=output_cols)
        writer.writeheader()

        if ju2_rows:
            writer.writerow({**empty_row, "반명": f"=== 주2 ({len(ju2_rows)}명) === Cutline: {cutline_str(LV_CUTLINES['ju2'])}"})
            writer.writerows(ju2_rows)

        if ju1_t1_rows:
            writer.writerow({**empty_row, "반명": f"=== 주1 {TRACK_1} ({len(ju1_t1_rows)}명) === Cutline: {cutline_str(LV_CUTLINES['track1_ju1'])}"})
            writer.writerows(ju1_t1_rows)

        if ju1_t2_rows:
            writer.writerow({**empty_row, "반명": f"=== 주1 {TRACK_2} ({len(ju1_t2_rows)}명) === Cutline: {cutline_str(LV_CUTLINES['track2_ju1'])}"})
            writer.writerows(ju1_t2_rows)

    print(f"Done. {len(summary)} students written to {os.path.basename(output_file)}")

    if all_duplicates:
        write_duplicate_report(all_duplicates, args.out_dir)
    else:
        print("No duplicates found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
