# TRACE — How the System Works

For a task-by-task walkthrough for school administrators and teachers, see the [User Guide](USER_GUIDE.md).

**TRACE** (Tracking and Risk Assessment for Continuous Education) is a web system for Philippine basic education schools. It keeps each class's record, works out the grades, warns teachers about students heading below the passing mark, and forecasts each student's final grade from the first term.

This guide explains every rule the system follows and the reason behind it. For installing and deploying, see [README.md](README.md) and [DEPLOYMENT.md](DEPLOYMENT.md). For how schools are kept apart, see [MULTI_SCHOOL.md](MULTI_SCHOOL.md).

**Contents**

1. [Who uses it](#1-who-uses-it)
2. [Two kinds of schools](#2-two-kinds-of-schools)
3. [Joining and signing in](#3-joining-and-signing-in)
4. [Setting up a school](#4-setting-up-a-school)
5. [Who can see and grade what](#5-who-can-see-and-grade-what)
6. [The class record and how grades are computed](#6-the-class-record-and-how-grades-are-computed)
7. [Importing an E-Class Record workbook](#7-importing-an-e-class-record-workbook)
8. [Early warning: the rule-based reading](#8-early-warning-the-rule-based-reading)
9. [The AI final-grade forecast](#9-the-ai-final-grade-forecast)
10. [The backup rule when the AI is unavailable](#10-the-backup-rule-when-the-ai-is-unavailable)
11. [Students and profiles](#11-students-and-profiles)
12. [Dashboards](#12-dashboards)
13. [Privacy, security and records](#13-privacy-security-and-records)
14. [How it is built](#14-how-it-is-built)
15. [Known limits](#15-known-limits)

---

## 1. Who uses it

| Role | Who | What they do | What they cannot do |
|---|---|---|---|
| **Teacher** | Subject teachers and advisers | Encode or import class records, view their students, the early-warning list and the AI forecasts | See classes they are not assigned to |
| **Admin** | The school head (principal) | Approve teachers, set up school years, levels, sections, subjects and assignments, change school settings, see teachers' grading progress | Open individual student records or early warnings |
| **System Administrator** | Whoever runs the deployment | Approve or reject new schools | Open any school's students, grades or warnings |

Students and parents have no accounts.

**Sidebar by role**

- Teacher: Dashboard, Students, Class Record (or *PACE Progress* at PACE schools), Early Warning, AI Risk Model (not shown at PACE schools)
- Admin: Dashboard, Teachers, Approvals, Class Management, School Settings
- System Administrator: Schools, Account

## 2. Two kinds of schools

Each school answers one question when it registers: *do teachers track progress through numbered booklets or modules (PACE)?* That answer is stored as `tracks_units` and changes the whole system for that school.

| | DepEd class-record school | PACE school |
|---|---|---|
| Teachers record | Written works, performance tasks, examinations | The current booklet (unit) each student has reached |
| Grades | Computed with the MATATAG rules (section 6) | None |
| Early warning based on | Grades (section 8) | Booklets behind (section 8) |
| AI forecast | Yes (section 9) | No |
| Term name | Taken from School Settings, e.g. "Quarter" | Same |

Pages built for one kind never show the other kind's content. A PACE school sees no grade columns, and a grade school sees nothing about booklets. Whether a school is public or private is a separate field and does not change any of this.

## 3. Joining and signing in

### How a school joins

1. The school head registers the school together with their own Admin account. The form asks for the school's name, type and address, whether it tracks booklets, the passing mark and the grading periods.
2. The school stays **pending** until the System Administrator approves it.
3. Teachers then sign up, pick the school from a list (shown as *School name — City*), and wait for the Admin to approve them.

The school's address is picked from the PSA's official list (PSGC): province, then a city or municipality in that province, then a barangay in that city. Typing filters each list by the letters a name starts with. A box stays locked until the one above it is chosen. The region fills itself in from the province.

### Signing in

| Rule | Setting |
|---|---|
| Password | At least 8 characters, with an uppercase letter, a lowercase letter, a number and a special character |
| Two-factor code | Required on first sign-in from a new browser or device; the device is then trusted for **90 days** |
| One-time code | Expires after **10 minutes**; **5** wrong tries void it |
| Failed sign-ins | After **5** failures the account locks for **5**, then **15**, then **60** minutes; after **3** lockouts it is locked permanently until an Admin unlocks it |
| Session | Access token lasts **60 minutes**, refresh token **7 days** |
| Forgot password | A one-time code for the registered email; at most **5** reset requests an hour; once the code is verified, the reset is valid for **30 minutes** |
| Temporary password | Issued by the Admin, shown **once**, and the teacher must change it at the next sign-in |

> No mail service is connected yet, so the one-time code is shown on screen (`DEBUG_OTP`). Connect email and turn this off before real records go in.

## 4. Setting up a school

The Admin sets these up in **Class Management**, in this order:

1. **School years.** Only the label is entered, e.g. `2026-2027`. It must be two consecutive years; spaces such as `2026 - 2027` are tidied automatically. The dates are derived from it (June 1 to May 31) and are used only to keep years in order. One year is marked *current*.
2. **Grade levels.** Only the level (e.g. `7`) and display name (e.g. `Grade 7`) are entered. Lists are ordered by the number in the level, so Grade 10 comes after Grade 7. A level with no number, such as Kinder, comes first.
3. **Sections.** Code, name, grade level and capacity.
4. **Subjects.** Name, code and grade level; PACE schools also enter the number of booklets per year. When a subject is edited, it can be marked **not in use**. That hides it from the class record and dashboards without deleting its grades.
5. **Teacher assignments.** A teacher is assigned to a section, either for one subject or as the section's **adviser**.

**School Settings** holds the passing mark (default **75**), the number of grading periods (default **3**), the name of a grading period (e.g. "Quarter", used everywhere a term is named), the address and contact details, and whether the school tracks booklets.

## 5. Who can see and grade what

The rule (in `backend/app/services/access.py`):

- A teacher **grades only the subjects assigned to them**, in the sections they are assigned to.
- An **adviser sees every subject** of their advisory section, but grades only the subjects assigned to them.
- An assignment with no subject means *all subjects in that section*. If the teacher also has a specific subject assignment in that section, they are limited to those subjects there.
- The Admin is not limited by assignments, but does not use the student pages.

Any other save is refused with a message such as *"You are not assigned to teach Science in this section."* A student or record from a section the teacher cannot see is reported as **not found**, so the system does not even reveal that it exists.

## 6. The class record and how grades are computed

DepEd schools use the **MATATAG three-term E-Class Record**. The rules live in `backend/app/services/grading.py`, and the browser uses a copy in `frontend/src/lib/grading.js`. Both give the same answer, and both match DepEd's workbook exactly.

### Components and weights

| Component | Items | Core subjects* | MAPEH |
|---|---|---|---|
| Written works (WW) | As many as the teacher sets up | 20% | 20% |
| Performance tasks (PT) | As many as the teacher sets up | 50% | 60% |
| Examinations | ST1, ST2 and the Term Exam, shared **30 / 30 / 40** | 30% | 20% |

\* Science, Mathematics, English, Filipino, Araling Panlipunan.

The teacher sets the **highest possible score** of every item in *Set up record*, separately for each grading period.

### Steps

1. **Percentage score** = total of a component's scores ÷ total of its highest possible scores × 100. The denominator includes every item in the setup, so a component reads low until all of its items are given.
2. **Weighted score** = percentage × the component's weight.
3. **Examinations:** each of ST1, ST2 and the Term Exam is scaled to its own share (30, 30, 40), and the three are added.
4. **Initial grade** = WW + PT + exam weighted scores.
5. **Transmuted (term) grade** comes from the official table. Some of its bands:

| Initial grade | Term grade | | Initial grade | Term grade |
|---|---|---|---|---|
| 99.50 – 100 | 100 | | 70.00 – 71.17 | 75 |
| 87.70 – 88.87 | 90 | | 65.34 – 69.99 | 74 |
| 75.90 – 77.07 | 80 | | 46.67 – 51.33 | 70 |
| 74.72 – 75.89 | 79 | | 0 – 4.67 | 60 |

6. **Descriptor:** Advancing (90+), Benchmarking (80–89), Connecting (75–79), Developing (65–74), Emerging (below 65).
7. **Final grade** = the average of the term grades. **Passed** if 75 or higher.

### Rules worth knowing

- **Blank is not zero.** An empty cell means "not given yet" and is stored as empty. A 0 means the student scored zero.
- **No grade on an incomplete record.** DepEd's workbook computes a grade as soon as *any* component has a score, which makes a half-done term look like failing. TRACE shows no term grade until written works, performance tasks and all three examinations have scores.
- **Full precision.** The initial grade is computed without rounding the steps and is rounded only for display. Rounding each step can push a student over a band edge, for example 73.533 becoming 73.54, which gives 78 instead of the official 77.
- **Scores above the highest possible score** are kept, because the school's own records have bonuses. They are highlighted in the grid.
- **Changing a setup regrades** every record in that section, subject and grading period, and only that grading period.

## 7. Importing an E-Class Record workbook

In Class Record, a teacher picks the section and subject and clicks **Import from Excel**. The import reads DepEd's *3-Term E-Class Record (Grades 2–10)* `.xlsx` file.

**What is read:**

- From the *INPUT DATA* sheet: school ID, school year, grade level, section and subject (used for warnings), and the boys' and girls' names.
- From each *TERM* sheet: the highest possible scores, the three weights, and the raw scores (WW1–5, PT1–3, ST1, ST2, TE).
- Everything the workbook computes itself is ignored, and TRACE computes it again.

**How it works:**

1. The file is read and a **preview** is shown. Nothing is saved yet. The preview shows:
   - how many students are already in the class and how many are new;
   - each term's student count and how many are fully graded;
   - warnings, such as the file being for another subject, section or school year, a name appearing twice, odd marks like "A" or "Ex" (read as blank), or scores above the highest possible score.
2. On **Import**:
   - students not in the school are **added and enrolled**;
   - students in the school but not enrolled this year are **enrolled**;
   - students enrolled in **another section** are skipped and listed;
   - each term's setup is saved, then the scores go through the same save as typed scores, so grades, warnings and the forecast follow.

**Matching names:** by last name and first name, ignoring capitals, spaces and punctuation. The middle initial does not count. Importing the same workbook again does **not** duplicate students.

**Importing again later** (for example, a term later):

- New and changed scores are taken from the file, and grades are recomputed.
- **The file wins:** a score changed in TRACE after the first import is replaced by the file's value.
- A **blank cell** in the file clears that score, if the student has any score in that term. A term that is entirely blank for a student is left alone.
- A student whose name was **re-spelled** in the file is treated as a new student.
- Students missing from the file stay enrolled.

**The file is not stored.** It is read in memory and discarded; only the names and scores taken from it are saved, and an audit-log entry records the import. The upload limit is **5 MB**. Old `.xls` files must be saved as `.xlsx` in Excel first.

## 8. Early warning: the rule-based reading

Every save re-scores the student, and the result goes to the **Early Warning** list. The rules are in `backend/app/services/risk.py`. They need no AI, so they always work.

### Risk levels

Each reading produces a risk score from 0 to 100:

| Score | Level | Shown as |
|---|---|---|
| 70 – 100 | critical | Critical |
| 50 – 69.9 | high | At Risk |
| 30 – 49.9 | moderate | Warning |
| below 30 | low | On Track |

### DepEd schools

What a student is judged on depends on how far the year has gone:

- **Before any term is finished:** the coursework banked so far in the current term (written works plus performance tasks, scaled to 0–100) is used. Once the term's record is complete, its term grade is used.
- **Once at least one term is finished:** the **year average** of the finished terms is used.

That number becomes a risk score against the school's **passing mark** (75 unless the school changed it):

| Where the student stands | Risk |
|---|---|
| Below the passing mark | Critical (more points below → higher score; 10 below is the maximum) |
| 0 to 5 points above it (75–79) | At Risk |
| 5 to 10 points above it (80–84) | Warning |
| More than 10 above | On Track |

Two extra rules on the year reading:

- The system watches a **line of 80**. A student whose year average is above passing but below 80 is lifted to **At Risk**, so they appear on the list a teacher works from.
- If the student would need an average **above 100** in the remaining terms to reach 80, they are marked as unable to reach it and shown at the top.

The suggested next step comes with a number, e.g. *"needs 84 across the next two terms"*, because a target is something a teacher can tell the student and guardian.

### PACE schools

- **Booklets behind** = expected booklet − current booklet (never negative).
- Risk score = **65 × min(booklets behind ÷ 4, 1)** + **35 × (1 − completion %)**. Four or more booklets behind is the maximum.

Attendance is not used at either kind of school, because the class records the system is built around have no attendance column.

### Counting

- Early Warning tiles count **flags** (student–subject pairs), with the number of students as a footnote.
- The dashboard counts **students**, by each student's worst flag.
- The sidebar badge counts critical students.
- A flag **closes by itself** when the student is back On Track; the history is kept.
- A teacher can **mark a flag as handled**. It reopens at the next save if the student is still at risk.

## 9. The AI final-grade forecast

DepEd schools only. As soon as a student's **first term** in a subject is fully graded, the AI service predicts their **final grade** in that subject, a **likely range** and the **chance of failing**.

### The model

| | |
|---|---|
| Algorithm | Extra Trees regressor (scikit-learn 1.8), 400 trees, at least 5 records per leaf, fixed random state 42 |
| Version | `4.0.0-final-grade` (service reports `v4`) |
| Predicts | The **change** from the first-term grade, which is added back onto the student's own first-term grade |
| Inputs (7) | First-term grade, first-term initial grade, WW %, PT %, exam %, component gap (highest minus lowest of the three percentages), grade level |
| Likely range | 80% interval from the model's own out-of-fold errors: about **−1.9 to +2.0** |
| Chance of failing | Share of those past errors that would carry the forecast below **75** |
| Flag | **HIGH** when the chance of failing is **50% or more**, otherwise LOW |
| Main factor | Shown for HIGH forecasts: the input with the largest *importance × distance from the training average* |

**Why regression and not yes/no:** only 14 of 1,393 records in the training data failed. A classifier would learn "fail" from 14 examples. Predicting the grade lets every student teach the model how grades move after the first term.

**Why the change from the first term:** the final grade contains the first term, so "final = first term" is the honest baseline and the model is measured against it. The training records were graded under older transmutation tables, and predicting a change is much less sensitive to that than predicting a grade.

### Training data

`Class_Record_Dataset_v6`: 1,393 learner–subject–school-year records of 582 learners from three public schools, Deogracias U. Tenazas Memorial ES (797), Gumagamot ES (299) and Looc NHS (297). The records cover Grades 1, 3, 4, 5, 6 and 8, school years 2023-2024 to 2025-2026. Names were replaced by a hashed key and sex was dropped.

### How accurate it is

Measured with learner-grouped cross-validation (5 folds × 5 repeats):

| Model | Average error (grade points) | Within ±2 | Failures caught |
|---|---|---|---|
| **Extra Trees (deployed)** | **1.23** | 79.8% | 11 of 14, 1 false flag |
| Random Forest | 1.25 | 79.5% | 11 of 14 |
| Gradient Boosting | 1.27 | 79.9% | 11 of 14 |
| Rule: first term + rise for grade level | 1.29 | 78.8% | 12 of 14, 4 false flags |
| Ridge Regression | 1.32 | 79.3% | 11 of 14 |
| Rule: first term + average rise | 1.35 | 79.1% | 11 of 14 |
| Final = first-term grade | 1.75 | 78.4% | 11 of 14 |

When a school is left out of training and the model is tested on it, it does **not** beat the best simple rule at that school. At Looc NHS it scored 1.78, against 1.50 for carrying the first-term grade forward. So check it against each school's own records, and retrain on them, before relying on it there.

### How the system calls it

- A forecast is requested only when the first term has the exam %, the initial grade and the transmuted grade.
- A whole class is sent as one batch (up to 100 records), with a **6-second** timeout. The *Wake it up* button waits up to 90 seconds, because the free server sleeps.
- Every answer is stored in `ai_risk_predictions` and never overwritten. Each entry keeps the inputs, the model version, the threshold, any substituted inputs, and whether it came from the model or the backup rule.
- Retraining: `ml/train_final_grade_model.py`. The model file is in `ai-service/model/`.

## 10. The backup rule when the AI is unavailable

If the AI service is asleep, down or too slow, the server answers by itself with this rule:

> **Predicted final grade = first-term grade + the average rise for that grade level**

| Grade level | Average rise | Flagged HIGH when first-term grade is |
|---|---|---|
| 1 | +0.88 | 73 or lower |
| 3 | +0.78 | 73 or lower |
| 4 | +1.59 | 73 or lower |
| 5 | +1.74 | 72 or lower |
| 6 | +1.01 | 73 or lower |
| 8 | +0.61 | 74 or lower |
| Any other level | +1.14 | 73 or lower |

- **Likely range:** prediction **−2.0 to +2.1**, which covered 80% of real final grades in the data.
- **Chance of failing:** the share of the rule's 101 recorded past errors that would push the prediction below 75. It is flagged **HIGH at 50% or more**, the same threshold as the AI.
- Grades are kept between **60 and 100**.
- Its average error is **1.29**, close to the AI's 1.23, so forecasts barely suffer while the AI is down.
- It is labelled *rule-based (rule-v4)* together with the reason, and is never shown as an AI prediction.
- At PACE schools, the backup is the booklet rule from section 8.

Fitted from the same data as the model, and stored in `backend/app/services/final_grade_rule.json`.

## 11. Students and profiles

- A student has one record and one **enrolment** per school year (section and grade level). Past years are kept.
- Profile details: birth date, address, parent or guardian, and guardian contact and email.
- A profile missing any of birth date, address, parent or guardian, or guardian contact is marked **Incomplete** on the Students page. The profile shows what is missing, with a *Fill in details* button. Students added by an import start this way.
- The student ID number (`login_id`, e.g. `S0012`) is assigned automatically.

## 12. Dashboards

- **Teacher:**
  - stat cards for their students, each opening the filtered list;
  - average grade by section and by subject, grades by grading period, and the risk distribution (at PACE schools: completion by section and subject, and actual against expected pace);
  - a *Needs attention first* list and recent activity;
  - a search box, which appears on the dashboard only.
- **Admin:**
  - cards for teachers, pending approvals, sections and learners enrolled;
  - *Grading progress by teacher* (at PACE schools, whether progress was updated in the last 30 days);
  - a *Needs attention* list and recent activity.
- **System Administrator:** schools by status, with approve, reject, deactivate and reactivate.

## 13. Privacy, security and records

- **One school, one boundary.** Every school record carries the school's ID, and every query is filtered by the signed-in account's school. A request for another school's record returns *not found*. An automated test (`backend/tests/test_tenant_boundary.py`) checks that every endpoint applies this.
- The System Administrator account belongs to no school and is refused by every endpoint that reads student data.
- Passwords are stored only as salted hashes (bcrypt).
- **Audit log:** approvals, rejections, deactivations, password resets, school decisions, saves and imports are recorded with who did them.
- **Not stored:** uploaded workbooks, and the names in the training data (hashed).
- The deployment has no backup on the free hosting tier. Arrange one before real records go in.

## 14. How it is built

| Part | Technology | Folder |
|---|---|---|
| Web app | React 18 + Vite | `frontend/` |
| Application server | FastAPI (Python 3.12), SQLAlchemy 2 async | `backend/` |
| AI service | FastAPI + scikit-learn 1.8, deployed separately | `ai-service/` |
| Database | PostgreSQL on Render; SQLite locally | — |
| Model training | Python, pandas, scikit-learn | `ml/` |
| Hosting | Render (`render.yaml`): `trace-web`, `trace-api`, `trace-ai`, database `lbca-db` | — |

The AI service holds no student data and has no database connection. It receives seven numbers and returns a forecast, so it can be retrained and redeployed without touching the rest.

**Useful commands**

- `python seed.py --demo` (in `backend/`): demo school with 4 teachers and 3 sections of 5 learners, arranged to show the access rule.
- `python seed.py --rebuild`: **wipes everything** except the system administrator, then loads the demo.
- `python add_missing_columns.py --apply`: adds new columns to an existing database.
- `python tests/test_tenant_boundary.py`: checks the school boundary.
- `node scripts/build-places.mjs` (in `frontend/`): rebuilds the address lists from the PSA.

## 15. Known limits

- The AI was trained on three public schools and no PACE school. Its accuracy at any other school is untested until it is checked against that school's records.
- The forecast is validated only at the end of the first term. Before that, only the rule-based early warning is available.
- Only 14 failing cases were in the training data, so the "failures caught" figure rests on very few cases. The average grade error is the more reliable figure.
- Email is not connected, so one-time codes show on screen.
- The import reads DepEd's 3-Term E-Class Record for Grades 2–10 only. MAPEH workbooks and SF1 are not supported yet.
- Re-importing overwrites scores changed in TRACE, and the preview does not yet list what will change.
- The `class_schedules` and `teacher_availabilities` tables still exist but are unused, since the timetable was removed.
