# Authentication, account boundaries, secrets, and deployment audit

Audit date: 20 October 2026 (Asia/Manila). Application commit at inspection: `a1a0e6a`.

**Assessment: not ready for real student data or public production launch.** The shipped deployment configuration permits anonymous account takeover, and session revocation does not work. The implementation is explicitly documented as a demonstration deployment; that documentation does not make these production blockers safe.

## Method and limits

Reviewed `backend/app/api/{deps,scoping}.py`, the auth/staff/school routers and schemas/models, security/configuration, app startup/static file handling, deployment blueprint/documentation, frontend token storage and password-change routing, and current/history filenames for likely committed secrets. Runtime probes used the real FastAPI ASGI application, HTTPX, actual SQLAlchemy models, and an independently created SQLite database with invented users in two schools plus an operator. No existing `backend/lbca.db` data was read or changed. No application source code was changed.

Reproduction command, from the repository root:

```powershell
backend/.venv/Scripts/python.exe audit/security_auth_probe.py
```

The script creates a uniquely named audit database and upload directory on every run. It writes **safe status/boolean results only** to `audit/security-auth-results.json`. Database/upload fixtures must not be published or committed. Results below are from the final run; concurrent outcomes vary by scheduling. These are application-level tests, not tests through a live Render ingress. No live hosting configuration, backup restore, production database permissions, or actual mailbox delivery was available to verify.

## Confirmed findings

### SA-01 — Critical — Anonymous password reset exposes the secret needed to take over an account

**Evidence:** `render.yaml:157` sets `DEBUG_OTP` to `true`; `backend/app/core/config.py:65` defaults it to true. `backend/app/api/routers/auth.py:566` returns `debug_otp` from the unauthenticated forgot-password endpoint; `auth.py:571` exchanges that code for a reset token, and `auth.py:617` accepts the token to set a new password. Login also returns its OTP at `auth.py:355`.

**Reproduction:** For an invented approved victim, called forgot-password knowing only their email, read the OTP from the response, exchanged it (`200`), reset their password (`200`), then logged in as that victim with the replacement password. No old password or mailbox access was needed. The probe reports `victim_account_login_succeeded: true`.

**Impact/preconditions:** Anyone who can reach this deployment and knows a registered email can take over an approved account, including a principal or system operator. They gain the victim's real permissions. This is more serious than merely ineffective two-factor authentication.

**Remediation:** Remove OTP/token disclosure from all public responses and logs; implement actual out-of-band delivery; default debug delivery off; reject unsafe production configuration at startup. Rotate/reset credentials and invalidate all active sessions/reset grants if this mode has ever held real accounts. Block launch until the complete non-debug flow is tested.

### SA-02 — High — Logout, password reset, forced reset, and refresh rotation do not revoke bearer credentials

**Evidence:** `backend/app/api/deps.py:21` validates JWT signature/type/expiry and the current account/school, including `Staff.is_active`, but never checks `StaffSession.is_active`, a session ID, or a credential version. `auth.py:487` marks database sessions inactive on logout, which has no effect on that dependency. `auth.py:460` accepts any valid signed refresh token and updates the user's latest active session, rather than the presented token's session. `auth.py:651` and `staff.py:422` change password hashes without invalidating JWT credentials.

**Reproduction:** Logout returned `204`; the same access token still read `/api/auth/me` (`200`) and its refresh token still minted tokens (`200`). Both pre-password-reset access and refresh tokens remained valid (`200`). Replaying an old refresh token after a successful refresh returned `200` again. A token issued before an admin forced reset still read protected staff data (`200`).

**Impact/preconditions:** A stolen token remains useful after the victim logs out or recovers the account. An attacker can refresh for the original refresh token's seven-day validity. New credentials do not evict the attacker. Refresh can also update an unrelated latest session's record.

**Remediation:** Bind tokens to a server-side session ID and credential version; check active/revoked state on every request. Rotate refresh tokens atomically, hash stored refresh credentials, detect replay, and expire the correct session. Revoke existing sessions, trusted devices as appropriate, and outstanding reset grants after password recovery/forced resets. Test both old access and old refresh tokens for rejection.

### SA-03 — High — Turning off OTP disclosure leaves sign-in and password recovery with no delivery mechanism

**Evidence:** `auth.py:349` explicitly states no mail server exists. `auth.py:351` and `auth.py:563` only print codes; neither path invokes mail delivery. A repository search found no mail transport/provider implementation. The print statements run even with `DEBUG_OTP=false`.

**Verification:** With debug mode disabled at runtime, forgot-password returned `202` and no code in its response; source inspection confirms the only delivery is the unconditional console print. New devices require the same unimplemented mail flow.

**Impact/preconditions:** The required security fix to SA-01 alone makes legitimate new-device sign-in and recovery unusable. Anyone with service-log access can still read active OTPs and account email addresses.

**Remediation:** Add a real delivery provider, verified sender configuration, delivery/error handling, resend/cooldown controls, and a staging mailbox test. Never log OTPs in production. Test the entire sign-in and recovery flows with debug disabled.

### SA-04 — High — Password-reset OTPs have no per-code attempt limit

**Evidence:** `auth.py:594` checks reset-code expiry; `auth.py:596` merely increments attempts on incorrect codes. It never checks `OTP_MAX_ATTEMPTS`, unlike login OTP verification at `auth.py:406`.

**Reproduction:** Submitted 12 incorrect reset codes for one issued OTP; all returned `400`; the correct code still exchanged successfully (`200`). Requests used separate forwarded-IP buckets to demonstrate the independent account/code limit is absent.

**Impact/preconditions:** A six-digit recovery secret is vulnerable to distributed guessing and, where SA-05 applies, guessing from one client. Debug disclosure is not required. This finding establishes the missing cap; an exhaustive million-code attack was deliberately not run.

**Remediation:** Enforce an atomic persistent per-challenge attempt budget and per-account recovery budget. Invalidate on exhaustion, expiry, reissue, and success. Apply limits across processes/instances and monitor abuse.

### SA-05 — High — A caller-controlled forwarding header bypasses authentication rate limits

**Evidence:** `backend/app/api/deps.py:206` unconditionally trusts the first value of `X-Forwarded-For`. `backend/app/services/audit.py:47` uses the same trust rule for audit attribution. Login, OTP, registration, and recovery limits share this implementation.

**Reproduction:** From the same ASGI client, ten invalid logins with one claimed IP returned `401`; request eleven returned `429`; changing only `X-Forwarded-For` immediately returned `401` again. No actual network client change was made.

**Impact/preconditions:** An internet client can evade these application limits if it can reach the backend directly or if ingress preserves attacker-supplied first-hop forwarding data. Actual Render header sanitization was not tested and must not be assumed. Combined with SA-04 this removes both reset-code protections. Audit source-IP attribution can be forged. `_BUCKETS` also retains a dictionary entry for every distinct key; spoofed unique values can grow process memory (static observation; no exhaustion test run).

**Remediation:** Configure explicit trusted proxy hops and obtain the normalized client IP from that trusted boundary. Ignore arbitrary forwarding headers from untrusted peers. Add account/challenge limits and a shared, expiring store. Verify through the intended production ingress with spoofed headers.

### SA-06 — Medium — OTP consumption is not atomic and older codes become valid again

**Evidence:** Both `auth.py:387` and `auth.py:583` select the latest unused OTP, later set `is_used`, and commit separately. No conditional claim/update or row locking prevents parallel redemption. Code issuance at `auth.py:337` and `auth.py:551` does not invalidate previous challenges.

**Reproduction:** Across bounded runs, four parallel redemptions of one login OTP issued three or four token-bearing responses (`201`), and four parallel reset-code exchanges issued three or four reset-token responses (`200`). Separately, after using the newer of two login codes, the older code also issued a session (`201`).

**Impact/preconditions:** A party who captures an OTP can race the legitimate user and obtain additional sessions/reset grants; reissue does not retire old secrets. Duplicate trusted-device rows are also possible because the staff/device index is not unique (`backend/app/models/staff.py:155`), making later `.scalar_one_or_none()` queries vulnerable to multi-row errors under some races (static risk; that particular error was not reproduced).

**Remediation:** Assign a challenge ID bound to account/purpose/login context. Invalidate prior challenges on reissue. Claim a challenge using one atomic database operation and require exactly one affected row. Add a unique staff/device constraint, handle creation races, and invalidate other recovery grants after successful password recovery. Repeat concurrent tests on PostgreSQL.

### SA-07 — High, configuration-dependent — Public default JWT signing key is accepted

**Evidence:** `backend/app/core/config.py:49` has a known source-controlled placeholder key. No startup validation rejects it. `backend/app/core/security.py:61` signs and verifies tokens with that setting. Render's blueprint correctly generates a different key (`render.yaml:111`), so this is not a claim that its generated key is known.

**Reproduction:** In the isolated application, temporarily selected the source default, signed an access JWT externally for an invented approved user's known UUID, and read `/api/auth/me` successfully (`200`) without password/OTP/session issuance. Restored the random audit signing key immediately afterwards.

**Impact/preconditions:** Any installation launched with the default key allows token forgery for known account IDs. This affects direct/local/manual deployments; actual deployed environment values were unavailable for verification.

**Remediation:** Require a cryptographically random secret through configuration and fail startup on known defaults, empty values, or inadequate entropy. Rotate the key and all sessions anywhere the default was previously used. Preserve Render's generated secret configuration.

### SA-08 — Medium — Mandatory password change is only a UI suggestion

**Evidence:** `staff.py:425` sets `requires_password_change`; `auth.py:103` returns it. `deps.py:21` does not enforce it. The frontend redirects to the account page but does not create an API restriction (`frontend/src/pages/auth/LoginPage.jsx:35`, `VerifyOtpPage.jsx:45`).

**Reproduction:** After admin force-reset, a login response contained `requires_password_change: true`; that new token immediately read `/api/staff/teachers` (`200`) before changing its temporary password. The older token also continued working under SA-02.

**Impact/preconditions:** A person holding a shared/temporary password can continue using normal application permissions indefinitely without changing it, contrary to the admin-facing promise.

**Remediation:** Issue a restricted password-change session or enforce the flag server-side on every protected route, allowing only the necessary change/logout endpoints. Expire temporary credentials and revoke earlier sessions.

### SA-09 — Medium — Principal registration bypasses the advertised password policy

**Evidence:** `backend/app/schemas/staff.py:39` validates teacher password strength, but `backend/app/schemas/schools.py:65` only checks principal password length. `schools.py:150` immediately hashes it.

**Reproduction:** A principal registration using an eight-character lowercase-only password returned `201` and created the pending school/admin account.

**Impact/preconditions:** The more powerful account type accepts passwords rejected for ordinary teachers. Once the school is approved it is a real privileged account with a weak credential.

**Remediation:** Share one password validator across school registration, teacher registration, password change/reset, and CLI account creation. Test privileged signup explicitly.

### SA-10 — Medium — Passwords are silently truncated to the first 72 UTF-8 bytes

**Evidence:** `backend/app/core/security.py:20` slices encoded passwords before hashing/checking. Registration and changes permit up to 128 characters (`schemas/staff.py:30`, `staff.py:90`, `schemas/schools.py:65`).

**Reproduction:** Hashed one password and successfully authenticated a different password with the same first 72 bytes (`distinct_long_password_accepted: true`).

**Impact/preconditions:** Users are given a false impression that their entire password is secret material. Unicode passwords reach the byte limit much sooner. Policy-required uppercase/digit/special characters placed after the boundary are discarded from the effective password.

**Remediation:** Prefer a password hash supporting the full accepted input, such as Argon2id. Alternatively enforce the bcrypt byte boundary explicitly with a clear validation error and a considered migration plan. Do not silently alter passwords.

### SA-11 — Medium — School administrators cannot see important security audit events

**Evidence:** `staff.py:287` scopes audit entries solely through `AuditLog.admin_id`. Failed login/lockout/reset/registration events are written with only a target user, e.g. `auth.py:303`, `auth.py:560`, and `auth.py:662`, leaving `admin_id` null. School settings updates at `schools.py:222` also omit an actor even though the caller is authenticated.

**Reproduction:** The isolated DB contained three `password_reset_requested` events for school accounts; its principal's matching audit API returned zero. A successful school settings update (`200`) also produced zero visible settings audit entries.

**Impact/preconditions:** The administrator's audit interface omits recovery attempts and other attack signals. Missing actors make attribution incomplete. This limits incident detection even though some events exist in the database.

**Remediation:** Persist a tenant ID on audit entries, derive scope safely from actor/target at write time, and store authenticated actor IDs. Query by tenant rather than actor membership alone. Ensure login/reset failures are visible without leaking another school's data and retain events after account deletion.

### SA-12 — Medium — Teacher can bypass the stated restriction on viewing administrators

**Evidence:** `staff.py:183` documents that only admins see admins. But `staff.py:189` accepts any provided role before applying the non-admin restriction; `staff.py:316` also lets any school user retrieve any colleague's profile. `schemas/staff.py:48` exposes contact details, rejection reasons, account status, lock state, and login timing.

**Reproduction:** Teacher requests to `/api/staff?role=admin` and the principal's direct staff URL both returned `200`. A foreign school's staff URL correctly returned `404`.

**Impact/preconditions:** An approved teacher can see profiles and account metadata explicitly intended to be admin-only. This is a same-school policy mismatch, not a cross-school leak or privilege escalation.

**Remediation:** Enforce caller-specific allowed roles independently of query parameters; protect direct profile access consistently; separate public colleague-directory fields from administrative account metadata. If principals are intentionally public to staff, document that policy and still restrict security/rejection fields.

### SA-13 — Medium — Duplicate signup identifiers can crash instead of returning a conflict

**Evidence:** `auth.py:135` checks the input email before lowercasing; `auth.py:154` lowercases it on insertion. The database enforces unique email, and no integrity conflict is translated into a safe user-facing conflict.

**Reproduction:** Registering with the uppercase form of an already registered address returned `500` with `error_type: IntegrityError`; expected a controlled `409`. A separate isolated lifecycle run also confirmed that a different email reusing an existing staff member's employee number in the same school returned `500` / `IntegrityError`. Both transactions rolled back.

**Impact/preconditions:** Ordinary duplicate signup inputs produce a server error and confusing retry behavior. The same check-then-insert pattern also needs concurrent duplicate submission handling; no additional duplicate race claim is made from these probes.

**Remediation:** Normalize before lookup, enforce uniqueness in the database, and catch/translate the relevant unique constraint violation. Apply consistently to employee numbers and concurrent registration paths.

### SA-14 — Medium — Explicit null school settings reach non-null database columns

**Evidence:** `schemas/schools.py:69` allows optional settings to be explicitly null. `schools.py:217` writes every supplied value, including null, into fields declared non-null by the school model.

**Reproduction:** Principal PATCH `/api/schools/me` with `{"passing_mark":null}` returned `500` / `IntegrityError`. The DB rejected the write and the transaction rolled back.

**Impact/preconditions:** Invalid form/API payloads cause server errors. Several other required settings share this schema pattern; `passing_mark` is the specifically verified case.

**Remediation:** Distinguish omitted from nullable fields, reject null for required settings at validation, and preserve legitimate null clearing only on nullable columns. Return a helpful `422`.

### SA-15 — Medium — Profile upload trusts a claimed image MIME type and reads the entire body before limiting size

**Evidence:** `staff.py:118` only checks client-supplied content type; `staff.py:123` reads the full file before checking its byte limit; `staff.py:133` saves it without decoding the image. `main.py:135` serves uploads as unauthenticated static content.

**Reproduction:** A text payload labeled `image/png` was accepted (`200`) as a profile picture, then fetched anonymously (`200`). This proves invalid-image acceptance and public retrieval; it does **not** establish JavaScript execution or path traversal. No oversized-memory exhaustion test was run.

**Impact/preconditions:** Signed-in users can store non-image content and break their avatar. Large uploads can consume memory before application rejection. Profile-photo URLs are public to anyone who obtains one, including after logout, so private-media requirements cannot be met by this route.

**Remediation:** Stream with a hard byte cap enforced at ingress and application level; decode/validate/re-encode accepted image formats with dimension limits; reject malformed data. Decide explicitly whether avatars are public; use authorized downloads or signed private object URLs if privacy is required.

### SA-16 — Medium — Profile photos are stored on the deployed service's ephemeral filesystem

**Evidence:** `staff.py:130` saves under the local `UPLOAD_DIR`; `core/config.py:79` defaults to relative `uploads`; `render.yaml:92` deploys the API on the free plan with no durable object store or disk configured.

**Verification:** Static configuration plus the platform's documented behavior; no destructive hosted redeploy was attempted. Render states local file changes on free services are lost on redeploy, restart, and spin-down. [Render free-service documentation](https://render.com/docs/free)

**Impact/preconditions:** Under the provided blueprint, previously uploaded avatars disappear while their database paths remain, causing broken images after routine lifecycle events.

**Remediation:** Store uploads in durable object storage, or use a supported paid persistent disk with a configured absolute upload path and backups. Verify by uploading, restarting a staging instance, and fetching the same asset.

### SA-17 — High — Provided database plan expires and has no configured recoverable backups

**Evidence:** `render.yaml:39` selects free Postgres. No automated dump/restore workflow is configured in the repository. `DEPLOYMENT.md:16` and `DEPLOYMENT.md:215` acknowledge expiration and missing backups.

**Verification:** Render's current docs say free Postgres expires after 30 days and is deleted after a further 14-day upgrade grace period, and free databases lack managed backups. [Render free database limits](https://render.com/docs/free), [Render backup documentation](https://render.com/docs/postgresql-backups). Actual account plan, expiration date, off-repository backups, and restoration were not accessible.

**Impact/preconditions:** Using the provided plan unchanged for real school records creates a foreseeable availability and permanent-loss risk. This is a configuration finding, not a claim that an existing live database was deleted.

**Remediation:** Configure durable production database hosting, automated encrypted backups with retention, and monitoring. Perform and document a restore drill before launch. Do not use destructive fresh-start seeding to apply production schema changes.

### SA-18 — Low — Login responses reveal whether an address is registered

**Evidence:** `auth.py:211` returns a generic error for unknown accounts, while `auth.py:314` includes an attempt count for known accounts; inactive/pending/locked checks also return different status/detail before password verification (`auth.py:214`).

**Reproduction:** A known account with the wrong password and an unknown account both returned `401`, with different response messages. Existing-account status paths disclose still more detail by inspection.

**Impact/preconditions:** An anonymous caller can enumerate registered staff addresses/statuses, aiding phishing and targeted recovery attacks. No broad enumeration was performed.

**Remediation:** Use consistent outward authentication failure responses and avoid exposing account status until ownership is established. Keep detailed causes in tenant-scoped internal audit events. Rate-limit without relying on a spoofable header.

### SA-19 — Low — Profile validation permits empty required names

**Evidence:** `schemas/staff.py:70` imposes maximum lengths but no minimums or trimmed non-empty validation for first/last name/contact. Registration strips names only after validating raw input (`auth.py:156`), so whitespace-only input is another static concern.

**Reproduction:** PATCHing the authenticated first name to an empty string returned `200` and stored the empty name.

**Impact/preconditions:** Account/profile forms can create incomplete identities and confusing directory/audit displays.

**Remediation:** Normalize whitespace before validation, require non-empty first/last names and a validated contact format, and enforce the same rules on create and update.

### SA-20 — High — Concurrent incorrect passwords bypass the account lockout counter

**Evidence:** `auth.py:264` verifies against a previously read Staff row and increments its in-memory `login_attempts` value at `auth.py:265`, then commits. There is no atomic increment or lock. Lockout decisions at `auth.py:267` therefore use a stale per-request value under concurrency.

**Reproduction:** Sent six simultaneous wrong-password requests for one clean approved fixture account, each through its own forwarded-IP bucket. All six returned `401`, but the stored account state was `login_attempts=1`, `lockout_count=0`, and neither temporary nor permanent lock. The configured threshold is five wrong passwords.

**Impact/preconditions:** Parallel password guesses evade the per-account brute-force protection even if IP limits are distributed or bypassed. An application-level lockout cannot be relied on for credential-guessing resistance. The probe used six guesses, not an exhaustive attack.

**Remediation:** Atomically increment and evaluate failures with database concurrency control or an account-keyed shared limiter. Ensure success/failure races cannot reset an attacker's attempt budget. Add a real concurrent test against PostgreSQL and verify the lock threshold remains effective.

## Security checks that passed in this scope

- Anonymous staff-directory access returned `401`.
- A teacher's attempted admin account mutation returned `403`.
- A principal's direct lookup of another school's staff returned `404`.
- The system operator's student-list access returned `403`.
- Signing-key algorithm verification is constrained to the configured algorithm and bearer tokens include expirations; passwords use salted bcrypt. These positive properties do not correct SA-02/SA-07/SA-10.
- SQLAlchemy expression predicates were used in the reviewed account/school queries; no raw interpolation-driven SQL injection was found in this scope.
- No actual API keys, committed `.env` files, private key files, or committed database files were found by the current tracked-file scan and targeted historical filename scan. Only example environment files and explicit development placeholders were found. This does not certify all git history, binary documents, or live hosting secrets.
- Render configuration generates JWT and AI shared secrets, limits browser origins through a configured list, and sets several static-site response headers. Production environment values and ingress behavior remain unverified.

## Additional hardening and launch-verification requirements

- Access/refresh tokens and trusted-device IDs live in localStorage (`frontend/src/api/client.js:40`). An XSS compromise would expose them. No exploitable XSS was established here; consider short-lived in-memory access credentials and HttpOnly refresh cookies with appropriate CSRF controls. The static deployment has no Content-Security-Policy configured.
- OTP/reset/session records retain raw active credentials in the database. Consider storing hashes of recoverable/refresh secrets, expiring/deleting stale challenges, and limiting privileged log/database access.
- Profile-photo public visibility is an explicit product/privacy decision, not evidence of cross-tenant enumeration; filenames are random. Do not describe public avatars as private assets.
- Verify negative paths for deactivated accounts and suspended schools through the live ingress and actual PostgreSQL environment after fixes, then repeat concurrent challenge/session tests. Code checks account/school status per request, which is a useful control, but account lock flags are only enforced by login.
- Review database service credentials/least privilege, encrypted backups, retention/deletion policy, restore procedure, and hosting logs in the real deployment. Those controls cannot be certified from this checkout.

SA-01 through SA-05, session/challenge lifecycle, secure non-debug delivery, safe signing configuration, and durable recoverable storage require resolution and regression evidence before any launch-readiness claim.

## Lifecycle coverage appendix

`audit/security_lifecycle_probe.py` additionally exercised all **27 actual routed method/path operations** in the auth/staff/school modules: 9 auth, 12 staff, and 6 school operations. This count is derived from the live FastAPI route table, not a hand-maintained list. `audit/security-lifecycle-results.json` records every operation, invocation status, lifecycle state check, and expected-result comparison. The run produced **118 probe records**, with no uninvoked operations. Every operation received a successful request and an unauthenticated/invalid-input/forbidden request. This is route invocation coverage, not a claim that every possible branch or deployment behavior is proven correct.

| Area | Additional verified flows |
|---|---|
| School/operator lifecycle | Public school+principal registration; pending school visible to operator and absent from public directory; principal denied before approval; rejection requires a reason; reject then approve; approved principal sign-in/settings edit; suspension immediately blocks login and an existing token; public directory excludes suspended school; restore permits login again; duplicate school returns `409`; teacher/principal/operator role boundaries reject disallowed actions. |
| Staff lifecycle | Teacher registration/pending queue/approval; rejected and deactivated users cannot log in; deactivation rejects an existing token; reactivation/unlock; directory/profile/edit; valid password change rejects wrong/current/weak inputs and accepts the new password; valid avatar creation/removal clears its field and changes the old asset URL from `200` to `404`; removing an absent avatar is idempotent; principal self-reject/self-deactivate/self-delete return `400`; cross-school lookup/deletion return `404`; teacher administrative actions return `403`; staff deletion returns `204`, makes profile lookup `404`, and invalidates the deleted identity's token with `401`. |
| Auth/recovery lifecycle | Complete login/OTP, identity, refresh/logout, reset-code exchange/password reset, and used-reset-token rejection. Known logout/token revocation and public debug-code issues remain separately documented above. |

The only unexpected status in this lifecycle run was the duplicate employee-number signup `500`, now included in SA-13. All other declared status expectations and presence/exclusion/deletion checks passed. Fixtures were created in a new uniquely named audit database; the shared browser server and existing application database were not accessed. Runtime OTP/token values are excluded from the saved evidence.
