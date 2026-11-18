import { useEffect, useRef, useState } from 'react';
import { CheckCircle2, ExternalLink, Eye, EyeOff, Lock, Send } from 'lucide-react';
import { emailCorrectionApi } from '../../api/endpoints';
import useRetryAfter from '../../hooks/useRetryAfter';
import { ErrorNote, FieldError, Modal } from './ui';

/** An operator confirms identity; ownership of the replacement address is verified separately. */
export default function EmailCorrectionModal({ targetName, currentEmail, targetId, targetType = 'teacher', onClose }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [reason, setReason] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [fieldErrors, setFieldErrors] = useState({});
  const [focusRequest, setFocusRequest] = useState(0);
  const [sent, setSent] = useState(null);
  const busyRef = useRef(false);
  const formRef = useRef(null);
  const errorRef = useRef(null);
  const successRef = useRef(null);
  const retry = useRetryAfter();

  useEffect(() => {
    if (!focusRequest) return;
    const invalid = formRef.current?.querySelector('[aria-invalid="true"]');
    const target = invalid?.closest('.field') || errorRef.current;
    (invalid || errorRef.current)?.focus({ preventScroll: true });
    target?.scrollIntoView({ block: 'nearest' });
  }, [focusRequest]);

  useEffect(() => { if (sent) successRef.current?.focus(); }, [sent]);

  const clearField = (id) => setFieldErrors((current) => {
    const next = { ...current };
    delete next[id];
    return next;
  });
  const flagErrors = (fields) => {
    setFieldErrors(fields);
    setFocusRequest((current) => current + 1);
  };
  const close = () => { if (!busyRef.current) onClose(); };
  const controlProps = (id) => ({
    id: `email_correction_${id}`,
    'aria-invalid': Boolean(fieldErrors[id]) || undefined,
    'aria-describedby': fieldErrors[id] ? `email_correction_${id}-error` : undefined,
  });

  const submit = async (event) => {
    event.preventDefault();
    if (busyRef.current || retry.seconds > 0) return;
    const replacement = email.trim();
    const fields = {};
    const emailInput = formRef.current?.querySelector('[name="new_email"]');
    if (!replacement) fields.new_email = 'Enter the email this person can open.';
    else if (emailInput?.validity.typeMismatch) fields.new_email = 'Enter a valid email address.';
    else if (replacement.toLowerCase() === currentEmail?.trim().toLowerCase()) fields.new_email = 'Enter a different email address.';
    if (!reason.trim()) fields.reason = 'Briefly explain why this email needs correcting.';
    if (!password) fields.current_password = 'Enter your own password to confirm this request.';
    if (!confirmed) fields.identity_confirmed = 'Confirm that you verified this person’s identity.';
    setError('');
    if (Object.keys(fields).length) return flagErrors(fields);
    busyRef.current = true;
    setBusy(true);
    setFieldErrors({});
    try {
      const payload = { new_email: replacement, current_password: password, reason: reason.trim(), identity_confirmed: true };
      const request = targetType === 'principal' ? emailCorrectionApi.requestPrincipal : emailCorrectionApi.requestTeacher;
      const result = await request(targetId, payload);
      setPassword('');
      setShowPassword(false);
      setSent(result);
    } catch (err) {
      retry.start(err.retryAfter);
      const fieldsFromServer = {};
      if (err.status === 422 && Array.isArray(err.payload?.detail)) {
        for (const issue of err.payload.detail) {
          const key = (issue.loc || []).filter((part) => part !== 'body')[0];
          if (['new_email', 'reason', 'current_password', 'identity_confirmed'].includes(key)) {
            fieldsFromServer[key] = (issue.msg || 'Please check this field.').replace(/^Value error, /, '');
          }
        }
      }
      if (err.payload?.detail?.code === 'password_incorrect') fieldsFromServer.current_password = 'Your password is incorrect. Enter it again.';
      if (err.payload?.detail?.code === 'email_already_used') fieldsFromServer.new_email = 'This email is already used by an account. Choose a different address.';
      setError(err.retryAfter > 0 ? 'Please wait before requesting another correction.' : err.message);
      setPassword('');
      flagErrors(fieldsFromServer);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  const verificationPath = sent?.challenge_id ? `/correct-email?challenge=${encodeURIComponent(sent.challenge_id)}` : null;
  return (
    <Modal title={targetType === 'principal' ? 'Correct principal email' : 'Correct teacher email'} subtitle={<span style={{ overflowWrap: 'anywhere' }}>{targetName}</span>} onClose={close} size="sm">
      {sent ? (
        <div className="stack" style={{ gap: '1rem', overflowWrap: 'anywhere' }}>
          <div ref={successRef} tabIndex={-1} role="status" className="alert alert-success"><CheckCircle2 size={18} /><span>Verification sent to <strong>{sent.new_email || email.trim()}</strong>.</span></div>
          <p>Ask this person to open the email and use its verification link and six-digit code within <strong>five minutes</strong>.</p>
          <p className="muted">Their current email stays in place until verification succeeds. They can then sign in with the corrected email and their existing password.</p>
          {verificationPath && <a className="btn btn-secondary" href={verificationPath} target="_blank" rel="noopener noreferrer"><ExternalLink size={16} /> Open verification page</a>}
          <p className="field-hint">If the code expires or the email does not arrive, close this window and start a new correction request.</p>
          <button type="button" className="btn btn-primary" onClick={close}>Done</button>
        </div>
      ) : (
        <form ref={formRef} className="stack" style={{ gap: '1rem' }} onSubmit={submit} noValidate>
          <p className="muted">Use this when the person cannot access their account email. Confirm their identity before continuing. Their assignments, grades and account status are preserved.</p>
          <div className="field"><span className="field-label">Current account email</span><p style={{ margin: '.25rem 0', overflowWrap: 'anywhere' }}>{currentEmail}</p></div>
          {error && <div ref={errorRef} tabIndex={-1}><ErrorNote message={error} /></div>}
          <div className="field">
            <label className="field-label" htmlFor="email_correction_new_email">Replacement email <span className="req">*</span></label>
            <FieldError id="email_correction_new_email" message={fieldErrors.new_email} />
            <input {...controlProps('new_email')} name="new_email" className="input" type="email" autoComplete="off" autoCapitalize="none" spellCheck={false} required maxLength={254} value={email} disabled={busy} autoFocus onChange={(event) => { setEmail(event.target.value); clearField('new_email'); }} />
            <span className="field-hint">The person must be able to open this inbox. A verification code will be sent there.</span>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="email_correction_reason">Reason for correction <span className="req">*</span></label>
            <FieldError id="email_correction_reason" message={fieldErrors.reason} />
            <textarea {...controlProps('reason')} className="textarea" required maxLength={500} value={reason} disabled={busy} onChange={(event) => { setReason(event.target.value); clearField('reason'); }} placeholder="For example: the teacher entered an incorrect email during testing." />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="email_correction_current_password">Your password <span className="req">*</span></label>
            <FieldError id="email_correction_current_password" message={fieldErrors.current_password} />
            <div className="input-group">
              <span className="input-icon"><Lock size={16} /></span>
              <input {...controlProps('current_password')} className="input" type={showPassword ? 'text' : 'password'} autoComplete="current-password" required value={password} disabled={busy} onChange={(event) => { setPassword(event.target.value); clearField('current_password'); }} />
              <button type="button" className="input-affix" aria-label={showPassword ? 'Hide password' : 'Show password'} onClick={() => setShowPassword((value) => !value)} disabled={busy}>{showPassword ? <EyeOff size={17} /> : <Eye size={17} />}</button>
            </div>
            <span className="field-hint">Enter your own administrator password.</span>
          </div>
          <div className="field">
            <FieldError id="email_correction_identity_confirmed" message={fieldErrors.identity_confirmed} />
            <label className="checkbox" style={{ alignItems: 'flex-start' }}><input {...controlProps('identity_confirmed')} type="checkbox" required checked={confirmed} disabled={busy} onChange={(event) => { setConfirmed(event.target.checked); clearField('identity_confirmed'); }} /> I verified this person’s identity and confirmed that they requested this email correction.</label>
          </div>
          {retry.seconds > 0 && <p className="field-hint" role="status">Try again in {retry.seconds} second(s).</p>}
          <div className="row" style={{ justifyContent: 'flex-end', flexWrap: 'wrap', gap: '.75rem' }}>
            <button type="button" className="btn btn-secondary" disabled={busy} onClick={close}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={busy || retry.seconds > 0}>{busy ? <><span className="spinner" /> Sending…</> : <><Send size={16} /> Send verification</>}</button>
          </div>
        </form>
      )}
    </Modal>
  );
}
