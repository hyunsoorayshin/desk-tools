# Grade Summary Generator -- User Guide

Drop the grade-export CSV files from your grade-management system into a folder, run one script, and get a summary CSV with each student's per-subject averages, total average, and a level verdict.

---

## Before you start

You need Python 3.9 or newer. Open a terminal and run:

```
python3 --version
```

If you see something like `Python 3.x.x` you are ready. Otherwise download Python from https://www.python.org. No other packages are needed.

---

## Folder layout

```
desk-tools/
├── g1_5/                    <- grades 1-5 (Track A / Track B)
│   ├── CSV/                 <- put CSV files here
│   └── generate_summary.py
├── g6/                      <- grade 6 (Track 1 / Track 2)
│   ├── CSV/                 <- put CSV files here
│   └── generate_summary.py
└── analyze_cutlines.py      <- optional: propose cutlines from a summary
```

---

## How to run

### Step 1: prepare the CSV files

Put the exported CSV file(s) in the `CSV` folder of the matching variant:

- **Grade 6** scores -> `g6/CSV/`
- **Grades 1-5** scores -> `g1_5/CSV/`

> The CSV must contain the columns `원생명` (student), `반명` (class) and `시험일` (test date, `YYYY-MM-DD`). Every other column is treated as a subject score column.
> You can put several CSV files in the folder at once.

The repository ships with a synthetic `sample_grades.csv` in each `CSV/` folder so you can try the scripts immediately. Delete or move it before running on your own data, or the sample will be summarised too.

### Step 2: run the script

From the `desk-tools` folder:

**Grade 6:**
```
python3 g6/generate_summary.py
```

**Grades 1-5:**
```
python3 g1_5/generate_summary.py
```

To read from and write to other folders:

```
python3 g1_5/generate_summary.py --csv-dir /path/to/exports --out-dir /path/to/output
```

### Step 3: check the results

When the script finishes, a `_summary.csv` file appears next to the script (or in `--out-dir`):

- One CSV file: `<original name>_summary.csv`
- Several CSV files (grade 6 only): `combined_summary.csv`
- Grades 1-5 produce one summary file per input CSV.

Open the summary in Excel or Google Sheets.

If the same student had two score entries for the same class within the same week, the later entry is kept and the earlier one is listed in `duplicate_entries.md` so it can be deleted from the source system.

---

## Settings (optional)

Edit the `SETTINGS` block at the top of each `generate_summary.py`:

| Setting | Default | Meaning |
|---------|---------|---------|
| `LEVELING` | `False` | `True` applies the cutlines and writes UP / STAY / DOWN. `False` writes "PENDING" for everyone. |
| `MIN_TESTS` | `5` | Minimum number of test dates in a track. Students with fewer are left out. |
| `START_CUTOFF` | `None` | e.g. `datetime(2026, 1, 26)`: students whose first score is on or after this date are excluded. `None` disables the filter. |
| `PARTICIP_WEIGHT` / `ACADEMIC_WEIGHT` | `0.1` / `0.9` | Weight of the `Particip` column versus all other subjects in the total average. |
| `PARTICIP_COL` | `"Particip"` | Header of the participation column. If the CSV has no such column, the total is just the academic average. |
| `TRACK_WEIGHTS` (g6 only) | `{TRACK_1: 0.6, TRACK_2: 0.4}` | Weight of the Track 1 and Track 2 totals for students in both tracks. |
| `LV_CUTLINES` | `{2: 80, 3: 70, 4: 60}` | Cutline per level. A total average of 80 or more means Lv2, 70 or more Lv3, 60 or more Lv4, below that "P". **These are placeholders; set them for your institution.** |

---

## Reading the output

Students are grouped into sections:

**Grade 6 (g6):**
- **주2**: students in both a Track 1 and a Track 2 class (Track 1 60% + Track 2 40% weighted total)
- **주1 Track 1**: Track 1 class only
- **주1 Track 2**: Track 2 class only

**Grades 1-5 (g1_5):**
- **주2**: students in both a Track A (Mon/Tue/Wed) and a Track B (Thu/Fri/Sat) class
- **주1 Track A**: Track A class only
- **주1 Track B**: Track B class only

Within each section students are sorted by total average, highest first.

How the track is detected:
- g1_5: from the weekday code in the class name, e.g. `G3-21M(TeacherA)` is Track A and `G3-21TH(TeacherB)` is Track B. Codes M / TU / W are Track A, TH / F / SA are Track B.
- g6: a class name starting with `[E]` is Track 1; otherwise the tag in parentheses decides -- ASCII letters mean Track 1, Korean means Track 2.

How the current Level is read: the digit right after `G<grade>-` in the class name (`G3-21M` is level 2). A tag in the student's name overrides it: `L3` sets the level, `(P)` marks the pre-level, and for g6 `E2K3` (or `K3E2`), `E3`, `K2` set per-track levels (the `E` tag refers to Track 1, the `K` tag to Track 2). A trailing `주1` and any `*` are removed from the displayed name.

---

## Proposing cutlines (optional)

`analyze_cutlines.py` reads a summary CSV and suggests cutlines from the medians of the current levels (70% of the upper level's median + 30% of the lower level's median), then lists who would move under those numbers:

```
python3 analyze_cutlines.py g1_5/sample_grades_summary.csv
```

The report is written to `cutline_report.md` next to the summary (or pass `--out`). It is a descriptive statistic to inform a human decision, nothing more.

---

## Troubleshooting

| Problem | What to do |
|---------|------------|
| `No CSV files found` | Check that there is a `.csv` file in the CSV folder (or in `--csv-dir`). |
| `python3: command not found` | Check that Python is installed. On Windows try `python` instead of `python3`. |
| Korean text is garbled | The script tries UTF-8 and CP949/EUC-KR automatically. If it still fails, re-save the CSV as UTF-8. |
| A student is missing from the result | They probably have fewer than `MIN_TESTS` test dates, or started after `START_CUTOFF`. |
| A class is missing (g1_5) | Its name does not match `G<n>-<n><DAY>(...)`, so no track could be detected. |
