# LBCA Monitoring System — API

FastAPI + SQLAlchemy (async). SQLite out of the box, PostgreSQL when you want it.

## Run it

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python seed.py                     # demo school: staff, sections, 118 students
uvicorn app.main:app --reload --port 8000
```

Docs: <http://localhost:8000/docs>. Reset and rebuild the demo data with
`python seed.py --reset`.

## Layout

```
app/
├── core/
│   ├── config.py        settings, read from the environment or .env
│   └── security.py      bcrypt hashing, JWTs, OTPs, password policy
├── db/
│   ├── session.py       async engine, session factory, Base
│   └── types.py         GUID / INET types that work on SQLite and Postgres
├── models/
│   ├── staff.py         staff, devices, sessions, OTPs, resets, audit log
│   ├── academic.py      school years, grade levels, sections, subjects,
│   │                    assignments, timetable, availability
│   └── students.py      students, enrolments, PACE records, warnings
├── schemas/             Pydantic request/response models
├── services/
│   ├── risk.py          the early-warning engine
│   ├── enrichment.py    flattens the relational model for the UI
│   └── audit.py         audit-trail helpers
├── api/
│   ├── deps.py          current user, role guards, rate limiting
│   └── routers/         auth · staff · academic · students · pace · dashboard
└── main.py              app assembly, CORS, error shaping
```

## The risk engine

`services/risk.py` is deliberately the only place risk is calculated. It scores
0–100 from three signals and buckets the result:

```python
behind_component     = min(1, paces_behind / 4) * 55      # 4+ behind saturates
completion_component = (1 - pace_percent / 100) * 25      # progress protects
attendance_component = min(1, (95 - attendance) / 20) * 20  # below 95 % counts
```

`evaluate()` returns every derived field at once — percentage, booklets behind,
score, level, status and the suggested intervention. Both the PACE router and
the seed script call it, so the numbers are identical wherever they appear.

Adjust the weights or the thresholds (`CRITICAL_AT`, `HIGH_AT`, `MODERATE_AT`)
in that one file and the whole system moves with them.

## Endpoints

| Group | Prefix |
|---|---|
| Authentication | `/api/auth/*` — register, login, verify-otp, refresh, logout, forgot/reset password |
| Staff | `/api/staff/*` — directory, own profile and photo, approvals, audit logs |
| Class management | `/api/school-years`, `/api/grade-levels`, `/api/sections`, `/api/subjects`, `/api/teacher-assignments`, `/api/class-schedules`, `/api/teacher-availabilities` |
| Students | `/api/students`, `/api/students/{id}/profile`, `/api/enrollments` |
| PACE & warnings | `/api/paces`, `/api/paces/bulk`, `/api/early-warnings` |
| Dashboard | `/api/dashboard` |

## Security

- **Passwords** — bcrypt, cost 12. Policy: 8+ characters with upper, lower,
  digit and symbol, enforced by Pydantic before anything reaches the database.
- **Sign-in** — 5 failures locks the account for 5 / 15 / 60 minutes; a third
  lockout is permanent until an administrator lifts it. The failure message is
  the same whether or not the email exists, so accounts cannot be enumerated.
- **Two-factor** — an unrecognised device gets a 6-digit code, valid 10 minutes,
  5 attempts. Verified devices stay trusted for 90 days.
- **Tokens** — 60-minute access token, 7-day refresh token, both signed HS256.
- **Rate limits** — per-IP, in process: 10 logins/minute, 10 OTP attempts/minute,
  5 password resets per 5 minutes. Swap for Redis if you run more than one
  instance.
- **Audit** — every approval, rejection, lockout, forced reset and student
  change is recorded with the actor and their IP.

## Configuration

Copy `.env.example` to `.env`. The important ones:

| Variable | Default | Note |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./lbca.db` | `postgresql+asyncpg://…` for Postgres |
| `SECRET_KEY` | placeholder | **Change this before deploying** |
| `DEBUG_OTP` | `true` | Returns the OTP in the response. Turn off once email works. |
| `ALLOWED_ORIGINS` | localhost dev ports | Comma-separated frontend origins |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | |
| `TWO_FACTOR_DAYS_INTERVAL` | `90` | How long a device stays trusted |

## Sending real OTP emails

Two places print a code instead of sending it, both in
`app/api/routers/auth.py` — search for `[OTP]`. Replace those prints with your
mail call and set `DEBUG_OTP=false`.
