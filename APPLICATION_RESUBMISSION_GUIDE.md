# Rejected applications and corrections

Rejection keeps the existing account, email, school records, and decision history.
The email cannot be used to register a second account. Attempt to sign in with
the existing email and password. **Correct and resubmit** appears only when that
attempt confirms an eligible rejected application. It is hidden on the initial
sign-in page and clears if either credential is changed.

## Teacher applications

1. The school administrator opens **Approvals** and rejects the request with a
   reason. The teacher receives a branded rejection email explaining how to
   correct it.
2. The account disappears from **Teachers** and appears under
   **Approvals → Rejected**. **View history** shows previous decisions and reasons.
3. The applicant attempts to sign in with their existing email and correct
   password. After the rejection message appears, they select **Correct and
   resubmit** and complete security verification when CAPTCHA is enabled.
4. TRACE emails a six-digit code. The applicant verifies it before seeing or
   editing their application. Codes expire after five minutes and allow five
   attempts. Verification grants fifteen minutes to submit a correction.
5. The applicant corrects their details and submits. The same account returns
   to **Pending** for a fresh administrator decision. Its email, password, role,
   and account ID stay the same.

The administrator can also **Approve** a rejected request directly, **Reopen** it
for review in Pending, or **Block** it with a reason. A blocked applicant cannot
correct or resubmit their application. An administrator must reopen a blocked
application before making another decision. Reopening and blocking send email
notifications too.

## School and principal applications

The system operator manages these applications under **Schools**, including
Rejected and Blocked tabs, decision history, approval, reopening, and blocking.
Rejecting or reopening the school updates its principal application as well.

A rejected principal uses the same **Correct and resubmit** link and email-code
verification. They can correct their personal details and the existing school
profile. Resubmission returns both the school and principal to Pending. The
school ID stays the same, and the operator must approve the application again.

## Choosing the wrong school

A teacher may explicitly choose **I selected the wrong school** while correcting
an application, select an approved school, and confirm the old and new choices.
This option is available only for an account that has never been approved and
has no teaching, student, session, device, or other existing school records.
The history records the change; records from the previous school are not moved.

Previously approved teachers and accounts with existing records must contact
their administrator for a transfer. A principal corrects their existing school
application rather than switching to another school.

## Verification and history

- Email codes and correction tokens are single use. Neither grants a login
  session or access to students, classes, or grades.
- The correction token stays in browser memory. Reloading the page requires
  email verification again.
- A changed administrator decision, changed account details, expired proof,
  or a completed correction invalidates older verification. The applicant must
  start again when necessary.
- Resubmission revokes existing login codes, trusted devices, and sessions.
  After approval, the applicant signs in normally with their existing password.
- School administrators see their own teachers' applications and history.
  The operator manages school/principal decisions. Applicants see only their
  own application after verifying their email.
- Existing rejection reasons and available audit decisions are imported when
  an older rejected application is first reviewed or corrected. Decisions that
  were never recorded cannot be reconstructed.
- An ordinary rejection does not delete anything. Permanent deletion remains
  a separate administrative cleanup operation.

## Deploy this update

This release updates **both the Azure API and Cloudflare Pages**. It adds two
tables, `application_events` and `resubmission_challenges`. The API creates these
missing tables at startup using its existing database connection. Existing
tables, account IDs, emails, and academic data are preserved. There is no manual
SQL migration, table reset, or reseeding step for this feature.

1. Back up the current database using your established backup procedure. Review,
   commit, and push the release to `main`. With your existing Git integration,
   Cloudflare Pages will start building the frontend.
2. Update the Azure API while Pages builds. From Windows PowerShell:

   ```powershell
   ssh -i "$env:USERPROFILE\Downloads\trace-server_key.pem" traceadmin@23.100.91.187
   ```

3. Run each command separately in the VM terminal. Stop if the pull fails:

   ```bash
   cd /opt/trace
   sudo -u trace git pull --ff-only origin main
   sudo systemctl restart trace-api
   sudo systemctl status trace-api --no-pager -l
   curl --fail --silent --show-error https://api.traceedu.app/health
   ```

   Expect `active (running)` and `"status":"ok"`. If startup fails, inspect:

   ```bash
   sudo journalctl -u trace-api -n 80 --no-pager -o cat
   ```

   The database user must have permission to create the two missing tables.
   This is the same permission used by TRACE's existing startup initialization.

4. Keep the existing Resend and Turnstile keys. No new credentials or packages
   are needed. Keep `EMAIL_ENABLED=true`, `OTP_EXPIRY_MINUTES=5`,
   `DEBUG_OTP=false`, and the existing production CAPTCHA configuration.
   The new correction form uses the Turnstile action `resubmit`.
5. Wait for Cloudflare Pages to show **Success** and confirm the API is healthy
   before using the new forms. Reload the website with **Ctrl + F5**. Pushing
   updates Pages; it does not replace the manual Azure update above unless an
   automated backend deployment service is actually installed and enabled.

## Check after deployment

Use test accounts with inboxes you control:

1. Reject a teacher with a reason. Confirm the rejection email arrives, Teachers
   hides them, and Approvals → Rejected shows them with history.
2. In a signed-out browser, confirm **Correct and resubmit** is initially hidden.
   Attempt to sign in using the rejected account's correct email and password;
   the rejection message and correction link should appear. Changing either
   credential must hide the link. Use the link, verify the emailed code, correct
   details, and submit. Confirm the same account is Pending and its previous
   reason remains in history. Login must still require approval, with no
   correction link for the Pending account.
3. Approve the corrected request. Confirm the approval email arrives and normal
   sign-in works using the existing email and password.
4. Test **Reopen** and **Block** on another rejected test account. A blocked
   applicant must not receive a usable correction code. Reopen must return it
   to Pending.
5. Test a rejected school/principal application. Correct its profile and confirm
   both school and principal return to Pending under their original IDs.
6. For a never-approved teacher without records, test the explicit wrong-school
   correction. Confirm only the new school's administrator receives the Pending
   application. Previously approved accounts must not offer this option.

Automated checks use isolated databases, mocked email/CAPTCHA providers, and
browser API fixtures; they do not send live email or modify the deployed database.
