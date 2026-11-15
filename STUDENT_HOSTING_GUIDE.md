**TRACE: move API and AI to your own running server**

Updated **9 November 2026**. Your domain is **traceedu.app**.
The frontend will use **https://www.traceedu.app**, the API and AI will run on
one Azure Ubuntu VM, and your existing database will stay on Render.

| Progress | Status |
|---|---|
| GitHub Student Developer Pack | Approved |
| `traceedu.app` registration | Done |
| Cloudflare DNS | Active |
| Resend `auth.traceedu.app` | Verified for sending |
| Resend sending API key | Create/save it if you have not finished step 4 |
| Azure for Students subscription | Active; $100 starting credit |
| Azure VM `trace-server` | Created in East Asia; public IPv4 `23.100.91.187` |
| SSH, `trace` account and read-only GitHub deploy key | Working |
| Repository and Python environments | `Sefffzxc/TRACE` (formerly `lbca`); installed `main` commit `9c0d625`; API/AI dependencies installed |
| `api` and `ai` DNS | A records to `23.100.91.187`, DNS only |
| Existing database | Render Free `trace-db`; actual database name `trace_ht2p` |
| Azure backup | pg_dump 18.6 dump and archive-readability check succeeded; file mode 600, 184K |
| Private Windows backup | SCP completed; owner ACL applied without errors; SHA-256 matches Azure |
| New Render credential | `trace_azure` created; old credential still active |
| VM directories | `/var/lib/trace/uploads`: trace-owned 750; `/etc/trace`: root-owned 700 |
| VM environment files | Both saved, root-owned 600; all 8 private preflight checks passed |
| API and AI systemd services | Units root-owned 644, verified; both active and enabled at boot |
| Local API health | Status OK; both intended frontend origins; no schema mismatch reported |
| Local AI health | Status OK; `model_loaded: true`, version `v4-2026-10-final-grade`, error null |
| Caddy and domain HTTPS | Configuration applied; both HTTPS health checks passed from the VM with normal TLS verification |
| Public HTTPS health | API status OK with both frontend origins; AI status OK, model loaded, expected version and no error |
| Cloudflare Pages build and deployment | Success at `https://trace-285.pages.dev`; app loads on `www`; embedded API URL not independently inspected |
| Frontend custom domains | `www.traceedu.app`: Active, SSL enabled; apex `traceedu.app` not confirmed |
| OTP verification | Confirmed working using the code copied from API logs; inbox delivery not confirmed |
| Existing data and refresh | Dashboard/class/student records load and still load after refresh; no grading changes made |
| Outside-VM check, AI authentication, reboot, remaining application/inference/email tests, credential revocation and full restore | Still to do |

**Your next action is step 7 below: open AI Risk Model in the school staff's
grade-record sidebar, check for service/model warnings, and view the model card and existing prediction details.**
Records and refresh checks passed. A fresh Azure model prediction is still unverified.
Inbox delivery, the apex domain, outside-VM health check, AI request
without a token returning `401`, actual reboot and other application tests remain
pending. Credential rotation is still incomplete.

| Component | Final address | Hosting |
|---|---|---|
| Frontend, shared with users | `https://www.traceedu.app` | Cloudflare Pages |
| Frontend, alternate address | `https://traceedu.app` | Same Pages project |
| Backend API | `https://api.traceedu.app` | Azure Ubuntu VM |
| AI service | `https://ai.traceedu.app` | Same VM |
| OTP sender | `TRACE <noreply@auth.traceedu.app>` | Resend |
| Database | Existing Render Postgres hostname | Same Render database |

The final setup has **API + AI on Azure, frontend on Pages, and only PostgreSQL
on Render**. The VM has no visitor-based sleep; plan for maintenance and outages.
Keeping old Render apps running during the transition is optional. Retire them
after the new setup passes its checks, keeping the existing database.

**1. Domain registration — completed.**

Your registered name is **traceedu.app**. Keep its renewal date and renewal price
in Name.com. You own one domain; `www`, `api`, `ai` and `auth` are subdomains and
need no separate registrations. [Student Pack domain offer](https://education.github.com/pack)

**Done when:** `traceedu.app` appears in your Name.com account. Already done.

**2. Cloudflare DNS — completed.**

Cloudflare is active for `traceedu.app`. Add future records in
**Cloudflare > traceedu.app > DNS > Records**. Name.com remains your registrar;
Cloudflare manages the DNS. Keep the verified email records when adding web/API
records. [Cloudflare DNS setup](https://developers.cloudflare.com/dns/zone-setups/full-setup/setup/)

**Done when:** Cloudflare shows the domain as **Active**. Already done.

**3. Resend sending domain — completed.**

`auth.traceedu.app` is verified. Preserve the records shown in your Resend setup:

| Type | Name in Cloudflare | Content/target |
|---|---|---|
| TXT | `resend._domainkey.auth` | Full DKIM value from your Resend dashboard |
| CNAME | `rsend.auth` | Exact target from your Resend dashboard; DNS only |
| CNAME | `send.auth` | Exact target from your Resend dashboard; DNS only |

These reflect your current verified setup. **Copy the types, names and values
Resend actually supplies** if you need to recreate them. Do not replace the
CNAMEs with TXT or MX records from a different tutorial. Cloudflare's Name field
already has `traceedu.app`, so `send.auth.traceedu.app` becomes `send.auth`.
[Resend's Cloudflare instructions](https://resend.com/docs/guides/dns/cloudflare)

The TRACE sender will be **`TRACE <noreply@auth.traceedu.app>`**. You do not need
a separate inbox to send from this verified subdomain.
[Verified sending domains](https://resend.com/docs/dashboard/domains/introduction)

**Done when:** Resend shows `auth.traceedu.app` as verified. Already done.

**4. Save the Resend sending key.**

**Where:** [Resend > API Keys](https://resend.com/api-keys).

If you already created and saved the key, keep it for step 5. Otherwise:

1. Click **Create API Key** and name it `TRACE production`.
2. Choose **Sending access**, restricted to `auth.traceedu.app` if available.
3. Create it and save the full key beginning with `re_` in your password manager.

The key goes into the new VM's API settings. Keep it out of chat, screenshots,
GitHub and frontend settings. You cannot view its value again after creation.
[API key instructions](https://resend.com/docs/dashboard/api-keys/introduction)

**Done when:** you have the full sending key saved privately.

**5. Configure and start the Azure API and AI with the new database credential.**

Azure for Students is active and the VM is created. Keep this existing server:

| Setting | Confirmed value |
|---|---|
| VM / administrator | `trace-server` / `traceadmin` |
| Resource group / region | `trace-hosting` / East Asia |
| Image / size | Ubuntu 24.04 LTS x64 / `Standard_B2als_v2`, 2 vCPU, 4 GiB RAM |
| OS disk | 30 GiB, `StandardSSD_LRS` |
| Purchase option / shutdown | On-demand; automatic shutdown off |
| Public IPv4 | `23.100.91.187` |

The portal quoted **$38.40/month ($0.0526/hour) for compute alone**. A full $100
balance covers about **2.6 months of compute**, with less time after disk, public
IP and chargeable traffic. Check the actual remaining balance. Credit is usable
within 12 months; it does not promise twelve months of this server. Arrange
continued service before the balance is exhausted.
[Azure student spending terms](https://azure.microsoft.com/en-us/pricing/offers/ms-azr-0170p/)

**Backup completed on Azure:** pg_dump exited **0**, and PostgreSQL 18
`pg_restore --exit-on-error --file=/dev/null` also exited **0**. The private
archive is **184K**, mode **600**, at:
`/home/traceadmin/trace-backups/trace-before-azure-20261109T082306Z.dump`.
The protected Windows copy is at
`C:\Users\macos\TRACE-private-backups\trace-before-azure-20261109T082306Z.dump`.
SCP reached **100%** and its owner ACL completed without errors. Both copies have
SHA-256 `38f8dcde4c4f0f2b8383bc1bb7af17ab03ed19a4756e03023153ec37550ce011`.
Archive readability and matching files are confirmed; **a full restore test is pending**.
[Render manual backups](https://render.com/docs/postgresql-backups#exporting-with-pg_dump)

**Where now:** **step 8** of [STUDENT_SERVER_SETUP.md](STUDENT_SERVER_SETUP.md).
Both service units passed `systemd-analyze verify`, were enabled successfully,
and `systemctl is-active` reported **active** for both. Local health passed;
this has not verified application records, login, email or an inference request.
`SECRET_KEY` signs login tokens and should retain the old API's production value.
`AI_SERVICE_TOKEN` authenticates API-to-AI requests and uses the same value in
both environment files.

Caddy **2.11.9** is installed from its
[official Ubuntu repository](https://caddyserver.com/docs/install#debian-ubuntu-raspbian),
and its domain configuration is applied. From the VM, both HTTPS health checks
passed with normal certificate verification: API status OK with both frontend
origins; AI status OK, `model_loaded: true`, version `v4-2026-10-final-grade`,
error null. An independent check from outside the VM is still pending.

Before the final cutover, in **Ubuntu SSH**, check that a prediction request
without a token is rejected:

```bash
curl -i -X POST https://ai.traceedu.app/predict -H 'Content-Type: application/json' -d '{}'
```

Expect **`401 Unauthorized`**; this result has not been confirmed yet. This checks
authentication; an actual prediction remains to be tested. Keep secret values private.

**Optional transition:** if you choose to keep the old Render API running,
update its `DATABASE_URL` to the new **Internal Database URL**, redeploy and test
it. Otherwise, retire the old API after the Azure deployment is validated.
Revoke the old credential once all clients using it are updated or retired;
credential rotation remains incomplete until then.

The repository is now `Sefffzxc/TRACE` (formerly `lbca`); the installed checkout
is branch **`main`**, commit `9c0d625`. Existing local folder names stay unchanged.
Use **main** for Pages too. For later local code changes, commit and push the
intended changes before updating the VM; uncommitted changes stay on your laptop.

1. Backups are verified; **step 1** of the appendix records the backup and
   credential details. Preserve available profile photos.
   Keep `trace-db` / `trace_ht2p` and its records; do not recreate or reseed it.
2. Continue with **step 8** of the appendix for access checks, then **step 9** for Pages.
   Cloning, dependencies, local service startup and Caddy HTTPS setup are done.
3. Use Render's **External Database URL** for the VM. The Internal Database URL
   only works inside Render. [External connections](https://render.com/docs/postgresql-creating-connecting#external-connections)
4. In the new VM's **`/etc/trace/api.env`**, use the following email/frontend
   values alongside the database, secret, uploads and AI settings in the appendix:

```dotenv
EMAIL_ENABLED=true
RESEND_API_KEY=PASTE_YOUR_PRIVATE_RESEND_KEY_HERE
EMAIL_FROM="TRACE <noreply@auth.traceedu.app>"
EMAIL_TIMEOUT_SECONDS=10
DEBUG_OTP=false
LOG_OTP_CODES=true
ALLOWED_ORIGINS=https://www.traceedu.app,https://traceedu.app
```

`LOG_OTP_CODES=true` is temporary for your defense. **Teachers still receive the
email**; the code is also copied to API logs. `DEBUG_OTP=false` keeps the code out
of API responses. Anyone who can read the API logs can read the temporary copy.

5. Independently check from your Windows browser:
   `https://api.traceedu.app/health` and `https://ai.traceedu.app/health`.
   API health must pass; AI health must report **`model_loaded: true`**.

The `api` and `ai` **A records** already point to `23.100.91.187`, DNS only.
Keep the verified Resend CNAMEs. Render's network rules include the VM's `/32`
and temporary broader **Any** rules; review those broader rules during cutover.

**Done when:** the API and AI respond at their new HTTPS addresses using the same
database, and the Azure API connects with the new `trace_azure` credential.

**6. Deploy the frontend on Cloudflare Pages.**

**Where:** Cloudflare Pages and **step 9** in
[STUDENT_SERVER_SETUP.md](STUDENT_SERVER_SETUP.md).

**Current status:** Pages reported a successful deployment at
**https://trace-285.pages.dev**. The app's existing records load and survive a page
refresh; the embedded API target has not been independently inspected.
**`www.traceedu.app` is Active with SSL enabled**; the apex is not confirmed yet.

**Pages entry:** from **Workers & Pages > Create application**, the footer link
**Continue to Pages** opens the Pages flow, where you import your Git repository.
The separate `trace` screen with **Deploy command: `npx wrangler deploy`** and
**Delete Worker** is a Worker. Leave that Worker alone for now.
[Pages Git setup](https://developers.cloudflare.com/pages/get-started/git-integration/)

Keep these intended Pages settings:

| Setting | Value |
|---|---|
| GitHub repository | `Sefffzxc/TRACE` (renamed from `lbca`) |
| Production branch | `main` |
| Root directory | `frontend` |
| Build command | `npm ci && npm run build` |
| Build output directory | `dist` |
| Production build environment variable | `VITE_API_URL=https://api.traceedu.app` |

The Pages form includes **Build output directory**. Add `VITE_API_URL` to the
**production build** environment before deploying; the Workers runtime variables
screen does not configure this Pages build. Use the API URL exactly, without
`/api` at the end.

`www.traceedu.app` is already active. For the remaining apex, open **Custom domains >
Add custom domain** and add **`traceedu.app`** through the Pages DNS workflow.
Replace only its conflicting old `@` parking A record pointing to `91.195.240.94`,
as the appendix explains. Keep the working `www`, `api`/`ai` records and Resend
TXT/CNAME records. Wait for **Active** and a working HTTPS certificate on the apex.
You can test login at **https://www.traceedu.app** now using step 7.
Both frontend origins are allowed by the API configuration in step 5.
[Pages custom domains](https://developers.cloudflare.com/pages/configuration/custom-domains/)

**Done when:** `https://www.traceedu.app` loads and sends API requests to
`https://api.traceedu.app`.

**7. Test the complete system and finish the cutover.**

**Where:** the new website, Resend, Ubuntu SSH and **step 10** in the appendix.

**Confirmed:** existing dashboard/class/student records load and remain available
after refreshing the page. These checks did not change grades.

**Next AI check:** using the school staff grade-record menu, open **AI Risk Model**.
Confirm there is no service/model warning and **About the model** shows version
`v4-2026-10-final-grade`, then view existing prediction details. Leave
**Re-score records** untouched during this check; it updates predictions in bulk.
Historical prediction rows alone do not verify a fresh prediction on Azure;
that inference check remains pending.

**Test login:** open `https://www.traceedu.app` in a private/incognito window and
use an existing account's valid credentials. If you cannot open that account's
email inbox, use the temporary OTP log copy below. **OTP verification with the
log copy is confirmed working**; actual inbox delivery is still a separate test.

**Ubuntu SSH terminal:** start watching logs, then request the login OTP in the browser:

```bash
sudo journalctl -u trace-api -f -o cat
```

Find the newest **`[OTP] login code for <email>: <code>`** line for your account
and enter that code in the browser. Keep it private. To check recent history:

```bash
sudo journalctl -u trace-api --since "10 minutes ago" --no-pager -o cat | grep '\[OTP\]'
```

The log line appears only after the email provider accepts the send request and
the database commit succeeds. A failed request may produce no OTP line; check
the API errors privately. Keep `EMAIL_ENABLED=true`, `LOG_OTP_CODES=true` and
`DEBUG_OTP=false` during this temporary defense setup. Press **Ctrl+C** to stop
watching; the API keeps running. Email remains enabled when you read the log copy.

**Optional Azure Portal view:** open **Virtual machines > trace-server > Operations >
Run command > RunShellScript** and paste this finite command:

```bash
journalctl -u trace-api -n 40 --no-pager -o cat
```

Request the OTP in the browser, then click **Run**. After about 20 seconds or
longer, read **Output**; run it again to refresh the snapshot. Keep `-f` for SSH
only, and keep OTP output private.
[Azure Run Command](https://learn.microsoft.com/en-us/azure/virtual-machines/linux/run-command)

**Test email separately:** use an approved test account whose inbox you can open
to receive a new login OTP and test **Forgot password**. Check **Resend > Emails**
for delivery status if needed. No email-delivery test is confirmed yet.
If using a real teacher's account during the defense, tell them in
advance that they will receive an email. Resend's free plan currently limits
sending to **100 emails/day**; check your monthly quota too.
[Resend pricing and limits](https://resend.com/pricing)

Still verify a fresh model prediction, profile photos and automatic recovery
after a VM reboot using the appendix checklist.
Then retire the old Render **frontend/API/AI apps**, keeping the **database**.
Revoke the old database credential after its remaining clients are updated or retired.

**Done when:** the new frontend, API, AI and email work together, records are
present, and API/AI no longer depend on Render app hosting.

**8. After your defense, turn off OTP logging.**

**Where:** the VM's **Ubuntu SSH terminal**.

Run `sudo nano /etc/trace/api.env`, set `LOG_OTP_CODES=false`, save and run:

```bash
sudo systemctl restart trace-api
```

Keep `EMAIL_ENABLED=true` and `DEBUG_OTP=false`. Teachers continue receiving
emails. Confirm a new OTP arrives without its code appearing in new API logs;
old log entries remain until their normal retention period expires.

**Done when:** new codes arrive by email and are absent from new API logs.

- [x] `traceedu.app` registered; Cloudflare active; Resend subdomain verified.
- [ ] Resend sending key saved privately.
- [x] Azure for Students active; `trace-server` created in East Asia.
- [x] SSH works; deploy key, main checkout and both Python environments are ready.
- [x] API/AI DNS records added; pg_dump 18.6 installed.
- [x] Database dumped on Azure; archive-readability check completed, both exit 0.
- [x] Protected Windows backup saved; source and destination SHA-256 match.
- [x] New default credential `trace_azure` created; old credential remains active.
- [x] Upload/secret directories and root-only environment files created.
- [x] User reported both `api.env` and `ai.env` saved.
- [x] All 8 private configuration preflight checks passed without exposing values.
- [ ] Azure API uses the new credential; old credential revoked after other clients are updated or retired.
- [ ] Full restore into a separate empty database tested.
- [x] API and AI active, enabled at boot, and healthy on localhost.
- [x] Caddy 2.11.9 installed, domain configuration applied, both HTTPS health checks passed from the VM.
- [x] Pages build and deployment reported success at `trace-285.pages.dev`.
- [x] `www.traceedu.app` Active with SSL enabled.
- [x] OTP verification works using the code copied from API logs.
- [x] Existing dashboard/class/student records load and still load after refresh.
- [ ] Apex `traceedu.app` active; embedded API target inspected.
- [ ] Independent outside-VM health checks and unauthenticated AI request returning 401 confirmed.
- [ ] Actual reboot, inbox delivery and fresh model inference tests completed.
- [ ] `www.traceedu.app` frontend and email tests pass; old Render apps retired.
- [ ] After the defense: `LOG_OTP_CODES=false` and API restarted.

**Free Render Postgres expires 30 days after creation.** The manual backup
required no upgrade. To keep using this same database beyond its Free
expiry, upgrade it **in place** to a paid plan. Set reminders for that deadline,
domain renewal and Azure credit usage.
[Render free database limits](https://render.com/docs/free)
