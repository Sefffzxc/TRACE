# Backend academic, learner data, grading, import, and AI audit

Audit date: 20 October 2026 (Asia/Manila). Outcome: **not ready for launch**. Multiple independently reproduced access-control and grade-integrity defects remain unresolved. Application source was not changed.

## Method and reproducible evidence

- `backend/.venv/Scripts/python.exe audit/probe_backend_data_ai.py` runs real FastAPI routes through HTTPX ASGI transport, request validation, SQLAlchemy queries, real SQLite tables, role checks, and class-access checks. Only the authenticated identity dependency is overridden; authentication is audited separately. Each run creates a fresh synthetic database below `audit/backend-data-ai-*`; the original `backend/lbca.db` is never opened by this harness.
- `ml/.venv-ml/Scripts/python.exe -X utf8 audit/probe_ai_service.py` loads the shipped 11.5 MB v4 model and runs inference over the real AI app and installed ML dependencies.
- Structured results: `audit/backend-data-ai-evidence.json` and `audit/ai-service-evidence.json`; readable runs: `audit/backend-data-ai-run.log`, `audit/ai-service-run.log`. Each finding cites the exact evidence probe name for lookup. These artifacts contain synthetic learner data only.
- Network-failure injection replaces only HTTP client responses and retry sleeps, recording the exact timeout and retry settings used by the real save path. This validates configured behavior without holding a request open for several minutes. It is not a measured production latency result.
- Existing tenant test: `backend/.venv/Scripts/python.exe -X utf8 backend/tests/test_tenant_boundary.py` passes. Without `-X utf8`, it fails on this Windows machine with a `UnicodeDecodeError` before completing route inspection. The existing test stubs dependencies and checks AST scope declarations; it does not exercise actual database isolation or class-level authorization.

Severity: High = launch blocker affecting protected learner data, grade correctness, durable state, or a central user flow. Medium = material integrity, validation, concurrency, or operational defect. No fixes are included in this audit.

## Verified issues

### BD-01 — High — Teacher can write a subject they are not assigned to teach

Evidence: `backend/app/api/routers/pace.py:568` checks `require_grade` against the stored subject; `pace.py:572` then accepts a replacement `subject` from `PaceUpdate`. Probe `rename_to_unassigned_subject`: a Math-only teacher creates a permitted Math PACE and PATCHes it with `{"subject":"Science","current_pace":5}`. Server returns 200 and persists Science. The teacher has no Science assignment.

Impact: unauthorized subject records and grades can be injected by renaming an allowed record. The resulting record can disappear from that teacher's filtered view while remaining visible to Science staff/admin. Collisions with an existing Science record can also produce a database error.

Recommendation: make the subject identity immutable on record updates, or authorize both old and new classes and validate a scoped subject ID and all relevant uniqueness constraints before moving the record.

### BD-02 — High — Student and enrollment identity can disagree on saved grades

Evidence: `backend/app/api/routers/pace.py:501` and `pace.py:502` independently check that student/enrollment exist; `pace.py:504` authorizes using enrollment. Bulk repeats the same pattern at `pace.py:656` and `pace.py:662`. Neither verifies `enrollment.student_id == payload.student_id`. Probe `mismatched_student_enrollment`: teacher cannot GET Private Learner (404), yet POST `/api/paces` with Private Learner's ID and their own learner's enrollment succeeds (201). The saved response has `student_id=2,enrollment_id=1`; later PACE reads label the private learner as belonging to the teacher's section.

Impact: grade/warning attribution can be corrupted for a learner outside the teacher's class. Display names, sections, summaries, histories, and predictions can refer to different children. Bulk existing-record updates also never repair a previously mismatched student ID.

Recommendation: derive student identity from the scoped enrollment; reject all inconsistent IDs. Validate section, grade-level, subject, enrollment, term, and school as one relation. Add database constraints where possible.

### BD-03 — High — Data-quality endpoints bypass class privacy and allow author spoofing

Evidence: `backend/app/api/routers/pace.py:1114`, `pace.py:1143`, `pace.py:1174` use school scope but never call `class_access`. Creation at `pace.py:1164` trusts caller-supplied `teacher_id` when that teacher exists in the school.

Probes: `positive_scope_private_student` returns 404 for a learner in an unassigned section. Nevertheless `unassigned_quality_list` returns that learner's name and private correction detail (200), `unassigned_quality_resolve` resolves their log (200), and `spoof_quality_author` creates a log about that learner attributed to the administrator (201).

Impact: any approved teacher can inspect and change school-wide data-quality cases, enumerate private learner names through these cases, and falsify who raised a correction.

Recommendation: enforce learner/record class visibility on list/read/write/resolve; set author from the authenticated user. If reassignment is required, expose a separate administrator-only operation with audit history.

### BD-04 — High — Student PATCH response reveals grades hidden by GET permissions

Evidence: GET at `backend/app/api/routers/students.py:169` passes the access predicate to `paces_by_student`; PATCH at `students.py:372` omits it. Probe `subject_scope_get`: Math-only teacher sees one Math subject at grade 100, low risk. The same teacher PATCHes only an address (`subject_scope_patch`) and receives two subjects, hidden Science as weakest subject, its grade 60, and critical risk. No Science assignment or adviser permission exists.

Impact: the response to an innocuous profile update discloses other subject grades and standing contrary to the app's stated privacy policy.

Recommendation: use a common scoped response builder for GET and all mutation responses, with identical subject visibility filtering.

### BD-05 — High — Invalid profile update commits data that permanently breaks learner reads

Evidence: `backend/app/schemas/students.py:47` accepts any string on update, while the read/create base at `students.py:23` accepts only Male/Female. Router `backend/app/api/routers/students.py:361` assigns the value and commits at `students.py:367` before response validation.

Probes `invalid_student_update` and `invalid_student_read`: PATCH `{"gender":"invalid-gender"}` returns 500, then a separate GET of that learner also returns 500. The invalid value was persisted despite the reported failure. Collection/profile views using `StudentResponse` can fail on the same row. Explicit nulls on nonnullable update columns and missing update length constraints create related validation inconsistencies.

Impact: a validly authenticated teacher can accidentally or deliberately corrupt an accessible profile and break views that include it.

Recommendation: share all create/update domain constraints, distinguish omitted fields from prohibited nulls, validate the resulting object before committing, and repair previously persisted invalid values.

### BD-06 — High — Changing grade headers leaves warning/dashboard/AI state stale

Evidence: `backend/app/api/routers/pace.py:924` recomputes affected grades when the header changes, but the save path never re-evaluates warnings or records a new prediction. Ordinary row saves do both (`pace.py:730`, `pace.py:737`).

Probes `normal_saved_grade`, `warning_before_header_change`, `lower_grade_via_header`, `warnings_after_header_change`, `student_after_header_change`, `dashboard_after_header_change`: a complete grade 100 has no warning. Doubling highest scores changes its grade to 70 and the learner detail to critical, yet early warnings remain empty and dashboard still counts that learner on track. Existing predictions still reflect the old header inputs. The fixture has no other learners in the teacher's visible section at this point.

Impact: critical learners can be absent from the intervention list immediately after a legitimate class-wide scoring change. Different screens contradict each other.

Recommendation: recompute grades, year standing, warning lifecycle, and prediction eligibility consistently in the same transaction after any source change. Apply the same invariant to imports, score clearing, and deletion paths.

### BD-07 — High — Editing a single PACE's scores ignores its saved class header

Evidence: `backend/app/api/routers/pace.py:578` calls `_recompute_grades(row, _default_setup())`, which has empty highest-score arrays. Probe `patch_scores_uses_header`: after a real header and bulk save produce grade 100, PATCH `{"ww_scores":[90]}` returns 200 but clears WW/PT/exam percentages, initial grade, term grade, descriptor, and `record_complete`, despite retaining all raw arrays and the valid header in the database.

Impact: the supported per-record API silently removes derived academic results and changes warning/AI behavior. A later bulk save may rebuild them, but users are not told the edit discarded their grade.

Recommendation: load the setup identified by the record's enrollment section, scoped subject, and grading period on every score mutation. Unify single and bulk write paths.

### BD-08 — High — Incomplete exams are treated as a finished term and sent to the AI model

Evidence: `backend/app/services/grading.py:258` correctly computes `complete=False` when exams are missing, but `grading.py:259` and `grading.py:261` still produce an initial and transmuted grade. `backend/app/services/ml_client.py:133` checks only nonnull percentages/grades, not `record_complete`. `backend/app/services/enrichment.py:242` includes these grades as completed year terms.

Probe `incomplete_pure_grading`: WW/PT 100/100 with exam scores `[100,null,null]` and all maxima 100 produces `complete=false` yet term grade 82 and `ai_first_term_ready=true`. The real `incomplete_exam` bulk request reports `scored=1,held_back=0`; `incomplete_record_read` contains `record_complete=false` with a term grade and AI output.

Impact: missing exams effectively contribute zero to a purported term grade; the app treats a term in progress as a finished year-model input, can issue misleading failure warnings, and violates its own promise to hold incomplete records back.

Recommendation: withhold finished-term grades/AI eligibility until required assessments are complete, and explicitly define how unfinished coursework is displayed. Test partial WW/PT and each missing exam independently.

### BD-09 — High — Historical grades contaminate current-year risk summaries

Evidence: `backend/app/services/enrichment.py:108` loads all PACE rows for a student with no current/active enrollment condition; `enrichment.py:127` groups one entry per enrollment and subject, then `build_student_response` rolls all entries together. Latest AI summary has the same historical scope at `enrichment.py:380`.

Probe `historical_grades_current_summary`: current-year Math grade 100 is low risk. Add a Math grade 60 under an inactive prior-year enrollment. The current-year response still identifies the current enrollment but now reports critical risk, lowest grade 60, and two tracked subjects for one Math subject.

Impact: old failures can keep a currently successful learner flagged; histories are blended with the current roster rather than presented separately. Student detail can disagree with dashboard, which selects active enrollment IDs.

Recommendation: calculate current summaries and latest forecasts strictly for the selected/current enrollment/year. Keep archival results in a separate explicitly historical response.

### BD-10 — High — Import silently attaches one learner's scores to another same-name learner

Evidence: `backend/app/services/eclass_import.py:132` excludes middle name, sex, and an immutable student identifier from the matching key. `backend/app/api/routers/class_import.py:155` and `class_import.py:157` overwrite collisions in a dictionary. School-wide matching at `class_import.py:165` does the same. There is no ambiguity refusal.

Probes `same_name_import_preview`, `same_name_import_save`, `same_name_import_actual_owner`: enroll synthetic Alex Twin A. and Alex Twin B. in the section; upload a correctly laid-out workbook naming `TWIN, Alex A.`. Preview says matched with no warning, save succeeds, but the scores belong to B.'s student ID instead of A.'s.

Impact: a standard import can silently corrupt a different child's official records and forecasts. First/last spelling concatenation and punctuation removal can introduce further key collisions.

Recommendation: match on stable learner IDs where present. Otherwise detect all collisions, display candidate identities including middle name and other safe context, and require an explicit reviewed match before saving.

### BD-11 — High — Failed multi-term import leaves partially committed learners and grades

Evidence: `backend/app/api/routers/class_import.py:356` calls `save_class_rows` inside its term loop; that helper commits at `backend/app/api/routers/pace.py:743`. Later term schema construction can still fail at `class_import.py:339`. Import preview and save do not validate all score bounds identically.

Probes `import_partial_failure_preview`, `import_partial_failure_save`, `import_partial_committed_state`: generated workbook has a new learner, valid Term 1 score 90, and Term 2 score 1001. Preview succeeds with an above-maximum warning. Save returns 500 (`ValidationError`), yet the new learner, enrollment, and Term 1 are committed. Term 2 is absent. The request was not atomic.

Impact: users receive a failure after partial writes and may retry, unaware which terms or children were imported. Mid-request AI/database failures can produce similar ambiguity.

Recommendation: fully validate the complete import before any writes; make nested save helpers flush rather than commit; commit exactly once at the owning import boundary. If partial imports are intentional, return a precise resumable per-term result rather than 500.

### BD-12 — High — Ordinary class saves wait for AI cold-start retries before committing

Evidence: `backend/app/api/routers/pace.py:737` awaits `_score_batch` before commit; `_score_batch` calls `ml_client.predict_many` at `pace.py:786`. `backend/app/services/ml_client.py:434` uses `_send_with_wake`, whose defaults are four attempts at 90 seconds each plus three 15-second pauses (`ml_client.py:324`, `ml_client.py:343`, `ml_client.py:359`). The function's comment says inline saves never use this path, but they do.

Probe `ordinary_save_ai_outage` runs the real bulk save with injected HTTP 503 responses and immediate simulated sleeps. It records four clients with `timeout=90.0` and pauses `[15,15,15]`. Immediate 503s entail 45 seconds of retry delays; repeated timeout failures allow up to roughly 405 seconds before fallback and commit, excluding setup overhead.

Impact: a slow or unavailable model delays a central data-save transaction for minutes, holds locks/connections, and can outlive browser/proxy timeouts. Users may retry while an earlier transaction still runs.

Recommendation: use a short hard deadline without wake retries for inline saves and commit durable grades promptly. Run forecast generation after commit in a bounded background job, or preserve a clear pending/fallback state. Reserve long wake retries for explicit refresh/rescore actions.

### BD-13 — Medium — Simultaneous first saves race into unhandled 500 errors

Evidence: `backend/app/api/routers/pace.py:675` selects a row and then inserts if absent; the unique enrollment/subject/term constraint is not handled as a concurrent upsert. Probe `concurrent_new_record_creation`: five simultaneous requests creating the same Term 2 record return one 200 and four 500 `IntegrityError`s. Probe `concurrent_same_record_saves` returned five 200s when a row already existed; the final state uses last-writer behavior with no revision conflict detection.

Impact: concurrent teachers, duplicated submissions, and retries can report failed class saves or overwrite work. A failing bulk transaction can discard its other rows.

Recommendation: use database-native conflict-aware upserts or lock/retry the creation transaction; return a deliberate conflict when appropriate. Add optimistic revision checks for edits to avoid silent lost updates.

### BD-14 — High — Learners with AI predictions cannot be deleted

Evidence: `backend/app/models/students.py:451` declares a student backref without delete cascade or `passive_deletes`, while `students.py:407` makes prediction.student_id nonnullable. `backend/app/api/routers/students.py:404` deletes the learner through the ORM.

Probes `delete_student_with_prediction`, `state_after_student_delete`, `delete_student_exact_error`: DELETE of a synthetic learner with one AI prediction returns 500. Direct invocation of the same endpoint confirms `NOT NULL constraint failed: ai_risk_predictions.student_id`. The learner still exists after rollback. `ungraded_student_delete_healthy` succeeds with 204. The ORM attempts to null the child key before deleting the parent; the database's declared ON DELETE does not resolve that ORM behavior. Data-quality backrefs have an analogous nonnullable student link.

Impact: a normal learner-removal workflow stops working as soon as prediction history exists, including fallback predictions generated by ordinary saves.

Recommendation: explicitly define lifecycle policy for prediction and quality-log children (appropriate cascade/passive deletes or retained anonymized history), then test deletion with every related record type on both supported databases.

### BD-15 — High — Renaming a subject hides existing grades from its assigned teacher

Evidence: grades and access checks rely on `StudentPace.subject` text; `backend/app/api/routers/academic.py:531` updates Subject without migrating grade/warning/prediction subject text. `backend/app/services/access.py:111` reads current subject names for permission. Bulk creation at `backend/app/api/routers/pace.py:690` never sets `subject_id`.

Probes `subject_rename`, `teacher_paces_after_subject_rename`: admin renames Mathematics after it has saved records. The same teacher's GET `/api/paces?enrollment_id=...`, previously containing the record, becomes `[]` even though the rows still exist. Restoring the name makes them visible again.

Impact: routine academic maintenance can make current grades and warnings appear lost, and future encoding creates a new subject-text identity rather than updating historical records.

Recommendation: use stable scoped subject IDs for persistence, authorization, uniqueness, and joins; treat names as display labels. Migrate existing denormalized records safely.

### BD-16 — Medium — Invalid headers and grading periods are accepted, corrupting calculations or causing 500s

Evidence: `backend/app/schemas/students.py:281` lacks bounds/finite-number/shape validation on highest scores and individual exam subweights; its sum-only validator at `students.py:300` permits negative exam shares totaling 100. `students.py:381` puts no bounds on bulk grading_period. Save indexes/extends arrays using that value at `backend/app/api/routers/pace.py:725`.

Probes: `accept_invalid_setup` accepts highest score -10 and exam shares `[-100,100,100]` (200). `invalid_period_zero` aliases 0 to Term 1 (200); `invalid_period_negative_accepted` persists Term -1 (200) when existing list length permits Python negative indexing; `invalid_period_negative` with period -10 returns 500 IndexError; `invalid_period_seven` creates unsupported Term 7 (200). Header endpoints allow only 1..6. Extremely large period values would also drive proportional list allocation in the while loop; large-memory abuse was not executed.

Impact: mathematically invalid grades and warnings, inconsistent term identity, and avoidable server exceptions. Negative examination shares can make increasing an exam score reduce standing.

Recommendation: enforce finite positive maxima, bounded assessment-array sizes, exactly three nonnegative exam shares totaling 100, and a grading-period range derived consistently from the school's configuration. Reject invalid input before writes or allocations.

### BD-17 — Medium — Class-record setup accepts another school's section ID

Evidence: `backend/app/api/routers/pace.py:869` scopes the subject, then `require_grade` grants unrestricted admins access without ever resolving/scoping the section. Probe `crossschool_header`: School A's admin PUTs a setup using School B's section ID and School A's subject ID; server returns 200 and stores the cross-school link.

Impact: tenant data can contain foreign references, violating relational isolation even though this reproduction did not disclose School B's grades. Current single-column foreign keys do not enforce matching school IDs.

Recommendation: resolve the section with `get_scoped`, validate subject grade-level compatibility, and use composite tenant-aware foreign keys/constraints where practical. Test all writable foreign references, not just top-level row scope.

### BD-18 — Medium — Section capacity enforcement differs across enrollment flows

Evidence: `backend/app/api/routers/students.py:494` checks capacity on standalone enrollment creation, but inline enrollment in create_student at `students.py:322` and enrollment PATCH at `students.py:533` do not. Import only warns above capacity (`backend/app/api/routers/class_import.py:202`).

Probes `enroll_above_capacity_via_student` and `enrollment_patch_into_full_section`: a section of capacity 1 already containing one learner accepts another inline-created student (201) and accepts a learner transferred by PATCH (200). The database count exceeds capacity. The import also persists above capacity after warning.

Impact: roster limits can be bypassed through ordinary UI/API paths, and users get contradictory enrollment outcomes. Concurrent standalone creates also need transactional capacity protection.

Recommendation: decide whether capacity is a hard limit or warning and apply that policy through a shared enrollment service; enforce hard limits transactionally with a clear override policy if needed.

### BD-19 — Medium — SQLite foreign-key enforcement is enabled on only one connection

Evidence: `backend/app/db/session.py:61` executes PRAGMA foreign_keys=ON inside `init_models` only. New pooled connections do not inherit SQLite per-connection pragmas. Probe `sqlite_fk_pool` observes `foreign_keys=0` after realistic concurrent request traffic, without changing pragmas in the harness.

Impact: referential constraints and ON DELETE actions can silently differ between connections, leading to orphaned children and unreliable deletion. This finding applies to SQLite; production PostgreSQL behavior still requires separate verification.

Recommendation: enable foreign keys on every SQLite connection using the engine connection event, verify pool connections, and run explicit integrity checks on existing databases before launch. This is separate from the ORM backref deletion defect BD-14.

### BD-20 — Medium — Duplicate academic updates produce unhandled database errors

Evidence: `backend/app/api/routers/academic.py:420` mutates section_code then commits without checking uniqueness or catching constraint conflicts. Similar update loops exist for year, grade-level, and subject uniqueness.

Probe `duplicate_section_update`: PATCH an existing section to another section's code in the same school; server returns 500 `IntegrityError`, rather than a deliberate 409/422. Rollback preserves the prior row in this reproduction.

Impact: ordinary edit conflicts look like internal outages, and validation differs between create and update.

Recommendation: check relevant uniqueness on update and catch database integrity errors as a final race-safe guard; return consistent actionable errors without internal database details.

### BD-21 — High — Missing earlier terms cause an edited term to be counted twice in saved warnings

Evidence: `backend/app/services/enrichment.py:248` returns sorted grades as a compact list, discarding missing term positions. `backend/app/api/routers/pace.py:724` assumes index 0 is Term 1, extends that list, and writes by `term-1`. A prior Term 3 grade is consequently retained in position 1 and the edited grade is also put in position 3.

Probes `gap_terms_student` and `gap_terms_warning`: create only Term 3 with grade 100, then edit it to 60. Learner detail correctly reads the single existing grade 60 and critical risk, but the saved warning reports standing 80 and moderate risk: mean(previous 100,new 60). No Term 1 or 2 exists.

Impact: importing/encoding terms out of order can substantially understate risk in the intervention list and dashboard after an edit.

Recommendation: retain term positions in the helper's return value (a term-keyed mapping or a fixed padded list), replace rather than append a term, and compute all screens from the same completed-term set.

### BD-22 — Medium — School term-count settings are inconsistently honored

Evidence: `backend/app/schemas/schools.py:40` defaults registrations to four grading periods and allows 1..6. `backend/app/services/risk.py:246` hardcodes three by default. `backend/app/services/enrichment.py:135` does not pass the available school term count to evaluate_year; `enrichment.py:158` passes it only to needs_to_pass. Single-student routes do not load the setting at all.

Probe `four_period_school_required_average`: with one grade 100 and four periods configured, list needs_to_pass is 66.67, detail needs_to_pass is 62.5, and detail required_in_weakest is 70 rather than the correct 73.33 needed to finish at 80 across four terms. Only the configuration changed; the same learner and grade are read.

Impact: different screens give different advice, and save-time warning/projection arithmetic can assume a different academic year from the school's settings. New registrations use the inconsistent four-period default.

Recommendation: pass one validated school grading-policy object to every grade/risk/projection path, remove competing defaults, and explicitly limit unsupported policies until they are implemented.

## Positive checks and coverage

- Full academic CRUD on disposable rows: school year create/update/delete; grade level create/update/delete; section create/update/delete; subject create/update/delete; teacher assignment create/delete. All expected 200/201/204 results.
- Successful reads: students, student detail/profile, enrollments, PACE rows, school years/current year, grade levels, sections, subjects, assignments, my-classes, class-record header, early-warning list/summary, AI prediction list/summary, data-quality logs, dashboard.
- Healthy standalone enrollment create/delete and deletion of an ungraded learner succeed. Assigned-subject grade header/bulk save produces grade 100 and complete=true. Class record import preview/save of a valid generated workbook succeeds. Invalid non-Excel upload returns deliberate 400.
- Positive class-boundary check: an unassigned learner's normal student GET returns 404. The issues above demonstrate bypasses through specific endpoints rather than a claim that all routes lack isolation.
- Real shipped AI model: loads successfully; health200; correct token permits inference; missing token rejects predict/reload401; unknown features422; out-of-range first-term grade422; empty batch200/count0; 501-row batch413; 500-row batch200/count500 and reference order preserved; 10 concurrent inference calls all200. On this local machine the 500-row batch took approximately 0.07s and 10 requests approximately 0.39s. These are smoke timings, not production load-test capacity claims.
- AI output honestly reports default imputation for missing features and includes a version. Model-card metrics returned for deployed v4: MAE1.227, within2 points0.798, interval coverage0.792, fail true positives11, false negatives3, false positives1. The card documents only14 failed learner-subject-years and older grading tables; this sample cannot establish broad clinical/educational effectiveness or fairness. No sensitive raw learner workbook was printed or modified.

## Remaining limits

This sub-audit does not cover authentication, staff/school management, payment integrations, browser rendering, accessibility, or mobile flows; those are coordinated with the parent audit. Actual production PostgreSQL, hosting configuration, proxy deadlines, real email delivery, external network degradation, backups/restores, and sustained many-user capacity were not exercised here. The backend transport tests bypass login identity resolution only; they do not prove login/token lifecycle safety. The AI outage reproduction instruments responses/time rather than waiting through405 seconds. No large-memory denial-of-service payload, workbook decompression bomb, or destructive production test was attempted. Full legal accuracy of grading policy was not established; the recorded failures concern the app's own declared invariants and arithmetic.

Unexecuted cases must remain unverified, including invalid XLSX archive internals, every international name format, all supported PostgreSQL conflict timings, long-running soak behavior, and model transfer to unrepresented schools/grades. Existing scope-declaration tests are useful but insufficient to certify launch readiness while the reproduced issues remain.
