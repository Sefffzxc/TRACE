# TRACE User Guide: School Administrators and Teachers

This guide walks staff through using TRACE (Tracking and Risk Assessment for Continuous Education), from registering a school to recording results and following up with learners. Open the TRACE website address supplied by your school or system operator.

**Current build:** These instructions follow the current application screens. The [existing audit](audit/README.md) records unresolved issues with access, saving, grading, imports and deployment. Follow the verification steps below during testing; this guide does not establish that those issues have been fixed.

## 1. Understand the workflow

Complete the work in this order:

1. **School administrator:** Register the school and wait for the system operator's approval.
2. **School administrator:** Confirm school settings and create the current school year, grade levels, sections and subjects.
3. **Teachers:** Request staff accounts for the approved school.
4. **School administrator:** Approve the teachers and assign their sections and subjects.
5. **Teachers:** Add learners or import a supported class record, then verify enrollment and profile details.
6. **Teachers:** Record assessment scores or booklet progress, save, and check the saved results.
7. **Teachers:** Review Early Warning, provide support and check progress again.
8. **School administrator:** Review staff activity and grading progress on the Dashboard.

### Which account does what?

| Account | Main tasks | Menus in the current interface |
| --- | --- | --- |
| School administrator | Set up one school, approve teachers, assign classes and monitor staff progress | Dashboard, Teachers, Approvals, Class Management, School Settings |
| Teacher or adviser | Manage learners in assigned sections, encode records and follow up on warnings | Dashboard, Students, Class Record **or** PACE Progress, Early Warning; AI Risk Model at class-record schools |
| System operator | Approve and manage school registrations | Schools, Account |

School administrators use a staff-management dashboard. Individual student profiles, class records and Early Warning are teacher screens. Students and parents do not have accounts in the current application.

### Choose the correct recording workflow

| School setting | What teachers use | Continue with |
| --- | --- | --- |
| Does not track numbered booklets or modules | Class Record for assessment scores; AI Risk Model for forecasts | [Section 5](#5-teacher-record-assessment-scores) |
| Tracks numbered booklets or modules | PACE Progress, or the school's chosen unit name followed by Progress | [Section 7](#7-teacher-record-pace-or-module-progress) |

Public/private status does not choose the recording workflow. The booklet/module setting does. Throughout this guide, a **grading period** means the school's configured term name, such as Quarter or Term.

## 2. School administrator: register and prepare the school

### Step 1: Register a new school

If your school is already registered and your administrator account exists, continue to Step 2.

1. On the sign-in page, select **Request a staff account**.
2. Under **I am registering as**, select **A school administrator**.
3. Enter the school name and choose **Public** or **Private**.
4. Select the address in order: **Province**, **City or municipality**, then **Barangay** where available. Enter the street details if known. Province and city/municipality are required; barangay and street are optional. Choose entries from the lists; the region fills automatically.
5. Enter the school's contact details where available.
6. Answer the numbered-booklet question:
   - Choose **No — we use the DepEd class record** for assessment scores.
   - Choose **Yes — and we call them…** for booklet/module progress. Enter the unit names and the usual number per subject.
7. Set the **Passing mark**, **Grading periods in a year**, and the name of one period. For the supported three-term Excel workbook, configure three grading periods.
8. Complete your own administrator details: name, email, contact number, password and password confirmation.
9. Select **Register school**.
10. At **Awaiting approval**, wait for the system operator to approve the school. Approval activates the administrator account created with the registration.

Use the administrator registration option only to create a new school. An existing school should not be registered again to obtain another staff account.

### Step 2: Sign in

1. Enter your **School email** and **Password**.
2. Select **Sign in**.
3. If **Verify it's you** appears, enter the six-digit verification code and select **Verify and continue**. The code expires after ten minutes.
4. Confirm that the administrator Dashboard opens.

**Verification in this build:** Email delivery is not implemented. In a local/demo setup, a code may appear as **Development mode — your code is**. If no code appears, contact the system operator; repeatedly checking email will not resolve the missing delivery service.

### Step 3: Confirm School Settings

1. Open **School Settings**.
2. Check the school name, address and contact details.
3. Confirm the passing mark, grading-period count and period name.
4. Confirm whether the school tracks booklets/modules. If enabled, check the singular/plural unit names and default units per subject.
5. Select **Save changes** after making changes.
6. Check that the saved values appear correctly.

Agree on these settings before teachers encode records. Changing the tracking mode changes the teacher menus; changing a setting is not a conversion of existing scores into booklet progress or vice versa. The AI's failure forecast uses 75 as its passing threshold, so a different school passing mark needs separate interpretation.

### Step 4: Create the current school year

1. Open **Class Management** → **School years**.
2. Select **Add school year**.
3. Enter two consecutive years, for example `2026-2027`.
4. Select **Make this the current school year** if it is the year staff will use.
5. Select **Create** and confirm that the correct year is marked current.

New student enrollment uses the current school year. Confirm it before teachers add learners.

### Step 5: Create grade levels

1. Open **Grade levels**.
2. Select **Add grade level**.
3. Enter the level and display name, for example `7` and `Grade 7`.
4. Select **Create**.
5. Repeat for every grade level the school will use.

### Step 6: Create sections

1. Open **Sections**.
2. Select **Add section**.
3. Enter a section code, section name, grade level and capacity.
4. Select **Create**.
5. Repeat for the other sections and check that each belongs to the correct grade level.

For example, create a Grade 7 section named `Faith` before assigning its teachers or enrolling its learners.

### Step 7: Create subjects

1. Open **Subjects**.
2. Select **Add subject**.
3. Enter the subject name, code and grade level.
4. At a booklet/module school, also enter the total number of units for the subject.
5. Select **Create** and repeat for the remaining subjects.

Check codes and grade levels carefully. When retiring a subject, the edit form's **In use** setting lets you hide it from normal recording without deleting its existing results.

### Step 8: Approve teacher accounts

Teachers first complete the [registration steps in Section 3](#3-teacher-request-an-account-and-sign-in).

1. Open **Approvals**.
2. Find the teacher's pending request.
3. Check the name and email against the school's staff list.
4. Select **Approve** for a valid request. To decline a request, select **Reject**, enter a reason, then select **Reject request**.
5. Tell the teacher when their account is ready to use.
6. Open **Teachers** to confirm their account status.

Approval allows sign-in. Class assignments are a separate step.

### Step 9: Assign teachers to sections and subjects

1. Open **Class Management** → **Teacher assignments**.
2. Select **Add assignment**.
3. Choose the teacher and section.
4. Choose the subject the teacher will encode. Check that it belongs to the section's grade level.
5. Select **This teacher is the section adviser** when appropriate.
6. Select **Create**.
7. Repeat for each section/subject the teacher handles.
8. Ask the teacher to reopen their recording page and verify the available sections and subjects.

An adviser can view all subjects in the advisory section, but encoding follows the teacher's subject assignments. A blank subject assignment can grant all-subject encoding in that section; specific subject assignments restrict encoding to those subjects. Prefer explicit subject assignments when setting up subject teachers.

Only one adviser is retained for a section when an adviser assignment is added. Assignment rows have **Remove**, without an edit form; to change an assignment, remove the incorrect one and create the correct one.

### Step 10: Monitor staff work and maintain accounts

1. Return to **Dashboard**.
2. Review **Teachers**, **Pending approvals**, **Sections** and **Learners enrolled**.
3. Review **Grading progress by teacher**. At booklet/module schools, progress measures recent updates instead of graded assessments.
4. Check **Needs attention** for missing advisers, classes not started and staff activity that needs follow-up.
5. Open the linked setup/staff page to resolve an issue, then reopen the Dashboard to check it.

Use **Teachers** to search staff and filter by status. For account assistance, find the teacher, select **Unlock account** or **Issue temporary password** as appropriate, then confirm the action. Capture a temporary password immediately: its notification disappears after a few seconds. Give it directly to the intended teacher; they must change it after signing in. **Deactivate account** and **Reactivate account** control whether a teacher can continue accessing the system. Approval decisions and other administrative actions appear in the **Audit trail** on Approvals.

**Administrator handoff check:** The school is approved, settings are agreed, the correct year is current, grade levels/sections/subjects exist, teachers are approved, and each teacher has the correct assignments. Learner entry and monitoring can then begin.

## 3. Teacher: request an account and sign in

### Step 1: Request your staff account

1. Open the school's TRACE website and select **Request a staff account**.
2. Choose **A teacher**.
3. Select **Your school**, checking both its name and city.
4. Enter your name, email, contact number, password and password confirmation.
5. Use a password with at least eight characters, an uppercase letter, a lowercase letter, a number and a symbol.
6. Select **Submit request**.
7. Wait for your school administrator's approval.

If the school is missing from the list, ask the administrator to check its registration and approval. If your account already exists, use sign-in or password recovery.

### Step 2: Sign in and check your access

1. Select **Back to sign in** and enter your school email and password.
2. Select **Sign in** and complete verification if prompted, as described in [Section 2, Step 2](#step-2-sign-in).
3. Check the Dashboard and open **Students**.
4. Open **Class Record** or your school's **PACE Progress** menu.
5. Confirm that your assigned section and subjects are available.
6. If an assignment is missing, contact the school administrator before entering records.

Being an adviser does not automatically grant permission to encode every subject you can view.

## 4. Teacher: add learners and complete profiles

### Step 1: Check whether the learner already exists

1. Open **Students**.
2. Search by learner name or student ID. Check grade/section filters if a learner appears to be missing.
3. Open an existing learner's name to inspect the profile.
4. Add a new learner only after checking for an existing record. Your search covers the learners you are assigned to see; if the learner may already be in another section, ask the school administrator/system operator to arrange a check of the existing enrollment.

### Step 2: Add and enroll a new learner

1. Select **Add student**.
2. Enter the first name, middle name where applicable, last name, birth date, gender and address.
3. Under **Enrolment**, choose the grade level and section.
4. Enter the parent/guardian name, relationship and contact number; add email where available.
5. Select **Add student** in the form.
6. Wait for **Student added and enrolled**.
7. Check the learner's profile and section. Enrollment is created in the current school year and the student ID is generated automatically.

If you will import a supported Excel class record, the import can add learners instead. Continue with [Section 6](#6-teacher-import-an-excel-class-record), then return to complete their profiles.

### Step 3: Fill in missing profile information

1. In **Students**, look for an **Incomplete** profile indicator.
2. Open the profile and select **Fill in details**. You can also use the **Edit student** pencil in the Students list.
3. Complete the missing birth date, address and guardian/contact details.
4. Select **Save changes** and verify the updated profile.

Use the profile's **Overview**, **Grades** or **PACE progress**, and **Enrolment history** tabs to review the learner. The history is read-only. The current interface does not provide a complete transfer, withdrawal or new-year re-enrollment flow for existing learners; ask the system operator to arrange the required enrollment change rather than creating a duplicate student. **Remove student** deletes the learner record; it is not a withdrawal or transfer action.

## 5. Teacher: record assessment scores

Use this section if your menu shows **Class Record**. Booklet/module schools continue with [Section 7](#7-teacher-record-pace-or-module-progress).

### Before editing any class record or progress grid

The current audit found draft loss and records appearing under a changed selection. Use this sequence for every encoding session:

1. Select one section and wait for it to finish loading. Verify the displayed learner names.
2. Select the subject and, for assessment records, the grading period. Check all selections before typing.
3. Keep the learner search empty while encoding multiple learners.
4. Finish **Save setup** or **Cancel** before changing any section, subject or period.
5. Save learner edits before changing selections, searching, navigating away or refreshing.
6. If a search filter was applied after editing, clear it before saving so every edited learner is visible.
7. While a save is running, wait without making more edits or changing selections.
8. After confirmation, reopen the same record and compare the saved raw values with the source record. For assessment grades, also open **Students** → the learner → **Grades** to check the stored period grade; the Class Record grid recalculates its preview in the browser.

These checks reduce common mistakes; unresolved save and concurrency defects still need application fixes. Coordinate with colleagues so two people do not edit the same class record at the same time.

### Step 1: Set up one section, subject and grading period

1. Open **Class Record**.
2. Select the section, subject and grading period.
3. Select **Set up record**.
4. Choose the appropriate record template. The available templates use these component weights:

   | Template | Written Works | Performance Tasks | Examinations |
   | --- | --- | --- | --- |
   | Core subject | 20% | 50% | 30% |
   | MAPEH | 20% | 60% | 20% |

5. Enter the **highest possible score** for each item under **Written / Oral Works (WWs)**, **Product / Performance Tasks (PTs)** and **Examinations (EXs)**.
6. Check the three examination columns: **ST1**, **ST2** and **TE**. Their default examination shares are 30%, 30% and 40%.
7. Select **Save setup**.
8. Confirm the displayed setup before entering learner scores.

The setup belongs to the selected section, subject and grading period. Set it up separately for each period. Editing an existing setup recalculates records in that context; check existing results afterward.

### Step 2: Enter and save learner scores

1. Find the learner's row.
2. Enter raw scores under **Written Works**, **Performance Tasks** and **Examinations**.
3. Leave a cell blank when the score has not been recorded. Enter `0` only for an actual zero score.
4. Review highlighted scores above the highest possible score. Correct typing errors; retain a bonus score only when it matches the source record.
5. Review the calculated totals and grades shown as you type. A `*` marks a provisional grade.
6. Clear the learner search, confirm the section/subject/period, then select **Save class record**.
7. Wait for the saved-record count. Reopen the same record to check raw scores, then open **Students** → the learner → **Grades** to check the stored period grade.
8. Repeat for the other subjects and periods you teach.

**Grade checks:** A displayed grade does not prove that every assessment is encoded. The audit found incomplete assessments treated as complete, and differences between browser previews and saved grades near rounding boundaries. Compare with the school's source record before using a grade for reporting; report discrepancies to the system operator.

To correct a saved score, reopen the same section, subject and period, change the cell, select **Save class record**, then reopen and verify the result again.

## 6. Teacher: import an Excel class record

This is an alternative to manually entering learners and scores at class-record schools. It reads the supported **DepEd 3-Term E-Class Record for Grades 2–10**, saved as `.xlsx`, up to 5 MB. The importer does not support every Excel layout; MAPEH workbooks and SF1 are not supported.

### Step 1: Prepare and preview the workbook

1. Keep a copy of the source workbook and check its learner names, school year, section, subject and scores.
2. Confirm that the correct school year is current in TRACE and that the school's grading-period setup matches the workbook.
3. Open **Class Record** and select the destination section and subject.
4. Finish saving any typed edits and close the setup editor.
5. Select **Import from Excel** → **Choose the E-Class Record file**.
6. Choose the `.xlsx` workbook and wait for the preview. Choosing a file alone does not save the import.
7. Review every warning and each period's learner/item counts.
8. Expand **Learners** and check each matching status:

   | Status | What will happen |
   | --- | --- |
   | In this class | Scores are applied to the matching enrolled learner |
   | New, will be added | A learner record and enrollment are created |
   | Will be enrolled | An existing learner is enrolled for the current year |
   | Other section, skipped | The learner is skipped because of enrollment in another section |

9. If names, destination or warnings are incorrect, select **Cancel** and resolve them before importing.

The importer matches first and last names without using middle initials. Have same-name learners and spelling changes resolved before importing; the current matching behavior can select the wrong learner or create a duplicate.

### Step 2: Import and verify

1. Select **Import** only after checking the preview.
2. Wait for confirmation.
3. Open each imported grading period and compare learner names, assessment setup and raw scores with the workbook. Check stored period grades through **Students** → the learner → **Grades**.
4. Check skipped learners and resolve their enrollment separately.
5. Open **Students** and complete imported learners' missing profile details.

The import reads the workbook's available terms, rather than only the period currently selected on screen. Re-importing replaces existing scores with the file's values. Blank cells can clear saved scores in an imported term, although a completely blank term for a learner is left alone. Learners omitted from the workbook remain enrolled.

The preview does not show every score that will be overwritten. Keep the source workbook and check saved records after importing. If an import reports an error, inspect the destination before retrying: the audit found that an unsuccessful import can leave some earlier changes saved.

## 7. Teacher: record PACE or module progress

Use this section if your menu shows **PACE Progress**, **Module Progress** or another school-defined unit name. Follow the [grid editing sequence in Section 5](#before-editing-any-class-record-or-progress-grid) before entering progress.

1. Open your school's unit **Progress** page.
2. Select the section and wait for its roster to load. Check the learner names.
3. Select a subject you are assigned to encode.
4. Check the subject's total units and the displayed expected progress.
5. For each learner, enter **Current PACE** (or the school's unit label): the progress count reached within that subject's configured total.
6. Enter **Expected**: the progress count the learner should have reached under the school's plan.
7. Review the displayed completion percentage and standing.
8. Clear any learner search, then select **Save progress**.
9. Wait for confirmation, reopen the same section/subject and verify the saved counts.
10. Open **Early Warning** to review the saved progress warnings.

Example: for a 12-unit subject, current `4` and expected `6` represent about 33% completion and two units behind. Use progress counts within the configured total rather than a printed booklet identifier such as `1072`.

Check **Expected** against the school's plan every time. The current automatic default does not reliably follow the school year, particularly from January to May. The grid's immediate standing preview and the saved warning use different calculations, so review the saved Early Warning result as well.

To correct progress, reopen the same section and subject, update the counts and select **Save progress**. **Discard changes** removes unsaved edits when you intentionally want to abandon them.

Booklet/module schools do not have Class Record or AI Risk Model screens in this interface.

## 8. Teacher: review warnings and follow up

1. Open **Early Warning** after verifying the saved records.
2. Filter by section, learner name or level: **Critical**, **At risk** or **Watch**.
3. Start with Critical learners and check the subject and reason for the warning.
4. Open the learner profile to review stored grades or unit progress and guardian details. To verify raw assessment scores, return to the same section, subject and period in **Class Record**.
5. Review the suggested next step and any target shown for remaining periods.
6. Arrange the appropriate support with the learner and guardian, such as reviewing missing work, reteaching or setting a realistic progress target.
7. Keep intervention notes using the school's agreed recordkeeping process.
8. Select **Mark handled** when the warning has been addressed. Select **Show handled** to review handled items later.
9. Record the learner's next results, save, verify and review the warning again.

**Mark handled** records a response to the warning; it does not change the learner's results. A later save can reopen a warning if the learner is still at risk.

Warnings identify learner–subject pairs. One learner can have several flags, so Early Warning's flag total can differ from the Dashboard's student count. **On track** means low rule-based risk; check the source results before drawing conclusions about achievement.

At class-record schools, an AI forecast can appear beside the school-rule warning. Treat the forecast and the warning as separate readings.

## 9. Teacher: review AI forecasts

This page is available at class-record schools. Forecasts require a saved first-period record with the necessary calculated grades; they do not appear just because a learner has been added.

1. Verify the first-period assessment scores and saved grades.
2. Open **AI Risk Model**.
3. Read the service-status message. If offered, select **Wake it up** and wait for the result.
4. Review the compact badges and source in **Latest forecasts**. Open **Show input**, the badge tooltip or the learner's **Grades** tab for forecast details such as the likely range and chance of failing.
5. Use **Flagged at risk**, **Not flagged** or the learner search to narrow the list.
6. Select **Show input** to inspect the values, model version and scoring details behind a forecast.
7. If forecasts need updating after the AI service becomes available, set the section filter to **All sections** and select **Re-score records**. Re-scoring with a selected section may fail in the current build. Learner search and risk filters narrow the display/export; they do not limit which records are re-scored.
8. Wait for confirmation and check the refreshed results.
9. Select **Export** if you need the displayed forecast list as CSV.

**School rule** or a rule-based badge means the fallback produced the forecast. **Partial data** means some inputs were substituted; inspect the inputs before using the result. A forecast is an estimate to support teacher review. The model was evaluated using completed first-term records from a limited set of schools, and its failure probability uses a passing threshold of 75.

## 10. Export records, manage your account and end a session

### Export or print

1. In **Students**, apply the intended filters and select **Export CSV** to download all matching learners, including other pages of the filtered list.
2. In **Early Warning**, select **Export list**; in **AI Risk Model**, select **Export** for the filtered list.
3. For an individual learner, open the profile and select **Print profile**. Use the browser's print dialog to print or save as PDF.
4. Check the exported file against the selected filters and share it through the school's approved process.

### Update your profile or change your password

1. Open the top-right name/avatar menu and select **My profile**.
2. Update your name or contact details, then select **Save changes**. The email field is read-only.
3. To change your photo, select **Upload** and choose a supported image up to 4 MB, or use **Remove**.
4. To change your password, select the **Security** tab within the account page.
5. Enter the current password, new password and confirmation, then select **Update password**.
6. If signing in with an administrator-issued temporary password, complete this password change when prompted.

### Recover a forgotten password

1. On the sign-in page, select **Forgot password?**.
2. Enter your school email and select **Send reset code**.
3. Enter the six-digit code and select **Continue**. Use **Send another code** if needed.
4. Enter and confirm the new password, then select **Update password**.
5. Select **Go to sign in** and use the new password.

Code delivery has the same limitation described in [Section 2](#step-2-sign-in). If a code is unavailable, contact the school administrator or system operator for account assistance.

### Sign out

1. Save and verify any outstanding work.
2. Open the top-right name/avatar menu and select **Sign out**.
3. Wait for the sign-in page before leaving the device.

## 11. Daily and end-of-period checklist

| When | School administrator | Teacher |
| --- | --- | --- |
| Start of a school year | Confirm settings/current year, prepare classes, approve staff and verify assignments | Check assignments and roster; arrange existing-learner re-enrollment; complete profiles |
| Each working day | Review approvals and Dashboard attention items | Encode one class context at a time; save, reopen and verify; review priority warnings |
| After an intervention | Coordinate staff follow-up where needed | Record the next results, check the warning and update the school's intervention notes |
| End of a grading period | Review staff grading progress and resolve setup gaps | Compare all saved assessments/grades with the source record; review forecasts and remaining-period targets |
| Before the next school year | Arrange enrollment rollover with the system operator | Preserve source records and verify each continuing learner's enrollment before encoding |

Creating a new current school year does not itself provide a complete learner-promotion or re-enrollment workflow in the current interface.

## 12. Common problems

| Problem | What to do |
| --- | --- |
| School is absent from teacher registration | Ask the administrator to check that the school has been registered and approved. |
| Registration is pending | A teacher needs school-administrator approval; a new school needs system-operator approval. |
| Account is locked | Follow the displayed wait time or ask the school administrator to unlock the account through Teachers. Avoid repeated guesses. |
| No verification/reset code arrives | Email delivery is absent in this build. Use the displayed development code for a demo when available, or contact the system operator. |
| Section or subject is missing, or a save says you are not assigned | Ask the administrator to check Teacher assignments, the subject's grade level and whether it is In use. |
| Learners do not match the selected section | Stop editing. After saving any confirmed work, reopen the page, select one section, wait for loading and verify names before continuing. |
| A grade or warning looks incorrect | Check saved raw scores, highest possible scores, template, grading period and current year. Compare with the source record and report any discrepancy. |
| An import fails or skips learners | Inspect saved records before retrying; review preview warnings, same-name matches and enrollment in other sections. |
| AI forecast is missing or rule-based | Check the saved first-period record and service status. Once available, use All sections → Re-score records and inspect the resulting source. |
| A page or save remains loading | Preserve the source values outside the form. If you reopen the page, check what was saved before submitting again; a pending request may have completed. Report repeated failures to the system operator. |
| An existing learner needs a transfer or new-year enrollment | Ask the system operator to arrange the enrollment change; editing profile details does not move the enrollment. |

For the application's calculation rules and technical background, see [SYSTEM_GUIDE.md](SYSTEM_GUIDE.md). Installation and deployment instructions are in [README.md](README.md) and [DEPLOYMENT.md](DEPLOYMENT.md).
