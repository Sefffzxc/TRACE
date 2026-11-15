**TRACE server setup: API + AI + frontend migration**

Updated **9 November 2026**. This is the technical appendix to
[STUDENT_HOSTING_GUIDE.md](STUDENT_HOSTING_GUIDE.md). Finish that guide's domain,
DNS, Resend domain verification and API key steps first (steps 1–4). Configure
email directly on the new Ubuntu API in step 5 below. Keep the old Render apps
available during cutover, then retire them after the new setup passes its checks.

Replace `YOURDOMAIN.app` with **`traceedu.app`**, `VM_PUBLIC_IP` with the server's
static IPv4 address, and the remaining placeholders with your own values. Your
repository is **`Sefffzxc/TRACE`** (renamed from `Sefffzxc/lbca`) and production branch is **`main`**. Use that
same branch for the VM clone and the Cloudflare Pages build.
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

   In Render, open the existing database used by your API. Keep this same
   database and its data. You confirmed its plan is **Free**. A manual
   `pg_dump` backup works on Free; upgrading is not required to take this backup.
   Render's hosted backup/recovery features require a paid database plan.
   [Render backup and export instructions](https://render.com/docs/postgresql-backups)

   **Render Free Postgres expires 30 days after creation.** Moving the API and
   AI does not change that deadline. Check this database's expiry date now and
   arrange an in-place paid upgrade before expiry for continued use on Render.
   Keep the backup even if you upgrade.
   [Render free database limits](https://render.com/docs/free#free-postgres)

   Copy the database's **External Database URL** from Render's connection panel
   into your password manager. The existing Internal Database URL will not work
   from Azure or DigitalOcean. Keep the full external hostname and TLS enabled.
   The backend already converts Render's PostgreSQL URL into the asyncpg format
   and handles its TLS settings; you do not need to rewrite the driver name.
   [Render external connections](https://render.com/docs/postgresql-creating-connecting#external-connections)

   **Create a manual backup before starting the new API.** Your VM already has
   PostgreSQL client **18.6** at `/usr/lib/postgresql/18/bin/pg_dump`; skip
   installation on this VM. On a replacement Ubuntu 24.04 VM, install the client
   from the official PostgreSQL repository:

   ```bash
   sudo apt-get install -y --no-install-recommends postgresql-common
   sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh
   sudo apt-get update
   sudo apt-get install -y --no-install-recommends postgresql-client-18
   /usr/lib/postgresql/18/bin/pg_dump --version
   ```

   Install only the client package for this export; the live database remains
   on Render. The dump client must support the database server's major version.
   [PostgreSQL Ubuntu packages](https://www.postgresql.org/download/linux/ubuntu/),
   [pg_dump documentation](https://www.postgresql.org/docs/18/app-pgdump.html)

   In Render's database **Info / Connections** panel, identify the full external
   hostname, database username and **Database** value. The database value can
   differ from the service's display name. The external hostname ends in
   `render.com`; the shorter internal `dpg-...-a` hostname will not work from
   Azure. Keep the password private and paste it only at the hidden prompt.

   Run the following **one command at a time** as `traceadmin` in Ubuntu. Replace
   the three `YOUR_RENDER_...` values before running `pg_dump`. This creates a
   private backup outside the repository, with a new UTC filename on each run:

   ```bash
   install -d -m 700 "$HOME/trace-backups"
   umask 077
   trace_backup_path="$HOME/trace-backups/trace-before-azure-$(date -u +%Y%m%dT%H%M%SZ).dump"
   PGSSLMODE=require /usr/lib/postgresql/18/bin/pg_dump --host="YOUR_RENDER_EXTERNAL_HOST" --port=5432 --username="YOUR_RENDER_DB_USER" --dbname="YOUR_RENDER_DB_NAME" --password --format=custom --file="$trace_backup_path"
   ```

   At `Password:`, enter the database password saved from Render. No characters
   appear while you type or paste. The export reads the database; it does not
   replace its data. A successful run normally returns to the shell without
   output. Immediately run `echo "Backup exit code: $?"` and require `0`.
   If it reports an error, stop and fix it before continuing. A failed dump can
   leave a partial file, so file existence alone is not success.

   After the export finishes without errors, check that its contents can be
   decoded and inspect the file's size and permissions:

   ```bash
   /usr/lib/postgresql/18/bin/pg_restore --exit-on-error --file=/dev/null "$trace_backup_path"
   echo "Archive check exit code: $?"
   ls -lh "$trace_backup_path"
   ```

   Require archive check exit code `0`. Your completed export and archive check
   both returned `0`. The file is **184 KiB**, with owner-only `600` permissions:
   `/home/traceadmin/trace-backups/trace-before-azure-20261109T082306Z.dump`.
   The Render service is named `trace-db`; its PostgreSQL database name is
   `trace_ht2p`.

   This `pg_restore` command generates SQL and discards it without connecting to
   a database. It checks archive readability, not a full restore. Before cutover,
   also copy the completed backup to private storage outside the VM and verify
   restoration into a separate, isolated test database. Never point a restore
   at the existing Render database. The archive contains school records: keep
   it out of Git, chat, public folders and screenshots of its contents.
   [pg_restore output modes](https://www.postgresql.org/docs/18/app-pgrestore.html)

   **Copy the checked backup off the VM.** In the existing Ubuntu SSH session,
   calculate the source checksum and keep the session open:

   ```bash
   sha256sum /home/traceadmin/trace-backups/trace-before-azure-20261109T082306Z.dump
   ```

   Open a separate **Windows PowerShell** window, showing `PS C:\...>` rather
   than the Ubuntu prompt. Create a folder outside the repository and remove
   inherited access before downloading. Run one line at a time, and stop if
   `icacls` reports any failed files:

   ```powershell
   $traceBackupDir = "$env:USERPROFILE\TRACE-private-backups"
   New-Item -ItemType Directory -Path $traceBackupDir -Force | Out-Null
   $traceBackupOwner = (Get-Acl -LiteralPath $traceBackupDir).Owner
   icacls.exe $traceBackupDir /inheritance:r /grant:r "${traceBackupOwner}:(OI)(CI)F"
   scp -i "$env:USERPROFILE\Downloads\trace-server_key.pem" traceadmin@23.100.91.187:/home/traceadmin/trace-backups/trace-before-azure-20261109T082306Z.dump $traceBackupDir
   Get-FileHash -Algorithm SHA256 -LiteralPath "$traceBackupDir\trace-before-azure-20261109T082306Z.dump" | Format-List
   ```

   Compare the complete SHA-256 values from Ubuntu and Windows; capitalization
   does not matter. They must match. Keep both copies private: this is a
   compressed database archive, not an encrypted file. For a later backup,
   substitute its actual filename in both terminals.
   Your Windows copy completed successfully, its folder permissions were set
   with zero failed files, and both checksums matched on 9 November 2026:
   `38f8dcde4c4f0f2b8383bc1bb7af17ab03ed19a4756e03023153ec37550ce011`.
   The Windows file is in `C:\Users\macos\TRACE-private-backups`. The isolated
   restore test remains pending.
   [Windows folder permissions](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/icacls),
   [PowerShell file checksums](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.utility/get-filehash)

   **Rotate the exposed database credential — in progress.** Keep the replacement
   password and connection URLs out of chat, screenshots, shell command
   arguments and Git. Use Render's managed credential flow on the existing
   `trace-db` database:

   - **Completed:** the new default credential `trace_azure` has been created.
     Keep its password and new Internal and External Database URLs private.
     The old credential remains active while applications move in stages.
   - **Next:** finish `/etc/trace/ai.env` using step 5 below. You saved `api.env`;
     its contents and the new **External Database URL** still need validation.
     Preserve the existing production `SECRET_KEY`. After configuration, use
     fresh connections to verify application access and existing records.
   - Updating the old Render API's credential is optional transition
     maintenance if you need to keep using that deployment. In that case,
     update **trace-api > Environment > DATABASE_URL** with the new **Internal
     Database URL**, save and deploy its existing build, then check `/health`
     and existing records. This is not a prerequisite for configuring Azure.
     The final API and AI run on Azure; only the database stays on Render.
   - After every client has moved and no connections remain under the old
     username, remove the old credential in **Info > Credentials**. This is
     when the credential rotation is complete. Render preserves objects owned
     by the original user by revoking that user's login rather than dropping
     its role. Keep the existing database and records throughout the process.

   Do not recreate the database or apply the unrelated all-Render Blueprint
   during this migration. If an environment variable is managed through a
   Blueprint database reference, account for that reference when updating the
   client so a later sync cannot bring back stale configuration.
   [Render credential rotation](https://render.com/docs/postgresql-credentials)

   TRACE stores profile photos on the API's local filesystem, outside PostgreSQL.
   Export the current API upload directory if those files are available. Files
   already lost from an ephemeral Render filesystem cannot be recovered by
   moving the database. You will copy available uploads to the VM in step 5.

   Keep the old API and AI available as a fallback until the replacement passes
   the checks in step 10, then retire those apps and revoke the old database
   credential promptly. Record the current configuration securely, especially
   `SECRET_KEY`, so the new Azure deployment can reuse it.

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
   pushed code. Your VM already cloned this repository under its former name
   `Sefffzxc/lbca`, branch `main`, at
   `9c0d625`, and both Python environments have their dependencies installed.
   These installation commands are recorded for rebuilding the server; do not
   clone over the existing checkout. Use `main` for the frontend later.

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
   Paste the public key and leave write access disabled. Clone using that key.
   Compare the first connection's host
   fingerprint against [GitHub's published fingerprints](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints)
   before accepting it.
   [GitHub deploy-key instructions](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys)

   ```bash
   sudo -u trace -H git clone --branch main --single-branch git@github.com:Sefffzxc/TRACE.git /opt/trace
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

   **Both environment files passed the private configuration check.** Continue
   and both services now pass their local health checks. Continue with step 7
   for public HTTPS. API and AI run on this Azure VM;
   only PostgreSQL stays on Render. The new `trace_azure` database credential is
   ready, and the dump/archive check and matching private Windows copy are done.
   A full isolated restore test remains pending before cutover.

   Use the verified Resend sender and saved API key from main-guide steps 1–4.
   Put them directly on this VM. Email delivery on the old Render API is not a
   prerequisite. The settings below enable email plus the temporary code logging
   you requested for your defense. Set `LOG_OTP_CODES=false` after the defense
   and restart the API; email delivery will continue.

   Storage and configuration files are now prepared on your VM:
   `/var/lib/trace/uploads` is owned by `trace` with mode `750`, `/etc/trace`
   is root-owned with mode `700`, and `api.env` / `ai.env` are root-owned with
   mode `600`. **Both files are saved, and all eight configuration checks
   returned `OK` on the VM.** Service startup and local health checks also
   passed. Public HTTPS health and OTP verification using the API log copy
   have also passed. Existing records load and remain visible after a page
   refresh. Inference and inbox delivery remain pending.
   These creation commands are for a
   replacement VM:

   ```bash
   sudo install -d -o trace -g trace -m 750 /var/lib/trace/uploads
   sudo install -d -o root -g root -m 700 /etc/trace
   sudo touch /etc/trace/api.env /etc/trace/ai.env
   sudo chown root:root /etc/trace/api.env /etc/trace/ai.env
   sudo chmod 600 /etc/trace/api.env /etc/trace/ai.env
   sudo nano /etc/trace/api.env
   ```

   These commands preserve any existing file contents. Edit existing settings
   carefully when updating the deployment.

   The following `api.env` template is a reference; your saved file passed the
   static check. Replace every placeholder when configuring a new file. Preserve the
   existing production `SECRET_KEY`; generate a key only for a deployment that
   does not already have one. Read that value privately from the existing
   Render API's **Environment** settings and copy it into the Azure file. Use
   the new credential's **External Database URL** from Render's database
   connection panel for `DATABASE_URL`. Reuse or generate a strong AI token and
   put the same AI token in both files. `SECRET_KEY` signs login tokens;
   `AI_SERVICE_TOKEN` authenticates the API's requests to the AI service. The
   following command generates **`AI_SERVICE_TOKEN`**. Save its output privately
   for both environment files. To generate it on the VM, use
   `python3.12 -c 'import secrets; print(secrets.token_urlsafe(48))'`.

   ```dotenv
   DATABASE_URL=PASTE_THE_NEW_RENDER_EXTERNAL_DATABASE_URL
   SECRET_KEY=PASTE_THE_EXISTING_PRODUCTION_SECRET_KEY
   ALLOWED_ORIGINS=https://www.traceedu.app,https://traceedu.app
   UPLOAD_DIR=/var/lib/trace/uploads
   DEBUG_OTP=false
   LOG_OTP_CODES=true
   EMAIL_ENABLED=true
   OTP_EXPIRY_MINUTES=5
   RESEND_API_KEY=PASTE_YOUR_RESEND_SENDING_API_KEY
   EMAIL_FROM="TRACE <noreply@auth.traceedu.app>"
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

   These files are loaded by the systemd units in step 6. Running Python or
   Uvicorn manually does not load `/etc/trace/api.env`; without the production
   settings the API can fall back to a local SQLite database and default secret.
   Finish the backup and configuration first, then start through systemd.

   **AI configuration reference.** You reported this file saved. It uses the
   same `AI_SERVICE_TOKEN` as `api.env`; keep its value private:

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

   **Validate the saved files before service startup.** The local repository now
   includes [scripts/check_trace_vm_config.py](scripts/check_trace_vm_config.py).
   Your VM run completed with all eight checks reporting `OK`. The instructions
   below are recorded for rechecking this initial defense configuration.
   It reads only `/etc/trace/api.env` and `/etc/trace/ai.env`, and prints check
   names with `OK` / `FAIL`. It checks placeholders, the new external database
   connection settings, matching AI tokens, email/OTP settings and local paths.
   It does not connect to the database or import the application. Passing these
   checks does not prove the credentials work or that the old signing key was
   copied correctly; those require the service and login tests.

   The checker supports the simple one-line `KEY=value` format shown above,
   including matching outer quotes. It rejects duplicate keys, `export`,
   backslash escapes, variable references and multiline values rather than
   interpreting them differently from systemd. If a check fails, edit the
   protected file privately and share only the check's name.

   First, in a separate **Windows PowerShell** terminal, upload the checker from
   this local checkout:

   ```powershell
   scp -i "$env:USERPROFILE\Downloads\trace-server_key.pem" "$env:USERPROFILE\Desktop\lbca\scripts\check_trace_vm_config.py" traceadmin@23.100.91.187:/home/traceadmin/check_trace_vm_config.py
   ```

   Then, in the **Ubuntu SSH terminal**, run:

   ```bash
   sudo python3.12 /home/traceadmin/check_trace_vm_config.py
   ```

   Continue with systemd only after every check reports `OK`. Keep the files'
   contents private; only the check labels are intended to be shared.

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

   **Completed on the VM:** both units were installed with mode `644`, verified
   by systemd, enabled at boot and started. Both report `active`; AI health
   reports `model_loaded: true`, version `v4-2026-10-final-grade` and no error.
   API health reports `status: ok`, the two frontend origins and no schema
   mismatch. OTP verification using the API log copy also works. A real
   reboot and the remaining application checks are pending.

   Reviewed unit files are prepared in the local checkout:
   [trace-ai.service](deploy/systemd/trace-ai.service) and
   [trace-api.service](deploy/systemd/trace-api.service). They run as `trace`,
   load the protected environment files and bind to loopback ports 8001/8000.
   The API starts after the AI process, and remains available with its fallback
   if inference is unavailable. Model readiness is checked separately below.

   First, in **Windows PowerShell**, upload both files:

   ```powershell
   scp -i "$env:USERPROFILE\Downloads\trace-server_key.pem" "$env:USERPROFILE\Desktop\lbca\deploy\systemd\trace-ai.service" "$env:USERPROFILE\Desktop\lbca\deploy\systemd\trace-api.service" traceadmin@23.100.91.187:/home/traceadmin/
   ```

   Then, in the **Ubuntu SSH terminal**, install and verify them. Run one
   command at a time. Stop if installation or verification reports an error:

   ```bash
   sudo install -o root -g root -m 644 /home/traceadmin/trace-ai.service /etc/systemd/system/trace-ai.service
   sudo install -o root -g root -m 644 /home/traceadmin/trace-api.service /etc/systemd/system/trace-api.service
   sudo systemd-analyze verify /etc/systemd/system/trace-ai.service /etc/systemd/system/trace-api.service
   ```

   Use one worker initially: additional workers duplicate model memory or
   database connection pools. Do not use development `--reload` mode. Start both:

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now trace-ai trace-api
   systemctl is-active trace-ai trace-api
   curl --fail --retry 12 --retry-connrefused --retry-delay 2 --max-time 10 http://127.0.0.1:8001/health
   curl --fail --retry 12 --retry-connrefused --retry-delay 2 --max-time 10 http://127.0.0.1:8000/health
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

   **Completed:** Caddy `v2.11.9` serves both domains over HTTPS. Your normal
   certificate-verified health checks from the VM returned API `status: ok`
   with both frontend origins, and AI `status: ok`, `model_loaded: true`, the
   expected model version and no error. API and AI also pass loopback checks.
   Continue with step 8, then Pages. An independent check from your browser
   outside the VM remains pending. The instructions below record how to load the prepared
   [deploy/Caddyfile](deploy/Caddyfile) for `api.traceedu.app` and
   `ai.traceedu.app`. Both DNS A records should still point directly to
   `23.100.91.187` with DNS-only proxy status. Azure's NSG must allow public TCP
   **80 and 443**; the HTTP destination port is **80**, rather than 8080.

   **Ubuntu terminal (the SSH connection):** run the stable-package commands
   from [Caddy's official installation guide](https://caddyserver.com/docs/install#debian-ubuntu-raspbian).
   These installation commands are recorded for rebuilding the server; your
   current installation is complete. Stop if any command fails on a replacement VM.

   ```bash
   sudo apt install --yes debian-keyring debian-archive-keyring apt-transport-https curl gnupg
   curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
   curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
   sudo chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg
   sudo chmod o+r /etc/apt/sources.list.d/caddy-stable.list
   sudo apt update
   sudo apt install --yes caddy
   caddy version
   ```

   Installation creates the `caddy` system service. In **Windows PowerShell**,
   upload the prepared configuration from this checkout:

   ```powershell
   scp -i "$env:USERPROFILE\Downloads\trace-server_key.pem" "$env:USERPROFILE\Desktop\lbca\deploy\Caddyfile" traceadmin@23.100.91.187:/home/traceadmin/TRACE-Caddyfile
   ```

   Back in **Ubuntu SSH**, install it in place of the default sample site:

   ```bash
   sudo install -o root -g caddy -m 644 /home/traceadmin/TRACE-Caddyfile /etc/caddy/Caddyfile
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
   curl --fail --retry 6 --retry-all-errors --retry-delay 5 --connect-timeout 10 --max-time 20 https://api.traceedu.app/health
   curl --fail --retry 6 --retry-all-errors --retry-delay 5 --connect-timeout 10 --max-time 20 https://ai.traceedu.app/health
   ```

   Let each health command finish; retries allow time for the first certificate
   to be issued. TLS certificate verification stays enabled. Both domain health
   checks have now passed from this VM.

   If certificates fail, check DNS, stale AAAA records, cloud firewall rules,
   any active Ubuntu firewall and Caddy's logs. Keep 8000/8001 private. If you
   enable Ubuntu's firewall separately, allow SSH from your administrator IP
   before enabling it, and allow public TCP 80/443.

8. **Confirm the model is protected and reachable.**

    **This is your next check.** The two HTTPS health endpoints are working;
    token-protected prediction access still needs the check below.

    Both public health URLs should respond. An inference call without the token
    must be refused. From Ubuntu, this deliberately incomplete request should
    return **401**, because the service checks the token before inference:

    ```bash
    curl -i --max-time 20 -X POST https://ai.traceedu.app/predict -H 'Content-Type: application/json' -d '{}'
    ```

    Keep `AI_SERVICE_URL=http://127.0.0.1:8001` on the API. The public AI domain
    gives the service its own address, but the backend uses the private path.

9. **Deploy the frontend on Cloudflare Pages.**

    **Pages deployment succeeded at `https://trace-285.pages.dev`.** Your next
    action is finishing the two custom domains below. `www.traceedu.app` now
    shows **Active / SSL enabled** in Pages; apex-domain activation is not yet
    confirmed. The rendered app, its
    embedded API address and authenticated operations still need verification.

    Your earlier `trace` screen was a **Worker** project: it has a deploy command
    (`npx wrangler deploy`) and a **Delete Worker** button. For this guide,
    create a separate **Pages** project using the steps below. Leave the Worker
    in place while setting up Pages. The Pages build form has a **Build output
    directory** field, which should be `dist`.

    In Cloudflare, open **Workers & Pages > Create application > Pages > Import
    an existing Git repository** (or **Connect to Git**). Connect only
    **`Sefffzxc/TRACE`**, which you confirmed is the renamed repository. In your
    dashboard's newer layout, **Continue to Pages** appears at the bottom beside
    **Need to use the legacy Pages workflow?** Use that entry, then Git import.
    Choose production branch **`main`** and enter:

    | Setting | Value for this repository |
    |---|---|
    | Root directory | `frontend` |
    | Framework preset | React/Vite, or None with the explicit settings below |
    | Build command | `npm ci && npm run build` |
    | Build output directory | `dist` |
    | Production environment variable | `VITE_API_URL=https://api.traceedu.app` |

    The repository has a committed `frontend/package-lock.json`, so `npm ci`
    installs its locked dependencies. Do not put `/api` at the end of
    `VITE_API_URL`: TRACE's requests already include their API paths.
    Add this public variable in the Pages production **build environment**.
    Workers **Runtime variables and secrets** are separate and do not set the
    value embedded into a Vite build.
    [Cloudflare React deployment](https://developers.cloudflare.com/pages/framework-guides/deploy-a-react-site/)

    On your deployment-success screen, click **Add custom domain**, or use
    **Continue to project > Custom domains > Set up a domain**. Add
    **`www.traceedu.app`** first, continue through the DNS confirmation and
    activation, then repeat for **`traceedu.app`**. Let the Pages workflow create
    the required records pointing to **`trace-285.pages.dev`** and provision
    HTTPS. Wait until both domains show **Active** with working certificates.
    Share `https://www.traceedu.app` with users after the application tests.
    The API configuration above already permits both frontend origins.
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

    **Confirmed on 9 November:** the user reports that the OTP code from the
    API logs works. Existing records load and remain visible after a page
    refresh. Inbox delivery, inference and the remaining checks below still
    need verification.

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
      logs. In Ubuntu SSH, run `sudo journalctl -u trace-api -f -o cat`, then
      request the login OTP in your browser. Find the newest `[OTP] login code`
      line for that account and enter its code on the site. This can be used
      when the account's inbox is unavailable, with its valid login credentials.
      Keep the code and email address private. Press Ctrl+C to stop viewing logs.
      With email enabled, a rejected send request rolls back the OTP and no
      usable code is logged; resolve that error before trying again.
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
    recreate the existing `trace-db` database. Its actual database name is
    `trace_ht2p`; the repository's older blueprint still names `lbca-db`.
    The current plan is confirmed Free; no paid upgrade has been confirmed.
    Keep its expiry deadline from step 1 in view and do not apply the old
    Blueprint as part of this migration.

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
