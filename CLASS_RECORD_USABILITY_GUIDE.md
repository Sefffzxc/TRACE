# Class record autosave and assigned classes

This update improves score entry for teachers and assignment management for
principals. Update both the Azure API and Cloudflare Pages. The adviser separation
follow-up adds a table and a one-time backfill: follow
[the adviser deployment guide](ADVISER_ASSIGNMENT_GUIDE.md) for the current release.
No new environment variables, credentials, or packages are required.

## Entering scores

1. Open **Class Record** and choose the section, subject, and grading period.
2. Select **Set up record**. Enter each assessment's maximum in the explicitly
   labeled **Highest possible score** row. Learner score rows remain editable.
3. Enter the learners' scores below their corresponding maxima. Grades preview
   using the current setup as you type. A blank score stays blank; zero means
   the learner scored zero.
4. Valid setup changes and learner scores save automatically after a short pause.
   Changes are batched rather than sent for each keystroke. **Done setup** finishes
   saving before closing the setup controls; it does not discard learner edits.
5. Check the saving status. **Saved** means the API acknowledged the changes.
   **Not saved** explains what needs attention and offers **Retry save**. Failed,
   invalid, or uncertain entries remain on the page. **Save now** can flush changes
   immediately.

Scores must be valid numbers within the assessment maximum. Enter a maximum
before a learner score can be saved for that assessment. Invalid rows stay
editable while valid learner rows can save. Search filters do not exclude hidden
learners from a save.

Changing the section, subject, or grading period saves pending entries first.
In-app navigation is blocked while unsaved changes are being flushed; a failure
keeps the teacher on the record to correct or retry. Closing or reloading a page
with pending entries prompts the browser's unsaved-changes warning.

## Importing Excel scores

Choose **Import from Excel**, review the class, learner matches, warnings, and
grading periods, then select **Import and save**. That action writes the setup and
scores through the existing import API. A successful import refreshes the record
and confirms that no second save is needed.

File selection remains a preview: it does not immediately create learners or
replace class scores. Import errors stay visible so the teacher can resolve them.

## Teacher monitoring filters

The **AI Risk Model** and **Early Warning** section pickers show only the teacher's
assigned sections, including advisory sections the teacher may monitor. The
default option is **My assigned sections**. A teacher with no assignments sees an
explanation to contact the school administrator.

Section filters use numeric IDs. Identically named sections in different grades
remain separate, and **Re-score records** sends the selected section's ID. The API
continues enforcing section and subject permissions on forecasts and warnings.

## Principal teacher assignments

Each section/subject combination has one teaching owner. Occupied subjects
display the current teacher and cannot be selected for another assignment.
**Section advisers** are assigned separately and do not reserve subjects. They
can view every advisory subject in Class Record but edit only subjects assigned
to teach. New teaching assignments always name a specific subject.

Older general assignments appear for explicit review in **Subject teachers**.
Adviser-only general rows never grant all-subject editing. Other legacy general
teaching rows retain their existing permissions until reviewed. See
[the adviser guide](ADVISER_ASSIGNMENT_GUIDE.md) before updating an existing system.

Only active, approved teachers can receive new assignments. The API checks
ownership again and serializes concurrent assignment changes. A conflicting
request receives a helpful 409 response; the form refreshes availability and
retains unsaved choices. To replace a teacher, deliberately remove the old
assignment before creating its replacement.

Existing duplicate assignments are not deleted automatically. Review and remove
unwanted older assignments through Class Management. This update does not change
existing grades, enrolled learners, or historical teacher records.

## Deploy and verify

For the current adviser separation release, use
[the migration and deployment steps](ADVISER_ASSIGNMENT_GUIDE.md#deployment-one-time-adviser-backfill-required).
The steps below describe the earlier autosave-only release.

1. Review, commit, and push the frontend, backend, tests, and this guide to `main`.
   Your existing Cloudflare Pages integration starts the frontend build.
2. Connect to the Azure VM with your usual SSH command. Run each command
   separately, and continue only if the pull succeeds:

   ```bash
   cd /opt/trace
   sudo -u trace git status --short
   sudo -u trace git pull --ff-only origin main
   sudo systemctl restart trace-api
   sudo systemctl status trace-api --no-pager -l
   curl --fail --silent --show-error https://api.traceedu.app/health
   ```

   The status command should show a clean checkout before pulling. Expect the
   service to be active and health to contain `"status":"ok"`.
3. Wait for the matching Cloudflare Pages deployment to show **Success**, then
   reload the website with **Ctrl + F5**. No AI-service restart is needed.
4. Using test learners, enter maxima and learner scores together, wait for
   **Saved**, then reload and confirm persistence. Test a zero and a cleared cell.
5. Test an invalid score and a failed save: entries must remain visible and
   navigation must wait for correction or a successful retry.
6. Confirm an Excel import persists after reload without another Save action.
7. Sign in as teachers with different assignments. Confirm both monitoring
   pickers show their own classes. Test a teacher with no assigned classes too.
8. As principal, try assigning an already occupied section/subject to another
   teacher. Confirm the option is unavailable and a stale conflicting request is
   refused. Other available subjects in the same section should remain assignable.

Automated tests use isolated databases, synthetic learners and files, and mocked
API/AI responses. They do not write to the live school database.

## Suggested next improvements

- Name assessments and optionally record their dates, such as **Quiz 1: Fractions**,
  so numbered score columns are easier to recognize.
- Add **Undo last edit** so teachers can recover from an accidental change after
  it has automatically saved.
- Support pasting a rectangular score block from Excel with a validation preview.

These are follow-up suggestions, not features included in this update.
