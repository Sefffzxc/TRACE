# Independent backend cross-check and coverage follow-up

Date: 20 October 2026 (Asia/Manila). Application source and existing application databases remain unchanged. This follows `backend-data-ai.md` and `security-auth.md`.

## Endpoint inventory and remaining route coverage

`audit/api-inventory.csv` contains **88 explicit endpoints: 82 backend and 6 AI-service endpoints**, with method, full route, router file, function, source line, declared dependencies, and an auth-guard summary. It was generated with AST parsing (`audit/generate_api_inventory.py`). A read-only comparison to the root agent's running backend `GET http://127.0.0.1:8000/openapi.json` returned 82 methods/routes and matched the inventory exactly: no missing or extra backend method/path pairs. No root browser database writes or service changes were performed. Automatic framework docs/schema routes and the static uploads mount are not counted as explicit business endpoints.

The six assigned routers contain 53 endpoints: academic 21, students 10, pace 13, AI 6, dashboard 2, import 1. Every one now has at least one actual HTTP invocation in `probe_backend_data_ai.py`, including successful academic CRUD, healthy enrollment CRUD, PACE header GET/PUT, PACE deletion, warning intervention PATCH, prediction detail/rescore, and admin dashboard. The AI backend's disabled service state returns 200 for status, 503 for the unavailable model card, and a correctly labeled fallback on rescore. The independent AI-service harness verifies the real loaded model separately. Exercising a route once does not certify all query/payload/role combinations; the reports list negative paths and unresolved defects explicitly.

New positive/negative probes added after the first report: `normal_header_read`, `backend_ai_disabled_status`, `backend_ai_disabled_model_card`, `backend_ai_prediction_detail`, `backend_ai_rescore`, `teacher_admin_dashboard_denied`, `healthy_admin_dashboard`, `healthy_warning_intervention_patch`, `admin_delete_pace`. The backend evidence now contains 71 named synthetic probes. The backend inventory reconciles as **53 academic/data/AI routes + 27 auth/staff/school routes + 2 metadata routes = 82**. Parent audit covers auth/staff/school, metadata, and browser flows.

BD-16 was checked for state dependence: after earlier terms exist, period -1 can be accepted and persisted because Python negative indexing happens to fit the existing list (`invalid_period_negative_accepted`, 200). A sufficiently negative period -10 deterministically causes the reported IndexError 500 (`invalid_period_negative`). Both are invalid inputs and neither is rejected at schema validation. The final harness records both outcomes explicitly.

Additional verification of **BD-06**: `admin_delete_pace` deletes the only PACE record for the synthetic gap-term learner (204), but `warnings_after_pace_delete` still returns its unresolved Math warning with the old standing 80. `backend/app/api/routers/pace.py:941` deletes and commits without recomputing/closing warning state. This confirms the same synchronization invariant fails on deletion as well as header changes.

## Independent class-privacy checks with real JWT authentication

`backend/.venv/Scripts/python.exe audit/crosscheck_backend_jwt.py` creates a new synthetic database, clears all FastAPI dependency overrides, issues a legitimate audit JWT using the app's token helper and a new random secret, and exercises the full production token/account/school authentication dependencies. It does not use login or claim to test OTP/session issuance. Its evidence is `audit/backend-jwt-crosscheck.json`.

Controls: dependency_override_count 0; anonymous student list 401; ordinary direct lookup of an unassigned student 404; teacher attempt to create an admin-only grade level 403.

Independent results:

| Finding | Verified outcome through the real auth dependencies |
| --- | --- |
| BD-01 unauthorized subject mutation | Math-only teacher PATCHes own Math record to unassigned History;200 and History persisted. |
| BD-02 mismatched learner/enrollment | Same teacher POSTs private learner ID with own learner enrollment;201 with mismatched IDs. |
| BD-03 quality-log privacy bypass | Unassigned learner's correction case visible through data-quality list;200. |
| BD-04 mutation response leaks hidden grade | GET learner reports lowest Math grade 100; PATCH only address reports hidden Science grade 60 and weakest_subject Science. |
| BD-05 durable profile poisoning | PATCH gender outside allowed read enum 500; separate subsequent GET 500. |

This removes the primary harness's auth-identity override as a possible explanation for the highest class-boundary findings. All other grading/import high findings were re-run on fresh fixture databases and checked against their cited source paths. No counterexample invalidated a reported high finding. The source line references around `enrichment.py:248` indicate the compact-list return block at lines 248-250; source line references are entry points, not full ranges.

## Independent authentication report cross-check

Re-ran `backend/.venv/Scripts/python.exe -X utf8 audit/security_auth_probe.py` on its own fresh synthetic database, reviewed the underlying auth/dependency code, and saved a snapshot to `audit/security-auth-crosscheck-results.json`. The run did not use identity overrides. No tokens/passwords/OTPs were printed by this cross-check; its log contains safe booleans/statuses only.

- SA-01: anonymous recovery response discloses its code in shipped debug configuration; code exchange 200, reset 200, and replacement-password victim login succeeds. `render.yaml:157` explicitly sets DEBUG_OTP true; this is a provided-configuration finding, not an observation of an unknown live installation.
- SA-02: logged-out access still works 200; logged-out refresh still works 200; pre-recovery access and refresh both work 200. Source confirms `Staff.is_active` and school status **are** checked, but `StaffSession.is_active`/revocation is not. The distinction matters when wording the finding.
- SA-04: correct reset code still exchanges 200 after 12 incorrect attempts. The code's expiry is checked; the missing per-code attempt budget is the verified flaw.
- SA-05: one claimed forwarding IP hits 429 on the eleventh invalid login; changing only the header yields 401 again. Production proxy-header sanitization remains unverified, as correctly caveated in the report.
- SA-07: access forgery works 200 when the audit temporarily selects the public source default. Render generates a different key. Do not describe the generated live secret as exposed.
- SA-20: six simultaneous incorrect passwords all return 401 while persisted failures equal 1 and no lockout is applied. Separate claimed-IP buckets isolate the per-account race from the per-IP limiter.
- Static checks agree with SA-03: the only OTP delivery is an unconditional print, with no mail provider call. Debug-off 202 alone is not evidence that a message was delivered.

SA-17 and ephemeral-upload claims depend on the supplied hosting plan plus current provider documentation; no real account plan/backups were inspected by this sub-audit. No inference of an actual existing production data loss is warranted.

## ML dataset privacy review: headers only

`audit/ml-csv-header-inventory.json` records headers from all five tracked ML CSV files. **No actual dataset row values were read, printed, copied, or tested for reidentification during this follow-up.** The inventory was obtained with `git ls-files ml` and CSV header reads.

| Tracked artifact | Privacy-relevant columns |
| --- | --- |
| `ml/artifacts_final/final_dataset.csv` | learner_key, school, subject, school_year, grade_level, component scores, term grade, final grade, failed, change |
| `ml/artifacts_year/year_dataset.csv` | learner_key, subject, school_year, grade_level, component scores, first-term grade, later-term count/average, risk target |
| `ml/classrecord_modelling.csv` | learner_key, school_year, quarter, grade_level, component scores, term_grade, at_risk |
| `ml/algorithm_comparison.csv` | Aggregate model metrics; no learner identity column in header. |
| `ml/threshold_sweep.csv` | Aggregate thresholds/metrics; no learner identity column in header. |

### BF-01 — High if repository/artifacts are shared outside authorized recipients — Committed pseudonymous grade datasets are dictionary-linkable

Evidence: the three learner-level files above are git tracked. `ml/train_final_grade_model.py:78` computes a deterministic truncated SHA256 over the publicly specified constant plus school and normalized name (`train_final_grade_model.py:82`). `ml/prepare_dataset.py:49` and `ml/train_year_model.py:91` use the corresponding name-only variant. There is no secret key. The training source's claim that the name cannot be recovered is too strong: a party with candidate names/a school roster can compute matching keys. Stable keys also permit linking that learner's subjects/terms/years, and the final artifact retains school and grade context.

`ml/.venv-ml/Scripts/python.exe audit/check_ml_partitioning.py` demonstrates dictionary matching using **only invented names and school**, yielding synthetic_name_dictionary_match_succeeds=true in `audit/ml-synthetic-checks.json`. No real-name dictionary or dataset-row matching was attempted. The confirmed issue is reproducible pseudonym linkage and committed learner-grade schema, not a claim that an identified real learner has been recovered or that the repository is publicly accessible.

Impact: publishing or sharing these artifacts can expose linkable educational results despite removing direct name columns. Dataset consent, public-release authorization, and repository access controls were not available to verify.

Recommendation: classify and restrict the learner-level artifacts as sensitive; distribute aggregate metrics/synthetic fixtures with application source. If individual data must be retained for authorized research, keep it outside public artifacts, use managed access/retention, and assess reidentification risk. A keyed pseudonym with protected keys can reduce casual dictionary matching but is not by itself a public anonymization guarantee. Review existing repository history/access before any public release; deleting a current file does not remove prior commits.

## Model evaluation and leakage review

### BF-02 — Medium — Advertised five cross-validation repeats reproduce the same partitions

Evidence: `ml/train_final_grade_model.py:237` shuffles dataframe row order for each repeat, then invokes `GroupKFold(n_splits=FOLDS)` with default shuffle disabled (`train_final_grade_model.py:241`). Group assignments are based on the same unique learner groups and group counts; row order does not randomize that partition. The model random seeds also remain fixed. The model card advertises five shuffled repeats (`train_final_grade_model.py:368`).

The synthetic-only reproduction `audit/check_ml_partitioning.py` follows this exact procedure with ten invented groups and varied group counts. It reports 5 repeats, 1 distinct partitioning, all_repeat_folds_identical=true. No actual learner data was loaded for this check. This demonstrates the procedure's defect; the production training script was not rerun for hours and no model files were changed.

Impact: the protocol implies more repeated-split diversity than it supplies. Fold averages are not invalid merely because folds repeat, but repetition supplies no additional partition variability and cannot substantiate extra confidence. The observed local inference correctness does not validate the study's generalization claims.

Recommendation: explicitly randomize the group split per repeat with a supported seeded splitter, or accurately report one deterministic five-fold run. Recompute metrics and update model cards/bundles together. Keep model selection evaluation separate from a final external/temporal validation set.

Other leakage/validity observations, with limits:

- Current v4 features are explicitly first-term grade/initial grade/components/gap and grade level (`ml/train_final_grade_model.py:64`); later-term/final/failed columns in the training CSV are targets/metadata, not model inputs. No obvious direct future-feature leakage was found in that feature list.
- Outer `evaluate_split` fits model/residual estimation from training learners only (`train_final_grade_model.py:175`), with inner grouped residual estimation (`train_final_grade_model.py:137`). Grouping by learner is a useful control against sharing one known learner's different subjects between ordinary train/test folds. Correctness of raw learner identity resolution and source-workbook target labels was not verified by reading sensitive raw records.
- The final grade includes the first term arithmetically, but the script predicts the change from first term and compares against carry-forward and mean-rise baselines. Calling that alone target leakage would be misleading; the relevant question is whether unseen learners/schools are predicted better than those baselines.
- The model card reports only 14 failures among 1,393 learner-subject-years and only 3 source schools. Its current card reports fail_tp 11 and fail_fn 3, not perfect failure detection. That is an acknowledged statistical limitation, not a newly measured external validation.
- The leave-one-school-out results include lower coverage than the nominal 80% interval on an unrepresented school (the checked-in card reports Extra Trees coverage 0.589 for one held-out school). Aggregate interval calibration should not be assumed for every new school.
- Grades represented in the card are1,3,4,5,6,8; the live service accepts1..12 and the successful smoke input uses grade7. There is no per-result out-of-domain warning for an unseen grade level. The card documents the training domain, but automatic predictions beyond it remain unvalidated here.
- The deployed probability is for final grade below 75, while school settings can choose another passing mark. The service schema/description consistently name 75, so this is a limitation requiring clear labeling, not proof its internal probability arithmetic is wrong. Check any user-facing claim that this AI threshold follows a school's customized passing mark.

None of these observations establish readiness. Access-control, durable grade state, secure recovery, and reliable save behavior still fail reproducible launch checks.

## Frontend/backend grading parity

### BF-03 — High — Client preview and server disagree on term grades, including pass/fail

Verification: `backend/.venv/Scripts/python.exe audit/check_grading_parity.py` executes the real frontend `computeRow` through Node and the real backend `grading.compute` through Python, mapping their differently named output fields. It compares **643 synthetic fixtures** at numeric tolerance **1e-9**: blank, zero, full, partial exams/WW, missing denominators, above-maximum bonus scores, Core/MAPEH weights, decimal rounding ties, every transmutation band immediately below/at/above its boundary, and 500 seeded random valid/partial records. Results are saved in `audit/grading-parity-results.json`; no actual learner data is read.

There are **26 fixtures with at least one mismatched field and 4 with different term grades**. Partial-record fixtures agree with each other but share the incomplete-term defect BD-08. No random fixture had a different term grade in this sample; the most serious mismatches occur at rounding and transmutation boundaries.

With every highest score set to 100 and default Core weights:

| Scores | Frontend preview | Backend result |
| --- | --- | --- |
| WW `[24.625]`, PT `[100]`, exams `[50.25,50.25,50.25]` | Term grade **75**, Connecting (passing) | Term grade **74**, Developing (failing) |
| WW/PT/exams all `95.96` | Term grade 97 | Term grade 96 |
| WW/PT/exams all `28.01` | Term grade 65 | Term grade 66 |
| WW/PT/exams all `23.35` | Term grade 64, Emerging | Term grade 65, Developing |

**Independent persisted HTTP check:** `crosscheck_backend_jwt.py` now saves the exact first fixture through PUT `/api/class-record/setup` and POST `/api/paces/bulk`, using a real approved teacher JWT and no dependency overrides. Setup/save/readback all return 200. A separate GET `/api/paces` returns WW total 24.62, initial grade 70.0, term grade 74, Developing, complete=true; a fresh SQLAlchemy read independently confirms the database stores 74. The real JavaScript helper returns WW total 24.63, initial grade 70, term grade 75, Connecting, complete=true for the identical payload/header. Thus both displayed initial grades round to 70 while the actual persisted term grades disagree; no UI formatting assumption is needed. Evidence is `BF03_persisted_grading_parity` in `backend-jwt-crosscheck.json` and the copied `persisted_http_crosscheck` section in `grading-parity-results.json`.

Evidence: `frontend/src/lib/grading.js:103` implements rounding with `Number(n.toFixed(2))`; `backend/app/services/grading.py:108` uses Python `round(sum(got),2)`. These differ on exact binary half ties: 24.625 becomes 24.63 in JavaScript and 24.62 in Python. That difference propagates into full-precision grade arithmetic. Additionally, the client sums exact components using sequential addition at `grading.js:199` while the backend uses Python's sum at `grading.py:259`, allowing floating-point band-edge differences even with two-decimal input. Both feed the unrounded initial value into transmutation (`grading.js:201`, `grading.py:261`).

Impact: a teacher can see a passing grade while typing, then save and receive a failing grade from the authoritative server. Descriptors, risk, and model input can change unexpectedly. Identical-looking rounded initial grades do not guarantee identical term grades at these boundaries.

Recommendation: define one explicit decimal precision/rounding contract and band-comparison rule, implement it identically across runtimes (or serve authoritative server previews), and add shared cross-runtime fixtures around every band and exact half tie. Specify accepted input precision and exactly where component totals, percentages, weighted scores, and the initial grade are quantized; specify the same tie-breaking rule and use decimal/scaled-integer arithmetic or a shared authoritative calculation. Keep display formatting separate from calculation values, and compare against grade bands at the agreed decimal precision. Do not select arbitrary epsilon adjustments without agreeing the school's intended arithmetic; verify against authoritative workbook examples. This audit establishes a parity defect, not a legal/policy judgment about which side is the official grading rule.
