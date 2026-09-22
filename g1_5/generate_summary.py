"""
Grade summary generator -- grades 1-5 variant (MTW / TFS tracks).

Reads every ``*.csv`` in a CSV directory (default: ``CSV/`` next to this
script).  Each file is a grade-management export with the columns
``반명`` (class name), ``원생명`` (student name), ``시험일`` (test date,
YYYY-MM-DD) and one column per subject.  Every other column is treated as
a subject; the subject list is derived from the CSV header, so nothing in
this file has to be edited when the curriculum changes.

For each input file a ``<name>_summary.csv`` is written with per-student
subject averages, a total average, the student's current Level and a
Status verdict (UP / STAY / DOWN / P, or PENDING while ``LEVELING`` is
False).  Same-week duplicate rows are dropped and listed in
``duplicate_entries.md``.

Class-name convention (only the shape matters, the teacher token is free
text): ``G<grade>-<level><section><DAY>(<teacher>)``, e.g.
``G3-21M(TeacherA)``.  The DAY code decides the track: M / TU / W -> MTW,
TH / F / SA -> TFS.  The digit right after the dash is the class level.

Student-name suffix conventions: ``L3`` overrides the level, ``(P)`` marks
the pre-level, a trailing ``주1`` (once-a-week) tag is stripped for display.

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

### ====== SETTINGS ====== ###
# All numbers below are PLACEHOLDERS.  Set them per institution.
LEVELING = False               # True: apply cutlines, False: every Status is "PENDING"
MIN_TESTS = 5                  # Minimum test dates per track to be included (< this = omitted)
START_CUTOFF = None            # e.g. datetime(2026, 1, 26): students whose first score is on or
                               # after this date are excluded.  None disables the filter.
PARTICIP_WEIGHT = 0.1          # Weight of the participation column in the total
ACADEMIC_WEIGHT = 0.9          # Weight of the mean of all other subject averages
PARTICIP_COL = "Particip"      # Header of the participation column (if present)
# Placeholder cutlines: total average >= value -> that level; below Lv4 -> "P".
# "ju2" (주2) = enrolled in both tracks (twice a week); "ju1" (주1) = one track only.
LV_CUTLINES = {
    "ju2":     {2: 80, 3: 70, 4: 60},  # both tracks, combined total
    "MTW_ju1": {2: 80, 3: 70, 4: 60},  # MTW-only students
    "TFS_ju1": {2: 80, 3: 70, 4: 60},  # TFS-only students
}
### ======================== ###

META_COLS = {"반명", "원생명", "시험일"}  # Non-subject columns in the CSV

# Day code -> track
MTW_DAYS = {"M", "TU", "W"}
TFS_DAYS = {"TH", "F", "SA"}


def round2(value):
    """Round to 2 decimal places, round-half-up (matches Excel's ROUND)."""
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def get_class_type(class_name):
    """Return 'MTW' or 'TFS' from the day code in the class name, else None."""
    m = re.search(r"G\d+-\d+([A-Z]+)\(", class_name)
    if not m:
        return None
    day = m.group(1)
    if day in MTW_DAYS:
        return "MTW"
    if day in TFS_DAYS:
        return "TFS"
    return None


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
    """Current level: an L# tag in the name wins, then (P), then the class code digit."""
    m = re.search(r"G\d+-(\d)", class_name)
    class_level = int(m.group(1)) if m else ""
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
    """Per-subject averages (2 dp), then the mean of those averages (2 dp).

    Total = mean(academic subject averages) * ACADEMIC_WEIGHT
          + participation average            * PARTICIP_WEIGHT
    If there is no participation column the total is just the academic mean.
    """
    subject_avgs = {}
    academic_avgs = []
    particip_avg = None

    for col in subject_cols:
        scores = score_dict.get(col, [])
        if scores:
            avg = round2(sum(scores) / len(scores))
            subject_avgs[col] = avg
            if col == PARTICIP_COL:
                particip_avg = avg
            else:
                academic_avgs.append(avg)
        else:
            subject_avgs[col] = ""

    academic_mean = round2(sum(academic_avgs) / len(academic_avgs)) if academic_avgs else 0

    if academic_avgs and particip_avg is not None:
        total_avg = round2(round2(academic_mean * ACADEMIC_WEIGHT) + round2(particip_avg * PARTICIP_WEIGHT))
    elif academic_avgs:
        total_avg = academic_mean
    elif particip_avg is not None:
        total_avg = particip_avg
    else:
        total_avg = 0

    return subject_avgs, total_avg


def cutline_str(cutlines):
    return " / ".join(f"Lv{k}: {v}" for k, v in sorted(cutlines.items()))


def process_file(input_file, out_dir):
    """Summarise one input CSV.  Returns the list of duplicate entries found."""
    print(f"\nProcessing: {os.path.basename(input_file)}")

    with open_csv(input_file) as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        all_rows = list(reader)

    # Subject columns = every non-empty header that is not a meta column
    subject_cols = [col for col in fieldnames if col and col.strip() and col not in META_COLS]
    print(f"  Subject columns: {subject_cols}")

    all_rows, duplicates = dedup_rows(all_rows)

    scores_by_type = defaultdict(lambda: defaultdict(list))   # (name, track) -> col -> [scores]
    dates_by_type = defaultdict(set)                           # (name, track) -> {dates}
    latest_class_by_type = {}                                  # (name, track) -> (date, class)
    student_scores = defaultdict(lambda: defaultdict(list))    # name -> col -> [scores]
    first_date_by_student = {}

    for row in all_rows:
        name = row["원생명"].strip()
        class_name = row["반명"].strip()
        test_date = row["시험일"].strip()

        if not name:
            continue

        ct = get_class_type(class_name)
        if ct is None:
            continue

        key = (name, ct)

        has_any_score = False
        for col in subject_cols:
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
        for ct in ["MTW", "TFS"]:
            key = (name, ct)
            if key in dates_by_type and len(dates_by_type[key]) >= MIN_TESTS:
                class_types.append(ct)

        if not class_types:
            continue

        ju = 2 if len(class_types) == 2 else 1  # 2 = both tracks, 1 = one track

        display_name = re.sub(r"\s*주1", "", name)
        display_name = re.sub(r"\s*\(?L\d\)?\s*", " ", display_name)
        display_name = display_name.replace("*", "").strip()

        if ju == 2:
            mtw_key, tfs_key = (name, "MTW"), (name, "TFS")
            _, mtw_class = latest_class_by_type[mtw_key]
            _, tfs_class = latest_class_by_type[tfs_key]
            class_display = f"{mtw_class} / {tfs_class}"
            mtw_dates = len(dates_by_type[mtw_key])
            tfs_dates = len(dates_by_type[tfs_key])
            test_count = f"MTW{mtw_dates}/TFS{tfs_dates}"

            _, mtw_total = calc_avgs(scores_by_type[mtw_key], subject_cols)
            _, tfs_total = calc_avgs(scores_by_type[tfs_key], subject_cols)

            # Pool the raw scores of both tracks for the combined subject averages and total
            combined_scores = defaultdict(list)
            for col in subject_cols:
                combined_scores[col].extend(scores_by_type[mtw_key].get(col, []))
                combined_scores[col].extend(scores_by_type[tfs_key].get(col, []))
            subject_avgs, combined_total = calc_avgs(combined_scores, subject_cols)

            level = get_level(name, mtw_class, "MTW")
            status = get_status(level, combined_total, "ju2")

            summary.append({
                "반명": class_display,
                "원생명": display_name,
                "Level": level,
                "Status": status,
                "주": ju,
                **subject_avgs,
                "MTW Average": mtw_total,
                "TFS Average": tfs_total,
                "Total Average": combined_total,
                "Test Count": test_count,
            })
        else:
            ct = class_types[0]
            key = (name, ct)
            _, latest_class = latest_class_by_type[key]
            level = get_level(name, latest_class, ct)
            num_dates = len(dates_by_type[key])
            subject_avgs, total_avg = calc_avgs(scores_by_type[key], subject_cols)
            status = get_status(level, total_avg, f"{ct}_ju1")

            summary.append({
                "반명": latest_class,
                "원생명": display_name,
                "Level": level,
                "Status": status,
                "주": ju,
                **subject_avgs,
                "MTW Average": "",
                "TFS Average": "",
                "Total Average": total_avg,
                "Test Count": num_dates,
            })

    def class_type_key(row):
        if row["주"] == 2:
            return 0
        ct = get_class_type(row["반명"])
        return 0 if ct == "MTW" else 1

    summary.sort(key=lambda r: (r["주"], class_type_key(r), -r["Total Average"]))

    ju2_rows = [r for r in summary if r["주"] == 2]
    ju1_mtw_rows = [r for r in summary if r["주"] == 1 and get_class_type(r["반명"]) == "MTW"]
    ju1_tfs_rows = [r for r in summary if r["주"] == 1 and get_class_type(r["반명"]) == "TFS"]

    base_name = os.path.splitext(os.path.basename(input_file))[0]
    safe_name = re.sub(r'[<>:"/\\|?*]', "_", base_name)
    output_file = os.path.join(out_dir, f"{safe_name}_summary.csv")
    output_cols = ["반명", "원생명", "Level", "Status", "주", "Test Count"] + subject_cols + ["MTW Average", "TFS Average", "Total Average"]
    empty_row = {col: "" for col in output_cols}

    with open(output_file, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=output_cols)
        writer.writeheader()

        if ju2_rows:
            writer.writerow({**empty_row, "반명": f"=== 주2 ({len(ju2_rows)}명) === Cutline: {cutline_str(LV_CUTLINES['ju2'])}"})
            writer.writerows(ju2_rows)

        if ju1_mtw_rows:
            writer.writerow({**empty_row, "반명": f"=== 주1 MTW ({len(ju1_mtw_rows)}명) === Cutline: {cutline_str(LV_CUTLINES['MTW_ju1'])}"})
            writer.writerows(ju1_mtw_rows)

        if ju1_tfs_rows:
            writer.writerow({**empty_row, "반명": f"=== 주1 TFS ({len(ju1_tfs_rows)}명) === Cutline: {cutline_str(LV_CUTLINES['TFS_ju1'])}"})
            writer.writerows(ju1_tfs_rows)

    print(f"  -> {len(summary)} students written to {os.path.basename(output_file)}")
    return duplicates


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
    print(f"\nDuplicate report written to {os.path.basename(report_path)}")


def main(argv=None):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description="Summarise grade-export CSVs (grades 1-5, MTW/TFS tracks).")
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

    all_duplicates = {}
    for input_file in input_files:
        duplicates = process_file(input_file, args.out_dir)
        if duplicates:
            all_duplicates[os.path.basename(input_file)] = duplicates

    if all_duplicates:
        write_duplicate_report(all_duplicates, args.out_dir)
    else:
        print("\nNo duplicates found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
