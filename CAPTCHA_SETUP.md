# Set up CAPTCHA for TRACE

This release adds Cloudflare Turnstile to both registration choices and to login
after repeated failures. It is **disabled by default** until you create a widget
and enable it on the Azure API. These steps describe the setup; they do not mean
the live widget has already been configured.

No database migration, new Python dependencies, or AI-service restart is needed.
The browser obtains the public site key from `GET /api/auth/captcha-config`.
You do not need a Turnstile environment variable in Cloudflare Pages.

## 1. Create a production widget in Cloudflare

1. Sign in to the Cloudflare dashboard and open **Turnstile**.
2. Select **Add widget** and name it **TRACE production**.
3. In hostname management, add:
   - `traceedu.app`
   - `www.traceedu.app`
4. Choose **Managed** mode. Leave **pre-clearance** disabled; this integration
   validates form tokens and does not need a clearance cookie.
5. Create the widget and keep its **site key** and **secret key** available in
   the dashboard while you configure the server.

Enter hostnames without `https://`, paths, or ports. The API verifies the hostname
where the browser widget ran, so use the frontend domains above. Do not add
`api.traceedu.app`, `localhost`, or preview domains to this production setup.

The **site key is public** and identifies the widget. The **secret key is private**
and is used only by the backend to contact Cloudflare. Never paste the secret
into chat, Git, screenshots, frontend code, or a `VITE_*` variable.

Cloudflare documents the [dashboard setup](https://developers.cloudflare.com/turnstile/get-started/widget-management/dashboard/)
and [Managed widget behavior](https://developers.cloudflare.com/turnstile/concepts/widget/).

## 2. Commit and push the code from your laptop

In VS Code Source Control, review the CAPTCHA changes, commit them, and push to
`main`. This starts the Cloudflare Pages frontend build. Pushing does not update
the Azure API, so complete the next steps promptly. Keep CAPTCHA disabled while
bringing both services onto this version.

If the frontend publishes before the updated API starts, sign-in and registration
can temporarily show **Retry security settings**. Complete the API update in
step 5, then reload the page. Plan this update during a brief maintenance window.

## 3. Connect to Azure

Run this in **Windows PowerShell on your laptop**:

```powershell
ssh -i "$env:USERPROFILE\Downloads\trace-server_key.pem" traceadmin@23.100.91.187
```

All remaining shell commands run in that **Azure SSH session**.

## 4. Save the keys with CAPTCHA initially disabled

Open the API environment file without printing its existing secrets:

```bash
sudoedit /etc/trace/api.env
```

Add or update these lines, replacing both placeholder values with the matching
keys from your new widget. Keep only one entry for each setting:

```dotenv
CAPTCHA_ENABLED=false
TURNSTILE_SITE_KEY=YOUR_PUBLIC_SITE_KEY
TURNSTILE_SECRET_KEY=YOUR_PRIVATE_SECRET_KEY
TURNSTILE_ALLOWED_HOSTNAMES=traceedu.app,www.traceedu.app
CAPTCHA_LOGIN_THRESHOLD=3
CAPTCHA_TIMEOUT_SECONDS=10
```

Save and close the editor. Preserve the other settings in this file, including
the database, email, and authentication settings. The file should retain its
existing root ownership and restrictive permissions.

If the editor is Nano, save with **Ctrl + O**, press **Enter**, then exit with
**Ctrl + X**.

## 5. Update and restart the API

Check the server checkout:

```bash
sudo -u trace -H git -C /opt/trace status --short
sudo -u trace -H git -C /opt/trace branch --show-current
```

The first command should print nothing, and the branch should be `main`. If there
are local changes, preserve and resolve them before pulling. Then run:

```bash
sudo -u trace -H git -C /opt/trace pull --ff-only origin main
sudo systemctl restart trace-api
sudo systemctl status trace-api --no-pager
```

Require **active (running)**. Check the new public configuration endpoint:

```bash
curl --fail --silent --show-error https://api.traceedu.app/api/auth/captcha-config
```

While disabled, the expected response is:

```json
{"enabled":false,"site_key":null}
```

Do not enable CAPTCHA if the endpoint is missing or the API fails to start.

## 6. Confirm the matching frontend finished deploying

In **Cloudflare → Workers & Pages → your TRACE Pages project → Deployments**,
confirm the deployment for your new commit succeeded.

Open `https://www.traceedu.app` and refresh with **Ctrl + F5**. Confirm the login
and registration pages load before proceeding. No CAPTCHA widget is expected
while `CAPTCHA_ENABLED=false`.

## 7. Enable verification

Edit the server environment file again:

```bash
sudoedit /etc/trace/api.env
```

Change only the activation setting:

```dotenv
CAPTCHA_ENABLED=true
```

Save, then restart and check:

```bash
sudo systemctl restart trace-api
sudo systemctl status trace-api --no-pager
curl --fail --silent --show-error https://api.traceedu.app/api/auth/captcha-config
curl --fail --silent --show-error https://api.traceedu.app/health
```

The configuration endpoint should now return `enabled: true` and your **public
site key**. It must never return the secret key. The health response should say
`"status":"ok"`. Refresh the registration page to obtain the enabled settings;
another Pages build is unnecessary for this setting change.

## 8. Verify the behavior

Use a controlled test account, preferably on a staging deployment. Avoid repeated
failed attempts against a real user's account: the existing lockout still applies.

- **Teacher registration:** human verification appears before submitting the
  request. A request without a valid token is rejected by the server.
- **School administrator registration:** the same verification is required before
  the school and its first administrator are created.
- **Normal login:** a fresh login without previous suspicious activity does not
  require CAPTCHA. Existing OTP, trusted-device, and approval checks continue.
- **Repeated failed login:** after three incorrect passwords, the next attempt
  requires verification. Five incorrect passwords still trigger the existing
  lockout. Passing CAPTCHA does not bypass that lockout or OTP.
- **Retry or expiration:** verification refreshes when a submitted request needs
  another attempt. An expired token must be replaced before proceeding. Confirm
  the field-level validation and scroll-to-error behavior still work.

Login also uses a 15-minute record of failures from the same client IP, including
unknown emails and attempts across different accounts. People sharing a school
or home internet connection may therefore be asked to verify sooner. A successful
login does not clear that IP history. Account failure counts remain in the
database; the IP history is held in memory per API worker and resets on restart.
The supplied service uses one worker. Multiple workers or servers would need
shared storage for consistent IP history.

The existing rate limits also remain: 10 login requests per minute per IP and
5 registration requests per five minutes per IP. Space test requests accordingly.

The server validates every required token with Cloudflare and checks both the
expected form action and the exact allowed hostname. Tokens expire after five
minutes and are intended for one validation, so retries obtain a fresh token.
[Cloudflare server-side verification](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/)
describes the verification contract and token reuse behavior.

## Troubleshooting and temporary rollback

| Symptom | Check or action |
|---|---|
| `/api/auth/captcha-config` returns `404` | The Azure API is still on an older version, or the frontend points at the wrong API. Confirm the pulled commit and restart `trace-api`. |
| Configuration or verification returns `503` | Check that the enabled API has both matching keys and a nonempty allowed-hostname list. Also check that Azure can reach Cloudflare over HTTPS. A verification outage blocks new registrations and challenged logins; users should retry. |
| Verification returns `400` | Complete a fresh challenge. If it persists, check the widget's allowed frontend domains, the backend hostname list, and that frontend and API are on the matching version. An expired token or action mismatch is rejected. |
| Widget does not load or shows a script error | Use the verification **Retry** control. Check the connection, browser extensions, and any content security policy. Turnstile needs `https://challenges.cloudflare.com` in applicable `script-src` and `frame-src` directives. |
| Console mentions `turnstile.ready()` and `async/defer` (error `3857`) | Publish the frontend loader fix, then refresh the page. Asynchronously loaded Turnstile must render after the script loads without calling `turnstile.ready()`. This frontend fix does not require an API restart or key changes. |
| CAPTCHA appears earlier than expected | Previous account failures or another person on the same public IP can trigger it. This is expected; verification does not imply that the account is locked. |
| A test key appears to pass in the browser but fails on the API | Never use dummy keys in production. Cloudflare's dummy success response uses `example.com` and omits the action, so it does not satisfy this integration's strict checks. Automated tests mock realistic verification responses. |

For API errors, inspect the logs without sharing credentials or tokens:

```bash
sudo journalctl -u trace-api -n 60 --no-pager
```

If a configuration problem prevents access, a deliberate temporary rollback is
to set `CAPTCHA_ENABLED=false` in `/etc/trace/api.env` and restart `trace-api`.
Refresh the login or registration page to reload the changed setting.
This removes CAPTCHA protection until it is re-enabled; the existing password,
rate-limit, lockout, OTP, and approval checks remain. Fix the configuration,
repeat the checks above, and enable it again. Do not remove verification checks
from the code to work around a failed widget.

Additional references: [Cloudflare CSP requirements](https://developers.cloudflare.com/turnstile/reference/content-security-policy/)
and [official testing-key behavior](https://developers.cloudflare.com/turnstile/troubleshooting/testing/).

## Manuscript wording after production verification

Use this only after setup and behavior have been verified:

> Cloudflare Turnstile was integrated into teacher and school administrator
> registration to help reduce automated account requests. Login requires human
> verification after repeated failed attempts, using account and recent client-IP
> failure history. The server validates verification tokens before processing
> protected requests, alongside the system's existing rate limits, account
> lockouts, approval checks, and email-based two-factor authentication.

