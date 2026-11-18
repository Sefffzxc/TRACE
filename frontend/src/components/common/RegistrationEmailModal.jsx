import { useEffect, useRef, useState } from 'react';
import { ShieldCheck } from 'lucide-react';
import { FieldError, Modal } from './ui';

export default function RegistrationEmailModal({ email, expiresIn, error, busy, retrySeconds, onVerify, onClose }) {
  const [code, setCode] = useState('');
  const input = useRef(null);
  const [expiresAt] = useState(() => Date.now() + expiresIn * 1000);
  const [seconds, setSeconds] = useState(expiresIn);

  useEffect(() => {
    const timer = setInterval(() => setSeconds(Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000))), 1000);
    return () => clearInterval(timer);
  }, [expiresAt]);
  useEffect(() => { input.current?.focus(); }, [error]);

  return (
    <Modal title="Verify your email" subtitle="Confirm your inbox before submitting your application." size="sm" onClose={onClose}>
      <p>We sent a six-digit code to <strong style={{ overflowWrap: 'anywhere' }}>{email}</strong>.</p>
      <p className="field-hint">Your account request will be submitted after you verify this email.</p>
      <form className="stack" style={{ marginTop: '1rem' }} onSubmit={(event) => { event.preventDefault(); if (!busy && !retrySeconds && code.length === 6) onVerify(code); }}>
        <div className="field">
          <label className="field-label" htmlFor="registration-email-code">Verification code</label>
          <FieldError id="registration-email-code" message={error} />
          <input
            ref={input} id="registration-email-code" className="input" inputMode="numeric" autoComplete="one-time-code"
            maxLength={6} pattern="[0-9]{6}" value={code} disabled={busy} required
            aria-invalid={Boolean(error) || undefined} aria-describedby={error ? 'registration-email-code-error' : undefined}
            onChange={(event) => setCode(event.target.value.replace(/\D/g, '').slice(0, 6))}
          />
        </div>
        <p className="field-hint" role="status">
          {seconds > 0 ? `Code expires in ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}.` : 'Code expired. Return to the form to request a new one.'}
          {retrySeconds > 0 ? ` Try again in ${retrySeconds} seconds.` : ''}
        </p>
        <button type="submit" className="btn btn-primary" disabled={busy || code.length !== 6 || seconds === 0 || retrySeconds > 0}>
          <ShieldCheck size={16} /> {busy ? 'Submitting…' : 'Verify email and submit'}
        </button>
        <button type="button" className="btn btn-secondary" disabled={busy} onClick={onClose}>Change email or request a new code</button>
      </form>
    </Modal>
  );
}
