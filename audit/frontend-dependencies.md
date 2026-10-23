# Frontend dependency review

Checked 20 October 2026 (Asia/Manila). The committed lockfile resolves React Router and React Router DOM to **6.30.6**, Vite to **6.4.4**, Recharts to **2.15.4**, and esbuild to **0.25.12**. No dependencies were upgraded. Browser tooling was installed separately under the ignored `audit/runtime/` directory.

`npm.cmd audit --json` completed against the public npm advisory registry. Its exit code was 1 because it reported affected packages; the scan itself succeeded. Evidence: [npm-audit.json](npm-audit.json). npm reports **two moderate affected package entries**, `react-router` and the dependent `react-router-dom`, representing the **same two underlying React Router advisories**, rather than four independent vulnerabilities. No high or critical npm advisory entry was returned in this snapshot. This result does not cover unpublished vulnerabilities or prove runtime safety.

## ND-01 - Medium maintenance risk - Installed React Router has an open-redirect advisory

The maintainer identifies GHSA-wrjc-x8rr-h8h6 / CVE-2026-53669 for versions `>=6.0.0,<7.18.0`, with a fix in 7.18.0. Attacker-controlled navigation paths can unexpectedly resolve to external destinations. [Maintainer advisory](https://github.com/remix-run/react-router/security/advisories/GHSA-wrjc-x8rr-h8h6)

**App assessment:** `frontend/src/App.jsx` uses declarative `BrowserRouter`; inspected links and navigation predominantly use fixed paths and numeric IDs. Header search encodes query parameters. No exploit allowing an attacker to pass an arbitrary navigation target was established. This is a confirmed affected-version match, not a demonstrated phishing or token-theft exploit in this application.

**Impact and solution:** A future or missed untrusted navigation path can expose users to redirection. Migrate to a compatible fixed Router version, validate allowed in-app navigation targets, and rerun role guards, deep links, browser refresh, auth redirects, and history navigation. npm's suggested fix crosses a major version; do not apply `audit fix --force` without reviewing migration and regression behavior.

## Recorded advisory without an applicable app path

GHSA-337j-9hxr-rhxg / CVE-2026-53666 affects some error deserialization during manual server rendering/hydration and is fixed in 7.18.0. The maintainer explicitly excludes declarative mode. This is a client-rendered Vite app using `BrowserRouter` and `createRoot`; no SSR/hydration path was found. Record it in the dependency inventory, but do **not** report an exploitable SSR injection vulnerability or count it as a second application bug. [Maintainer advisory](https://github.com/remix-run/react-router/security/advisories/GHSA-337j-9hxr-rhxg)

The source and lockfile remain unchanged. The production build passed separately; building successfully does not resolve a security advisory.
