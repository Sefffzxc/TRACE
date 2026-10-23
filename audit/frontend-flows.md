# Frontend route, workflow, resilience and usability audit

Audit date: **20 October 2026 (Asia/Manila)**. Scope: the React application under `frontend/`, its API adapters, shared state, all registered routes, every page module, shared components and grading helpers, stylesheet/layout rules, build configuration and shipped address assets. This report complements the backend/security reports and the root browser matrix; it is not a launch approval.

**Result: not ready for launch.** Confirmed defects can put a draft under the wrong subject, silently discard unsaved learners' scores, mark incomplete assessments complete, break session restoration, and leave core pages indefinitely loading. Browser checks additionally confirmed broken account navigation, blank-name acceptance, modal keyboard failures and an unrecoverable address lookup failure. No application source was repaired during this audit.

## Evidence and execution

- `npm.cmd run lint`: **passed**, no errors; four existing unused-variable warnings in `ui.jsx`, `AppShell.jsx` and `DashboardPage.jsx`.
- `npm.cmd run build`: **passed**, Vite 6.4.4, 2,245 transformed modules. Output: 844.47 kB JavaScript (229.20 kB gzip), 60.39 kB CSS (11.47 kB gzip). The first sandboxed attempt could not traverse parent directories; the approved build outside the sandbox succeeded. Build success does not validate workflows.
- `node audit/frontend-static-probes.mjs`: **eight exact-source regression probes passed**. These evaluate the original API client/helpers/component callbacks with fake network/state. They make no actual API requests or application writes. Confirmed session-refresh omission, retained 403 sessions, no request abort, storage initialization crash, incomplete grading, missing chart line, subject drift and hidden-draft loss.
- `node audit/frontend-ui-probes.cjs`: **19 complementary real-browser scenarios**, no harness errors or browser `pageerror` events. Chrome was driven against the audit-only application at `127.0.0.1:5173` / API `127.0.0.1:8000`, using the root audit's isolated `audit/browser.db`. Synthetic account and academic records were created; existing demo-account passwords and existing academic records were not changed. Sanitized results: `audit/frontend-ui-evidence.json`.
- The separate root browser evidence covers the route/role matrix, four viewport sizes and axe scans (`audit/browser-results.json`), corrected slow/offline/storage/draft scenarios (`audit/browser-scenarios-results.json`) and setup drift/response races/chart lines (`audit/browser-targeted-results.json`). The broad matrix's initial scenario harness errors were superseded by the corrected scenario files. Do not count mocked responses as production integrations or real email delivery.

## Confirmed findings

### FE-01 — High: PACE drafts migrate to a different subject

**Evidence:** `frontend/src/pages/app/RosterPage.jsx:129`, `:142`, `:160`, `:165`, `:258`. Drafts are keyed only by enrollment ID. Changing `subjectId` changes the subject used to render/save those same drafts, with no reset or subject-specific draft key. The exact original rows/save callbacks were executed by `frontend-static-probes.mjs`: values typed for English were posted as `subject: "Science"` after changing subject.

**Reproduction:** At a school with unit tracking, open `/pace`, enter a current value for a learner in English, change the subject to Science, then save. The old English draft remains dirty and is submitted under Science.

**Impact:** Incorrect subject progress, warnings and teacher decisions. **Recommendation:** key drafts by section, subject and learner, explicitly handle unsaved changes on context changes, and clear only the successfully saved draft snapshot. Regress with a subject switch before save.

### FE-02 — High: saving a filtered roster silently deletes hidden learners' drafts

**Evidence:** `RosterPage.jsx:129`, `:157`, `:167`, `:208`; `ClassRecordPage.jsx:249`, `:307`, `:319`, `:356`. Both pages filter learners by the search text *before* deriving dirty rows. Save sends only visible rows, then unconditionally empties the entire drafts object. Both unchanged callbacks were executed with Alice and Bob drafts: after filtering Alice, only Alice was posted and Bob's draft was removed.

**Reproduction:** Edit two learners, search for only one, click Save, clear the search. The second learner's change is gone. Even before saving, searching for a learner with no draft displays "All changes saved" while hidden drafts still exist.

**Impact:** Silent loss of school records and false confirmation of save state. The same unconditional clearing also drops entries skipped by the server and edits entered while a save is in flight; the inputs remain editable during saves. **Recommendation:** derive dirty/save state from all draft records, distinguish pending/saved/skipped snapshots, preserve failed and newer edits, and clearly scope any deliberate "save visible" action.

### FE-03 — High: class-record setup edits follow the user to another class/subject/period

**Evidence:** `frontend/src/pages/app/ClassRecordPage.jsx:110`, `:377`, `:399`, `:443`, `:454`, `:470`. `setupDraft` is not reset or tied to the context in which it was opened. Save serializes that old draft using the *current* `sectionId`, `subjectId` and `term`. All three selectors remain active while setup is being edited, and the save button has no in-flight guard.

**Reproduction:** Open Set up record for English/first period, change a highest possible score, switch the subject or period without cancelling, click Save setup. The old setup is written to the newly selected record and its learners' grades are recalculated.

**Verified browser result:** editing a first-period highest score to 37, switching to period 2 and saving produced a PUT to `grading_period=2` with that old 37 value (`browser-targeted-results.json`). **Impact:** assessment caps/weights can be applied to the wrong record. **Recommendation:** capture and validate the original setup context, key/reset the editor on context change, require a decision about unsaved edits and disable repeated saves while pending.

### FE-04 — High: section-load response races can put the wrong roster under the selected section

**Evidence:** `RosterPage.jsx:91`; `ClassRecordPage.jsx:196`. `loadRoster` writes enrollments and PACE rows with no cancellation or request-generation check. It is called every time `sectionId` changes. The header selectors update immediately, so an older request can finish last and replace the newer roster. A failed new-section request also retains the previous section's arrays.

**Verified reproduction:** delaying old section responses 1,700ms and selecting the next section leaves Grade 7 - Sampaguita selected while the displayed learners belong to Grade 7 - Rosal. The root browser checked the rendered names against the stale section's API response (`browser-targeted-results.json`).

**Impact:** Teachers can edit the wrong learners. The grade save's backend context checks may reject mismatched enrollments, but do not make the UI safe; the PACE save does not send a section context at all. **Recommendation:** abort/invalidate prior loads, clear or lock the roster until the selected context is ready, and preserve drafts by context.

### FE-05 — High: missing written/performance assessments are treated as a complete record

**Evidence:** `frontend/src/lib/grading.js:216` (returned `complete` flag); backend mirror `backend/app/services/grading.py:258`; provisional display `ClassRecordPage.jsx:723`. The completion flag tests whether each component has a total and whether every exam is present, but does not test whether every configured WW/PT assessment has a score.

**Verified example:** WW scores `[10, null]`, WW caps `[10, 10]`, complete PT and exams return `complete: true`, initial grade 90 and transmuted grade 91. The second written-work score is still absent. The class record therefore omits its provisional marker. The backend uses the same condition, so this is not merely a temporary preview discrepancy.

**Impact:** Incomplete coursework can be presented and stored as finalized, affecting warnings and forecasts. **Recommendation:** define completeness against every configured assessment with a valid positive cap, preserve genuine zero scores, mirror the rule in both implementations and test each missing-assessment position.

### FE-06 — Medium: a normal cold reload discards a usable refresh session

**Evidence:** `frontend/src/api/client.js:181`; `frontend/src/context/AuthContext.jsx:15`. The refresh condition excludes every `/api/auth/` path, including `/api/auth/me`. Startup calls that endpoint and clears all tokens on *any* failure.

**Verified:** with an expired access token and a valid fake refresh token, the original client sends only `/api/auth/me`, receives 401 and never calls refresh. `AuthContext` then clears the valid refresh token. The root browser matrix additionally checks this with the real refresh endpoint.

**Impact:** Users are logged out after access-token expiry; brief startup outages also erase otherwise valid sessions. **Recommendation:** refresh an expired authenticated `/me` call once; distinguish invalid credentials from connectivity/server errors and present a retryable session-restoration state.

### FE-07 — Medium: network requests have no timeout/cancellation; sign-out can hang

**Evidence:** `frontend/src/api/client.js:124`, `:146`, `:166`; `AuthContext.jsx:58`; `Header.jsx` sign-out handler. Neither normal nor refresh requests supply an AbortSignal or timeout. Auth restoration, most forms and logout await these requests before recovering locally. The exact-source probe leaves fetch pending and confirms no signal is supplied.

**Reproduction:** Leave `/api/auth/me` or logout in a pending network state. The splash or signed-in screen remains indefinitely; no retry/cancel action appears. Root browser observation of a stalled `/me` confirms the loading screen remains.

**Impact:** Slow/interrupted networks prevent use, and a user can click Sign out yet remain on the sensitive signed-in UI while the request hangs. **Recommendation:** apply operation-appropriate timeouts/cancellation, offer explicit retry/cancel states, and clear local sensitive state immediately on sign-out while revoking remotely in the background.

### FE-08 — Medium: school-settings outage leaves Early Warning permanently loading

**Evidence:** `AuthContext.jsx:91` silently stores `school = null` on lookup failure; `EarlyWarningPage.jsx:375` renders skeletons whenever `loading || !school`. The school context supplies neither an error state nor an automatic retry. `SchoolSettingsPage.jsx:133` instead shows an unavailable state saying "Try again" without an actual retry control.

**Verified reproduction:** respond 503 to `/api/schools/me` while warning endpoints succeed. Warning rows never render, the school's error is not shown, and the page has no recovery control. The root browser scenario confirms this.

**Impact:** A secondary settings failure hides otherwise available urgent warnings. **Recommendation:** expose school loading/error states, offer retries, and make route/layout gates wait for a settled settings state.

### FE-09 — Medium: blocked browser storage crashes the entire application

**Evidence:** `ThemeContext.jsx:7`, `:18`; `api/client.js:40`; `AppShell.jsx:19`. Storage access in initial render/effects is not guarded. `storageMigration.js` catches storage failures, but subsequent providers do not. There is no React error boundary.

**Verified:** the exact `initialTheme` helper throws when storage throws `SecurityError`. The root browser injects a denied-storage policy and observes an empty page.

**Impact:** Users with disabled site storage or restrictive browser policies cannot even reach sign-in. **Recommendation:** wrap storage access in a safe adapter with in-memory fallbacks and add a recoverable application error boundary.

### FE-10 — Medium: no complete UI for re-enrolling, transferring or closing an existing learner's enrollment

**Evidence:** enrollment create/update/remove adapters exist in `frontend/src/api/endpoints.js`, but have no frontend callers. `StudentFormModal.jsx:185` renders grade/section controls only for *new* learners and the edit request sends no enrollment. `StudentProfilePage.jsx` enrollment history is read-only; `ClassManagementPage.jsx:31` has no enrollment tab.

**Reproduction:** Try to move an existing learner to another section or enroll that learner in a new school year through the normal UI. No action is provided. The Excel importer can enroll matching existing learners for grade-record schools, but is not a general transfer/withdrawal mechanism and is unavailable to unit-tracking schools.

**Impact:** A common school lifecycle requires raw API use, imports or duplicate learner creation. **Recommendation:** add permission-aware enrollment/transfer/withdrawal/year-rollover flows with validation and history. Treat as a launch blocker if schools are expected to operate beyond their first enrollment or transfer learners.

### FE-11 — Medium: default expected PACE progress resets in January and ignores the selected school year

**Evidence:** `RosterPage.jsx:122`. The expected pace uses June 1 of `new Date().getFullYear()` and clamps negative elapsed weeks to 1; it never loads the current school's year dates. It is memoized only on subject, so it can also stay stale in an open tab.

**Reproduction:** In January–May of a school year, add a fresh progress row for a 12-unit subject. The June start is in the future and expected progress falls to 1 unit, despite much of the school year having elapsed. Existing rows preserve their prior expected value; the error affects defaults for new records and the displayed expected-by-today label.

**Impact:** Newly encoded learners receive understated expectations and missed pace warnings. **Recommendation:** derive the default from the current school's actual year/calendar and a testable clock, and update at appropriate date boundaries.

### FE-12 — Medium: PACE offers subjects the teacher is not assigned to grade

**Evidence:** `RosterPage.jsx:52`, `:74` loads all visible sections and all grade-level subjects, while `ClassRecordPage.jsx:128` correctly reads `assignmentApi.mine()` and filters by grading assignments. PACE has no equivalent filter.

**Reproduction:** A teacher with one assigned subject in a section opens `/pace`; subjects belonging to other teachers are still offered for that grade level. A save outside the teacher's grading permission is rejected by the backend, after the teacher has entered the roster.

**Impact:** A core form permits work that cannot be saved and misrepresents permissions. This finding does not claim that the backend accepts unauthorized writes. **Recommendation:** use the same assignment-aware subject/section choices in both record screens and clearly distinguish adviser visibility from permission to grade.

### FE-13 — Medium: exported CSV text can be interpreted as spreadsheet formulas

**Evidence:** `StudentsPage.jsx:247`, `EarlyWarningPage.jsx:221`, `AiInsightsPage.jsx:172`. Export escapes quotes only. User-controlled names/subject strings beginning with `=`, `+`, `-`, `@` or relevant control characters are not neutralized. Student/staff names are not restricted to non-formula text at the API boundary.

**Reproduction:** Store a synthetic name beginning `=1+1`, export the student or warning CSV, inspect its cell. It is quoted as `"=1+1"`, which CSV quoting does not turn into a guaranteed literal spreadsheet value. Actual execution depends on the spreadsheet/import settings; no dangerous spreadsheet payload was executed during this audit.

**Impact:** An untrusted record can manipulate spreadsheet output and potentially induce external requests/actions when another authorized user opens an export. **Recommendation:** centralize spreadsheet-safe CSV serialization and neutralize formula prefixes while retaining normal numeric data.

### FE-14 — Medium: account Security link changes the URL but leaves the Profile tab open

**Evidence:** `AccountPage.jsx:34` initializes tab from query parameters once; `Header.jsx` navigates to `/account?tab=security`. No effect synchronizes tab when the query changes on the mounted page.

**Verified reproduction:** Go to `/account` on Profile, open the user menu and click Security. URL is `?tab=security`, active tab remains `Profile`, and `#cur_pw` is absent. `frontend-ui-evidence.json` records this.

**Impact:** The security menu fails to open its intended function. **Recommendation:** derive tab from the current search params or synchronize both tab and URL when they change. Apply the same approach to Class Management's tab parameter, which is also read only on mount.

### FE-15 — Medium: the UI accepts blank first and last names for the signed-in account

**Evidence:** `AccountPage.jsx:64`, `:215`, `:236`; first/last inputs have no required or minimum validation and the profile request sends them as entered. Backend validation also accepts the empty strings.

**Verified reproduction:** On a synthetic account, clear both fields and Save changes. API returns 200 and profile refresh succeeds with blank names. The test immediately restored the synthetic names. No demo account was altered.

**Impact:** Accounts, approvals, audit labels and teacher assignments lose reliable human identification. **Recommendation:** trim and require nonempty names on both client and server, with field feedback and consistent length limits.

### FE-16 — Medium: modal dialogs do not manage keyboard focus or prevent background interaction

**Evidence:** `components/common/ui.jsx:120`. Modal handles Escape and scroll locking only: no initial focus, focus trap, restoration, background inertness or accessible title association. `ConfirmDialog` passes `onClose` even while `busy`, so Escape/backdrop/close can dismiss an operation despite disabled footer Cancel.

**Verified reproduction:** Open Add subject. Focus remains on the background Add subject button (`inside: false`). Tabbing from the form escapes into background controls. Escape closes the dialog. These results are captured by the complementary browser probe.

**Impact:** Keyboard/screen-reader users can lose their place or activate background controls; pending destructive actions can be dismissed before their result is visible. **Recommendation:** use a proven accessible dialog primitive, name each dialog with its heading, focus and trap appropriately, restore focus, and consistently enforce pending-operation close rules.

### FE-17 — Medium: mobile layouts do not consistently collapse to a usable column

**Evidence:** `styles/base.css:702` sets `.grid-2` minimum columns to 340px, larger than the 288px content area of a 320px viewport. Inline two-column grids at `AccountPage.jsx:165`, `DashboardPage.jsx:326`, `AdminDashboard.jsx:185` have no mobile override. `AppShell`/mobile CSS use 16px content side padding.

**Reproduction:** Visit these pages at 320/375px width. Compare document scrollWidth with innerWidth and inspect the profile/editor cards and attention/activity panels. The root browser matrix records actual overflow per route at 320, 375, 768 and 1440 pixels; use that evidence for the exact affected-route list.

**Verified browser details:** the teacher dashboard at 320px has document width 356px; the account profile at 375px remains two columns of about 136px and 190px (root screenshots `dashboard-mobile-320.png` and `account-mobile-375.png`). The account remains operable, but cramped; do not describe it as a totally broken page. **Impact:** horizontal dashboard scrolling and unnecessarily narrow account/attention/activity panels on small phones. **Recommendation:** use `minmax(min(100%, ...), 1fr)` or explicit small-screen single-column layouts, replace inline fixed column templates with responsive classes, and keep wide tables within their intended scroll container.

### FE-18 — Medium: section and subject charts omit their passing-mark reference line

**Evidence:** `components/charts/Charts.jsx:203`, `:232`, `:255`. A custom `<PassingLine>` component wraps `<ReferenceLine>`. The installed Recharts renderer (`recharts/lib/util/ReactUtils.js`, `renderByOrder`) only renders recognized direct child types. It does not expand this custom wrapper.

**Verified:** Passing the actual wrapper structure to the installed `renderByOrder` with a ReferenceLine handler returns zero rendered elements and never invokes that handler. A real grade dashboard also renders four charts and zero `.recharts-reference-line` elements (`browser-targeted-results.json`). The expected passing line/label is therefore omitted from the section/subject charts.

**Impact:** The visual lacks the threshold its surrounding copy promises. **Recommendation:** render `ReferenceLine` directly as the chart child or use a supported chart extension mechanism. Verify its SVG element and label in a real browser.

### FE-19 — Medium: forecasts beyond the first 1,000 records silently disappear

**Evidence:** `AiInsightsPage.jsx:74`; `EarlyWarningPage.jsx:132`; backend `api/routers/ai.py:110`, `:183` enforce a limit. Both pages ask for 1,000 latest predictions, perform all further filtering/export locally, and have no pagination or truncation notice. The summary counts the wider dataset independently.

**Reproduction:** With more than 1,000 visible learner-subject forecasts, open AI Risk, search for an older omitted learner or export all predictions. That record is absent. Early Warning's prediction lookup similarly displays no forecast for omitted entries.

**Impact:** Large schools see incomplete search/exports and contradictory summary/detail counts without warning. **Recommendation:** implement API pagination and server-side filters, explicitly return totals/cursors, and provide an export that includes the complete authorized result set. The >1,000-record browser fixture was not created in this audit; the cap is confirmed in both caller and endpoint code.

### FE-20 — Medium: address-data failures are silently cached and prevent school registration recovery

**Evidence:** `components/common/AddressFields.jsx:13`, `:17`, `:24`; `RegisterPage.jsx:268` requires a region populated from the province list. Failed fetches resolve to empty arrays/objects and the module caches those resolved failures indefinitely. There is no loading error or retry UI. School-directory failures are likewise retained as an empty directory for the mounted registration page.

**Verified reproduction:** Abort `/places/provinces.json`, open school registration, restore the network, type Laguna and switch teacher/admin modes. City remains disabled, no error is displayed, and there are zero Retry/Try again buttons. A full page reload is required. Normal Laguna → City of Calamba → Pansol keyboard selection was separately verified to work when assets load.

**Impact:** A single temporary static-file failure blocks the school signup funnel without explanation. **Recommendation:** represent loading/error/empty separately, evict failed cached promises and expose retries without discarding entered form data.

### FE-21 — Medium: a student-profile error persists after a successful retry/context change

**Evidence:** `StudentProfilePage.jsx:67`, `:76`, `:104`. Loading a student sets `loading` but never clears `error`. A later successful response updates `data` while the old nonempty error still causes an early return. A failure loading the edit-form lists also replaces the entire previously working profile with an error-only page.

**Reproduction:** Make a profile request fail once, then navigate between student IDs without unmounting the route component or trigger a successful reload after the error. Success data arrives but the previous error remains the rendered state. An edit-list failure also removes the profile instead of presenting an inline retry.

**Impact:** Recoverable failures make profiles unusable until a full route unmount/reload. **Recommendation:** reset errors when a new request starts/succeeds, separate profile-load and edit-form errors, and expose a retry action.

### FE-22 — Low: temporary password is visible only in a disappearing toast

**Evidence:** `TeachersPage.jsx:95`; `ToastContext.jsx:38` auto-dismisses warnings after 5,200 ms. The response's one-time temporary password is placed only in that toast; no persistent confirmation or copy control exists.

**Reproduction:** Issue a temporary password for a synthetic teacher and wait six seconds. The only UI showing it disappears and cannot be recovered except by issuing another password. Do not test this on real/demo staff accounts during an audit.

**Impact:** Administrators may lose the generated credential before securely delivering it. **Recommendation:** use a persistent, accessible one-time credential dialog with an explicit copy action and dismissal.

### FE-23 — Low: the mobile drawer cannot be closed with Escape

**Evidence:** `components/layout/AppShell.jsx:29`, `:34`, `:97`; no drawer key handler or dedicated close button. Source contains no focus confinement/body-scroll handling for the mobile navigation.

**Verified reproduction:** At 375px, open navigation and press Escape. `.sidebar.is-open` remains. Clicking Teachers navigates and closes it successfully.

**Impact:** Keyboard users have no conventional dismissal, and focus can move behind the drawer. **Recommendation:** treat the drawer as an accessible overlay with focus management, explicit close and Escape support.

### FE-24 — Low: teacher-only route guard admits the system operator

**Evidence:** `frontend/src/App.jsx:71` rejects only `isAdmin`; it does not require `isTeacher`. The operator's sidebar correctly omits monitoring links, but direct `/students`, `/pace`, `/class-record`, `/early-warning` or `/ai-risk` can mount teacher screens. The root browser confirms operator direct `/students` remains mounted while the API returns 403.

**Impact:** Broken/inappropriate screens and avoidable unauthorized API requests. Backend scope checks still protect the data; this finding is not an authorization bypass. **Recommendation:** explicitly require the teacher role and use a deliberate forbidden/redirect path for every other role.

### FE-25 — Low: several list/navigation controls are inaccessible from the keyboard

**Evidence:** `StudentsPage.jsx:389` sortable table headers have click handlers with no button, focusability or key handler. Learner rows at `:444` and dashboard concern rows at `DashboardPage.jsx:365` are click-only; no direct student-name link is rendered in those cells.

**Reproduction:** Tab through the Students table and try sorting or opening a learner by the name/row. Keyboard focus reaches edit/delete actions but not the sortable heading or profile row. These source findings complement the root axe scans; automated axe does not detect every click-only workflow.

**Impact:** Keyboard/screen-reader users cannot perform primary list actions consistently. **Recommendation:** use real buttons for sorting, links for names/navigation, and `aria-sort` for the active heading.

### FE-26 — Low: section filters match names instead of stable IDs

**Evidence:** `EarlyWarningPage.jsx:162`, `:320`; `AiInsightsPage.jsx:150`, `:309`. Option values and row matching use `section.name` / `section_name`, despite each option already having a section ID. The backend permits school-unique section *codes* rather than guaranteeing names are globally unique across grades.

**Reproduction:** Create two sections with the same display name and different codes/grade levels; selecting either name in these pages selects/returns both. Duplicate names also produce identical option values.

**Impact:** Teachers cannot accurately narrow flags/forecasts by section. **Recommendation:** include and filter stable `section_id` values end to end; display grade/name/code as needed to distinguish choices.

## Coverage ledger

All **49 files under `frontend/src`** (37 JSX, 7 JS and 5 CSS) were inventoried. All page/component logic was reviewed, including branches not reached in a normal teacher session. CSS review covered global primitives, tokens/dark mode/reduced motion, signed-in shell/drawer/header, auth layout, grids/tables/modals, teacher cards, profile banners, class record and import states. Build/public assets and `scripts/build-places.mjs` were inspected; the address dataset was exercised in a browser, but not every individual geographic name was independently verified against the PSA.

| Route/page | Source/workflow coverage | Execution evidence / remaining limits |
| --- | --- | --- |
| `/`, `/dashboard` teacher | Session gate, charts, risk links, concerns, activity, settings mode | Root role/viewport/axe matrix; chart wrapper exact-source probe. Production data freshness and full student-volume performance not certified. |
| `/dashboard` principal / `AdminDashboard` | Counts, grading progress, attention, teacher/class-management links | Root browser matrix; mobile layout findings. No manual visual inspection of every possible empty/populated attention combination. |
| `/login` | Valid/invalid/pending, busy/error, trusted-device metadata, OTP routing, password visibility | Root valid logins all three roles; complementary real pending403/approved201/reset-password login. Email delivery is not implemented/verified by UI tests. |
| `/register` teacher | School directory, form/password validation, pending/success states | Actual synthetic signup201 → pending403 → principal UI approval200 → UI OTP/login201. |
| `/register` school | All school/account fields, unit labels/singularization, address dependencies, approval state | All logic reviewed; keyboard province/city/barangay success and transient-data-failure tests. Full school-registration browser submit not run by this sub-audit; correlate backend/root evidence. |
| `/verify` | Direct-entry redirect, six-digit input/autocomplete/paste/key behavior, busy/error, expiry display | Actual OTP verification during synthetic/root login. Real ten-minute expiry waiting not performed; server expiry covered separately. |
| `/forgot`, `/reset` | Email/code/resend, invalid code/format handling, reset token state, strength/mismatch/success | Actual synthetic request202 → code → reset200 → new-password login201; mismatched reset confirmation feedback. No SMTP delivery test. |
| `/students` | Full listing, search/filters/sort/pagination, CSV, add/edit/remove modal hooks, risk/AI columns | Root route matrix/offline/delayed API checks; CSV/security and keyboard review. Exact all-record browser pagination/export spreadsheet opening not performed. |
| `/students/:studentId` | Overview, guardian, PACE/grades, enrollment history, print, incomplete-profile edit | Root valid/not-found route matrix. Source review of stale errors and historical aggregation. Browser print dialog/PDF output not exercised. |
| `/pace` | Section/subject options, defaults, drafts, save/skips/empty/errors, risk preview | Exact-source subject drift and filtered-draft-loss tests; root real PACE browser scenario. Permission mismatch and date-boundary defaults confirmed by source. |
| `/class-record` | Assignments, period isolation, setup/weights/caps, cells/arrows, save, provisional grades, AI wake, import | Exact-source hidden-draft/completeness tests; root real class-record browser scenario. Full arbitrary XLSX import and all keyboard-cell traversal combinations not run by this sub-audit. |
| `/early-warning` | Teacher mine scope, summary, level/section/search/resolved, resolve, CSV, AI lookup | Root route/axe/viewport and settings503 scenario; all branches reviewed. Real resolution mutation not performed by this sub-audit. |
| `/ai-risk` | Service status/wake, prediction summary/search/filters/export, rescore, input/modal/model card | Root route matrix; exact API wrapper/source checks. Live hosted model wake/rescore lifecycle not certified. |
| `/teachers` | Status/search, deactivate/reactivate/unlock/force reset confirmation/error/busy | Root route matrix; all callbacks reviewed; one-time password usability issue. Existing-account destructive actions intentionally not applied. |
| `/approvals` | Pending, approve, rejection/reason, audit trail, chrome refresh | Actual synthetic pending queue → UI approve200 and removal from queue. Rejection/modal source reviewed; real rejection not performed here. |
| `/class-management` years | Add/edit/remove, current flag, date label/validation, dependencies | Real invalid year422 feedback and valid synthetic create201. Existing current year not changed. |
| `/class-management` grade levels | Add/edit/remove, level sorting, counts | Real synthetic create201 and edit200. |
| `/class-management` sections | Grade dependency, code/name/capacity, add/edit/remove | Real synthetic create201; other callbacks/source reviewed. |
| `/class-management` subjects | Grade/name/code, unit count, active flag, add/edit/remove | Real synthetic create201; modal keyboard checks. |
| `/class-management` assignments | Teacher/section/subject/adviser choices and removal | Real synthetic create201 and UI confirmation/delete204. |
| `/school-settings` | Identity/address, unit-mode switch/warnings, labels/defaults, passing mark/periods, serialization/diff, save/errors | Root route/axe/viewport. Source validation reviewed; complementary tests did not mutate existing settings. |
| `/schools` operator | Status filters, approve/reject/reason/suspend/restore, busy/error/empty | Root operator route matrix; source reviewed. Cross-school authorization and school lifecycle mutations belong to backend/security reports. |
| `/account` | Profile/photo, password, status, URL tab and forced-change banner | Real blank-name acceptance/restoration, local password mismatch, header-tab failure; root role/viewport/axe. Real uploads/removal and valid account password-change endpoint not performed here; reset path did change a synthetic password. |
| `*` / unknown route | Guest/auth shell behavior, 404 fallback, dashboard return | Root guest/teacher/operator matrix with invalid routes. Production static-host SPA fallback must also be checked on deployed host. |

Shared components explicitly covered: `ui.jsx` (RiskBadge, Avatar, ProgressBar, EmptyState, ErrorNote, Spinner, TableSkeleton, Modal, ConfirmDialog, Pagination, PageHead), `StudentFormModal`, `ImportClassRecord`, `OtpInput`, `AddressFields/Combo`, `TraceMark`, `ai.jsx` (all exports), `Charts.jsx` (all chart variants), `chartTheme`, `AppShell`, `Header`, `Sidebar`, all three contexts, API client/endpoints, storage migration, grading/standing helpers and image/system configuration.

No payment, checkout, billing, subscription or financial transaction UI/API adapter exists in the reviewed frontend. Those flows are **absent from application scope**, not marked tested or passing. No privacy policy/terms route is registered; whether those documents are required is a product/legal requirement to establish separately, not an invented broken link.

## Additional risks and limits requiring launch evidence

- API/client source review found no injected HTML renderer, `dangerouslySetInnerHTML`, or eval-style application code. This is useful negative evidence, not a guarantee of no XSS. Persistent access/refresh tokens are readable from JavaScript localStorage; any future script injection would expose them. See the security report for authentication/session/deployment consequences.
- Most data loads lack abort/unmount coordination and many pages combine unrelated dependencies in `Promise.all`; a single failing dependency can suppress the entire page. FE-04/08/20/21 capture concrete cases. There is no general error boundary for malformed success payloads or render-time exceptions.
- 403 responses leave local tokens/user state in place and no session-expired event is fired (confirmed exact-source probe). Server rejection protects subsequent data requests; automatic clearing for a deactivated account/school is not implemented in the UI. Cross-tab logout synchronization is also absent (`storage`/BroadcastChannel listeners are not present).
- No navigation or `beforeunload` protection exists for unsaved record drafts. ClassRecord explicitly clears scores on period/subject change; PACE clears on section reload. Back/reload/close can lose unsaved entries without a decision. This is distinct from the *silent save-triggered* losses in FE-02 and should be included in the draft-preservation repair.
- Stale setup is displayed while a new class-record setup request is pending; failed setup fetch silently falls back to defaults. The `.catch()` lacks the cancellation guard present in `.then()`, so an old failed request can reset a newer setup. Include request-generation checks and a deliberate setup-error state in FE-03/04 remediation.
- Full student lists, search directories, warnings and teachers are loaded into the browser with no server pagination; only the Students view slices locally. Build also eagerly imports every route/chart into one 229 kB gzip JavaScript bundle. Load/performance limits need measured production-sized fixtures, not a claim based on a small demo dataset.
- Remote Google Fonts/Unsplash assets are optional network dependencies. Image fallback is deliberate; external availability, deployed security headers, private file access, custom domains, real TLS/CORS, service-worker/offline behavior and hosting SPA rewrites require deployment-side verification.
- Tests exercised one installed Chrome and emulated viewport widths, not Safari/iOS, Firefox, physical touch devices, screen-reader speech, 200–400% zoom, low-memory hardware, full real-time multi-user edits, real email transport, or a live public deployment. Automated axe results and browser overflow metrics in the root report must not be described as full accessibility/mobile certification.

## Browser appendix: successful complementary flows

The 19 scenarios in `frontend-ui-evidence.json` demonstrated real teacher registration, approval and password-reset integration with the audit API, not just renders. Passing cases include pending-account rejection, approval removal from queue, approved OTP sign-in, password mismatch feedback, valid reset and subsequent new-password sign-in, all five academic setup create modals, grade-level edit, assignment deletion/confirmation, working address keyboard selections, and mobile link navigation closing the drawer.

Failed user-facing expectations were recorded as findings rather than relabeled test successes: Security menu tab did not switch; empty names were accepted; modal focus started and escaped outside; Escape did not close mobile navigation; and address lookup failure never recovered or displayed feedback. The script itself completed cleanly.

**Launch gate:** resolve and regression-test the high-severity record-integrity defects, then validate the medium-severity authentication/resilience and operational-flow gaps against the actual deployment. A build/lint pass and successful happy paths do not override unresolved findings or untested integrations.
