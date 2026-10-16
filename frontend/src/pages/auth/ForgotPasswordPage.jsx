import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, KeyRound, Mail, Send } from 'lucide-react';
import AuthLayout from './AuthLayout';
import OtpInput from '../../components/common/OtpInput';
import { authApi } from '../../api/endpoints';
import { ErrorNote } from '../../components/common/ui';

/**
 * Two steps in one screen: ask for the email, then take the code that was
 * sent to it. Verifying the code hands back a short-lived reset token, which
 * the next screen exchanges for a new password.
 */
export default function ForgotPasswordPage() {
  const navigate = useNavigate();
  const [step, setStep] = useState('email');
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [debugOtp, setDebugOtp] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const requestCode = async (event) => {
    event.preventDefault();
    setError('');
    setBusy(true);
    try {
      const result = await authApi.forgotPassword(email.trim());
      setDebugOtp(result?.debug_otp || null);
      setStep('code');
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const verify = async (submitted) => {
    const value = submitted || code;
    if (value.length !== 6) {
      setError('Enter all six digits.');
      return;
    }
    setError('');
    setBusy(true);
    try {
      const result = await authApi.verifyResetOtp(email.trim(), value);
      navigate('/reset', { state: { token: result.token, email: email.trim() } });
    } catch (err) {
      setError(err.message);
      setCode('');
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthLayout>
      <button
        type="button"
        className="auth-back"
        onClick={() => (step === 'code' ? setStep('email') : navigate('/login'))}
      >
        <ArrowLeft size={15} /> {step === 'code' ? 'Use a different email' : 'Back to sign in'}
      </button>

      {step === 'email' ? (
        <>
          <h2>Reset your password</h2>
          <p className="auth-lede">
            Enter the school email on your account and we&rsquo;ll send a six-digit code to it.
          </p>

          <form className="auth-form" onSubmit={requestCode} noValidate>
            <ErrorNote message={error} />
            <div className="field">
              <label className="field-label" htmlFor="forgot_email">
                School email
              </label>
              <div className="input-group">
                <span className="input-icon">
                  <Mail size={16} />
                </span>
                <input
                  id="forgot_email"
                  className="input"
                  type="email"
                  placeholder="you@school.edu.ph"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  required
                  autoFocus
                />
              </div>
            </div>

            <button type="submit" className="btn btn-primary btn-lg btn-block" disabled={busy}>
              {busy ? (
                <>
                  <span className="spinner" /> Sending…
                </>
              ) : (
                <>
                  <Send size={17} /> Send reset code
                </>
              )}
            </button>
          </form>
        </>
      ) : (
        <>
          <h2>Enter the code</h2>
          <p className="auth-lede">
            If <strong>{email}</strong> is registered, a six-digit code is on its way. It is
            valid for ten minutes.
          </p>

          <form
            className="auth-form"
            onSubmit={(event) => {
              event.preventDefault();
              verify();
            }}
          >
            <ErrorNote message={error} />

            {debugOtp && (
              <div className="dev-code">
                <KeyRound size={15} />
                <span>Development mode — your code is</span>
                <strong>{debugOtp}</strong>
              </div>
            )}

            <OtpInput value={code} onChange={setCode} onComplete={verify} disabled={busy} />

            <button
              type="submit"
              className="btn btn-primary btn-lg btn-block"
              disabled={busy || code.length !== 6}
            >
              {busy ? (
                <>
                  <span className="spinner" /> Verifying…
                </>
              ) : (
                'Continue'
              )}
            </button>
          </form>

          <p className="auth-switch" style={{ marginTop: '1.25rem' }}>
            <button
              type="button"
              className="link-button"
              onClick={() => requestCode({ preventDefault() {} })}
              disabled={busy}
            >
              Send another code
            </button>
          </p>
        </>
      )}
    </AuthLayout>
  );
}
