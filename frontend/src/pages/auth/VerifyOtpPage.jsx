import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { ArrowLeft, KeyRound, ShieldCheck } from 'lucide-react';
import AuthLayout from './AuthLayout';
import OtpInput from '../../components/common/OtpInput';
import { authApi } from '../../api/endpoints';
import { useAuth } from '../../context/AuthContext';
import { ErrorNote } from '../../components/common/ui';

/**
 * Step two of signing in from a device the school has not seen before.
 */
export default function VerifyOtpPage() {
  const navigate = useNavigate();
  const { state } = useLocation();
  const { signIn } = useAuth();

  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [seconds, setSeconds] = useState(600);

  // Nobody should land here directly — send them back to sign in.
  useEffect(() => {
    if (!state?.userId) navigate('/login', { replace: true });
  }, [state, navigate]);

  useEffect(() => {
    if (seconds <= 0) return undefined;
    const timer = setInterval(() => setSeconds((value) => value - 1), 1000);
    return () => clearInterval(timer);
  }, [seconds]);

  const verify = async (submitted) => {
    const value = submitted || code;
    if (value.length !== 6) {
      setError('Enter all six digits.');
      return;
    }
    setError('');
    setBusy(true);
    try {
      const result = await authApi.verifyOtp(state.userId, value);
      const profile = signIn(result);
      navigate(profile?.requires_password_change ? '/account?change=1' : '/dashboard', {
        replace: true,
      });
    } catch (err) {
      setError(err.message);
      setCode('');
    } finally {
      setBusy(false);
    }
  };

  const minutes = Math.floor(Math.max(0, seconds) / 60);
  const remainder = String(Math.max(0, seconds) % 60).padStart(2, '0');

  return (
    <AuthLayout>
      <button type="button" className="auth-back" onClick={() => navigate('/login')}>
        <ArrowLeft size={15} /> Back to sign in
      </button>

      <h2>Verify it&rsquo;s you</h2>
      <p className="auth-lede">
        This device hasn&rsquo;t been used before, so we sent a six-digit code to{' '}
        <strong>{state?.email}</strong>. It is valid for ten minutes.
      </p>

      <form
        className="auth-form"
        onSubmit={(event) => {
          event.preventDefault();
          verify();
        }}
      >
        <ErrorNote message={error} />

        {state?.debugOtp && (
          <div className="dev-code">
            <KeyRound size={15} />
            <span>Development mode — your code is</span>
            <strong>{state.debugOtp}</strong>
          </div>
        )}

        <OtpInput value={code} onChange={setCode} onComplete={verify} disabled={busy} />

        <div className="otp-hint">
          {seconds > 0 ? (
            <span>
              Code expires in{' '}
              <strong>
                {minutes}:{remainder}
              </strong>
            </span>
          ) : (
            <span>This code has expired — sign in again to get a new one.</span>
          )}
        </div>

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
            <>
              <ShieldCheck size={18} /> Verify and continue
            </>
          )}
        </button>
      </form>

      <p className="auth-switch" style={{ marginTop: '1.25rem' }}>
        Didn&rsquo;t get a code?{' '}
        <button type="button" className="link-button" onClick={() => navigate('/login')}>
          Sign in again
        </button>
      </p>
    </AuthLayout>
  );
}
