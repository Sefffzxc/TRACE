# Group sign-in and approval-list fix

This release addresses failures reported while 10–13 people used TRACE together.
The changes need deployment to both the Azure API and Cloudflare Pages. They
require no database migration, new packages, or CAPTCHA key changes.

## What caused the failures

- The API previously allowed only 10 sign-ins/OTP requests per minute and 5
  registrations per five minutes **per public IP**. A room sharing Wi-Fi shared
  those limits. A blocked registration does not create a pending account.
- Password hashing/checking ran directly on the API event loop, delaying other
  requests while each bcrypt operation completed.
- Login held a database connection while waiting for the OTP email provider.
  Enough simultaneous emails could occupy the entire connection pool and delay
  the administrator's requests.
- The Approvals page loaded pending teachers and audit history together. An
  audit-history failure discarded a successful pending-teacher response.
- Temporary account-lock messages rounded minutes down, allowing users to retry
  before the actual lock expired.

These are confirmed code paths. The live VM's logs and the email provider's
usage dashboard still need checking if errors remain after deployment.

## Changes

| Protection | Per account/email | Default per shared IP |
| --- | --- | --- |
| Sign-in | 10 requests/minute | 60 requests/minute |
| OTP verification | 10 requests/minute | 60 requests/minute |
| Registration | 5 requests/5 minutes | 60 requests/5 minutes |
| Password-reset requests | 5 requests/5 minutes | 60 requests/5 minutes |

The five-wrong-password lockout, five-attempt login OTP check, CAPTCHA and school
approval requirements continue to apply. Rate-limit identities use the client
address supplied by the trusted server/proxy configuration, rather than trusting
raw forwarding headers. Account identifiers are hashed inside the limiter.

Password work now runs in worker threads. Login releases its database connection
before email delivery and saves a usable OTP only after the provider accepts the
email. Email request starts are paced to avoid provider bursts, with bounded
waiting and retries for the provider's explicit short-term rate-limit response.
Provider daily/monthly quotas are handled as delivery failures.

Approvals now displays the queue independently of audit-history errors, refreshes
every 30 seconds while visible, refreshes when returning to the tab, and has a
**Refresh requests** button. Queue errors retain previously loaded requests.

Sign-in and registration use the server's `Retry-After` value to show a countdown
and disable repeated submissions during the wait. Temporary account-lock waits
use the full remaining time. The browser does not automatically resend forms.

## Deploy

1. In your local project, review the changes, commit them, and push to `main`.
   This starts the Cloudflare Pages deployment when your existing Git integration
   is enabled. Wait for that deployment to show **Success**.

2. In Windows PowerShell, connect to the existing Azure VM:

   ```powershell
   ssh -i "$env:USERPROFILE\Downloads\trace-server_key.pem" traceadmin@23.100.91.187
   ```

3. In the VM's Ubuntu terminal, update the checkout and restart the API. Run each
   command separately and stop if the pull fails:

   ```bash
   cd /opt/trace
   sudo -u trace git pull --ff-only origin main
   sudo systemctl restart trace-api
   sudo systemctl status trace-api --no-pager -l
   curl --fail --silent --show-error https://api.traceedu.app/health
   ```

   Expect `active (running)` and a health reply with `"status":"ok"`.
   An installed `trace-deploy` service may already perform the backend update;
   do not assume it exists on a VM where the unit was never installed.

4. The new settings have working defaults. Adding them to `/etc/trace/api.env`
   is optional, unless that file already overrides these values:

   ```dotenv
   AUTH_LOGIN_IP_LIMIT=60
   AUTH_OTP_IP_LIMIT=60
   AUTH_REGISTER_IP_LIMIT=60
   AUTH_RESET_IP_LIMIT=60
   EMAIL_SEND_RATE_PER_SECOND=2
   EMAIL_QUEUE_TIMEOUT_SECONDS=20
   ```

   If you edit that file, restart `trace-api` again. Keep the existing secrets
   private. Keep `EMAIL_ENABLED=true`, `DEBUG_OTP=false`, and your existing
   CAPTCHA configuration. Keep the deployed API at **one worker**, as the rate
   counters and email pacing are currently per process. More workers require
   shared counters and a shared email queue first.

5. Reload the website with **Ctrl + F5** after both deployments finish.

## Repeat the group test

Use test accounts with inboxes you control. Each new-device login sends an
actual email and consumes the provider's quota.

1. On the same Wi-Fi, have 13 people submit distinct teacher registration
   requests for the same approved school. Each should receive the registration
   success message. A CAPTCHA, validation or rate-limit error is a failed
   submission; resolve it before counting that registration as successful.
2. Sign in as that school's administrator. Click **Refresh requests** and check
   all successful registrations appear. Other schools' requests should remain
   outside this queue.
3. Approve the test teachers. Have all 13 sign in together with their correct
   passwords on new devices. Email starts are spaced, so some codes can arrive
   several seconds after the first. Keep the admin Approvals page open and check
   it remains usable while emails are being sent.
4. Complete the login OTPs. Verify ordinary pages and refreshes work.
5. On a separate test account, check that repeated wrong passwords still trigger
   CAPTCHA and the account lock. Observe the full countdown before trying the
   correct password again. Do not deliberately lock a real user's account.

If errors remain, inspect the VM's relevant messages without sharing OTP logs:

```bash
sudo journalctl -u trace-api --since "10 minutes ago" --no-pager -o cat | grep -E '429|QueuePool|TimeoutError|Email provider|Email request|Email send queue'
```

Also check **Resend > Logs** and usage. Provider `429` can mean short-term request
rate limiting **or** daily/monthly quota exhaustion; waiting a few seconds only
helps with the former. Inspect the specific error type. See
[Resend's usage-limit documentation](https://resend.com/docs/api-reference/rate-limit).

Local regression checks use temporary databases, synthetic accounts and mocked
external services; they do not prove production email delivery or VM capacity.
Run backend checks with `python -m unittest discover -s tests` from `backend`, and
the frontend regression with `node audit/auth-traffic-browser.cjs` from the project
root after installing its documented local audit tooling.
