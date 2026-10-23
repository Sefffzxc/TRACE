# Python dependency security review

Checked 20 October 2026 (Asia/Manila). **Known vulnerable dependencies remain; this review does not support launch approval.** No packages or application files were installed, upgraded, or modified.

## Inventory and reproducible evidence

`audit/check_python_dependencies.py` uses only the standard library. It reads `pip list` from the existing backend and ML test interpreters, reads both requirement files, and queries PyPI's per-version public vulnerability metadata. Full sanitized results, package versions, dependency metadata, advisory aliases, and fix versions are in `audit/python-dependencies.json`.

```powershell
backend/.venv/Scripts/python.exe audit/check_python_dependencies.py
backend/.venv/Scripts/python.exe audit/probe_dependency_behaviors.py
```

The first command needs internet access; the sandbox socket restriction required approved escalation during this audit. It does not install anything. The second command uses isolated small in-memory examples and a temporary local static directory, without an application database or external network access. Its results are `audit/dependency-behaviors.json`.

Inventory covered **54 distinct package/version pairs**, with **zero metadata-query errors**:

| Inventory | Packages | Scope |
|---|---:|---|
| `backend/.venv` installed | 41 | Actual interpreter used for API/auth probes |
| `ml/.venv-ml` installed | 34 | Actual interpreter used for AI probes; includes development/training packages beyond inference requirements |
| Backend requirements | 15 direct pins | All match the backend installed snapshot |
| AI-service requirements | 7 direct pins | All match the ML probe interpreter |

The deployed Render environments were unavailable, so their complete resolved versions are **not** verified. Both requirement files pin direct packages but leave transitive packages unresolved by a committed lockfile. Current PyPI records identified affected versions of `starlette`, `python-multipart`, `python-jose`, `python-dotenv`, `ecdsa`, and the installation tool `pip`. Advisory aliases are duplicate records, not additional independent bugs. Maintainer review also identified multipart advisory GHSA-vffw-93wf-4j4q, absent from that PyPI version's returned list; automated metadata alone is therefore insufficient.

## DEP-01 — High — Unauthenticated static-file requests reach vulnerable Range processing

Both interpreters contain `starlette==0.41.3`. The maintainer identifies GHSA-7f5h-v6xp-fcq8 / CVE-2025-62727 in versions `>=0.39.0, <=0.49.0`; its first fixed version is `0.49.1`. Crafted Range headers can consume excessive CPU on `StaticFiles` and `FileResponse`. [Maintainer advisory](https://github.com/Kludex/starlette/security/advisories/GHSA-7f5h-v6xp-fcq8)

**Application exposure:** `backend/app/main.py:135` mounts publicly readable `/uploads` using `StaticFiles`. Existing uploaded files supply an unauthenticated target. No AI inference route serving files was found, so the same installed framework advisory is not independently established as reachable in the AI service.

**Bounded verification:** Called the installed parser with only 1–8 KB malformed headers. Recorded processing times were approximately 0.003, 0.024, 0.079, and 0.375 seconds. This confirms the inefficient parser path without running a sustained denial-of-service attack. Live proxy/header-size constraints were not tested.

**Remediation:** Upgrade FastAPI and Starlette together to compatible versions covering all relevant advisories; verify static downloads and reject pathological Range requests at ingress as defense in depth. Do not treat ingress filtering as the dependency fix.

## DEP-02 — High — Upload endpoints use known vulnerable multipart/form parsers

Backend requirements and installed versions both specify `python-multipart==0.0.20`; the installed Starlette is `0.41.3`. Relevant maintainer advisories:

| Advisory | Severity | First fixed version | Applicability to this app |
|---|---|---|---|
| [GHSA-pp6c-gr5w-3c5g / CVE-2026-42561](https://github.com/Kludex/python-multipart/security/advisories/GHSA-pp6c-gr5w-3c5g) | High | multipart `0.0.27` | Unbounded part-header count/size causes parser CPU work. Backend upload routes invoke this parser. |
| [GHSA-mj87-hwqh-73pj / CVE-2026-40347](https://github.com/Kludex/python-multipart/security/advisories/GHSA-mj87-hwqh-73pj) | Moderate | multipart `0.0.26` | Large multipart preamble/epilogue consumes CPU. Same upload parsing path. |
| [GHSA-5rvq-cxj2-64vf / CVE-2026-53539](https://github.com/Kludex/python-multipart/security/advisories/GHSA-5rvq-cxj2-64vf) | High | multipart `0.0.30` | Quadratic URL-encoded body parsing. File/Form routes parse request forms before final payload validation. |
| [GHSA-82w8-qh3p-5jfq / CVE-2026-54283](https://github.com/Kludex/starlette/security/advisories/GHSA-82w8-qh3p-5jfq) | High | Starlette `1.3.1` | URL-encoded form field/size limits ignored; CPU/memory exhaustion. Backend Form/File paths are relevant. |
| [GHSA-2c2j-9gv5-cj73 / CVE-2025-54121](https://github.com/Kludex/starlette/security/advisories/GHSA-2c2j-9gv5-cj73) | Moderate | Starlette `0.47.2` | Upload spool rollover can block the event loop. Upload parsing occurs before the handler's own file-size rejection. |

**Application exposure:** Photo upload declares `File(...)` at `backend/app/api/routers/staff.py:114`; class import declares File/Form fields at `backend/app/api/routers/class_import.py:81`. FastAPI's installed request handler reads form bodies before resolving authentication dependencies, so an unauthenticated malformed form may still exercise the parser. Successful file storage still requires authentication. No form/file routes were found in the AI service.

**Bounded verification:** The installed multipart parser accepted a 32 KB part-header value. An installed Starlette request parsed five URL-encoded fields despite `max_fields=1`. These demonstrate missing limits; memory exhaustion and prolonged request flooding were not attempted.

**Remediation:** Upgrade to a compatible framework/parser set covering all relevant advisory fixes, and enforce early body-size/field/header limits with timeouts at the trusted ingress. The current application size checks run after parsing and cannot protect the parser itself.

Additional multipart matches require narrower conditions and must **not** be described as proven application compromises:

| Advisory | Severity | First fixed version | Applicability assessment |
|---|---|---|---|
| [GHSA-wp53-j4wj-2cfg / CVE-2026-24486](https://github.com/Kludex/python-multipart/security/advisories/GHSA-wp53-j4wj-2cfg) | High | `0.0.22` | Arbitrary file write needs the library's non-default `UPLOAD_KEEP_FILENAME=True` and configured upload directory. This app uses Starlette UploadFile and generated filenames; required configuration was not found. The app's own `UPLOAD_DIR` setting is not evidence of configuring the multipart library's File writer. |
| [GHSA-v9pg-7xvm-68hf / CVE-2026-53540](https://github.com/Kludex/python-multipart/security/advisories/GHSA-v9pg-7xvm-68hf) | Low | `0.0.31` | Negative Content-Length bug requires direct `parse_form()` use. The maintainer explicitly excludes Starlette/FastAPI's direct streaming parser usage. No app call to `parse_form()` was found. |
| [GHSA-6jv3-5f52-599m / CVE-2026-53538](https://github.com/Kludex/python-multipart/security/advisories/GHSA-6jv3-5f52-599m) | Low | `0.0.30` | Semicolon parsing differs from intermediaries; an actual authorization bypass requires a relevant upstream/body-inspector rule. None was available to verify. |
| [GHSA-vffw-93wf-4j4q](https://github.com/Kludex/python-multipart/security/advisories/GHSA-vffw-93wf-4j4q) | Low | `0.0.30` | Extended multipart field/filename interpretation can bypass upstream inspection. Locally confirmed `name=comment; name*=utf-8''role` parses as `role`; no application privilege bypass established. Profile uploads generate their own filenames. |

The path-write advisory text has an inconsistent mitigation sentence mentioning `0.0.21`; its structured patched-version field and PyPI metadata identify `0.0.22`. Use verified current fixed releases rather than that stray sentence.

## DEP-03 — High on Windows — StaticFiles resolves attacker-controlled UNC paths before containment checking

GHSA-wqp7-x3pw-xc5r / CVE-2026-48818 affects Starlette `<1.1.0`. On Windows, UNC resolution can trigger outbound SMB authentication and expose NTLM credentials. The maintainer states POSIX systems are unaffected. [Maintainer advisory](https://github.com/Kludex/starlette/security/advisories/GHSA-wqp7-x3pw-xc5r)

**Application exposure:** This checkout's backend runs on Windows and exposes `StaticFiles` with default symlink handling (`main.py:135`). The Render Python deployment is Linux, so this advisory must not be reported as a demonstrated Render credential leak. Risk on Windows also depends on reachability, the service identity, outbound SMB access, and ingress normalization.

**Safe verification:** Sent an encoded-backslash static URL to a small local ASGI fixture using the installed Starlette. A patched-in resolver intercepted exactly one UNC resolution **before any path/network I/O**. No outbound SMB connection or credential leakage was attempted. This verifies the dangerous resolution path, not an actual stolen credential.

**Remediation:** Upgrade the compatible framework, avoid Starlette serving static files on Windows until patched, and block unnecessary outbound SMB. Keep development servers bound to loopback unless remote access is deliberately configured.

## Other Starlette matches: no authorization exploit established

| Advisory | Severity / first fix | Source/app review |
|---|---|---|
| [GHSA-86qp-5c8j-p5mr / CVE-2026-48710](https://github.com/Kludex/starlette/security/advisories/GHSA-86qp-5c8j-p5mr) | Moderate / `1.0.1` | Malformed Host can poison reconstructed `request.url.path`. App authorization uses dependencies/tenant scope, not that reconstructed path. Its use was found in error logging/responses, so misleading logs are possible; no auth bypass established. |
| [GHSA-x746-7m8f-x49c / CVE-2026-48817](https://github.com/Kludex/starlette/security/advisories/GHSA-x746-7m8f-x49c) | Moderate / `1.1.0` | Requires an `HTTPEndpoint` subclass registered without restricted methods and internal request-shaped methods. No such app subclass was found. |
| [GHSA-jp82-jpqv-5vv3 / CVE-2026-54282](https://github.com/Kludex/starlette/security/advisories/GHSA-jp82-jpqv-5vv3) | Low / `1.3.0` | Malformed request target can poison reconstructed hostname; requires a relevant pre-routing/exception consumer and server/proxy acceptance. No security-sensitive hostname consumer was found. |

## DEP-04 — Medium maintenance risk — Obsolete JOSE library and transitive ECDSA advisory

`backend/requirements.txt:7` and the actual backend both use `python-jose==3.3.0`; its dependency `ecdsa==0.19.2` is installed.

| Match | Maintainer evidence | Applicability |
|---|---|---|
| CVE-2024-33663 / GHSA-6c5p-j8vq-pqhj, OpenSSH asymmetric/HMAC confusion | [Maintainer issue](https://github.com/mpdavis/python-jose/issues/346), [3.4.0 fix release](https://github.com/mpdavis/python-jose/releases/tag/3.4.0) | Requires asymmetric key material accepted for HMAC or insufficient algorithm restriction. App uses an HMAC secret and explicitly passes `[settings.ALGORITHM]` (`backend/app/core/security.py:78`). No exploit established for that configuration. |
| CVE-2024-33664 / GHSA-cjwg-qfpm-7377; PyPI additionally lists CVE-2024-29370 | [Maintainer JWE bomb issue](https://github.com/mpdavis/python-jose/issues/344), [3.4.0 JWE limit fix](https://github.com/mpdavis/python-jose/releases/tag/3.4.0) | Compressed encrypted JWE processing; app calls signed JWT encode/decode, and no `jwe.decrypt` call was found. Treat the extra PyPI CVE as an inventory match, not a separately proven attack path. |
| CVE-2026-85394 / GHSA-3qf3-8w2g-rqmx, incomplete DER-key confusion fix | [Maintainer repository report](https://github.com/mpdavis/python-jose/issues/414) | Still reported through 3.5.0 with no fixed version in the returned metadata. Again requires inappropriate asymmetric/HMAC key acceptance; app's secret/allowlist avoids the reported prerequisite. Merely upgrading to 3.4.0 is not a claim of resolving every current JOSE advisory. |
| CVE-2024-23342 / GHSA-wj6h-64fc-37mp in ECDSA | [Maintainer advisory](https://github.com/tlsfuzzer/python-ecdsa/security/advisories/GHSA-wj6h-64fc-37mp) | Timing side channel in ECDSA signing/key operations; maintainer has no planned fix and excludes verification. App uses HMAC signing, so the vulnerable ECDSA private-key operations were not found. |

**Remediation:** Upgrade or replace obsolete JWT dependencies after compatibility/security review; retain explicit algorithms and correct key types; avoid unused vulnerable cryptographic backends where possible. Test issuance, expiry, rejection of wrong algorithms, refresh, and logout with the chosen library. This dependency finding is separate from the **confirmed critical account takeover** and session bugs in `security-auth.md`; do not inflate severity by treating dormant crypto CVEs as the cause of those bugs.

## DEP-05 — Low in current app — python-dotenv local symlink overwrite match

Both the backend pin and installed version are `python-dotenv==1.0.1`. GHSA-mf9w-mj56-hr94 / CVE-2026-28684 affects its environment-file rewriting and is fixed in `1.2.2`. Exploitation requires local control over a symlink plus a `set_key()`/`unset_key()` write path and a cross-filesystem rename fallback. [Maintainer advisory](https://github.com/theskumar/python-dotenv/security/advisories/GHSA-mf9w-mj56-hr94)

No such rewriting call was found in app code; settings only load environment configuration. No remote application exploit is established. Upgrade the dependency and keep deployment configuration directories unwritable to untrusted users.

## DEP-06 — Medium build-tool risk — Both environments use pip 24.0

PyPI metadata lists six unique vulnerability families. These affect installation/download workflows, not an API request handler. No malicious package or index was supplied. Actual Render build-image pip version is unknown. The maintainer changelog documents the fixes and links the patches. [pip maintainer changelog](https://pip.pypa.io/en/stable/news/)

| CVE / GHSA | First fix from metadata | Relevant condition / primary patch |
|---|---|---|
| CVE-2025-8869 / GHSA-4xh5-x5gv-qwph | `25.3` | Legacy tar extraction without PEP 706; the app's Python 3.12 has that mechanism, so that fallback condition is not established here. [Maintainer patch](https://github.com/pypa/pip/pull/13550) |
| CVE-2026-1703 / GHSA-6vgw-5pg2-w6jp | `26.0` | Malicious wheel path-prefix traversal during installation. [Maintainer patch](https://github.com/pypa/pip/pull/13777) |
| CVE-2026-3219 / GHSA-58qw-9mgm-455v | `26.1` | Ambiguous combined archive interpretation during install. [Maintainer patch](https://github.com/pypa/pip/pull/13870) |
| CVE-2026-6357 / GHSA-jp4c-xjxw-mgf9 | `26.1` | Importing newly installed code in pip's deferred self-update check. [Maintainer patch](https://github.com/pypa/pip/pull/13923) |
| CVE-2026-8643 / GHSA-wf93-45jw-7689 | `26.1.2` | Malicious entry-point names escaping the scripts directory. [Maintainer patch](https://github.com/pypa/pip/pull/14000) |
| CVE-2026-13346 / GHSA-qwm4-qh6w-59xr | `26.2` / `26.2.0` | Doubly encoded package URLs from a malicious index; not ordinary HTTP app input. [Maintainer patch](https://github.com/pypa/pip/pull/14110) |

Upgrade the build toolchain deliberately and use trusted package indexes with locked hashes. Do not run blanket upgrades against the working app as a substitute for reviewing compatible changes.

## DEP-07 — Medium — Current framework pin prevents compatible security-only transitive updates

The installed/PyPI metadata for `fastapi==0.115.6` specifies `starlette>=0.40.0,<0.42.0`; both backend and AI-service requirements pin that FastAPI version. All listed Starlette fixes are outside this permitted range. Installing only a new Starlette while retaining this FastAPI pin would create an unsupported/conflicting dependency set. Direct-pins-only files also let unrelated transitive versions vary between deployments.

Upgrade to a compatible FastAPI/Starlette/parser set, produce a full lock/constraints artifact with hashes, and scan the resolved deployment environment in CI. Keep model-facing `numpy`, `pandas`, `scikit-learn`, and `joblib` versions consistent with the saved model and verify predictions after any ML dependency change. The current seven direct AI pins match the successfully tested model environment; no PyPI vulnerability entry was returned for those exact direct ML package versions at scan time. That is a database result, not proof that all possible vulnerabilities or model supply-chain risks are absent.

This audit excludes OS/Python interpreter CVEs, container images, unavailable live deployment dependency inventories, package provenance/compromise analysis, and unpublished vulnerabilities. Repeat the scan after selecting upgrades and verify in staging; no dependency fixes were applied during this audit.
