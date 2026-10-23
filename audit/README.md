# TRACE pre-launch audit

**Decision: DO NOT LAUNCH with real student data.** Critical account takeover and multiple high-severity authorization, academic-record integrity, import, concurrency, and operational failures remain unresolved. Passing builds and happy paths do not make the application ready.

Audited **20 October 2026 (Asia/Manila)**, working tree based on commit `a1a0e6a`. Application source and the existing `backend/lbca.db` were left unchanged. All test writes went to separate invented-data databases below `audit/`; browser fixtures, uploads, installed audit tooling, and logs are ignored by Git. This deliverable documents findings and reproducible probes; it does not apply fixes.

## Read the findings

| Report | Coverage |
| --- | --- |
| [Findings index](findings-index.csv) | Searchable severity/ID/title/document index. Some reports describe the same root issue from frontend and backend perspectives; index entries are not an inflated count of independent vulnerabilities. |
| [Authentication and security](security-auth.md) | Account takeover, recovery, JWT sessions, OTPs, rate limits, roles, uploads, account validation, hosting/storage. |
| [Backend data and AI integration](backend-data-ai.md) | Every academic/student/PACE/warning/import/dashboard/AI route, actual access checks, grading, historical state, validation, deletion, concurrent writes, and cold-model handling. |
| [Independent cross-check and ML review](backend-followup.md) | Real-JWT reproduction of major authorization findings, independent auth cross-check, endpoint census, tracked learner-dataset privacy, model evaluation and grading parity. |
| [Frontend workflows](frontend-flows.md) | Every page and shared component, forms, drafts, route guards, errors/loading, accessibility/mobile, CSV exports, missing enrollment lifecycle. |
| [Browser and HTTP evidence](browser-runtime.md) | Actual role/route matrix, screen sizes, delay/offline/outage tests, browser reproductions, accessibility, finite concurrent reads, readiness and documentation defects. |
| [Python dependencies](python-dependencies.md) | Resolved and pinned dependency inventory, current primary advisories, bounded vulnerable-parser probes, applicability and dormant matches. |
| [Frontend dependencies](frontend-dependencies.md) | npm scan and primary-advisory review; affected versions distinguished from demonstrated app exploit paths. |

Every finding includes severity, concrete evidence, impact/preconditions, and recommended remediation. Reports distinguish executed behavior, deterministic source/helper checks, configuration-dependent exposure, and work that still requires a deployed environment. All findings are **open** unless explicitly stated to be an advisory without an applicable application path.

## Findings that block launch

1. **Anonymous account takeover in the supplied deployment configuration (SA-01).** The public recovery response reveals its reset code because `DEBUG_OTP=true` is both the default and the blueprint value. Knowing a registered email was enough to reset an invented victim's password and sign in. This was independently reproduced. Turning debug off alone also breaks legitimate new-device sign-in/recovery because actual email delivery is absent (SA-03).
2. **Logout and recovery do not evict bearer credentials (SA-02).** Old access and refresh tokens still work after logout/password recovery. The dependency checks the staff account's status but never checks the session record's revoked state. Refresh rotation can be replayed.
3. **Teacher boundaries fail on actual data mutations (BD-01–04).** A teacher can rename an allowed record into an unassigned subject, mismatch a private learner with an allowed enrollment, inspect/change other classes' correction cases, and receive hidden grades in a profile-update response. Independent checks used real JWT authentication with no dependency overrides.
4. **Saved and displayed academic results are unreliable (BD-05–09, BD-15/21/22, FE-01–05, BF-03).** Invalid updates can persist and make future reads fail. Headers/deletions leave stale warnings. Historical years and missing terms contaminate standing. Drafts can move to another subject/period, disappear after filtering, or display under the wrong section. Incomplete assessments are treated as finalized. Cross-runtime rounding can change a displayed passing result to a saved failing result.
5. **Imports can assign scores to the wrong child or partially commit on failure (BD-10/11).** Same-name matching drops distinguishing information; a later-term failure leaves earlier writes behind. Predictions also prevent some student deletions (BD-14).
6. **Concurrent access exposes unsafe writes (SA-06/20, BD-13).** One-time secrets can be consumed more than once, parallel wrong passwords lose increments and evade lockout, and simultaneous first saves cause unhandled database errors. General edits also have no conflict detection.
7. **Ordinary saves wait for AI wake retries (BD-12).** Source and injected transport/timer checks show a configured worst-case budget of up to 405 seconds before commit for one failed scoring batch. This is a configured bound, not a measured 405-second hosted request. The promised short/nonblocking fallback is not what the save path implements.
8. **Framework/upload dependencies have reachable security advisories (DEP-01/02/07).** Bounded tests confirmed problematic parsing behavior. FastAPI's current pin prevents updating Starlette independently; review a compatible framework/parser upgrade. Crypto/SSR advisories with absent prerequisites are explicitly separated from actual exploit paths.
9. **The supplied hosting plan is unsuitable for durable production records (SA-16/17, RT-01).** The database plan expires, a recoverable backup/restore process is absent from the repository, uploads are ephemeral, and health can return 200 with a known schema mismatch. Actual account-level hosting/backups were not available to inspect.
10. **Dataset release needs a privacy decision (BF-01).** Three tracked learner-level datasets include stable pseudonyms and academic results; the publicly specified unkeyed hash can be matched against candidate names. Only headers/source and an invented-name proof were inspected. This does not establish that the repository is public or that a real learner was identified.

Medium/low issues are documented rather than dismissed: session restoration, stuck loading/error states, address lookup recovery, 1,000-record prediction truncation, missing enrollment-transfer/rollover UI, mobile overflow, keyboard/focus defects, contrast and control names, CSV formula handling, account-navigation bugs, and stale setup documentation all require triage and retest.

## What was actually verified

| Area | Evidence and result |
| --- | --- |
| Application inventory | [88 explicit endpoint operations](api-inventory.csv): 82 backend, 6 AI service. Backend inventory exactly matches live OpenAPI. Static docs/schema and uploads mounts are additional framework surfaces. |
| Backend academic/student workflows | 71 named isolated route probes, successful CRUD and negative cases. All 53 operations in academic/student/PACE/import/dashboard/AI routers were invoked. [Evidence](backend-data-ai-evidence.json). |
| Staff/school/auth lifecycle | All 27 operations received successful and negative requests across 118 recorded probes, including school approval/suspension/restore and account/photo deletion. With the 53 data operations and 2 meta routes, all 82 explicit backend operations were invoked. [Evidence](security-lifecycle-results.json). |
| Authentication/authorization | Real auth/recovery/session/OTP probes, two-school boundaries, account/operator roles, concurrency; independent auth and real-JWT data checks. [Auth](security-auth-results.json), [independent JWT check](backend-jwt-crosscheck.json), [auth cross-check](security-auth-crosscheck-results.json). |
| Anonymous HTTP checks | All 35 explicit backend GET operations tested: 32 protected returned 401; public directory/root/health returned 200. [Evidence](http-runtime-results.json). |
| Browser pages/roles | 37 role/route visits, four widths each (148 measurements), axe scans, signed-out redirects and signed-in routes. Every registered page family reviewed; PACE additionally exercised with unit tracking. [Matrix](browser-route-matrix.json). |
| Actual form journeys | Three role logins plus 19 complementary scenarios, including signup→pending→approval→OTP login; reset→new-password login; five academic create forms/edit/delete, invalid input, address selection, keyboard/navigation. [Evidence](frontend-ui-evidence.json). |
| Adverse browser conditions | Corrected outage/offline/storage/session/draft tests and reordered response tests. [Scenarios](browser-scenarios-results.json), [targeted checks](browser-targeted-results.json). |
| Multi-user reads | 100 real authenticated HTTP requests at concurrency 25, all 200; p95 3.04 seconds on the small local fixture. Separate write/security concurrency tests failed. This does not establish production capacity. |
| Actual AI artifact | Shipped v4 model loaded successfully. Valid inference, 500-record batch, ten concurrent predictions, bad inputs/unknown fields, and service-token rejection exercised. [Evidence](ai-service-evidence.json). |
| Frontend/backend grade parity | 643 synthetic cases compared; 26 differed, including 4 term-grade mismatches. Actual authenticated save and independent database read confirmed client passing 75 versus server failing 74. [Evidence](grading-parity-results.json). |
| Frontend build/lint | Production build passed; lint had zero errors and four warnings. Eager JavaScript bundle was 844.47 kB / 229.20 kB gzip. |
| Existing tenant test | Passed with UTF-8 enabled. It checks declared dependency boundaries using stubs/AST and does not substitute for the failed actual permission tests. |
| Static assets | All 83 address JSON files parsed; actual cascading location selection worked when data loaded. |
| Dependencies | 54 Python package/version pairs checked with zero metadata-query failures; npm registry scan completed. Advisory prerequisites and untested live resolved versions are documented. |

The requested payment, checkout, subscription, and billing flows **do not exist in this application**. No processor integration was found in either app tier; payments are outside current scope, not marked as passing. This app uses SQLAlchemy/SQLite/PostgreSQL rather than Firebase/Supabase rules; permission review focused on API guards, tenant/class queries, database constraints and connection configuration. Actual hosted database account grants were unavailable.

## What this audit does not certify

- No deployed URL, hosting account, actual PostgreSQL environment, production secrets, real mail provider, or backup destination was provided. Live TLS, proxy forwarding/body limits, deployed CORS/headers/SPA rewrites, database roles/network exposure, deliverability, monitoring/alerts, restore recovery, and migration/rollback must be verified in staging. Supplied defaults/configuration were reviewed; they are not evidence of an unknown live account's exact settings.
- Browser execution used one installed Chrome on Windows with emulated screen widths. Safari/iOS, Firefox, physical touch devices, low-memory hardware, screen-reader speech and zoom were not certified. Axe is an automated check, not full accessibility certification.
- Delay/abort/outage injections cover concrete failure handling. Prolonged packet loss, full bandwidth shaping, endurance/soak, production-scale school volumes, and PostgreSQL race/locking behavior remain unverified. Small-fixture concurrency results cannot promise a user limit.
- The ML service's inference works, but that does not establish fair/accurate generalization to new schools or unseen grade levels. The checked-in card's small failure count, held-out-school interval coverage, unchanged passing threshold, and flawed repeated-fold claims are documented. No sensitive raw learner records were loaded to revalidate the research dataset.
- No repository scan can prove there are no secrets or unknown vulnerabilities. Reviewed files/history names did not establish a real private provider-key leak; the known default signing key and debug codes are confirmed unsafe configuration issues. Known metadata matches were not automatically labeled exploitable.

These are explicit remaining checks, not assumed passes. A complete review of the available local application is not a certification of unavailable infrastructure or every possible payload/browser combination.

## Remediation and retest order

1. Contain account takeover; implement real OTP delivery and safe production startup checks. Enforce session revocation/refresh rotation, atomic challenge consumption and lockout counters, trusted proxy attribution, and account/challenge budgets.
2. Repair class/tenant authorization and identity relationships. Use one shared validated write/response path; add regression tests for real authenticated subjects/classes/schools and inconsistent foreign keys.
3. Define and implement a single academic-state invariant: deterministic frontend/backend grading, complete inputs, current enrollment/year/period, synchronized warnings/predictions on every mutation, transactional imports, stable subject IDs, and safe deletion/cascades.
4. Preserve drafts by context and successful snapshot; handle cancellation/reordering, conflicts, validation errors, outages, session restoration, and retry states. Complete enrollment lifecycle UI and fix inaccessible/mobile controls.
5. Upgrade compatible vulnerable dependencies; establish reviewed migrations, nonexpiring durable storage, private upload policy, backup/restore, readiness, deployment configuration, and dataset release controls.
6. Rerun the supplied reproductions against fixes; convert critical invariants into maintained tests. Then exercise the complete secure debug-off flows and operational recovery against the actual staging deployment and representative multi-school volumes.

**Launch approval remains withheld.** Every critical/high defect must be fixed and retested; remaining issues and infrastructure evidence must be resolved or explicitly accepted with a justified scope. No code was repaired or deployed as part of this audit.
