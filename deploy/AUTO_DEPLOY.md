**TRACE: automatic API and AI deployment on Azure**

These files are prepared locally. Automatic deployment is **not active until you
install them on `trace-server`**. After installation, the VM checks
`Sefffzxc/TRACE`, branch `main`, about once a minute using its existing read-only
GitHub deploy key. Cloudflare Pages keeps its own Git deployment for the frontend.

The helper runs Git and Python checks as `trace`; its root process handles the
fixed TRACE service actions. It needs no new key, incoming GitHub-runner SSH
access or network security rule.

**1. Windows PowerShell: commit and push the setup files.**

Open PowerShell in your existing `C:\Users\macos\Desktop\lbca` folder. The local
folder name stays `lbca`, although the GitHub repository is now `TRACE`.

```powershell
git branch --show-current
git status --short
```

Confirm the branch is `main`. In VS Code Source Control, stage the new deployment
scripts, units, this guide and the related hosting-guide updates. Keep this first
bootstrap push limited to deployment files and documentation; deploy any pending
API/AI application changes manually first. Keep `.env` files, database URLs,
private keys and backups out of the commit.

```powershell
git diff --cached --stat
git commit -m "Add Azure automatic deployment"
git push origin main
```

**Done when:** GitHub `Sefffzxc/TRACE` on `main` contains the four setup files below
and this guide. A local save or commit alone does not update the VM.

**2. Windows PowerShell: copy four files to the VM.**

Run from the same repository folder. PowerShell uses the backticks shown for
line continuation; leave no spaces after them.

```powershell
scp -i "$env:USERPROFILE\Downloads\trace-server_key.pem" `
  .\deploy\trace-deploy.py `
  .\deploy\install-trace-autodeploy.sh `
  .\deploy\systemd\trace-deploy.service `
  .\deploy\systemd\trace-deploy.timer `
  traceadmin@23.100.91.187:/home/traceadmin/
```

**Done when:** all four transfers finish successfully. The files are together in
`/home/traceadmin/`; copying them does not enable deployment.

**3. Ubuntu SSH: update the checkout, then install.**

Your VM originally cloned commit `9c0d625`. Bring it up to the bootstrap commit
before installing: the installer does **not** pull the repository for you.
This manual pull is appropriate here because step 1 contains only deployment
setup and documentation changes.

```bash
sudo -u trace -H git -C /opt/trace status --short
```

Expect no output. If it shows changes, resolve them before continuing. Then run:

```bash
sudo -u trace -H git -C /opt/trace pull --ff-only origin main
```

If the pull fails, resolve that before installing; do not force-reset the checkout. `--ff-only`
refuses diverged history. [Git pull documentation](https://git-scm.com/docs/git-pull#Documentation/git-pull.txt---ff-only)

```bash
sudo bash /home/traceadmin/install-trace-autodeploy.sh
```

The installer checks prerequisites, installs the helper and units as root-owned
files, validates them, checks the current release's health, runs one deployment
check, and enables the timer only after that succeeds.
If it reports an error, read it before continuing. It refuses installation while
a deployment is already running. On a reinstall it stops future timer checks
before checking whether a deployment is still running.

**4. Ubuntu SSH: confirm it is working.**

```bash
systemctl is-enabled trace-deploy.timer
systemctl list-timers trace-deploy.timer --no-pager
sudo journalctl -u trace-deploy -n 40 --no-pager -o cat
sudo -u trace -H git -C /opt/trace rev-parse --short HEAD
```

Expect an enabled timer, a scheduled check and a successful initial check in the
logs. A completed oneshot service normally becomes `inactive`; that alone is
not a failure. The timer waits about 60 seconds after each run finishes, so a
deployment can take longer than one minute. Its first boot check is scheduled
after two minutes; enabling it later can trigger immediately.
[Ubuntu timer manual](https://manpages.ubuntu.com/manpages/noble/man5/systemd.timer.5.html),
[oneshot service behavior](https://manpages.ubuntu.com/manpages/noble/man5/systemd.service.5.html)

**For future code changes:** commit and push to `main` from Windows. The VM
fetches the branch, checks the changes and updates eligible code or AI model
artifacts. Backend changes restart API; AI changes restart both API and AI.
Frontend-only or ordinary documentation changes advance the checkout without an
application restart; Pages handles the frontend deployment independently.
Allow roughly a minute plus deployment time; restarts can briefly interrupt
requests. Check the journal and deployed commit above, then test the affected
feature in the browser.
Git fetch retrieves remote changes before the helper decides whether to deploy.
[Git fetch documentation](https://git-scm.com/docs/git-fetch)

Health checks use **local HTTP** `/health` on ports 8000 and 8001. Both must
report `status: ok`; AI must report `model_loaded: true`, and API must not report
an outdated schema. These checks do not verify public HTTPS, email delivery,
browser workflows or a fresh model prediction. See the [deployer source](trace-deploy.py).

**Changes that need a manual deployment**

The helper leaves the running checkout alone when it detects the paths below.
It logs **MANUAL UPDATE REQUIRED**; the timer remains enabled and will keep
checking until you pause it or finish the manual update.

| Change | Required handling |
|---|---|
| Any filename starting `requirements`; `pyproject.toml`, `setup.py`, `setup.cfg`, `.python-version`, `poetry.lock`, `uv.lock`, `Pipfile`, `Pipfile.lock` | Install and verify dependencies or interpreter changes manually. |
| `backend/app/main.py`, `backend/app/db/**`, `backend/app/models/**`; any `migrations` or `alembic` directory | Review startup/database impact and deploy with the needed backup/migration steps. |
| `backend/app/core/config.py`, any filename ending `.env` or starting `.env.`, all `deploy/**` including its documentation | Apply and validate manually; reinstall this helper when installed deployment files change. |
| Tracked local edits, a branch other than `main`, unexpected origin or diverged history | Resolve the checkout/history before deployment can continue. |

Pause the timer before manual work, finish the manual deployment using the
[server guide](../STUDENT_SERVER_SETUP.md), verify health and the affected
features, then resume it. AI model artifacts under `ai-service/` are distinct
from the database model definitions above. Other changes that require new
dependencies or database work also need a manual deployment, even if their paths
are outside this automatic gate.

**Pause or resume checks — Ubuntu SSH**

```bash
sudo systemctl disable --now trace-deploy.timer
systemctl show trace-deploy.service -p ActiveState -p SubState
```

Disabling the timer prevents future checks and survives reboot. It does **not**
cancel a deployment already running. If the service is still running or changing
state, let it finish; do not stop `trace-deploy.service` mid-deployment.

```bash
sudo systemctl enable --now trace-deploy.timer
```

This resumes checks and enables them after reboot. To request a check immediately:

```bash
sudo systemctl start trace-deploy.service
```

**If a deployment fails**

The helper attempts to restore the previous code with `git reset --keep`, restart
the old services and check health. It records the failed commit so the timer
does not repeatedly deploy that same commit. After an interrupted deployment,
the next check also attempts recovery before considering another update.
Read the journal before retrying; rollback is attempted recovery, not a guarantee
of availability.
[Git reset behavior](https://git-scm.com/docs/git-reset)

If it logs **RECOVERY REQUIRED**, pause the timer and wait for the current run
to finish. Manually repair the checkout/services and review the private state
at `/var/lib/trace-deploy/status.json` before resuming. Neither a new push nor
`--retry` bypasses this state; do not blindly delete it to force deployment.

Usually, fix the problem on Windows and push a new commit. To deliberately retry
the same failed commit after fixing its cause, pause the timer as above, wait for
any current deployment to finish, then run:

```bash
sudo /usr/bin/python3 -I -B /usr/local/lib/trace/deploy_trace.py --retry
```

Read the result, verify service health, then re-enable the timer. `--retry` only
bypasses the skip for that run of the same failed commit; manual-update checks and
the recovery-required block still apply.

The helper does not run package-install or database-migration commands, replace
environment files or update installed service units. Application startup still
runs normally when services restart. Production secrets under `/etc/trace`
and uploaded photos under `/var/lib/trace/uploads` stay outside the Git checkout.
Code rollback does **not** undo database writes, emails or other actions already
performed by the application. Keep the existing backups and remaining cutover
checks in the [hosting guide](../STUDENT_HOSTING_GUIDE.md).
