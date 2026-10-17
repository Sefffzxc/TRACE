# TRACE — Reference for an AI Assistant

> **For the person:** paste this whole file at the start of a new chat with an AI assistant (for example Claude in the browser). Then:
> - For a question about **how the system behaves** (grading, rules, thresholds, the AI model), also paste the matching section of `SYSTEM_GUIDE.md`.
> - For a **code change**, also paste the full current contents of every file involved. The assistant cannot see the repository, so it only knows what you paste.
> - Ask for the **complete new version of the function or component** you're changing, not fragments, then paste it back yourself.

---

## What this is

TRACE (Tracking and Risk Assessment for Continuous Education) is a capstone web system for Philippine basic education schools. It keeps DepEd MATATAG class records, computes grades, flags students heading below the passing mark (a rule-based early warning), and forecasts each student's final grade from the first term (an AI regression model with a rule-based fallback). It is multi-school: each school's data is isolated. It also supports PACE (booklet-based) schools, which get progress tracking instead of grades.

## Stack and layout

| Part | Tech | Folder |
|---|---|---|
| API | FastAPI, Python 3.12, SQLAlchemy 2 **async**, Pydantic v2 | `backend/app/` |
| Web | React 18 + Vite, plain CSS (no Tailwind, no UI kit), lucide-react icons | `frontend/src/` |
| AI service | FastAPI + scikit-learn 1.8, separate deployment, holds no student data | `ai-service/main.py`, `ai-service/model/` |
| Training | pandas, scikit-learn | `ml/train_final_grade_model.py` (`ml/raw/` is git-ignored) |
| DB | SQLite locally (`backend/lbca.db`), PostgreSQL on Render | — |
| Hosting | Render free tier, `render.yaml`: `trace-web`, `trace-api`, `trace-ai`, DB `lbca-db` | — |

### Backend files

- `main.py`: app, CORS, the `CatchUnhandledErrors` middleware (added *before* CORS so 500 errors keep their CORS headers), router registration
- `core/config.py`: all settings (env-overridable); `core/security.py`: hashing, JWT, password policy
- `api/deps.py`: `get_current_user`, `get_current_admin`, `get_super_admin`, **`school_scope`** (the tenant gate)
- `api/scoping.py`: `scoped(stmt, Model, scope)`, `get_scoped(db, Model, id, scope)`, `get_scoped_optional`
- `api/routers/`:
  - `auth.py`: login, OTP, refresh, reset
  - `staff.py`: accounts, approvals, admin actions
  - `schools.py` (`/api/schools`): register, directory, settings, system-admin decisions
  - `academic.py`: school years, grade levels, sections, subjects, teacher assignments, `/api/my-classes`
  - `students.py`: students, enrolments, profile
  - `pace.py`: class records (`/api/paces`, `/api/paces/bulk`), class-record setup, early warnings, data-quality logs
  - `class_import.py`: `/api/class-record/import` (E-Class Record `.xlsx`)
  - `dashboard.py`: `/api/dashboard`, `/api/dashboard/admin`
  - `ai.py` (`/api/ai`): status, predictions, summary, rescore, model card
- `services/`:
  - `grading.py`: MATATAG grade maths
  - `risk.py`: early-warning rules; pure functions, does not import the ORM
  - `access.py`: `ClassAccess`, the teacher rule
  - `ml_client.py`: calls the AI service; the fallback rule
  - `final_grade_rule.json`: fallback parameters
  - `eclass_import.py`: workbook parser
  - `enrichment.py`: builds list responses
  - `audit.py`: `log_event`, `Action`
- `models/`: `schools.py`, `staff.py`, `academic.py`, `students.py`
- `schemas/`: same names
- Scripts in `backend/`:
  - `seed.py`: `--demo`, or `--rebuild`, which **wipes the DB**
  - `add_missing_columns.py`: dry run by default, `--apply` to change
  - `tests/test_tenant_boundary.py`

### Database tables

`schools`, `staff`, `staff_devices`, `sessions`, `otp_codes`, `password_resets`, `audit_logs`, `school_years`, `grade_levels`, `sections`, `subjects`, `teacher_assignments`, `students`, `student_enrollments`, `student_paces` (**the class record table**: one row per enrolment, subject and grading period; holds score arrays as JSON text plus computed grades, and PACE fields), `class_record_setups` (highest possible scores and weights per section, subject and period), `early_warnings`, `ai_risk_predictions` (append-only), `data_quality_logs`. `class_schedules` and `teacher_availabilities` still exist but are **unused** (the timetable was removed).

### Frontend files

- `App.jsx`: routes and guards (`RequireTeacher`, `RequireGradeRecord`). Routes:
  - auth: `/login /register /verify /forgot /reset`
  - teacher: `/dashboard /students /students/:id /class-record /pace /early-warning /ai-risk`
  - admin: `/teachers /approvals /class-management /school-settings`
  - system admin: `/schools`
  - all roles: `/account`
- `api/client.js` (fetch wrapper, `VITE_API_URL`) and `api/endpoints.js` (one object per area: `studentApi`, `paceApi`, `classRecordApi`…)
- `context/AuthContext.jsx`: `user`, `school`, `tracksUnits`, `unitLabel`, `periodLabel`, `gradingPeriodCount`
- `pages/app/`: one file per page. `ClassRecordPage.jsx` is the DepEd grid; `RosterPage.jsx` is PACE progress.
- `components/common/ui.jsx`: `Modal`, `PageHead`, `EmptyState`, `ErrorNote`, `TableSkeleton`, `Avatar`, `RiskBadge`, `ConfirmDialog`. Also `ai.jsx`, `AddressFields.jsx`, `ImportClassRecord.jsx`, `StudentFormModal.jsx`.
- `lib/grading.js`: **browser copy of `grading.py`, which must stay identical in behaviour**, plus `gradingPeriods(n)` and `periodName(n, label)`. `lib/standing.js`: `BASIS_LABEL`.
- `styles/`: `tokens.css` (colour tokens, light and dark), `base.css`, `layout.css`, `pages.css`, `auth.css`

## Rules the code must keep

1. **Tenant boundary.** Every endpoint touching a school's data takes `scope: int = Depends(school_scope)` and reads through `scoped()` / `get_scoped()`. Another school's record returns **404**, not 403. A new endpoint in a tenant router must follow this, or `tests/test_tenant_boundary.py` fails.
2. **Teacher access.** Use `access = await class_access(db, current, scope)`, then `access.require_grade(section_id, subject_name)` to save and `access.can_see(...)` / `access.require_section(...)` to read. Advisers see all subjects of their section but grade only their assigned ones.
3. **Two kinds of school.** Branch on `school.tracks_units` (frontend: `tracksUnits`). PACE pages show no grades or AI; grade pages show no PACE content.
4. **Blank is not zero.** A missing score is `null` all the way through. Never default a score to 0.
5. **No grade on an incomplete record.** WW, PT and all three exams must be present. Compute the initial grade at full precision and round only for display.
6. **Grading maths lives in two places** (`grading.py` and `grading.js`). Change both, the same way.
7. **Attendance is not used anywhere.** Don't reintroduce it.
8. **The AI is optional.** Saves never wait on it or fail because of it. When it's down, `ml_client.fallback_prediction` answers and the result is stored with `source="fallback"`. Never present a fallback as a model prediction.
9. **AI inputs are exactly 7 first-term features**, in the model's order: `q1_term_grade, q1_initial_grade, q1_ww_pct, q1_pt_pct, q1_exam_pct, q1_component_gap, grade_level`. The service rejects unknown fields (`extra="forbid"`). Forecasts are requested only when the first term is complete.
10. **Words come from settings.** The grading period's name is `periodLabel` (e.g. "Quarter"), and PACE units use `unitLabel`. Don't hard-code "Term" or "PACE" in the UI.
11. **Admins don't see student monitoring pages.** Their dashboard is about teachers and the school.

## Conventions

- Match the surrounding style. Python modules and functions have plain-English docstrings that explain *why*; comments are sparing.
- UI text is short and plain, for teachers who are not technical. No long sentences on pages.
- Colours come from CSS variables in `tokens.css` (dark mode included), never hard-coded hex values in components.
- Async SQLAlchemy throughout (`await db.execute(...)`), and `expire_on_commit=False`.
- Errors to users go through `HTTPException(status_code, detail="Plain sentence.")`.
- Audit important actions with `log_event(db, Action.X, actor_id=..., detail=..., request=request)`.
- Keep dependencies minimal: the backend runs on Render's free tier with 512 MB. The backend must **not** import scikit-learn, numpy or pandas.

## Changing the database

There are no Alembic migrations. A new column needs to be:
1. added to the model in `models/`;
2. added to the schema in `schemas/`;
3. applied to an existing DB with `python add_missing_columns.py --apply`. Locally, set `DATABASE_URL`; on Render, run it against the external database URL.

New tables are created automatically at startup.

## Running and checking

```bash
# backend (from backend/)
python -m uvicorn app.main:app --reload --port 8000
python seed.py --demo                      # demo school; password Trace@2026
python tests/test_tenant_boundary.py       # needs PYTHONUTF8=1 on Windows

# frontend (from frontend/)
npm run dev                                # http://localhost:5173
npm run build
npx eslint src                             # 4 known warnings are pre-existing; add no new ones

# AI service (from ai-service/), optional locally
python -m uvicorn main:app --port 8001
```

Demo accounts (after `--demo`): `principal@lnhs.deped.gov.ph`, and `teacher1@` to `teacher4@lnhs.deped.gov.ph`, all with password `Trace@2026`. The one-time sign-in code is printed in the backend console (`DEBUG_OTP=true`).

## Key numbers (details in SYSTEM_GUIDE.md)

- Weights: core subjects WW 20 / PT 50 / Exam 30, MAPEH 20 / 60 / 20; exams split ST1 30 / ST2 30 / TE 40. Passing mark 75, school-configurable; 3 grading periods by default.
- Early-warning score bands: critical ≥ 70, high ≥ 50, moderate ≥ 30. Students are lifted to At Risk when their year average is below 80.
- AI: Extra Trees regressor v4, MAE 1.23. HIGH when P(final < 75) ≥ 0.5. 80% range from out-of-fold errors. 6 s timeout, batches of 100.
- Fallback: first-term grade + average rise for the grade level (rise +0.61 for Grade 8, +1.14 for unknown levels), range −2.0 / +2.1, MAE 1.29.
- Security:
  - 5 failed sign-ins lock the account for 5, 15, then 60 minutes; 3 lockouts lock it permanently;
  - two-factor trust lasts 90 days; one-time codes expire after 10 minutes;
  - access token 60 minutes, refresh token 7 days.

## Known issues and gaps

- No email service, so one-time codes are shown on screen. This must change before real use.
- The import reads only DepEd's 3-Term E-Class Record (Grades 2–10). No MAPEH workbook, no SF1. A re-import overwrites scores edited in TRACE.
- No database backups on the free tier.
- The AI was never validated on a PACE school or on schools outside the training data.
