**TRACE server setup: API + AI + frontend migration**

Updated **9 November 2026**. This is the technical appendix to
[STUDENT_HOSTING_GUIDE.md](STUDENT_HOSTING_GUIDE.md). Finish that guide's domain,
DNS, Resend domain verification and API key steps first (steps 1–4). Configure
email directly on the new Ubuntu API in step 5 below. Keep the old Render apps
available during cutover, then retire them after the new setup passes its checks.

Replace `YOURDOMAIN.app` with **`traceedu.app`**, `VM_PUBLIC_IP` with the server's
static IPv4 address, and the repository/key placeholders with your own. Use the
same `YOUR_PRODUCTION_BRANCH` for the VM clone and the Cloudflare Pages build.
Buy only one domain; `api`, `ai` and `auth` are subdomains of it.

**Your VM has been created.** Its current public IPv4 is **`23.100.91.187`**,
administrator is **`traceadmin`**, and downloaded SSH key is
**`trace-server_key.pem`**. Use that IP for `VM_PUBLIC_IP` below. Update this
record if you replace the VM or its public IP. Connect using step 4; preserve
the existing database and uploads as described in step 1 before cutover.

| Address | What runs there |
|---|---|
| `https://www.YOURDOMAIN.app` | Main frontend address on Cloudflare Pages |
| `https://YOURDOMAIN.app` | Same frontend, also attached to Pages |
| `https://api.YOURDOMAIN.app` | Caddy forwards to FastAPI on `127.0.0.1:8000` |
| `https://ai.YOURDOMAIN.app` | Caddy forwards to AI on `127.0.0.1:8001` |
| `noreply@auth.YOURDOMAIN.app` | Resend sends OTP emails; configured in the main guide |
| Existing Render database | Same PostgreSQL instance and school records |

TRACE uses a CPU scikit-learn model; no GPU subscription is needed. Both Python
services share one VM. The API calls the AI locally and connects to Render using
the database's **External Database URL** over TLS.

After cutover, **only the database stays on Render**. The API and AI run as
separate services on the Azure VM, with automatic startup and crash restarts.

**Where to run commands:** the `ssh` and `scp` examples run on your **Windows
PowerShell** terminal. All `bash` commands run **inside Ubuntu after SSH login**.
Configuration blocks go into the named files, not directly into the terminal.
When using `nano`, press **Ctrl+O**, **Enter**, then **Ctrl+X** to save and exit.

The server has no visitor-based idle sleep. Plan for updates and outages; a single
VM cannot guarantee 100% uptime. Azure credit is finite. A paid fallback is a
DigitalOcean Basic 2 GiB VM, currently $12/month before extras and taxes.
[Azure student terms](https://azure.microsoft.com/en-us/pricing/offers/ms-azr-0170p/),
[DigitalOcean pricing](https://www.digitalocean.com/pricing/droplets)

This appendix replaces the all-Render steps in `DEPLOYMENT.md` for this migration.
Keep the existing database; do not apply `render.yaml` to create another one.

1. **Preserve the existing Render database and uploaded photos.**

   In Render, open the database you already use, currently named `lbca-db` in the
   repository blueprint. Keep this same database and its data. Upgrade it in
   place if it is on Free: **Render Free Postgres expires after 30 days** and
   does not provide backups. Moving the other services will not remove that
   deadline. [Render free database limits](https://render.com/docs/free)

   Take a database backup before cutover, and verify you can restore it into a
   separate test database. Use Render's paid backup/export facilities or
   `pg_dump` with a PostgreSQL client version compatible with the server.
   [Render backup and export instructions](https://render.com/docs/postgresql-backups)

   Copy the database's **External Database URL** from Render's connection panel
   into your password manager. The existing Internal Database URL will not work
   from Azure or DigitalOcean. Keep the full external hostname and TLS enabled.
   The backend already converts Render's PostgreSQL URL into the asyncpg format
   and handles its TLS settings; you do not need to rewrite the driver name.
   [Render external connections](https://render.com/docs/postgresql-creating-connecting#external-connections)

   TRACE stores profile photos on the API's local filesystem, outside PostgreSQL.
   Export the current API upload directory if those files are available. Files
   already lost from an ephemeral Render filesystem cannot be recovered by
   moving the database. You will copy available uploads to the VM in step 5.

   Keep the old API and AI available until the replacement passes the checks in
   step 10. Record their current configuration securely, especially `SECRET_KEY`.

2. **Create the server — completed for your current VM.**

   Your current server is `trace-server`, in resource group `trace-hosting`,
   region **East Asia**, with Ubuntu 24.04 LTS x64, **Standard_B2als_v2**
   (2 vCPUs, 4 GiB RAM), an image-default 30 GiB **StandardSSD_LRS** OS disk,
   on-demand pricing and automatic shutdown disabled. The steps below record
   how to recreate it; continue with the connection step for this running VM.

   Redeem [Azure for Students](https://azure.microsoft.com/en-us/free/students/)
   using your approved Student Pack benefit. Choose **Azure for Students** with
   the $100 credit; this offer does not require a credit card. Verify that the
   student subscription appears in Azure before creating resources. Then open
   [Azure Portal](https://portal.azure.com/), search **Virtual machines**, and
   select **Create > Azure virtual machine**.

   Create an Ubuntu Server **24.04 LTS, x86-64** VM. Use the student
   subscription, a dedicated resource group such as `trace-hosting`, and an SSH
   key. Name the VM `trace-server` and the administrator `traceadmin` to match
   the examples below. [Azure's VM creation walkthrough](https://learn.microsoft.com/en-us/azure/virtual-machines/linux/quick-create-portal)

   Start with **at least 2 GiB RAM** for the combined API, model and operating
   system. This is a starting recommendation, not a measured requirement. Select
   a current size allowed by your subscription; for example, `B2als_v2` has
   4 GiB if available. The free-listed `B2ats_v2` has only 1 GiB and should not
   be mistaken for a 2 GiB server. Region and quota availability vary.
   [Azure AMD B-series sizes](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/general-purpose/basv2-series)

   Check the subscription's allowed regions first: open **Policy > Authoring >
   Assignments**, scope it to **Azure for Students**, and inspect **Allowed
   resource deployment regions > Parameters > Allowed locations**, if present.
   Your confirmed list is `indiasouthcentral`, `japaneast`, `indonesiacentral`,
   `eastasia` and `koreacentral`; **Southeast Asia is outside that list**.
   **East Asia** was the working choice for your VM. An allowed region still
   needs an available VM size and sufficient quota/capacity.
   [Azure location policies](https://learn.microsoft.com/en-us/azure/governance/policy/samples/built-in-policies#general)

   Use a persistent **Standard SSD (LRS)** OS disk and a **static public IPv4
   address**. Keep automatic shutdown and Spot disabled for the running service.

   Review the **combined** monthly estimate for compute, OS disk, public IP and
   chargeable traffic before creating it. Your selected VM's compute quote was
   **$0.0526/hour, about $38.40/month**. A $100 balance covers approximately
   **2.6 months of compute alone**; disk, public IP and chargeable traffic can
   shorten that further. Add budget alerts and monitor the remaining balance.
   When the credit runs out, the student subscription is disabled unless you
   arrange continued paid service. Plan that transition beforehand.
   [Azure calculator](https://azure.microsoft.com/en-us/pricing/calculator/),
   [student spending limit](https://azure.microsoft.com/en-us/pricing/offers/ms-azr-0170p/)

   If the available Azure size is too expensive or unavailable, create a
   DigitalOcean Basic 2 GiB server (currently $12/month before extras and taxes)
   with Ubuntu 24.04 in Singapore instead. The remaining Linux configuration is
   the same; use your provider's
   actual SSH username.

   In the cloud firewall/network security group, allow inbound TCP **80 and 443**
   publicly. Allow **22** only from your administrator IP. Leave **8000 and
   8001** closed. Caddy will accept public requests and forward them internally.

   For the VM portal's **Advanced** network security group configuration, keep
   just one SSH allow rule and add these web rules:

   | Rule | Source | Destination port | Protocol / action | Priority |
   |---|---|---|---|---|
   | Existing `default-allow-ssh` | Your current administrator public IPv4 `/32` | `22` | TCP / Allow | `1001` or another unused value |
   | `allow-http` | Any | `80` | TCP / Allow | `1010` |
   | `allow-https` | Any | `443` | TCP / Allow | `1020` |

   For all three, **Source port ranges** is `*` and **Destination** is **Any**.
   The HTTP destination port is **80**; replace the Custom service's initial
   value of 8080 if shown.

   **Portal preview workaround:** in the SSH rule, choose **Source > IP
   Addresses** and manually enter your current administrator public IPv4 with
   `/32` in **Source IP addresses/CIDR ranges**. In your setup, selecting
   **My IP address** displayed an IP but the submitted rule had no source
   address. This caused `SecurityRuleParametersMissing` for `default-allow-ssh`.
   Entering the explicit IP/CIDR resolved validation. If editing a rule rejects
   its own priority as a duplicate, choose another unused priority (for example,
   1002) for that same rule and verify the final list has only one SSH allowance,
   restricted to your IP. Save both the rule and its parent NSG dialog before
   running **Review + create** again.
   [Azure NSG rule fields](https://learn.microsoft.com/en-us/azure/virtual-network/manage-network-security-group#create-a-security-rule)

   The Basics summary can show **Public inbound ports: None** when the actual
   allow rules are configured in your custom Advanced NSG. Check that NSG's
   rules. Keep its SSH source current when your administrator's public IP changes.

   Once validation passes, select **Create**, then **Download private key and
   create resource** when prompted. Keep the downloaded `.pem` private and
   record its location. Wait for deployment to complete, select **Go to resource**,
   and copy the VM's public IP from Overview.

   In Render's database external-access rules, allow the VM's public outbound
   IPv4 address as a `/32`. For a directly attached public IP this is normally
   the VM public IP; if you configure a NAT gateway, use its egress IP instead.
   Keep any IP needed by the old deployment until cutover. Do not leave
   unrestricted database access as the permanent configuration.

3. **Create DNS records for the Python services.**

   In Cloudflare DNS, add these records using the VM's real static IPv4 address:

   | Type | Name | Target | Proxy setting |
   |---|---|---|---|
   | A | `api` | `VM_PUBLIC_IP` | DNS only, gray cloud |
   | A | `ai` | `VM_PUBLIC_IP` | DNS only, gray cloud |
   | Frontend records | `@` and `www` | Created by the Pages custom-domain workflow in step 9 | Managed by Pages |

   Remove conflicting old `api`/`ai` records. Add an AAAA record only if you have
   also configured working IPv6. Keep the API and AI DNS-only for this guide so
   their HTTPS connections terminate directly at Caddy.

4. **Connect and install the application.**

   First, in **VS Code on Windows**, commit and push the reviewed changes to
   your GitHub production branch, including `backend/app/services/email.py`,
   the updated auth router and configuration. The VM can only clone committed,
   pushed code. Use that same branch for the frontend later. Replace
   `YOUR_PRODUCTION_BRANCH` below with its actual name, such as `main`.

   From **Windows PowerShell**, your current connection command is:

   ```powershell
   ssh -i "$env:USERPROFILE\Downloads\trace-server_key.pem" traceadmin@23.100.91.187
   ```

   Adjust the key path if you moved or renamed the downloaded file. Successful
   login shows a prompt such as `traceadmin@trace-server:~$`. Keep the key's
   contents private. An SSH timeout can indicate that your current public IP
   differs from the SSH rule's source; update that one source IP in the Azure
   network security group before retrying.

   **Windows: "UNPROTECTED PRIVATE KEY FILE" / "bad permissions".** If SSH
   reports this before `Permission denied (publickey)`, repair the local key's
   file permissions, then retry. For your downloaded key, the extra permissions
   were inherited from its folder, including access for `CodexSandboxUsers`.
   Run this in your own **Windows PowerShell** window:

   ```powershell
   $traceKeyPath = "$env:USERPROFILE\Downloads\trace-server_key.pem"
   $traceKeyOwner = (Get-Acl -LiteralPath $traceKeyPath).Owner
   icacls.exe $traceKeyPath /inheritance:r /grant:r "${traceKeyOwner}:(R)"
   ssh -i $traceKeyPath traceadmin@23.100.91.187
   ```

   This removes the inherited permissions on this file and grants read access
   to its existing owner. Check that `icacls` reports zero failed files before
   retrying SSH. It does not read or change the key's contents.
   [Windows OpenSSH private-key permissions](https://github.com/PowerShell/Win32-OpenSSH/wiki/Security-protection-of-various-files-in-Win32-OpenSSH#user-private-key-files),
   [Microsoft icacls reference](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/icacls)

   All following `bash` commands run **inside Ubuntu over SSH**, not in your
   Windows project terminal. Stop and resolve any failing command before
   continuing. Install the Python runtime and basic tools:

   ```bash
   sudo apt-get update
   sudo apt-get upgrade -y
   sudo apt-get install -y python3.12-venv python3-pip git curl ca-certificates nano
   python3.12 --version
   sudo adduser --disabled-password --gecos "" trace
   sudo install -d -o trace -g trace -m 755 /opt/trace
   sudo install -d -o trace -g trace -m 700 /home/trace/.ssh
   sudo -u trace -H ssh-keygen -t ed25519 -C trace-deploy -f /home/trace/.ssh/id_ed25519
   ```

   For this repository-scoped deployment key, leave the passphrase empty when
   prompted so the service account can fetch updates without an interactive
   agent. Keep the private key on the server. Display the **public** key:

   ```bash
   sudo cat /home/trace/.ssh/id_ed25519.pub
   ```

   In the GitHub repository, open **Settings > Deploy keys > Add deploy key**.
   Paste the public key and leave write access disabled. Clone using that key;
   replace `YOUR_USERNAME/YOUR_REPOSITORY`. Compare the first connection's host
   fingerprint against [GitHub's published fingerprints](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints)
   before accepting it.
   [GitHub deploy-key instructions](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys)

   ```bash
   sudo -u trace -H git clone --branch YOUR_PRODUCTION_BRANCH --single-branch git@github.com:YOUR_USERNAME/YOUR_REPOSITORY.git /opt/trace
   sudo -u trace python3.12 -m venv /opt/trace/backend/.venv
   sudo -u trace /opt/trace/backend/.venv/bin/python -m pip install -r /opt/trace/backend/requirements.txt
   sudo -u trace python3.12 -m venv /opt/trace/ai-service/.venv
   sudo -u trace /opt/trace/ai-service/.venv/bin/python -m pip install -r /opt/trace/ai-service/requirements.txt
   ls -lh /opt/trace/ai-service/model/final_grade_model_v4.joblib
   ls -lh /opt/trace/ai-service/model/model_card_v4.json
   ```

   Keep the AI dependency versions pinned. The committed model must be loaded
   with the compatible scikit-learn/numpy/pandas versions in its requirements.
   Do not retrain the model on this production server.

5. **Set persistent storage and production configuration.**

   Use the verified Resend sender and saved API key from main-guide steps 1–4.
   Put them directly on this VM. Email delivery on the old Render API is not a
   prerequisite. The settings below enable email plus the temporary code logging
   you requested for your defense. Set `LOG_OTP_CODES=false` after the defense
   and restart the API; email delivery will continue.

   Create storage outside the source checkout and root-controlled secret files:

   ```bash
   sudo install -d -o trace -g trace -m 750 /var/lib/trace/uploads
   sudo install -d -o root -g root -m 700 /etc/trace
   sudo install -o root -g root -m 600 /dev/null /etc/trace/api.env
   sudo install -o root -g root -m 600 /dev/null /etc/trace/ai.env
   sudo nano /etc/trace/api.env
   ```

   The `install` commands above initialize new files; on later updates **edit
   the existing files**, because rerunning them would empty your configuration.

   Put the following in `api.env`, replacing every placeholder. Preserve the
   existing production `SECRET_KEY`; generate a key only for a deployment that
   does not already have one. Reuse or generate a separate strong AI token and
   put the same AI token in both files. To generate a new value on the VM, use
   `python3.12 -c 'import secrets; print(secrets.token_urlsafe(48))'`.

   ```dotenv
   DATABASE_URL=PASTE_THE_EXISTING_RENDER_EXTERNAL_DATABASE_URL
   SECRET_KEY=PASTE_THE_EXISTING_PRODUCTION_SECRET_KEY
   ALLOWED_ORIGINS=https://www.YOURDOMAIN.app,https://YOURDOMAIN.app
   UPLOAD_DIR=/var/lib/trace/uploads
   DEBUG_OTP=false
   LOG_OTP_CODES=true
   EMAIL_ENABLED=true
   RESEND_API_KEY=PASTE_YOUR_RESEND_SENDING_API_KEY
   EMAIL_FROM="TRACE <noreply@auth.YOURDOMAIN.app>"
   EMAIL_TIMEOUT_SECONDS=10
   AI_ENABLED=true
   AI_SERVICE_URL=http://127.0.0.1:8001
   AI_SERVICE_TOKEN=PASTE_A_STRONG_SHARED_AI_TOKEN
   AI_TIMEOUT_SECONDS=6
   AI_WAKE_TIMEOUT_SECONDS=90
   AI_BATCH_SIZE=100
   ```

   Use Render's full URL, retaining its encoded password and TLS options; do
   not set `sslmode=disable`. These are systemd environment files: use
   `KEY=value`, without `export`. Do not commit them or copy secrets into
   frontend settings.

   Then open the AI configuration:

   ```bash
   sudo nano /etc/trace/ai.env
   ```

   ```dotenv
   AI_SERVICE_TOKEN=PASTE_THE_SAME_SHARED_AI_TOKEN
   MODEL_DIR=/opt/trace/ai-service/model
   MODEL_VERSION=v4-2026-10-final-grade
   ALLOWED_ORIGINS=
   OMP_NUM_THREADS=1
   OPENBLAS_NUM_THREADS=1
   ```

   An empty AI CORS list is appropriate because the browser calls the API, not
   the model. The token protects inference; CORS does not authenticate callers.
   Public AI health and model information endpoints remain readable.

   Copy any exported **contents** of the old upload directory into
   `/var/lib/trace/uploads`, preserving its `profile_pics` subdirectory. For
   example, if you exported them to a local `exported-uploads` folder, run this
   in a **separate Windows PowerShell terminal**, with your actual paths/IP:

   ```powershell
   scp -i "C:\path\to\trace-vm.pem" -r "C:\path\to\exported-uploads" traceadmin@VM_PUBLIC_IP:~/trace-upload-import
   ```

   Then, **back in Ubuntu**, copy those contents into place and set ownership
   (adjust `/home/traceadmin` if you chose another SSH administrator):

   ```bash
   sudo cp -a /home/traceadmin/trace-upload-import/. /var/lib/trace/uploads/
   sudo chown -R trace:trace /var/lib/trace/uploads
   ```

   Skip this copy if you have no exported files. Back up uploads separately from PostgreSQL.
   Keep all other production settings you intentionally customized, such as
   token lifetimes or lockout settings.

6. **Make the API and AI start at boot and restart after a crash.**

   Create `/etc/systemd/system/trace-ai.service` using
   `sudo nano /etc/systemd/system/trace-ai.service`:

   ```ini
   [Unit]
   Description=TRACE AI inference
   Wants=network-online.target
   After=network-online.target

   [Service]
   User=trace
   Group=trace
   WorkingDirectory=/opt/trace/ai-service
   EnvironmentFile=/etc/trace/ai.env
   Environment=PYTHONUNBUFFERED=1
   ExecStart=/opt/trace/ai-service/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8001 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1
   Restart=always
   RestartSec=5
   UMask=0027
   NoNewPrivileges=true

   [Install]
   WantedBy=multi-user.target
   ```

   Create `/etc/systemd/system/trace-api.service`:

   ```ini
   [Unit]
   Description=TRACE monitoring API
   Wants=network-online.target trace-ai.service
   After=network-online.target trace-ai.service

   [Service]
   User=trace
   Group=trace
   WorkingDirectory=/opt/trace/backend
   EnvironmentFile=/etc/trace/api.env
   Environment=PYTHONUNBUFFERED=1
   ExecStart=/opt/trace/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1
   Restart=always
   RestartSec=5
   UMask=0027
   NoNewPrivileges=true

   [Install]
   WantedBy=multi-user.target
   ```

   Use one worker initially: additional workers duplicate model memory or
   database connection pools. Do not use development `--reload` mode. Start both:

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now trace-ai trace-api
   sudo systemctl status trace-ai trace-api --no-pager
   curl --fail http://127.0.0.1:8001/health
   curl --fail http://127.0.0.1:8000/health
   ```

   Inspect the JSON, not only HTTP status. The AI must report
   `"model_loaded": true`; it can return HTTP 200 when the model failed to load.
   The API must not report `"up_to_date": false` in a schema diagnostic.

   If needed, inspect logs with:

   ```bash
   sudo journalctl -u trace-ai -n 80 --no-pager
   sudo journalctl -u trace-api -n 80 --no-pager
   ```

   Existing database accounts and records remain in place. **Do not run seed
   reset, rebuild or fresh-start commands.** Startup creates missing tables but
   does not add missing columns. If a schema mismatch exists, use the repository's
   `add_missing_columns.py` dry run with the same production environment, review
   the proposed changes and backup, then plan the required migration. Hosting
   migration alone does not require resetting or reseeding the database.

7. **Put HTTPS in front of both services.**

   **Ubuntu terminal (the SSH connection):** run the stable-package commands
   from [Caddy's official installation guide](https://caddyserver.com/docs/install#debian-ubuntu-raspbian).
   This is a fresh-server installation; stop if any command fails.

   ```bash
   sudo apt install --yes debian-keyring debian-archive-keyring apt-transport-https curl gnupg
   curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
   curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
   sudo chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg
   sudo chmod o+r /etc/apt/sources.list.d/caddy-stable.list
   sudo apt update
   sudo apt install --yes caddy
   sudo nano /etc/caddy/Caddyfile
   ```

   Installation creates the `caddy` system service. In the editor, replace the
   default sample site with the following, using your real domain:

   ```caddyfile
   api.YOURDOMAIN.app {
       reverse_proxy 127.0.0.1:8000 {
           header_up X-Forwarded-For {remote_host}
       }
   }

   ai.YOURDOMAIN.app {
       reverse_proxy 127.0.0.1:8001 {
           header_up X-Forwarded-For {remote_host}
       }
   }
   ```

   Caddy supplies and renews HTTPS certificates once DNS points to the server
   and inbound ports 80/443 are reachable. The explicit forwarded-address header
   gives TRACE the client's address rather than trusting one supplied by a
   visitor. [Automatic HTTPS](https://caddyserver.com/docs/automatic-https),
   [reverse-proxy headers](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy#headers)

   ```bash
   sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
   sudo systemctl enable --now caddy
   sudo systemctl reload caddy
   curl --fail https://api.YOURDOMAIN.app/health
   curl --fail https://ai.YOURDOMAIN.app/health
   ```

   If certificates fail, check DNS, stale AAAA records, cloud firewall rules,
   any active Ubuntu firewall and Caddy's logs. Keep 8000/8001 private. If you
   enable Ubuntu's firewall separately, allow SSH from your administrator IP
   before enabling it, and allow public TCP 80/443.

8. **Confirm the model is protected and reachable.**

    Both public health URLs should respond. An inference call without the token
    must be refused. From Ubuntu, this deliberately incomplete request should
    return **401**, because the service checks the token before inference:

    ```bash
    curl -i -X POST https://ai.YOURDOMAIN.app/predict -H 'Content-Type: application/json' -d '{}'
    ```

    Keep `AI_SERVICE_URL=http://127.0.0.1:8001` on the API. The public AI domain
    gives the service its own address, but the backend uses the private path.

9. **Deploy the frontend on Cloudflare Pages.**

    In Cloudflare, open **Workers & Pages > Create application > Pages > Import
    an existing Git repository**. Connect only the GitHub repository needed for
    TRACE. Choose the same production branch used for the VM clone and enter:

    | Setting | Value for this repository |
    |---|---|
    | Root directory | `frontend` |
    | Framework preset | React/Vite, or None with the explicit settings below |
    | Build command | `npm ci && npm run build` |
    | Build output directory | `dist` |
    | Production environment variable | `VITE_API_URL=https://api.YOURDOMAIN.app` |

    The repository has a committed `frontend/package-lock.json`, so `npm ci`
    installs its locked dependencies. Do not put `/api` at the end of
    `VITE_API_URL`: TRACE's requests already include their API paths.
    [Cloudflare React deployment](https://developers.cloudflare.com/pages/framework-guides/deploy-a-react-site/)

    Deploy, then open the Pages project's **Custom domains** and attach both
    `www.YOURDOMAIN.app` and `YOURDOMAIN.app`. Share the `www` address with users;
    both will serve the same frontend. Let Pages create the DNS records and
    HTTPS certificates. The API configuration above permits both origins.
    [Pages custom domains](https://developers.cloudflare.com/pages/configuration/custom-domains/)

    Your earlier DNS screen showed parking A records for the root domain and
    `www`, pointing to `91.195.240.94`. Replace those conflicting parking records
    when attaching Pages. Keep the Resend verification/sending records and the
    API/AI records. The frontend's records should point to Pages, rather than
    the Ubuntu server. Wait until both custom domains and certificates are active.

    TRACE uses React Router. Pages automatically handles its SPA routes when
    there is no top-level `404.html`; do not add one that breaks that behavior.
    Test opening a route such as `/login` directly and refreshing `/dashboard`.
    [Pages SPA behavior](https://developers.cloudflare.com/pages/configuration/serving-pages/#single-page-application-spa-rendering)

    `VITE_API_URL` is embedded at **build time**. Any later API-domain change
    requires a new frontend build. Put only public configuration in `VITE_*`:
    never the database URL, JWT secret or AI token.

    A Pages preview URL has a different origin. Test authenticated use through
    your custom domain; allow a specific preview origin in the API only if you
    intentionally need it.

10. **Validate and switch users to the new address.**

    Check the following before retiring the old application services:

    - `https://www.YOURDOMAIN.app` and `https://YOURDOMAIN.app` both load with
      valid certificates.
    - API health returns the intended frontend in `allowed_origins`, with no
      schema mismatch. AI health reports `model_loaded: true`.
    - Existing users and school records are present. The database is the same
      Render instance, not a newly created empty instance.
    - Sign in from a new browser, receive the email and complete the
      OTP step, and verify password reset with `DEBUG_OTP=false`.
    - During the defense, the same newly issued code appears in the VM's API
      logs. View them with `sudo journalctl -u trace-api -n 80 --no-pager`.
    - Open a student's records, save an authorized test change, then confirm it
      persists after signing out and in.
    - On the AI page, score a suitable existing test record and confirm the
      result identifies the model rather than a rule-based fallback.
    - Existing profile photos load; upload a test photo and verify it survives
      an API restart.
    - Reload a nested frontend route. Check browser network requests use the
      new HTTPS API, with no localhost requests or CORS errors.
    - During an agreed maintenance window, reboot the VM and verify the API,
      AI and Caddy return automatically. Leave the site idle for more than
      15 minutes and verify the first request still works promptly.

    The frontend's new origin means users sign in again: TRACE stores browser
    tokens under each origin. There is no OAuth redirect or cookie-domain
    setting in the current application to migrate.

    Switch your shared links to the `.app` address. Once verified, retire only
    the old Render frontend/API/AI services, keeping the database and backup.
    Review any Render Blueprint changes carefully so they do not change or
    recreate `lbca-db`. Do not reapply the old free-tier blueprint over the
    database plan you just upgraded.

    After the defense, edit `/etc/trace/api.env` to set `LOG_OTP_CODES=false`
    and run `sudo systemctl restart trace-api`. Keep `EMAIL_ENABLED=true`
    and `DEBUG_OTP=false`.

11. **Keep the deployment running.**

    Cloudflare rebuilds the frontend on production-branch pushes. A plain VM
    does not automatically deploy your Python changes. For a routine update
    without a schema change, note the current commit, fetch the reviewed code,
    install its requirements, restart the processes and repeat health checks:

    ```bash
    sudo -u trace -H git -C /opt/trace rev-parse HEAD
    sudo -u trace -H git -C /opt/trace pull --ff-only
    sudo -u trace /opt/trace/backend/.venv/bin/python -m pip install -r /opt/trace/backend/requirements.txt
    sudo -u trace /opt/trace/ai-service/.venv/bin/python -m pip install -r /opt/trace/ai-service/requirements.txt
    sudo systemctl restart trace-ai
    sudo systemctl restart trace-api
    curl --fail https://ai.YOURDOMAIN.app/health
    curl --fail https://api.YOURDOMAIN.app/health
    ```

    Schedule updates because this simple process can briefly interrupt service.
    Coordinate API/frontend compatibility when a release changes their contract.
    For schema changes, back up and plan the migration before restarting against
    the new code. Save the previous commit so you can redeploy it if compatible
    with the database schema; a code rollback does not undo a database migration.

    Configure monitoring for the frontend and API, plus an AI health check that
    examines `model_loaded`, not just HTTP 200. A monitor detects a failure; it is
    not needed to keep the VM awake. Check memory and disk usage, keep Ubuntu
    security updates current, and allow for maintenance restarts.

    Keep database backups, a separate backup of `/var/lib/trace/uploads`, and
    encrypted recovery copies of `/etc/trace` and the deployment configuration
    outside the VM. Test restoration. Set reminders for domain renewal, Azure
    credit usage/expiry and database billing. Budget alerts notify you; they do
    not by themselves guarantee that resources will remain available.

    If you move between Azure and DigitalOcean later, reproduce the same server
    setup, copy the persistent files and secrets securely, allow the new egress
    IP in Render, validate it, then update the `api`/`ai` DNS records. Your
    frontend domain and Render database can stay the same.
