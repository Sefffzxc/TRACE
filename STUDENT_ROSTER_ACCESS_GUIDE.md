# Student creation in assigned sections

Student creation is restricted to teachers. A teacher can add or enroll learners
only in a section they advise or teach. A principal can view existing records
but cannot create students or enroll learners through a form or import.

## Teacher workflow

- **Students > Add student** shows only grade levels containing an assigned or
  advisory section. Its Section field contains only eligible sections within
  the selected grade. A teacher without assignments must obtain an assignment
  before adding students.
- **Students > Import students** imports learner names and enrollments into an
  eligible section. It previews matches, additions, and learners already in
  other sections. It does not import assessment setup, scores, or predictions.
  This lets an adviser populate the roster without teaching a subject there.
  It reads the learner lists in supported three-term E-Class Record `.xlsx`
  workbooks; assessment values are ignored in this mode.
- **Class Record > Import from Excel** continues importing setup and scores for
  the teacher's assigned subject. On a fresh section, it can also add or enroll
  the missing learners in the same import. Teachers do not need to wait for
  another teacher to create the roster first.
- Advisory viewing alone does not grant permission to import subject scores.
  Use **Import students** for roster entry when the adviser does not teach that
  subject.
- File selection previews the import. Saving requires the explicit import
  confirmation. Existing roster matching and other-section skipping remain in
  effect; review the preview rather than assuming every name is a new learner.

The principal has no **Add student** or **Import students** action. If a principal
uses the existing class-record score importer, a file that would add or enroll
learners is blocked before saving. The principal's existing score permissions
for learners already in the selected class are unaffected.

The API validates the teacher role, the school's section, current assignments,
and grade/section consistency. Omitting the enrollment fields or calling an
endpoint directly does not bypass these checks. Assignment-loading failures in
the UI do not expose all-school creation choices.

## Deployment

Update both the Azure API and Cloudflare Pages. This update adds no database
tables or columns and needs no new migration, packages, keys, or environment
variables. The earlier [adviser backfill](ADVISER_ASSIGNMENT_GUIDE.md) remains a
prerequisite if it has not already been completed. Do not rerun the academic
structure migration for this release.

### Windows PowerShell

From the local repository, review and stage the changes, including new files:

```powershell
cd "C:\Users\macos\Desktop\lbca"
git status --short
git add frontend/src backend/app backend/tests
git add STUDENT_ROSTER_ACCESS_GUIDE.md STUDENT_HOSTING_GUIDE.md backend/README.md ADVISER_ASSIGNMENT_GUIDE.md
git add audit/assigned-students-browser.cjs
```

Then review, commit, and push:

```powershell
git diff --cached --stat
git commit -m "Restrict student creation to assigned teacher sections"
git push origin main
git log -1 --oneline
```

Keep the short commit ID. The existing Cloudflare Pages Git integration builds
the frontend from the push. The Azure API still needs its server update unless
the optional automatic deploy helper has been installed and verified.

Connect to the existing VM:

```powershell
ssh -i "$env:USERPROFILE\Downloads\trace-server_key.pem" traceadmin@23.100.91.187
```

### Ubuntu SSH session

Run each command separately. Check that the server checkout is clean before
pulling; preserve any unexpected local changes rather than overwriting them.

```bash
cd /opt/trace
sudo -u trace git status --short
```

Expect no output. Then:

```bash
sudo -u trace git pull --ff-only origin main
sudo -u trace git log -1 --oneline
```

Continue only if the pull succeeded and the commit matches your computer.
Restart the API during a short pause in score entry:

```bash
sudo systemctl restart trace-api
sudo systemctl status trace-api --no-pager -l
curl --fail --silent --show-error https://api.traceedu.app/health
```

Expect **active (running)** and a health response containing `"status":"ok"`.
If startup is still finishing, wait a few seconds and retry the health command.
The AI service does not need a restart.

Wait for the matching Cloudflare Pages commit to show **Success**. Reload
`https://traceedu.app` with **Ctrl + F5**.

## Verify after deployment

1. As a subject teacher, confirm Add student shows only assigned grades/sections.
   Create a test learner in an eligible section and confirm it persists.
2. As an adviser with no teaching subject, import students into the advisory
   section. Confirm the roster updates while scores and setup do not change.
3. In an empty test section, let its subject teacher import a valid class record.
   Confirm learners are created and that subject's scores persist after reload.
4. Import the same roster again and check the preview matches existing learners
   instead of treating them as new. Review ambiguous or other-section names.
5. As an unassigned teacher, confirm no section is offered for student creation.
6. As principal, confirm Add student and Import students are absent. An import
   containing new learners must be blocked before records are changed.
7. Confirm existing class-record autosave and read-only advisory score viewing
   still work.

Use synthetic test learners and remove them through the application's normal
test-data workflow. Automated checks use isolated databases and local mock APIs;
they do not change live school records.
