# Deploying grades and sections without codes

This release combines **Grades & sections** and removes `sections.section_code`
and `subjects.subject_code` from the database, API and forms. Section and subject
IDs stay the same, so enrollments, grades, assignments and class-record setups
keep their links. Names must be unique within the same school and grade.

The VM needs a one-time database migration. Cloudflare Pages can publish the
frontend from a push to `main`; that does not update the Azure backend or database.
Coordinate the frontend publish with this maintenance window. The new combined
save endpoints return an error against the previous backend rather than silently
discarding sections.

## Before the update

1. Take a fresh database backup using the existing backup procedure in
   [STUDENT_HOSTING_GUIDE.md](../STUDENT_HOSTING_GUIDE.md). Verify the archive can
   be read and keep it outside the database server. Never run `seed.py` against
   the existing live database.
2. Commit and push the intended changes. Confirm the VM checkout has no local
   changes before pulling; preserve any local changes deliberately.
3. Schedule a brief maintenance window so users are not saving records. If an
   automatic deploy timer exists and is active, pause it for this update and
   restore it afterward. Your reported `trace-deploy.timer: Unit not loaded`
   means there is no timer to stop.

## On the Azure VM

Connect with your usual SSH command, then run these commands individually:

```bash
cd /opt/trace
git status --short
git rev-parse HEAD
sudo systemctl stop trace-api
sudo -u trace git -C /opt/trace pull --ff-only origin main
```

Record the previous commit and backup location. If pulling fails, resolve that
before migrating. The following transient service uses the API's existing
database settings without printing its environment file or credentials.

First inspect the proposed changes; this command does not modify the database:

```bash
sudo systemd-run --quiet --wait --pipe --collect \
  --property=User=trace --property=Group=trace \
  --property=WorkingDirectory=/opt/trace/backend \
  --property=EnvironmentFile=/etc/trace/api.env \
  /opt/trace/backend/.venv/bin/python /opt/trace/backend/migrate_academic_names.py
```

Confirm this is the intended database and the row counts are plausible. Duplicate
or blank names cause the script to stop before changing anything. It reports
the affected row IDs; resolve those names deliberately, then repeat the check.

After verifying the fresh backup, apply the migration:

```bash
sudo systemd-run --quiet --wait --pipe --collect \
  --property=User=trace --property=Group=trace \
  --property=WorkingDirectory=/opt/trace/backend \
  --property=EnvironmentFile=/etc/trace/api.env \
  /opt/trace/backend/.venv/bin/python /opt/trace/backend/migrate_academic_names.py --apply
```

Proceed only if it exits successfully. Then restart and check the API:

```bash
sudo systemctl restart trace-api
sudo systemctl status trace-api --no-pager
curl --fail --silent --show-error https://api.traceedu.app/health
```

The health response must not report an out-of-date schema. Confirm the matching
Cloudflare Pages deployment succeeded, then reload the site. Check that existing
sections, subjects and class records still load. Under **Class Management →
Grades & sections**, confirm grades contain their sections and the modal shows
grade details above the section list. Subject forms should request no code.

## Local development and recovery

For an existing local database, stop the API and run from `backend` using its
configured environment:

```powershell
.venv/Scripts/python.exe migrate_academic_names.py
.venv/Scripts/python.exe migrate_academic_names.py --apply
```

New empty databases created from the current models already omit the fields.
The migration can be rerun: it skips columns already removed and installs any
missing name constraints. SQLite runs integrity and foreign-key checks while
rebuilding the two tables; PostgreSQL locks the tables and removes the columns
in one transaction. Unexpected dependencies cause a failure and rollback.

If migration fails, keep the API stopped while investigating; no partial update
should be committed. Before a successful migration, the recorded old commit
can still run against the original schema. After success, rolling back only the
code is insufficient: the old models expect the removed columns. Restore the
verified backup together with the matching old application version if a full
rollback is required. Retain the backup until the release has been verified.

Database and service behavior follows the official [SQLite table-change
procedure](https://www.sqlite.org/lang_altertable.html), [PostgreSQL ALTER
TABLE documentation](https://www.postgresql.org/docs/18/sql-altertable.html) and
[systemd-run documentation](https://www.freedesktop.org/software/systemd/man/latest/systemd-run.html).
