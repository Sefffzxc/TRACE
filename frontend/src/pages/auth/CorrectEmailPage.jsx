import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { ArrowLeft, CheckCircle2, MailCheck } from 'lucide-react';
import AuthLayout from './AuthLayout';
import { emailCorrectionApi } from '../../api/endpoints';
import { storedUser, tokens } from '../../api/client';
import { ErrorNote, FieldError } from '../../components/common/ui';
import useRetryAfter from '../../hooks/useRetryAfter';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const EXPIRED = 'This email correction has expired or is no longer valid. Ask the administrator to send a new verification.';

/** The recipient can verify a corrected address without their unreachable old inbox. */
export default function CorrectEmailPage() {
  const [params] = useSearchParams();
  const challenge = params.get('challenge') || '';
  const validLink = UUID.test(challenge);
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [fieldErrors, setFieldErrors] = useState({});
  const [focusRequest, setFocusRequest] = useState(0);
  const [expired, setExpired] = useState(false);
  const [verifiedEmail, setVerifiedEmail] = useState('');
  const busyRef = useRef(false);
  const formRef = useRef(null);
  const errorRef = useRef(null);
  const headingRef = useRef(null);
  const retry = useRetryAfter();

  useEffect(() => { if (verifiedEmail) headingRef.current?.focus(); }, [verifiedEmail]);

  useEffect(() => {
    if (!focusRequest) return;
    const invalid = formRef.current?.querySelector('[aria-invalid="true"]');
    const target = invalid?.closest('.field') || errorRef.current;
    (invalid || errorRef.current)?.focus({ preventScroll: true });
    target?.scrollIntoView({ block: 'center' });
  }, [focusRequest]);

  const clearField = (id) => setFieldErrors((current) => {
    const next = { ...current };
    delete next[id];
    return next;
  });
  const flagErrors = (fields) => {
    setFieldErrors(fields);
    setFocusRequest((current) => current + 1);
  };
  const controlProps = (id) => ({
    id: `correct_email_${id}`,
    'aria-invalid': Boolean(fieldErrors[id]) || undefined,
    'aria-describedby': fieldErrors[id] ? `correct_email_${id}-error` : undefined,
  });

  const submit = async (event) => {
    event.preventDefault();
    if (busyRef.current || retry.seconds > 0 || expired || !validLink) return;
    const replacement = email.trim();
    const fields = {};
    const emailInput = formRef.current?.querySelector('[name="new_email"]');
    if (!replacement) fields.new_email = 'Enter the replacement email that received the code.';
    else if (emailInput?.validity.typeMismatch) fields.new_email = 'Enter a valid email address.';
    if (!/^\d{6}$/.test(code)) fields.code = 'Enter all six digits from the verification email.';
    setError('');
    if (Object.keys(fields).length) return flagErrors(fields);
    busyRef.current = true;
    setBusy(true);
    setFieldErrors({});
    try {
      const result = await emailCorrectionApi.verify({ challenge_id: challenge, new_email: replacement, code });
      if (result.user_id && String(storedUser.get()?.id) === String(result.user_id)) {
        tokens.clear();
        window.dispatchEvent(new CustomEvent('trace:session-expired'));
      }
      setVerifiedEmail(result.email || replacement);
      setCode('');
    } catch (err) {
      retry.start(err.retryAfter);
      const terminal = err.status === 410 || err.payload?.detail?.code === 'email_correction_expired';
      setExpired(terminal);
      setError(terminal ? EXPIRED : err.retryAfter > 0 ? 'Please wait before trying again.' : err.status === 400 ? 'The email or verification code is incorrect. Check the email and use its latest code.' : err.message);
      setCode('');
      flagErrors({});
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  if (verifiedEmail) return (
    <AuthLayout>
      <div className="auth-success">
        <span className="success-mark"><CheckCircle2 size={34} /></span>
        <h2 ref={headingRef} tabIndex={-1}>Email corrected</h2>
        <p className="muted" style={{ overflowWrap: 'anywhere' }}>Your account now uses <strong>{verifiedEmail}</strong>.</p>
        <p className="field-hint">Your assignments, grades and approval status are preserved. Sign in with this email and your existing password. You may be asked for a new sign-in code.</p>
        <Link className="btn btn-primary btn-block" to={`/login?email=${encodeURIComponent(verifiedEmail)}`}>Go to sign in</Link>
      </div>
    </AuthLayout>
  );

  const blocked = busy || retry.seconds > 0 || expired || !validLink;
  return (
    <AuthLayout>
      <Link className="auth-back" to="/login"><ArrowLeft size={15} /> Back to sign in</Link>
      <h2>Verify your corrected email</h2>
      <p className="auth-lede">Your administrator requested an email correction. Enter the replacement email and the six-digit code sent to it. The code is valid for five minutes.</p>
      {!validLink ? (
        <ErrorNote message="This verification link is missing or invalid. Open the link in your verification email, or ask your administrator to send a new correction request." />
      ) : (
        <form ref={formRef} className="auth-form" onSubmit={submit} noValidate>
          {error && <div ref={errorRef} tabIndex={-1}><ErrorNote message={error} /></div>}
          <div className="field">
            <label className="field-label" htmlFor="correct_email_new_email">Replacement email <span className="req">*</span></label>
            <FieldError id="correct_email_new_email" message={fieldErrors.new_email} />
            <input {...controlProps('new_email')} name="new_email" className="input" type="email" required maxLength={254} autoComplete="email" autoCapitalize="none" spellCheck={false} value={email} disabled={blocked} onChange={(event) => { setEmail(event.target.value); clearField('new_email'); }} autoFocus />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="correct_email_code">Six-digit verification code <span className="req">*</span></label>
            <FieldError id="correct_email_code" message={fieldErrors.code} />
            <input {...controlProps('code')} className="input" type="text" inputMode="numeric" autoComplete="one-time-code" required maxLength={6} pattern="[0-9]{6}" value={code} disabled={blocked} onChange={(event) => { setCode(event.target.value.replace(/\D/g, '').slice(0, 6)); clearField('code'); }} />
          </div>
          {retry.seconds > 0 && <p className="field-hint" role="status">Try again in {retry.seconds} second(s).</p>}
          {!expired && <button type="submit" className="btn btn-primary btn-lg btn-block" disabled={blocked}>{busy ? <><span className="spinner" /> Verifying…</> : <><MailCheck size={17} /> Verify and update email</>}</button>}
          <p className="field-hint">Need a new code or a different email? Ask your school principal to start another correction. Principals should contact the system administrator.</p>
        </form>
      )}
    </AuthLayout>
  );
}
