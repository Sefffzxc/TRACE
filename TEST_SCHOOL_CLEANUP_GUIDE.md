# Remove the selected test schools

Use `backend/cleanup_test_schools.py` to permanently delete **only the school IDs
you select**, including their linked test accounts, learners, enrollments,
assignments, class records, verification tokens and application history.
This is a database cleanup; no frontend build or schema migration is needed.

Deleting the selected school and its staff releases their school registration
and email addresses. You can register Gumagamot Elementary School again using
the same principal email after both unwanted Gumagamot registrations are removed.
The new registration starts with a new application and needs approval.

The global system administrator and unselected schools are preserved. History
belonging to another school is preserved when a deleted account was only its
actor; that actor reference becomes empty. The helper refuses a selection that
contains a system administrator or has unsafe links to another school's data.
It does not remove uploaded files from disk.

**Deletion is permanent unless you restore a backup.** The four screenshots do
not show school IDs, so identify the records before selecting them. Two schools
share the Gumagamot name: do not delete by school name or use a database reset.
Nothing in this guide has already been executed on your live database.

## 1. Copy the cleanup helper to the VM

In **Windows PowerShell**, from your project directory:

```powershell
scp -i "$env:USERPROFILE\Downloads\trace-server_key.pem" .\backend\cleanup_test_schools.py traceadmin@23.100.91.187:/home/traceadmin/cleanup_test_schools.py
ssh -i "$env:USERPROFILE\Downloads\trace-server_key.pem" traceadmin@23.100.91.187
```

In the **Ubuntu SSH terminal**:

```bash
sudo install -o trace -g trace -m 0640 /home/traceadmin/cleanup_test_schools.py /opt/trace/backend/cleanup_test_schools.py
```

Define this helper in the same terminal. It runs the script with the API's
existing database configuration without displaying the environment file:

```bash
trace_school_cleanup() {
  sudo systemd-run --quiet --wait --pipe --collect \
    --property=User=trace --property=Group=trace \
    --property=WorkingDirectory=/opt/trace/backend \
    --property=EnvironmentFile=/etc/trace/api.env \
    /opt/trace/backend/.venv/bin/python /opt/trace/backend/cleanup_test_schools.py "$@"
}
```

Run each block separately. If you close SSH, define the helper and repeat the
selection and preview before continuing; the shell variables do not survive.

## 2. List schools without changing anything

```bash
trace_school_cleanup --list
```

Match the ID, school name, city, status, principal email and learner count for
each intended test school:

| Screenshot | Status in screenshot | Learners in screenshot |
|---|---|---:|
| Gumagamot Elementary School, Lala | Rejected | 0 |
| Test Academy, City of Cagayan de Oro | Approved | 1 |
| Gumagamot Elementary School, Lala | Suspended | 41 |
| test, Carmen | Suspended | 20 |

Counts and statuses might have changed since the screenshots. **Stop here until
you have verified the exact four IDs.** If you are asking for help selecting
them, share the school list output; do not share passwords or environment files.
The API can remain running for this listing and the next preview.

## 3. Preview the exact selection

Run each `read` command, press Enter, then type the corresponding numeric school
ID at its prompt. Type only an ID, not a name, URL or command.

```bash
read -r -p "Rejected Gumagamot school ID: " trace_cleanup_rejected_id
```

```bash
read -r -p "Test Academy school ID: " trace_cleanup_academy_id
```

```bash
read -r -p "Suspended Gumagamot school ID: " trace_cleanup_suspended_id
```

```bash
read -r -p "test school in Carmen ID: " trace_cleanup_carmen_id
```

```bash
trace_cleanup_args=(
  --school-id "$trace_cleanup_rejected_id"
  --school-id "$trace_cleanup_academy_id"
  --school-id "$trace_cleanup_suspended_id"
  --school-id "$trace_cleanup_carmen_id"
)
trace_school_cleanup "${trace_cleanup_args[@]}"
trace_cleanup_preview_rc=$?
printf 'Preview exit code: %s\n' "$trace_cleanup_preview_rc"
```

This is a read-only preview. Check every selected school, account email and
table count. Only continue when the preview exits with `0` and contains exactly
the test schools you want removed. A refused selection needs investigation;
do not bypass the refusal with manual deletes.

## 4. Pause writes and take a fresh backup

Choose a time when nobody is entering records. The following block pauses an
automatic deployment timer only if it is currently active. An uninstalled timer
needs no action.

```bash
trace_cleanup_timer_was_active="${trace_cleanup_timer_was_active:-false}"
if systemctl is-active --quiet trace-deploy.timer; then
  trace_cleanup_timer_was_active=true
  sudo systemctl stop trace-deploy.timer
fi
if systemctl is-active --quiet trace-deploy.service; then
  printf 'A deployment is running. Wait for it to finish before stopping the API.\n'
else
  printf 'No deployment is running. Continue with the API stop.\n'
fi
```

If a deployment is running, wait for it to finish and rerun the last check. Then:

```bash
sudo systemctl stop trace-api
systemctl show trace-api -p ActiveState --value
```

The state must be `inactive`. Keep the API stopped until the cleanup is finished.

Take a **new** backup now; the November 15 migration backup does not include
later testing. Use the external PostgreSQL hostname from Render. The hostname
is just the server name, for example `dpg-....singapore-postgres.render.com`;
do not enter a `postgresql://` URL in the hostname prompt. For your existing
database, the database name is `trace_ht2p`; its dashboard label `trace-db` is
not the database name.

Run each prompt separately, entering its answer after pressing Enter:

```bash
read -r -p "Render external hostname only: " trace_db_host
```

```bash
read -r -p "Database username: " trace_db_user
```

```bash
read -r -p "Database name: " trace_db_name
```

```bash
install -d -m 700 "$HOME/trace-backups"
trace_backup_path="$HOME/trace-backups/trace-before-test-cleanup-$(date -u +%Y%m%dT%H%M%SZ).dump"
trace_backup_verified=false
PGSSLMODE=require /usr/lib/postgresql/18/bin/pg_dump \
  --host="$trace_db_host" \
  --port=5432 \
  --username="$trace_db_user" \
  --dbname="$trace_db_name" \
  --password \
  --format=custom \
  --file="$trace_backup_path"
trace_backup_rc=$?
printf 'Backup exit code: %s\n' "$trace_backup_rc"
if [ "$trace_backup_rc" -eq 0 ]; then
  chmod 600 "$trace_backup_path"
  /usr/lib/postgresql/18/bin/pg_restore --exit-on-error --file=/dev/null "$trace_backup_path"
  trace_archive_rc=$?
  printf 'Archive check exit code: %s\n' "$trace_archive_rc"
  if [ "$trace_archive_rc" -eq 0 ]; then
    trace_backup_verified=true
    ls -lh "$trace_backup_path"
  fi
fi
printf 'Backup verified: %s\n' "$trace_backup_verified"
```

Enter the real Render database password at `Password:`; it is hidden while you
type. Continue only when both exit codes are `0` and `Backup verified: true` is
printed. Save the displayed backup path. If backup or verification fails, do
not delete anything; investigate the error and restart the API using step 6
if you are postponing the cleanup.

## 5. Confirm the IDs and apply

With the API stopped, preview once more to check the final counts:

```bash
trace_school_cleanup "${trace_cleanup_args[@]}"
trace_cleanup_preview_rc=$?
printf 'Final preview exit code: %s\n' "$trace_cleanup_preview_rc"
```

Only continue after reviewing a successful final preview. Display the selected
IDs in the exact order required by the confirmation argument:

```bash
trace_cleanup_expected_ids="$(printf '%s\n' "$trace_cleanup_rejected_id" "$trace_cleanup_academy_id" "$trace_cleanup_suspended_id" "$trace_cleanup_carmen_id" | sort -n -u | paste -sd, -)"
printf 'Selected school IDs: %s\n' "$trace_cleanup_expected_ids"
read -r -p "Type those comma-separated school IDs to confirm deletion: " trace_cleanup_confirmation
```

The following block applies the deletion only after the backup and preview
checks and while the API is stopped. The script also independently checks the
confirmation against the selected IDs.

```bash
if [ "${trace_backup_verified:-false}" != true ] || \
   [ "${trace_cleanup_preview_rc:-1}" -ne 0 ] || \
   [ "$(systemctl show trace-api -p ActiveState --value)" != inactive ]; then
  printf 'Cleanup not run: verify the backup, successful preview and stopped API first.\n'
else
  trace_school_cleanup "${trace_cleanup_args[@]}" \
    --apply --confirm-school-ids "$trace_cleanup_confirmation"
  trace_cleanup_apply_rc=$?
  printf 'Cleanup exit code: %s\n' "$trace_cleanup_apply_rc"
fi
```

Successful deletion exits with `0`. The selected rows are deleted together in
one transaction. If the command fails, read the error and check `--list` before
deciding what to do next; do not use a database reset as a repair.

## 6. Verify and bring the API back online

```bash
trace_school_cleanup --list
sudo systemctl start trace-api
sudo systemctl status trace-api --no-pager
curl --fail --silent --show-error https://api.traceedu.app/health
```

The selected school IDs should be absent, other schools should remain, and the
health response should contain `"status":"ok"`. These start commands also apply
when you postpone cleanup or the helper refuses a deletion: inspect the failure
and current school list first, then restore normal service.

If this session paused an active deploy timer, restore it:

```bash
if [ "${trace_cleanup_timer_was_active:-false}" = true ]; then
  sudo systemctl start trace-deploy.timer
fi
```

Refresh **All schools**, then register the real Gumagamot school normally. Its
principal can reuse an email removed with the test account. If registration says
the email already exists, inspect which remaining account owns that email;
deleting one school does not free an email belonging to an unselected account.
Keep the backup until the new registration and your remaining records have been
checked.
