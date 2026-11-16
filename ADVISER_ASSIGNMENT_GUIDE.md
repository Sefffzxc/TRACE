# Separate section advisers and subject teachers

Section advisers can view all subjects in their advisory section. Teaching
assignments determine which subjects they may edit. Both are managed under
**Class Management**, in separate **Section advisers** and **Subject teachers**
tabs.

| Assignment | View | Edit/import scores and change score setup |
| --- | --- | --- |
| Adviser only | Every subject in their advisory section | None |
| Subject teacher | Their teaching subjects | Their teaching subjects |
| Adviser and subject teacher | Every advisory subject, plus their other teaching classes | Only their teaching subjects |

## Assigning staff

1. In **Section advisers**, choose the section and its adviser. This does not
   reserve any subject or prevent assigning other teachers to that section.
2. In **Subject teachers**, select the teacher, section, and actual subject.
   Each section/subject has one teaching owner. Assign additional subjects
   explicitly when a teacher teaches several subjects.
3. Changing or removing a teaching assignment does not remove the section's
   adviser. Change the adviser separately in **Section advisers**.
4. In **Class Record**, advisers can open every advisory subject. Subjects they
   do not teach display **Read only — advisory access**. Score entry, setup,
   imports, manual saving, and autosave are unavailable for those subjects.
   The API also rejects unauthorized writes.

For example, the adviser who teaches Mathematics can view the Science record,
but only that section's assigned Science teacher can edit it.

Student roster creation is separate from score editing. Advisers and subject
teachers can add or import learners into their advisory or teaching sections;
principals cannot create learners. See the
[student roster guide](STUDENT_ROSTER_ACCESS_GUIDE.md) for the teacher-only form,
roster import, and deployment steps.

## Reviewing older assignments

Older rows with no specific subject appear with a review action in **Subject
teachers**. Review each row and select the subjects the teacher actually teaches.
Select none when the row represented advisory responsibility without teaching.
The review replaces that general teaching row only after the entire selection
has passed validation; existing grades, class-record setups, and learners remain.

- A legacy blank-subject row marked as an adviser no longer grants permission to
  teach every subject or blocks another subject teacher. Assign the actual
  teaching subjects explicitly if that adviser also teaches.
- A legacy blank-subject row not marked as an adviser retains its previous
  teaching permissions until the principal reviews it. This avoids guessing
  whether an existing teacher really handles every subject.
- The one-time migration below copies existing adviser designations into the
  separate adviser table. It leaves original teaching rows available for review.
- A conflicting subject owner prevents the entire review from being applied.
  Resolve the ownership deliberately, then retry.

## Deployment: one-time adviser backfill required

This release adds the `section_advisers` table and a small migration-history
table. Unlike the earlier autosave-only update, it needs a one-time migration
to preserve existing adviser designations. The completion marker prevents a
rerun from restoring an older adviser after the principal removes or replaces
them.
No environment variables, packages, or credentials change. Do not run
`migrate_academic_names.py` again for this update.

1. Take and verify a fresh database backup with the existing backup procedure.
   Commit and push the backend and frontend changes to `main`. Coordinate the
   Cloudflare Pages release and API update during a short maintenance window.
2. Connect to the Azure VM. Confirm the checkout is clean, record the old commit,
   and stop the API before updating:

   ```bash
   cd /opt/trace
   sudo -u trace git status --short
   sudo -u trace git rev-parse HEAD
   sudo systemctl stop trace-api
   sudo -u trace git pull --ff-only origin main
   ```

   Continue only if the status showed no local changes and the pull succeeded.
3. Inspect the backfill using the API's existing database configuration. This
   first command does not change the database:

   ```bash
   sudo systemd-run --quiet --wait --pipe --collect \
     --property=User=trace --property=Group=trace \
     --property=WorkingDirectory=/opt/trace/backend \
     --property=EnvironmentFile=/etc/trace/api.env \
     /opt/trace/backend/.venv/bin/python /opt/trace/backend/migrate_section_advisers.py
   ```

   Resolve any reported conflicting legacy advisers before applying. An existing
   explicit adviser is never silently replaced with an older designation.
4. After checking the inspection and verified backup, apply:

   ```bash
   sudo systemd-run --quiet --wait --pipe --collect \
     --property=User=trace --property=Group=trace \
     --property=WorkingDirectory=/opt/trace/backend \
     --property=EnvironmentFile=/etc/trace/api.env \
     /opt/trace/backend/.venv/bin/python /opt/trace/backend/migrate_section_advisers.py --apply
   ```

   Do not continue if migration fails. The backfill is idempotent and does not
   delete teaching assignments or class records.
5. Start the API and verify it:

   ```bash
   sudo systemctl start trace-api
   sudo systemctl status trace-api --no-pager -l
   curl --fail --silent --show-error https://api.traceedu.app/health
   ```

   Expect an active service and `"status":"ok"`. No AI-service restart is needed.
6. Confirm Cloudflare Pages deployed the same commit successfully, then reload
   the website with **Ctrl + F5**.
7. Confirm previous advisers appear in **Section advisers**. Review older general
   teaching rows. Test a separate subject teacher in the same section, an adviser
   with no teaching subjects, and an adviser who teaches one subject. Reload
   after an authorized score edit to confirm it saved. Other advisory subjects
   must remain read-only.

The migration copies designation data only. If a rollback is required, coordinate
the matching frontend and backend version; older versions interpret legacy
adviser/subject rows differently. Keep the verified backup until testing is done.

For a local existing database, stop the local API and run the migration from
`backend` with its configured environment, first without `--apply` and then with
it after reviewing the output. A brand-new database has no adviser history to
backfill.
