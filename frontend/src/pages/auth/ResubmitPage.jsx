import { useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { ArrowLeft, CheckCircle2, Clock, Send } from 'lucide-react';
import AuthLayout from './AuthLayout';
import { authApi, schoolApi } from '../../api/endpoints';
import CaptchaField from '../../components/common/CaptchaField';
import OtpInput from '../../components/common/OtpInput';
import { ErrorNote, FieldError } from '../../components/common/ui';
import useCaptchaConfig from '../../hooks/useCaptchaConfig';
import useRetryAfter from '../../hooks/useRetryAfter';

const ACCOUNT_FIELDS = [
  ['first_name', 'First name', 100, true],
  ['last_name', 'Last name', 100, true],
  ['middle_name', 'Middle name', 100],
  ['contact_number', 'Contact number', 20, true],
];
const SCHOOL_FIELDS = [
  ['name', 'School name', 200, true], ['short_name', 'Short name', 40],
  ['address', 'Street and barangay', 255], ['city', 'City or municipality', 100, true],
  ['province', 'Province', 100], ['region', 'Region', 100],
  ['contact_number', 'School contact number', 30], ['contact_email', 'School contact email', 254],
];
const SCHOOL_DEFAULTS = {
  school_type: 'private', tracks_units: false, unit_label_singular: 'Module',
  unit_label_plural: 'Modules', default_units_per_subject: 12, passing_mark: 75,
  grading_periods: 4, grading_period_label: 'Quarter',
};

function validateForm(form) {
  const errors = {};
  for (const input of form.querySelectorAll('input[id], select[id]')) {
    if (!input.willValidate || ['checkbox', 'radio'].includes(input.type)) continue;
    const value = input.value.trim();
    const label = input.labels?.[0]?.textContent.replace(/\*/g, '').trim() || 'This field';
    if (input.required && !value) errors[input.id] = `${label} is required.`;
    else if (input.validity.typeMismatch) errors[input.id] = 'Enter a valid email address.';
    else if (input.validity.badInput || input.validity.rangeUnderflow || input.validity.rangeOverflow || input.validity.stepMismatch) {
      errors[input.id] = `Enter a whole number between ${input.min} and ${input.max}.`;
    } else if (value && input.minLength > 0 && value.length < input.minLength) {
      errors[input.id] = `${label} must have at least ${input.minLength} characters.`;
    }
  }
  return errors;
}

/** The email code grants permission to edit one rejected application only.
 * Keep its token in component memory; it must never become a signed-in session. */
export default function ResubmitPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const captcha = useCaptchaConfig();
  const retry = useRetryAfter();
  const [step, setStep] = useState('email');
  const [email, setEmail] = useState(() => location.state?.email || '');
  const [code, setCode] = useState('');
  const [challengeId, setChallengeId] = useState('');
  const [verification, setVerification] = useState(null);
  const [application, setApplication] = useState(null);
  const [account, setAccount] = useState({});
  const [school, setSchool] = useState({});
  const [changeSchool, setChangeSchool] = useState(false);
  const [schoolId, setSchoolId] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [directory, setDirectory] = useState([]);
  const [directoryError, setDirectoryError] = useState('');
  const [directoryAttempt, setDirectoryAttempt] = useState(0);
  const [directoryLoading, setDirectoryLoading] = useState(false);
  const [captchaToken, setCaptchaToken] = useState('');
  const [captchaReset, setCaptchaReset] = useState(0);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [error, setError] = useState('');
  const [fieldErrors, setFieldErrors] = useState({});
  const [focusRequest, setFocusRequest] = useState(0);
  const [successMessage, setSuccessMessage] = useState('');
  const formRef = useRef(null);
  const headingRef = useRef(null);
  const errorRef = useRef(null);

  useEffect(() => {
    headingRef.current?.focus({ preventScroll: true });
    headingRef.current?.scrollIntoView({ block: 'start' });
  }, [step]);

  useEffect(() => {
    if (!focusRequest) return;
    const invalid = formRef.current?.querySelector('[aria-invalid="true"]');
    const target = invalid?.closest('.field') || errorRef.current;
    (invalid || errorRef.current)?.focus({ preventScroll: true });
    target?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'center' });
  }, [focusRequest]);

  useEffect(() => {
    if (step !== 'edit' || !application?.can_change_school || !changeSchool) return undefined;
    let live = true;
    setDirectoryLoading(true);
    setDirectoryError('');
    schoolApi.directory().then((rows) => {
      if (live) setDirectory(rows || []);
    }).catch((err) => {
      if (live) setDirectoryError(err.message);
    }).finally(() => { if (live) setDirectoryLoading(false); });
    return () => { live = false; };
  }, [application?.can_change_school, step, changeSchool, directoryAttempt]);

  const clearField = (id) => setFieldErrors((current) => {
    const next = { ...current };
    delete next[id];
    return next;
  });
  const flagErrors = (fields) => {
    setFieldErrors(fields);
    setFocusRequest((current) => current + 1);
  };
  const begin = () => {
    if (busyRef.current || retry.seconds > 0) return false;
    busyRef.current = true;
    setBusy(true);
    setError('');
    setFieldErrors({});
    return true;
  };
  const finish = () => { busyRef.current = false; setBusy(false); };
  const restart = () => {
    if (busyRef.current) return;
    setVerification(null);
    setApplication(null);
    setAccount({});
    setSchool({});
    setCode('');
    setChallengeId('');
    setError('');
    setFieldErrors({});
    setCaptchaToken('');
    setCaptchaReset((current) => current + 1);
    setStep('email');
  };
  const handleError = (err, stage) => {
    retry.start(err.retryAfter);
    const fields = {};
    if (err.payload?.detail?.code?.startsWith('captcha_')) fields.captcha = err.message;
    if (err.status === 422 && Array.isArray(err.payload?.detail)) {
      for (const issue of err.payload.detail) {
        const loc = (issue.loc || []).filter((key) => key !== 'body');
        const key = loc[0] === 'school' ? `school_${loc[1]}` : loc[0] === 'captcha_token' ? 'captcha' : loc[0];
        if (key !== 'token' && key !== 'challenge_id') fields[key] = (issue.msg || 'Please check this field.').replace(/^Value error, /, '');
      }
    }
    if (stage === 'edit' && [401, 403, 410].includes(err.status)) setVerification(null);
    setError(err.retryAfter > 0 ? 'Please wait before trying again.' : err.message);
    flagErrors(fields);
  };

  const requestCode = async (event) => {
    event.preventDefault();
    if (busyRef.current || retry.seconds > 0) return;
    const fields = validateForm(event.currentTarget);
    if (captcha.loading || captcha.error || !captcha.config) {
      setError('Security verification settings are unavailable. Please retry below.');
      flagErrors(fields);
      return;
    }
    if (captcha.config.enabled && !captchaToken) fields.captcha = 'Please complete the human verification.';
    if (Object.keys(fields).length) return flagErrors(fields);
    if (!begin()) return;
    try {
      const result = await authApi.requestResubmission(email.trim(), captchaToken);
      setEmail(email.trim());
      setChallengeId(result.challenge_id);
      setCode('');
      setStep('code');
    } catch (err) { handleError(err, 'email'); }
    finally {
      setCaptchaToken('');
      setCaptchaReset((current) => current + 1);
      finish();
    }
  };

  const verifyCode = async (event) => {
    event.preventDefault();
    if (!/^\d{6}$/.test(code)) return flagErrors({ code: 'Enter all six digits.' });
    if (!begin()) return;
    try {
      const result = await authApi.verifyResubmission(email, challengeId, code);
      if (!result.resubmission_token || !result.application) throw new Error('Verification could not be completed. Request a new code.');
      setApplication(result.application);
      setVerification({ token: result.resubmission_token, expiresAt: Date.now() + result.expires_in * 1000 });
      setAccount(Object.fromEntries(ACCOUNT_FIELDS.map(([key]) => [key, result.application[key] || ''])));
      setSchool({ ...SCHOOL_DEFAULTS, ...result.application.school });
      setChangeSchool(false);
      setConfirmed(false);
      setSchoolId(String(result.application.school_id));
      setCode('');
      setStep('edit');
    } catch (err) { handleError(err, 'code'); }
    finally { finish(); }
  };

  const submit = async (event) => {
    event.preventDefault();
    if (!verification || Date.now() >= verification.expiresAt) {
      setVerification(null);
      setError('Your verification has expired. Verify your email again to continue.');
      return flagErrors({});
    }
    const fields = validateForm(event.currentTarget);
    if (changeSchool && (!schoolId || schoolId === String(application.school_id))) fields.school_id = 'Choose a different school.';
    if (changeSchool && !confirmed) fields.confirm_school_change = 'Confirm the school change before submitting.';
    if (Object.keys(fields).length) return flagErrors(fields);
    if (!begin()) return;
    try {
      const payload = {
        token: verification.token,
        ...Object.fromEntries(ACCOUNT_FIELDS.map(([key]) => [key, account[key].trim() || null])),
        // Preserve existing staff metadata with older API deployments too.
        employee_no: application.employee_no,
        department: application.department,
        ...(application.role === 'teacher' && changeSchool ? { school_id: Number(schoolId), confirm_school_change: true } : {}),
      };
      if (application.role === 'admin') {
        payload.school = {
          ...school,
          ...Object.fromEntries(SCHOOL_FIELDS.map(([key]) => [key, (school[key] || '').trim() || (['name', 'address', 'city'].includes(key) ? '' : null)])),
          default_units_per_subject: Number(school.default_units_per_subject),
          passing_mark: Number(school.passing_mark), grading_periods: Number(school.grading_periods),
          unit_label_singular: school.unit_label_singular.trim(), unit_label_plural: school.unit_label_plural.trim(),
          grading_period_label: school.grading_period_label.trim(),
        };
      }
      const result = await authApi.resubmitApplication(payload);
      setSuccessMessage(result.message || 'Your corrected application is pending review.');
      setVerification(null);
      setApplication(null);
      setAccount({});
      setSchool({});
      setStep('done');
    } catch (err) { handleError(err, 'edit'); }
    finally { finish(); }
  };

  const controlProps = (id) => ({
    id, 'aria-invalid': Boolean(fieldErrors[id]) || undefined,
    'aria-describedby': fieldErrors[id] ? `${id}-error` : undefined,
  });
  const input = (key, label, maxLength, required, isSchool = false, extra = {}) => {
    const id = isSchool ? `school_${key}` : key;
    return <div className="field" key={id}>
      <label className="field-label" htmlFor={id}>{label}{required && <> <span className="req">*</span></>}</label>
      <FieldError id={id} message={fieldErrors[id]} />
      <input {...controlProps(id)} className="input" maxLength={maxLength} required={required}
        minLength={key === 'contact_number' && !isSchool ? 7 : key === 'name' ? 3 : key === 'city' ? 2 : undefined}
        type={key === 'contact_email' || key === 'email' ? 'email' : 'text'}
        value={(isSchool ? school : account)[key] ?? ''} disabled={busy}
        onChange={(event) => {
          (isSchool ? setSchool : setAccount)((current) => ({ ...current, [key]: event.target.value }));
          clearField(id);
        }} {...extra} />
    </div>;
  };
  const selectedSchool = directory.find((row) => String(row.school_id) === schoolId);
  const blocked = busy || retry.seconds > 0;

  if (step === 'done') return <AuthLayout><div className="auth-success">
    <span className="success-mark"><CheckCircle2 size={34} /></span>
    <h2 ref={headingRef} tabIndex={-1}>Application resubmitted</h2>
    <p className="muted">{successMessage}</p>
    <p className="field-hint">Your existing account is now pending approval. You will receive an email when a decision is made. Keep using your original email and password.</p>
    <button type="button" className="btn btn-primary btn-block" onClick={() => navigate('/login')}>Back to sign in</button>
  </div></AuthLayout>;

  return <AuthLayout>
    <button type="button" className="auth-back" disabled={busy} onClick={() => navigate('/login')}><ArrowLeft size={15} /> Back to sign in</button>
    <h2 ref={headingRef} tabIndex={-1}>{step === 'email' ? 'Correct and resubmit' : step === 'code' ? 'Verify your email' : 'Correct your application'}</h2>
    <p className="auth-lede">{step === 'email' ? 'Use the email from your rejected application. Verify ownership before correcting your details.' : step === 'code' ? `If ${email} has an eligible rejected application, a six-digit code is on its way. It is valid for five minutes.` : 'Review the rejection reason, correct your details, and request approval again.'}</p>
    <form ref={formRef} className="auth-form registration-form" onSubmit={step === 'email' ? requestCode : step === 'code' ? verifyCode : submit} noValidate>
      {error && <div ref={errorRef} tabIndex={-1}><ErrorNote message={error} /></div>}
      {step === 'email' && <>
        <div className="field">
          <label className="field-label" htmlFor="email">Account email <span className="req">*</span></label>
          <FieldError id="email" message={fieldErrors.email} />
          <input {...controlProps('email')} className="input" type="email" autoComplete="email" maxLength={254} required value={email} disabled={busy}
            onChange={(event) => { setEmail(event.target.value); clearField('email'); }} />
        </div>
        {captcha.loading && <p className="field-hint" role="status">Loading security verification…</p>}
        {captcha.error && <div className="field"><ErrorNote message={captcha.error} /><button type="button" className="btn btn-secondary" onClick={captcha.retry}>Retry security settings</button></div>}
        {captcha.config?.enabled && <CaptchaField siteKey={captcha.config.site_key} action="resubmit" token={captchaToken}
          resetKey={captchaReset} error={fieldErrors.captcha} onTokenChange={(token) => { setCaptchaToken(token); if (token) clearField('captcha'); }} />}
      </>}
      {step === 'code' && <div className="field">
        <span className="field-label" id="code-label">Six-digit email code</span>
        <FieldError id="code" message={fieldErrors.code} />
        <div role="group" aria-labelledby="code-label" aria-describedby={fieldErrors.code ? 'code-error' : undefined}>
          <OtpInput value={code} onChange={(value) => { setCode(value); clearField('code'); }} disabled={blocked} />
        </div>
        <p className="field-hint">Enter the code, then select Verify email. If you requested another code, use the newest one.</p>
      </div>}
      {step === 'edit' && application && <>
        <div className="alert alert-info"><Clock size={17} /><span><strong>Last rejection reason</strong><br />{application.rejection_reason || 'No reason was recorded for this earlier decision.'}</span></div>
        <div className="field"><span className="field-label">Verified account</span><p>{application.email}</p><span className="field-hint">{application.role === 'teacher' ? 'Teacher' : 'School administrator'} · {application.school_name}</span></div>
        {ACCOUNT_FIELDS.map((args) => input(...args))}
        {application.role === 'teacher' && <>
          <div className="field"><span className="field-label">Current school</span><p>{application.school_name}</p></div>
          {application.can_change_school && <div className="field">
            <label className="checkbox"><input type="checkbox" checked={changeSchool} disabled={busy} onChange={(event) => { setChangeSchool(event.target.checked); setConfirmed(false); clearField('school_id'); clearField('confirm_school_change'); }} /> I selected the wrong school</label>
            <span className="field-hint">Choose the correct school. Its administrator must review your application again.</span>
          </div>}
          {application.can_change_school && changeSchool && <>
            <div className="field"><label className="field-label" htmlFor="school_id">Correct school <span className="req">*</span></label><FieldError id="school_id" message={fieldErrors.school_id} />
              <select {...controlProps('school_id')} className="select" value={schoolId} required disabled={busy || directoryLoading} onChange={(event) => { setSchoolId(event.target.value); setConfirmed(false); clearField('school_id'); clearField('confirm_school_change'); }}>
                <option value="">{directoryLoading ? 'Loading schools…' : 'Choose the correct school…'}</option>
                <option value={String(application.school_id)}>{application.school_name} (current)</option>
                {directory.filter((row) => row.school_id !== application.school_id).map((row) => <option key={row.school_id} value={row.school_id}>{row.name}{row.city ? ` — ${row.city}` : ''}</option>)}
              </select>
              {directoryError && <><ErrorNote message={directoryError} /><button type="button" className="btn btn-secondary" onClick={() => setDirectoryAttempt((current) => current + 1)}>Retry school list</button></>}
            </div>
            {schoolId !== String(application.school_id) && selectedSchool && <div className="field">
              <FieldError id="confirm_school_change" message={fieldErrors.confirm_school_change} />
              <label className="checkbox"><input {...controlProps('confirm_school_change')} type="checkbox" checked={confirmed} disabled={busy} onChange={(event) => { setConfirmed(event.target.checked); clearField('confirm_school_change'); }} /> I confirm changing my school from {application.school_name} to {selectedSchool.name}.</label>
            </div>}
          </>}
        </>}
        {application.role === 'admin' && <>
          <div className="section-title">School details</div>
          {SCHOOL_FIELDS.map(([key, label, maxLength, required]) => input(key, label, maxLength, required, true))}
          <div className="field"><label className="field-label" htmlFor="school_school_type">School type</label><FieldError id="school_school_type" message={fieldErrors.school_school_type} /><select {...controlProps('school_school_type')} className="select" disabled={busy} value={school.school_type} onChange={(event) => setSchool((current) => ({ ...current, school_type: event.target.value }))}><option value="public">Public</option><option value="private">Private</option></select></div>
          <div className="field"><label className="checkbox"><input type="checkbox" checked={school.tracks_units} disabled={busy} onChange={(event) => setSchool((current) => ({ ...current, tracks_units: event.target.checked }))} /> Teachers track numbered booklets or modules</label></div>
          {school.tracks_units && <>
            {input('unit_label_singular', 'Booklet or module name (singular)', 40, true, true)}
            {input('unit_label_plural', 'Booklet or module name (plural)', 40, true, true)}
            {input('default_units_per_subject', 'Booklets or modules per subject', undefined, true, true, { type: 'number', min: 1, max: 200, step: 1 })}
          </>}
          {input('passing_mark', 'Passing mark', undefined, true, true, { type: 'number', min: 1, max: 100, step: 'any' })}
          {input('grading_periods', 'Grading periods', undefined, true, true, { type: 'number', min: 1, max: 6, step: 1 })}
          {input('grading_period_label', 'Grading period name', 30, true, true)}
        </>}
        {application.history?.length > 0 && <details className="field"><summary className="field-label" style={{ cursor: 'pointer' }}>Previous decisions ({application.history.length})</summary>
          <ol className="stack" style={{ paddingLeft: '1.25rem', marginTop: '.75rem', overflowWrap: 'anywhere' }}>{application.history.map((item, index) => <li key={item.id || index}>
            <strong>{(item.action || 'Decision').replaceAll('_', ' ')}</strong>{item.created_at && <span className="field-hint"> · {new Date(item.created_at).toLocaleString()}</span>}
            {item.reason && <p>{item.reason}</p>}{typeof item.detail === 'string' && item.detail && <p className="field-hint">{item.detail}</p>}
          </li>)}</ol>
        </details>}
        {!verification && <p className="field-hint">Verify your email again before submitting these changes.</p>}
      </>}
      {retry.seconds > 0 && <p className="field-hint" role="status">Try again in {retry.seconds} second(s).</p>}
      <button type="submit" className="btn btn-primary btn-lg btn-block" disabled={blocked || (step === 'email' && (captcha.loading || Boolean(captcha.error))) || (step === 'code' && !/^\d{6}$/.test(code)) || (step === 'edit' && !verification)}>
        {busy ? <><span className="spinner" /> {step === 'email' ? 'Sending…' : step === 'code' ? 'Verifying…' : 'Submitting…'}</> : step === 'email' ? <><Send size={17} /> Send verification code</> : step === 'code' ? 'Verify email' : 'Resubmit for approval'}
      </button>
    </form>
    {step !== 'email' && <p className="auth-switch" style={{ marginTop: '1.25rem' }}><button type="button" className="link-button" disabled={blocked} onClick={restart}>{step === 'code' ? 'Request a new code or use a different email' : 'Verify email again'}</button></p>}
  </AuthLayout>;
}
