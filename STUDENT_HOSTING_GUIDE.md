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
| API, AI, frontend and email deployment | Still to do |

**Your next action is step 5: connect to `trace-server` with SSH.** The server
exists; the TRACE applications have not been installed or tested there yet.

| Component | Final address | Hosting |
|---|---|---|
| Frontend, shared with users | `https://www.traceedu.app` | Cloudflare Pages |
| Frontend, alternate address | `https://traceedu.app` | Same Pages project |
| Backend API | `https://api.traceedu.app` | Azure Ubuntu VM |
| AI service | `https://ai.traceedu.app` | Same VM |
| OTP sender | `TRACE <noreply@auth.traceedu.app>` | Resend |
| Database | Existing Render Postgres hostname | Same Render database |

Once configured, the VM will run API and AI without visitor-based sleep. Plan for
maintenance and outages; one VM cannot guarantee 100% uptime. Keep the old Render
apps available during cutover, then retire them after the new setup passes its
checks. The database stays on Render throughout.

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

**5. Connect to your created server and deploy the API and AI — start here next.**

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

**Where now:** your local **Windows PowerShell**, not Azure Cloud Shell.
The key file was found in Downloads. Run:

```powershell
ssh -i "$env:USERPROFILE\Downloads\trace-server_key.pem" traceadmin@23.100.91.187
```

The expected result is an Ubuntu terminal for `traceadmin`. Keep the private key
on your computer. See **step 4** of [STUDENT_SERVER_SETUP.md](STUDENT_SERVER_SETUP.md)
for the connection and installation instructions; the other appendix steps cover
database preservation, DNS, configuration and HTTPS. Use `traceedu.app` wherever
the appendix says `YOURDOMAIN.app`. VM creation is already done.

Before cloning onto the VM, commit and push the intended backend, AI and Resend
email changes from this checkout to GitHub. Choose one **production branch** and
use that same branch for the VM clone and Cloudflare Pages. Uncommitted local
changes will not appear on the server.

1. Preserve the existing Render database and back up its data and available
   profile-photo uploads. Keep this same database during the move.
2. Follow the appendix to install the updated repository code, including the
   Resend email changes, and configure the two Python services.
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

5. Complete the appendix's service startup, DNS and Caddy HTTPS steps. Open
   `https://api.traceedu.app/health` and `https://ai.traceedu.app/health`.
   API health must pass; AI health must report **`model_loaded: true`**.

Keep your verified Resend CNAMEs DNS only. The new `api` and `ai` records are
separate **A records** pointing to the VM, also DNS only in this guide.

**Done when:** the API and AI respond from the Azure VM at their HTTPS addresses,
using the existing Render database.

**6. Deploy the frontend on Cloudflare Pages.**

**Where:** Cloudflare Pages and **step 9** in
[STUDENT_SERVER_SETUP.md](STUDENT_SERVER_SETUP.md).

Create a Pages project from your GitHub repository using:

| Setting | Value |
|---|---|
| Root directory | `frontend` |
| Build command | `npm ci && npm run build` |
| Output directory | `dist` |
| Production environment variable | `VITE_API_URL=https://api.traceedu.app` |

Select the same production branch used for the VM. Deploy, then open the
project's **Custom domains** and attach **both**
`www.traceedu.app` and `traceedu.app`. Let Pages add the frontend DNS records and
replace the old `@`/`www` parking A records pointing to `91.195.240.94`, as the
appendix explains. Preserve the Resend TXT/CNAME records. Wait for HTTPS to
become active. Share **https://www.traceedu.app** with users.
Both frontend origins are allowed by the API configuration in step 5.
[Pages custom domains](https://developers.cloudflare.com/pages/configuration/custom-domains/)

**Done when:** `https://www.traceedu.app` loads and sends API requests to
`https://api.traceedu.app`.

**7. Test the complete system and finish the cutover.**

**Where:** the new website, Resend, Ubuntu SSH and **step 10** in the appendix.

**Test on the new site:** open `https://www.traceedu.app` in a private/incognito
window, sign in with an approved test account whose inbox you can open, receive
the emailed OTP and complete sign-in. Then test **Forgot password** with the same
test account. Check **Resend > Emails** for delivery status if needed.

**Ubuntu SSH terminal:** to watch the new API's logs during the test, run:

```bash
sudo journalctl -u trace-api -f
```

Search for **`[OTP]`** while a new code is issued. Press **Ctrl+C** to stop
watching; the API keeps running. Teachers receive the email even when you read
the log copy. If using a real teacher's account during the defense, tell them in
advance that they will receive an email. Resend's free plan currently limits
sending to **100 emails/day**; check your monthly quota too.
[Resend pricing and limits](https://resend.com/pricing)

Verify existing school records, a model prediction, profile photos, a refreshed
nested page and automatic recovery after a VM reboot using the appendix checklist.
Then retire the old Render **frontend/API/AI apps**, keeping the **database**.

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
- [ ] SSH connection opens the Ubuntu terminal.
- [ ] API and AI running on the VM; existing Render database retained.
- [ ] `www.traceedu.app` frontend and email tests pass; old Render apps retired.
- [ ] After the defense: `LOG_OTP_CODES=false` and API restarted.

Keep the Render database on a paid plan for ongoing operation: **Free Postgres
expires after 30 days**. Set reminders for domain renewal and Azure credit usage.
[Render free database limits](https://render.com/docs/free)
