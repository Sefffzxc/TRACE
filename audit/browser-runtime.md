# Browser, HTTP resilience, and operational verification

20 October 2026 (Asia/Manila). Tests used installed Chrome headless, a local Vite frontend, and a real Uvicorn API connected **only** to `audit/browser.db`. The existing application database was not opened. Application source was not changed. Test accounts, school settings, and academic records created/changed in that isolated fixture are invented.

## Browser coverage and reliable evidence

[browser-route-matrix.json](browser-route-matrix.json) contains **37 role/route visits**, each inspected at **1440, 768, 375, and 320 CSS pixels**, plus WCAG A/AA axe scans. It includes guest redirects; actual teacher, principal, and operator sign-in/OTP; every registered page family; allowed and forbidden role routes; a valid and nonexistent student; removed `/schedule`; and unknown paths. This is 148 viewport measurements, not 148 independent user journeys. The PACE route was additionally rendered after changing the isolated school's unit-tracking setting.

The initial combined harness completed the route matrix, then stopped because a test selector expected `English` rather than the actual option label `English (0 Modules)`. Its initial cold-session/filter scenarios also needed isolation/name-selector corrections. **Those initial scenario results are superseded.** The original `browser-results.json` is retained for transparency; use the extracted route matrix and the successful corrected [browser-scenarios-results.json](browser-scenarios-results.json) and [browser-targeted-results.json](browser-targeted-results.json) for findings. A harness error is not classified as an application defect.

The corrected browser scenarios and targeted checks confirmed:

| Finding | Observed browser outcome |
| --- | --- |
| FE-01, PACE subject draft drift | Entered English progress `7`; selecting Mathematics retained dirty value `7` under Mathematics. |
| FE-02, hidden drafts lost | Edited two learners, searched for one, clicked Save. Captured real UI request contained one entry; clearing search showed the second learner's original `20`, replacing its unsaved `4`. The save response was intercepted to leave stored grades intact. |
| FE-03, setup context drift | Edited first-period highest score from `20` to `37`, selected second period, saved. Captured PUT targeted `grading_period=2` with `ww_highest[0]=37`. The write was intercepted. |
| FE-04, stale section load | Delayed old-section API responses by 1.7 seconds. After selecting Sampaguita, the final table displayed the five Rosal learners. Actual enrollment API results confirmed the mismatch. |
| FE-06, cold-session restoration | Valid refresh endpoint independently returned 200. With invalid/expired access and that valid refresh stored, reload made **zero** refresh requests, navigated to `/login`, and cleared refresh storage. |
| FE-08, settings outage | Injected 503 only for school settings. Other warning APIs worked, but 56 skeleton elements remained, with no settings error or retry. |
| FE-09, denied storage | A simulated browser `SecurityError` from localStorage produced an empty application and unhandled page errors. |
| FE-17, mobile layout | Teacher dashboard and student profile had scrollWidth 356 at width 320; principal class management had scrollWidth 334. Account at 375 retained two narrow columns rather than stacking. |
| FE-18, passing lines | Four charts rendered; zero `.recharts-reference-line` elements. This corroborates the unsupported wrapper defect in section/subject charts; the separate quarter chart draws its threshold as a data series. |
| Offline student API | Network abort produced the intended visible `Cannot reach the server` error; it also displayed an empty filtered result below it. |
| Stalled session API | A deliberately unresolved `/api/auth/me` remained at `Restoring your session` after 2.2 seconds. Source confirms no timeout, cancellation, or retry UI; this short observation alone does not measure an infinite wait. |
| Delayed registration directory | An additional 1.2-second directory delay settled successfully without error. |

Screenshots were also visually inspected: [account at 375](account-mobile-375.png) and [dashboard at 320](dashboard-mobile-320.png). These show the narrow account columns and dashboard overflow/cramped attention/activity panels. An emulated width is not a physical-device or Safari/iOS test.

Complementary [frontend-ui-evidence.json](frontend-ui-evidence.json) covers 19 further scenarios, including real teacher registration, pending rejection, principal approval, OTP sign-in, password reset and new-password sign-in, all five class-management create forms, a grade edit, assignment deletion, address selection, navigation, and modal keyboard behavior. See [frontend-flows.md](frontend-flows.md) for each failure and coverage limits. These debug-OTP successes do not demonstrate secure email delivery.

## RT-01 - Medium - Readiness returns successful health with a known incompatible database schema

**Evidence:** `backend/app/main.py:202` returns a body with `status: ok` and HTTP 200 even when its schema state explicitly reports missing tables/columns. `render.yaml` uses `/health` for the API health check. Startup creates missing tables but does not migrate existing columns, and schema verification is diagnostic rather than deployment-blocking.

**Verification:** An isolated ASGI test injected a known failed schema diagnostic, without opening any database, and called the real health route. It returned 200 with `status: ok` and `schema.up_to_date: false`. Safe output: [health-probe.json](health-probe.json). The live audit fixture's normal schema was valid; this does not claim an unknown hosted database is currently broken.

**Impact:** Hosting/monitoring can accept a deployment whose ordinary queries will fail due to missing columns. An HTTP health probe cannot distinguish ready service from a known incompatible data layer.

**Recommendation:** Separate liveness from readiness; return a failing readiness status for unusable database/schema state, check database connectivity, and run versioned, reviewed migrations before promoting a deployment. Test upgrades on an existing-schema copy and a restore before launch.

## RT-02 - Low - Quick-start instructions, demo accounts, and route list are stale

**Evidence:** Root and backend READMEs instruct `python seed.py`, advertise the old `Lbca@2026` demo accounts, document `--reset`, and list `/schedule`. Current `seed.py` requires explicit `--demo`, creates different accounts with `Trace@2026`, has `--rebuild` rather than `--reset`, and frontend `App.jsx` has no schedule route. The frontend deployment blueprint also says no committed lockfile exists, although `frontend/package-lock.json` is tracked.

**Verification:** Executed `seed.py --help` and bare `seed.py` with an audit-only database setting. The help lists the current flags; bare invocation prints help and `Nothing was changed`. The browser visited `/schedule` and received the app's not-found screen. This is a stale documented link, not an active broken navigation link.

**Impact:** A new operator following the quick start gets no seeded users and cannot sign in with the listed credentials; stale recovery/setup guidance increases deployment mistakes.

**Recommendation:** Reconcile documentation with the executable workflow and current routes; publish a single authoritative setup/operations guide and validate its safe commands on a new fixture. Do not substitute a destructive rebuild command for an incremental migration.

## RT-03 - Medium - Automated accessibility checks find missing names, invalid ARIA, and insufficient contrast

**Evidence:** The route matrix retains selectors, failure text, and counts from actual axe scans. Findings include login text at contrast **4.15:1** against a required **4.5:1**; account file input without an accessible name; teacher status select without a name; dashboard search input using `aria-expanded` without the corresponding supported role; and chart image-role paths without accessible text. Other authenticated pages also have contrast failures.

**Impact:** Some controls and chart meaning are unclear to assistive technology, and small text is harder to read. Axe's internal `critical`/`serious` impact labels describe accessibility rules; they are **not** critical-security vulnerability ratings. The file input has a separate visible upload button, so this is not a claim that uploading is impossible.

**Recommendation:** Supply programmatic labels; implement the search as a valid combobox or remove unsupported ARIA; provide accessible chart summaries/names; adjust contrast tokens and recheck light/dark states. Combine axe with keyboard and screen-reader tests. FE-16/23/25 separately document focus and keyboard failures that axe alone misses.

## Real HTTP and concurrent read checks

[http-runtime-results.json](http-runtime-results.json) records **all 35 explicit backend GET routes without authentication**: the 32 protected operations returned 401; school directory, root, and health returned 200. Backend source inventory covers all 82 explicit methods/routes, including mutations tested by the other harnesses.

A finite local read burst issued **100 authenticated requests with concurrency 25** across dashboard, students, warnings, PACE, sections, grade levels, enrollments, and own classes. All 100 returned 200. Measured duration was **4.387 seconds**, p50 **700 ms**, p95 **3,040 ms**, maximum **3,994 ms**. This is a small Windows/SQLite fixture, not a capacity commitment for Render/PostgreSQL or an endurance test. Concurrent writes, OTPs, reset grants, and failed-login counters fail separate tests; passing reads does not override those failures.

Allowed-origin preflight returned 200 with the configured origin; an untrusted origin returned 400 without `Access-Control-Allow-Origin`. Search probes containing an SQL-like string, HTML-like string, `%`, and a 5,000-character string returned structured JSON without crashing. These are smoke tests, not proof against every injection variant. All **83 shipped address JSON files** parsed successfully; their geographical accuracy was not independently verified.

The local API did not emit `Cache-Control: no-store` on student data responses. No actual shared-cache leak was demonstrated; set an explicit sensitive-response caching policy and verify the real ingress/browser behavior. Local HTTP also cannot establish hosted TLS/HSTS, proxy limits, CSP, or hosting header configuration.

## Reproduction and limits

With the isolated audit API/frontend already running:

```powershell
node audit/browser-audit.cjs
$env:AUDIT_SCENARIOS='1'
node audit/browser-audit.cjs
node audit/browser-targeted.cjs
backend/.venv/Scripts/python.exe audit/http-runtime-probes.py
node audit/frontend-ui-probes.cjs
```

These scripts expect seeded invented audit accounts and intentionally create/update audit fixture data. **Do not point them at a production database or production API.** Tooling is available under the ignored audit runtime; it is not an app dependency. Browser launches used the installed Chrome executable.

No physical devices, other browser engines, prolonged load/soak, full packet-loss/bandwidth simulation, live email provider, production ingress, PostgreSQL deployment, backup restore, or external payment integration was available to certify. Response-delay, abort, outage, and denial-policy injection are deliberately identified above rather than represented as measurements of real user network conditions. Every unresolved finding remains open.
