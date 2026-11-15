from pathlib import Path
from urllib.parse import urlsplit
import hmac, sys

def load(path):
    result = {}
    for row in Path(path).read_text().splitlines():
        row = row.strip()
        if not row or row.startswith('#'):
            continue
        key, sep, value = row.partition('=')
        if not sep or not key.isidentifier() or key in result:
            raise ValueError()
        if value.startswith((chr(34), chr(39))):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError()
            value = value[1:-1]
        if any(c in value for c in (chr(92), chr(34), chr(39), chr(9))) or ' #' in value or '${' in value or value != value.strip():
            raise ValueError()
        result[key] = value
    return result

try:
    api, ai = load('/etc/trace/api.env'), load('/etc/trace/ai.env')
except Exception:
    print('FAIL: file format/access; keep file contents private')
    sys.exit(1)

def filled(value):
    return bool(value) and not value.upper().startswith(('PASTE_', 'YOUR_', 'CHANGE-ME', 'CHANGE_ME'))

try:
    db = urlsplit(api.get('DATABASE_URL', ''))
    db_ok = db.scheme in ('postgres', 'postgresql', 'postgresql+asyncpg') and db.hostname == 'dpg-dbicp3on74is73b2rnlg-a.singapore-postgres.render.com' and db.username == 'trace_azure' and bool(db.password) and db.path == '/trace_ht2p' and db.port in (None, 5432) and 'sslmode=disable' not in db.query.lower()
except ValueError:
    db_ok = False

checks = [
    ('API secret values filled', all(filled(api.get(k)) for k in ('DATABASE_URL', 'SECRET_KEY', 'RESEND_API_KEY', 'AI_SERVICE_TOKEN'))),
    ('New external database URL', db_ok),
    ('AI tokens filled and matching', filled(ai.get('AI_SERVICE_TOKEN')) and hmac.compare_digest(api.get('AI_SERVICE_TOKEN', '').encode(), ai.get('AI_SERVICE_TOKEN', '').encode())),
    ('Signing key differs from AI token', api.get('SECRET_KEY') != api.get('AI_SERVICE_TOKEN')),
    ('Email and OTP settings', api.get('RESEND_API_KEY', '').startswith('re_') and api.get('EMAIL_ENABLED') == 'true' and api.get('DEBUG_OTP') == 'false' and api.get('LOG_OTP_CODES') == 'true' and api.get('EMAIL_FROM') == 'TRACE <noreply@auth.traceedu.app>'),
    ('API timeout and batch settings', all(api.get(k) == v for k, v in (('EMAIL_TIMEOUT_SECONDS', '10'), ('AI_TIMEOUT_SECONDS', '6'), ('AI_WAKE_TIMEOUT_SECONDS', '90'), ('AI_BATCH_SIZE', '100')))),
    ('API origins and local settings', set(api.get('ALLOWED_ORIGINS', '').split(',')) == {'https://www.traceedu.app', 'https://traceedu.app'} and api.get('AI_SERVICE_URL') == 'http://127.0.0.1:8001' and api.get('UPLOAD_DIR') == '/var/lib/trace/uploads' and api.get('AI_ENABLED') == 'true'),
    ('AI model and thread settings', ai.get('MODEL_DIR') == '/opt/trace/ai-service/model' and ai.get('MODEL_VERSION') == 'v4-2026-10-final-grade' and ai.get('ALLOWED_ORIGINS') == '' and ai.get('OMP_NUM_THREADS') == '1' and ai.get('OPENBLAS_NUM_THREADS') == '1'),
]
for name, ok in checks:
    print(('OK: ' if ok else 'FAIL: ') + name)
sys.exit(0 if all(ok for _, ok in checks) else 1)
