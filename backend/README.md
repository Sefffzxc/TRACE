# TRACE — API

FastAPI + SQLAlchemy (async). SQLite out of the box, PostgreSQL when you want it.

## Run it

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # Windows: Copy-Item .env.example .env

python seed.py --demo              # add the local demonstration school
uvicorn app.main:app --reload --port 8000
```

Docs: <http://localhost:8000/docs>. Do not reset or reseed an existing production
database when configuring email or moving hosts.

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
- **Two-factor** — an unrecognised device gets a 6-digit code, valid 5 minutes,
  5 attempts. Verified devices stay trusted for 90 days.
- **Tokens** — 60-minute access token, 7-day refresh token, both signed HS256.
- **Rate limits** — in process, with separate account and shared-network limits:
  10 login/OTP attempts per account per minute, 5 registrations/reset requests
  per email per 5 minutes. Shared-IP defaults are 60 login/OTP requests per
  minute and 60 registrations/reset requests per 5 minutes, configurable with
  `AUTH_*_IP_LIMIT`. Rejected requests do not extend the current wait.
  Use a shared counter store before running multiple API workers/instances.
- **Concurrent sign-ins** — bcrypt runs in worker threads. Login releases its
  database connection before waiting on an OTP email. Outbound email starts are
  paced with `EMAIL_SEND_RATE_PER_SECOND=2` and a bounded 20-second queue;
  provider rate-limit retries are bounded and email quotas still apply.
- **Audit** — every approval, rejection, lockout, forced reset and student
  change is recorded with the actor and their IP.

## Configuration

Copy `.env.example` to `.env`. The important ones:

| Variable | Default | Note |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./lbca.db` | `postgresql+asyncpg://…` for Postgres |
| `SECRET_KEY` | placeholder | **Change this before deploying** |
| `DEBUG_OTP` | `false` | Returns the code in the response only when explicitly enabled for local development. The example .env enables it locally. |
| `LOG_OTP_CODES` | `false` | Copies codes to API logs independently of on-screen debug. Enable temporarily for the defense, then disable. |
| `OTP_EXPIRY_MINUTES` | `5` | Login, password-reset and application-correction code lifetime. |
| `EMAIL_ENABLED` | `false` | Send verification codes and account approval/rejection notices through Resend. |
| `RESEND_API_KEY` | empty | API-server secret, never a frontend variable. |
| `EMAIL_FROM` | empty | Verified sender, e.g. `TRACE <noreply@auth.yourtrace.app>`. |
| `EMAIL_TIMEOUT_SECONDS` | `10` | HTTP timeout per email request: more than 0, at most 60 seconds. |
| `ALLOWED_ORIGINS` | localhost dev ports | Comma-separated frontend origins |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | |
| `TWO_FACTOR_DAYS_INTERVAL` | `90` | How long a device stays trusted |

## Sending verification and account decision emails

`app/services/email.py` sends login and password-reset codes through Resend's
HTTPS API using the existing httpx dependency. Set `EMAIL_ENABLED=true`, the
verified `EMAIL_FROM`, and `RESEND_API_KEY`. Keep `DEBUG_OTP=false` on deployment.
No database migration is required. After changing the OTP lifetime, deploy the
frontend too so its countdown and instructions match the five-minute setting.

Principals receive an email when their school registration is approved or
rejected. Teachers receive an email when their account is approved or rejected;
rejection messages include the reason. Restoring a rejected registration also
sends an approval notice. A notification must be accepted by Resend before the
decision is saved; a failure rolls back the decision and audit entry and returns
a retryable error to the administrator.

All messages share a TRACE header with an inline PNG logo, attached using
[Resend's Content-ID support](https://resend.com/changelog/embed-images-using-cid).
The asset lives in `app/assets/email/trace-logo.png` and ships with the API.

For the defense, set `LOG_OTP_CODES=true`: the same emailed code appears as an
`[OTP]` entry in the API process logs after the code is committed. Teachers still
receive the email. Set the flag false and restart/redeploy the API after the
defense; email delivery continues.

An enabled email provider must accept the message before issuance succeeds.
Failures return a retryable sign-in error and do not save the failed code.
Password-reset requests retain the same public response for known and unknown
addresses, including provider failures; check API diagnostics and the Resend
dashboard if reset mail does not arrive. A provider acknowledgement does not
guarantee inbox delivery. Provider error bodies and credentials are not logged.

With email disabled, either `DEBUG_OTP=true` or `LOG_OTP_CODES=true` must be
explicitly enabled for local/testing use. If all delivery methods are disabled,
new-device login fails rather than issuing an inaccessible code.

See [the Student Pack guide](../STUDENT_HOSTING_GUIDE.md) for the domain/DNS,
Resend account and Ubuntu server configuration.

## Correcting rejected applications

Teachers and principals can verify ownership of their existing email and correct
a rejected application through `/resubmit`. The account returns to Pending with
the same ID, email, role, and password. School administrators review teacher
applications; the operator reviews school/principal applications. Both have
Rejected and Blocked queues, required rejection/block reasons, and decision
history. Blocked applications require an administrator to reopen them.

The sign-in page offers **Correct and resubmit** only after a correct-password
attempt returns an eligible rejection. Login returns structured
`application_rejected` / `can_resubmit: true` metadata in that 403 response;
incorrect credentials and other account states do not receive it. This grants
no login session, and email ownership verification is still required to edit.

The API adds `application_events` and `resubmission_challenges` through the
existing `init_models()` startup. This feature adds no existing-table columns
and requires no destructive migration. Correction codes expire in five minutes;
hashed, single-use correction tokens expire in fifteen minutes and never grant
normal account access. CAPTCHA uses the existing configuration with the
`resubmit` action. See [the resubmission guide](../APPLICATION_RESUBMISSION_GUIDE.md)
for school-change restrictions, deployment, and acceptance checks.
