# Many schools, one system

The system serves any number of schools from one deployment. Each school sees
only its own data. The person who operates the deployment can approve schools
and manage accounts — and cannot open a single student's record anywhere.

---

## The three roles

| Role | What they do | Which school |
|---|---|---|
| **System operator** (`super_admin`) | Approves, rejects and suspends schools. Sees how many staff and students each has. | None — belongs to no school |
| **School administrator** (`admin`) | Runs one school: approves its teachers, sets up its classes, edits its settings. | Exactly one |
| **Teacher** (`teacher`) | Records progress and grades for their own sections. | Exactly one |

## How a school joins

```
operator created from the command line
        ↓
principal registers their school on the website      →  pending
        ↓
operator approves it                                  →  the principal can sign in
        ↓
teachers pick that school when they register          →  pending
        ↓
the principal approves them                           →  they can sign in
```

A teacher waits for their principal; a principal waits for the operator. It is
the same mechanism at two levels, not two mechanisms.

---

## The boundary, and why it is the interesting part

A system operator who could read every child's record in every school would be
a worse problem than the one this system solves. So the operator's account is
not a bigger administrator — it is a different, narrower job.

| The operator **can** | The operator **cannot** |
|---|---|
| List every school | Open any student's record |
| Approve, reject, suspend, restore a school | See a grade, an absence or a risk flag |
| See a school's staff and student **counts** | Browse a class list |
| Manage their own account | Act inside a school's day-to-day work |

This is not a screen that was never built. It is enforced in the API, in one
place — `school_scope` in `backend/app/api/deps.py` — which every route that
touches a student's data depends on, and which refuses the operator outright.
An endpoint that forgot to scope its query would have no school id to query
with in the first place.

### Proving it

```bash
cd backend
python verify_boundaries.py
```

This reads the source and reports, route by route, which dependency guards it.
It also finds any query on a school-owned table that is not filtered by the
caller's school, and any single-row fetch that bypasses the scoping helper. It
exits non-zero if either boundary is broken, and it needs no database or
server, so it runs anywhere.

Current result: **61 routes refuse the operator**, 21 are open by design with a
stated reason each, **102 queries** on school-owned tables are all filtered,
and **no** bare `db.get()` remains.

The checker was tested by deliberately breaking the code three ways — removing
a scope dependency, unscoping one query, and reintroducing a bare `db.get` —
and it caught all three. A check that cannot fail proves nothing, so that test
is worth repeating if you change how scoping works.

---

## Public or private is not the same question as PACE

The one design point worth defending carefully.

PACE booklets come from School of Tomorrow / ACE. **Most private schools do not
use them** — ordinary private academies, Catholic schools and international
schools follow the DepEd curriculum, exactly like public schools. LBCA happens
to be both private *and* ACE, which makes the two look like one thing.

So registration asks both, separately:

- **Public or private** — how the school is run. Recorded, reported on, and
  deliberately **not** used to decide which features appear.
- **"Do teachers track progress through numbered booklets or modules?"** — the
  question that actually decides it, with the school's own name for them.

Answer no and the lesson-progress screens never appear: that school records
attendance and grades only, and the route is unreachable, not merely hidden.
Answer yes and every screen uses the school's word — "PACE Progress" at LBCA,
"Module Progress" somewhere else — because none of it is hard-coded.

Grading settings work the same way: passing mark, number of periods and what a
period is called are stored per school. A grading rule written into the code is
a rule that goes stale without anyone noticing.

---

## Running it

### A fresh database

```bash
cd backend
python seed.py --reset --score
```

Seeds **two** schools on purpose — a demo with one school proves nothing about
multi-tenancy:

| | Lord's Blessing Christian Academy | Mabini National High School |
|---|---|---|
| Type | private | public |
| Tracks units | yes, "PACE", 12 per subject | no |
| Subjects | ACE curriculum | DepEd curriculum |

Sign in as `superadmin@system.local` to see the operator's view, then as
`admin@lbca.edu.ph` or `principal@mabini.edu.ph` to see two schools that cannot
see each other. Every demo account uses the password printed in the summary.

### An existing single-school database

Your current deployment has real test data in it. Do **not** reset it — migrate:

```bash
cd backend
python migrate_multitenant.py \
  --school-name "Lord's Blessing Christian Academy" \
  --city "Cagayan de Oro" \
  --private \
  --tracks-units PACE
```

It creates the `schools` table, adds the `school_id` column everywhere, creates
one approved school from those arguments, and assigns every existing row to it.
Nothing is deleted, and running it twice is safe — each step checks first.

Then create the operator:

```bash
python seed.py --create-superadmin you@example.com
```

The password is printed once and not shown again.

**Test this locally against SQLite before running it on Render.** The schema
changes are compile-checked and statically verified, but they have not been
executed against a live database here, so the first real run should be one you
can throw away.

---

## What changed, in short

**New**
- `schools` table; `school_id` on fourteen tables
- `school_scope` / `school_admin_scope` / `get_super_admin` dependencies
- `app/api/scoping.py` — `scoped()` and `get_scoped()`, used everywhere
- `/api/schools/*` — registration, directory, operator console, school settings
- `migrate_multitenant.py`, `verify_boundaries.py`, `seed.py --create-superadmin`
- Registration page with the teacher / school-administrator choice
- Schools console for the operator; School Settings for a principal

**Changed**
- The first account to register no longer becomes an administrator. With many
  schools that would have handed control of everything to whoever signed up
  first. The operator is created from the command line; a school's first
  administrator is created with the school.
- `POST /api/auth/register` requires `school_id` and only accepts `role:
  "teacher"`. Self-registering as an administrator would have let anyone award
  themselves a school they merely picked from a list.
- Codes that used to be globally unique are now unique per school: school year,
  grade level, section code, subject code, student login id, employee number.
- Sign-in is refused while a school is pending or suspended, checked on every
  request rather than only at sign-in, so suspending a school takes effect at
  once.
