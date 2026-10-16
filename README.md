# TRACE

Tracking and Risk Assessment for Continuous Education.

Student PACE monitoring and early-warning system for **Lord's Blessing Christian
Academy** — rebuilt from the ground up with the same stack as the original
(React + Vite on the front, FastAPI + SQLAlchemy on the back).

```
lbca/
├── backend/      FastAPI API, SQLAlchemy models, seed data
├── frontend/     React 18 + Vite single-page app
├── ai-service/   the trained risk model, as its own inference service
├── render.yaml   one-click deployment of all four pieces
├── DEPLOYMENT.md how to put it online, and the free-tier traps
└── MULTI_SCHOOL.md  many schools in one deployment, and the operator boundary
```

Three tiers, and the model is its own tier on purpose: it can be retrained and
redeployed without touching the monitoring system, and the backend does not have
to carry scikit-learn, numpy and pandas to use it.

---

## Quick start

Two terminals. Backend first.

### 1 · Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python seed.py                     # creates lbca.db with demo data
uvicorn app.main:app --reload --port 8000
```

The API is then at <http://localhost:8000>, with interactive docs at
<http://localhost:8000/docs>.

### 2 · Frontend

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>.

### 3 · The AI risk service (optional locally)

```bash
cd ai-service
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --port 8001
```

The system works without it. Records saved while it is down are scored by the
rule-based engine and labelled **rule-based** on screen — never presented as
model output — and the **Re-score records** button on the AI Risk Model page
re-runs them once it is up.

To have the demo data carry real predictions from the start, seed with
`python seed.py --score` while this service is running.

### Demo accounts

Every seeded account uses the password **`Lbca@2026`**.

| Email | Role |
|---|---|
| `admin@lbca.edu.ph` | Administrator — sees everything |
| `mramos@lbca.edu.ph` | Teacher — adviser, Grade 7 - Faith |
| `jsantos@lbca.edu.ph` | Teacher — adviser, Grade 8 - Hope |
| `pending@lbca.edu.ph` | Teacher, still awaiting approval (try signing in as them) |

**The first sign-in on a new browser asks for a 6-digit code.** There is no mail
server wired up yet, so while `DEBUG_OTP=true` the code is shown on the
verification screen itself and printed in the backend console. Once that device
is verified it is trusted for 90 days.

---

## What the system does

**PACE progress.** The school teaches a PACE curriculum: each subject is a
sequence of numbered booklets. Teachers record how far each student has reached;
the system works out the percentage finished, how many booklets they are behind
the expected pace, and a risk score.

**Early warning.** Every PACE entry re-evaluates the student's risk and creates,
updates or clears the matching warning. That means the warning list can never
drift away from the progress data behind it — there is no separate "recalculate"
step to forget.

Risk is scored 0–100 from three signals: booklets behind (55 %), curriculum
completed (25 %) and attendance (20 %), then bucketed:

| Score | Level | Shown as |
|---|---|---|
| 70 + | `critical` | Critical |
| 50–69 | `high` | At risk |
| 30–49 | `moderate` | Watch |
| under 30 | `low` | On track |

A student's own risk is taken from their **weakest** subject — excellent in four
subjects and four booklets behind in the fifth still needs intervention.

**The AI risk classifier.** A second, independent reading of the same students.
Where the rules above count PACE booklets, the model reads the DepEd class
record — absences, Written Work and Performance Task scores, unsubmitted items,
extracurricular load, grade level — and estimates the probability of finishing
the quarter below the 75 passing mark. It is a Random Forest, trained and
audited separately (see `ai-service/model/model_card_v4.json`), and it runs as
its own service.

The two readings are shown side by side and neither overrides the other. Three
rules are built into how the system handles the model, because a number without
its provenance is the failure mode this project set out to avoid:

- **Every prediction is logged with its input.** `ai_risk_predictions` is
  append-only: a re-score adds a row rather than replacing one, and each row
  stores the exact nine values the model was given, the threshold it used and
  the model version. Any figure on screen can be recomputed by hand.
- **A fallback is never dressed up as a prediction.** If the model cannot be
  reached in time, the rule engine answers instead and the row is marked
  `source="fallback"`. The UI gives it a different badge and says why.
- **Missing inputs are declared, not invented.** A blank assessment cell is
  saved as *not encoded*, never as zero. The service substitutes its training
  median, reports which fields it filled in, and the prediction is badged
  **partial data**.

The model's measured performance and its documented limitations are served by
the model itself at `GET /model-card` and printed on the AI Risk Model page
beside the metrics — including that it has **not** been validated mid-quarter,
so the system must not be described as warning weeks ahead of a failing grade.

**Many schools, one system.** Any number of schools share one deployment and
each sees only its own data. A school head registers their school; the system
operator approves it; teachers then pick that school when they sign up and wait
for their own principal. Whether a school tracks lesson units — and what it
calls them — is a setting answered at registration, not something written into
the code, so a DepEd school never sees a PACE screen and a School of Tomorrow
school sees its own vocabulary throughout.

The system operator manages schools and accounts and **cannot open a student's
record in any school**. That is enforced in one dependency rather than left to
each endpoint to remember, and `backend/verify_boundaries.py` proves it against
the source. See **MULTI_SCHOOL.md**.

**Accounts.** Staff register themselves, then wait for an administrator to
approve them. Five wrong passwords locks the account for 5, then 15, then 60
minutes; a third lockout is permanent until an administrator lifts it. Every
privileged action is written to an audit trail.

---

## Screens

| Route | Who | What |
|---|---|---|
| `/login`, `/register`, `/verify`, `/forgot`, `/reset` | everyone | Sign-in, registration, 2FA, password reset |
| `/dashboard` | all staff | Risk counters, completion charts, the students to see first |
| `/students` | all staff | Directory with search, filters, sorting, CSV export |
| `/students/:id` | all staff | One student: details, per-subject PACE, enrolment history |
| `/pace` | all staff | Encode a whole section's progress in one submit |
| `/early-warning` | all staff | Flagged students, ranked, with suggested interventions — and the model's separate reading beside each one |
| `/ai-risk` | all staff | The classifier: service status, what it says, the input behind each prediction, and its limitations |
| `/schedule` | all staff | Weekly timetable (teachers see their own) |
| `/account` | all staff | Profile, photo, password |
| `/teachers` | admin | Staff directory, lock/unlock, temporary passwords |
| `/approvals` | admin | Registration queue and the audit trail |
| `/class-management` | admin | School years, grade levels, sections, subjects, assignments |

Teachers only see data for the sections they are assigned to; administrators see
the whole school.

---

## Changing the pictures

Every photograph the interface uses is listed in
**`frontend/src/config/images.js`** — the sign-in background, the dashboard
banner, the student-profile header. They currently point at free Unsplash
photos.

To use your own: drop the file into `frontend/public/images/` and change the
entry to `/images/your-file.jpg`. Nothing else needs touching. Every photo sits
behind a gradient, so the screens still look finished if an image is ever
unreachable.

The school name, tagline and address are in the same file, under `SCHOOL`.

## Changing the look

`frontend/src/styles/tokens.css` holds every colour, font size, radius and
shadow as a CSS custom property. Change the brand ramp there and the whole app
follows — including dark mode, which has its own set of values in the same file.

---

## Using PostgreSQL

SQLite is the default so the project runs with no database server. To switch,
put this in `backend/.env`:

```
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/lbca
```

The models use portable column types, so the same code creates the same schema
on either database.

---

## Deploying it online

`render.yaml` deploys all four pieces from one commit. **`DEPLOYMENT.md` has the
full walkthrough** — read the top of it first, because a free Render Postgres
expires 30 days after it is created, which makes deployment timing a scheduling
decision rather than a technical one.

---

## Before going live

1. Set a real `SECRET_KEY` — `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
2. Set `DEBUG_OTP=false` and wire up email delivery (the two places that print a
   code are in `app/api/routers/auth.py`). While it is on, two-factor
   authentication is decoration.
3. Set `ALLOWED_ORIGINS` to your deployed frontend URL.
4. Serve over HTTPS and move to PostgreSQL.
5. Point `VITE_API_URL` in `frontend/.env` at the deployed API, then
   `npm run build` and serve `frontend/dist/`.
6. Set `AI_SERVICE_TOKEN` to the same value on the API and the AI service, so
   the model service is not open to anyone who finds its URL.
7. Arrange backups. Nothing in this stack keeps a second copy of the database.
