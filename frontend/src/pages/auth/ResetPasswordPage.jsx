import { useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { CheckCircle2, Eye, EyeOff, Lock } from 'lucide-react';
import AuthLayout from './AuthLayout';
import { authApi } from '../../api/endpoints';
import { ErrorNote } from '../../components/common/ui';

function scorePassword(value) {
  if (!value) return { score: 0, label: 'Enter a password' };
  const checks = [
    value.length >= 8,
    /[A-Z]/.test(value),
    /[a-z]/.test(value) && /\d/.test(value),
    /[^A-Za-z0-9]/.test(value),
  ];
  const score = checks.filter(Boolean).length;
  const labels = [
    'Too short',
    'Weak — add a capital letter',
    'Fair — add numbers',
    'Good — add a symbol',
    'Strong',
  ];
  return { score, label: labels[score] };
}

export default function ResetPasswordPage() {
  const navigate = useNavigate();
  const { state } = useLocation();

  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [show, setShow] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  const strength = useMemo(() => scorePassword(password), [password]);

  useEffect(() => {
    if (!state?.token) navigate('/forgot', { replace: true });
  }, [state, navigate]);

  const submit = async (event) => {
    event.preventDefault();
    setError('');

    if (password !== confirm) {
      setError('The two passwords do not match.');
      return;
    }
    if (strength.score < 4) {
      setError(
        'Password must be at least 8 characters with an uppercase letter, a lowercase letter, a number and a symbol.',
      );
      return;
    }

    setBusy(true);
    try {
      await authApi.resetPassword(state.token, password, confirm);
      setDone(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return (
      <AuthLayout>
        <div className="auth-success">
          <span className="success-mark">
            <CheckCircle2 size={34} />
          </span>
          <h2>Password updated</h2>
          <p className="muted">You can now sign in with your new password.</p>
          <button
            type="button"
            className="btn btn-primary btn-block"
            style={{ marginTop: '1.25rem' }}
            onClick={() => navigate('/login')}
          >
            Go to sign in
          </button>
        </div>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout>
      <h2>Choose a new password</h2>
      <p className="auth-lede">
        Setting a new password for <strong>{state?.email}</strong>.
      </p>

      <form className="auth-form" onSubmit={submit} noValidate>
        <ErrorNote message={error} />

        <div className="field">
          <label className="field-label" htmlFor="new_password">
            New password
          </label>
          <div className="input-group">
            <span className="input-icon">
              <Lock size={16} />
            </span>
            <input
              id="new_password"
              className="input"
              type={show ? 'text' : 'password'}
              autoComplete="new-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
              autoFocus
            />
            <button
              type="button"
              className="input-affix"
              onClick={() => setShow((value) => !value)}
              aria-label={show ? 'Hide password' : 'Show password'}
            >
              {show ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
          <div className="strength" data-score={strength.score}>
            <div className="strength-bars">
              <i />
              <i />
              <i />
              <i />
            </div>
            <span className="strength-label">{strength.label}</span>
          </div>
        </div>

        <div className="field">
          <label className="field-label" htmlFor="confirm_password">
            Confirm new password
          </label>
          <input
            id="confirm_password"
            className={`input ${confirm && confirm !== password ? 'has-error' : ''}`}
            type={show ? 'text' : 'password'}
            autoComplete="new-password"
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
            required
          />
          {confirm && confirm !== password && (
            <span className="field-error">The passwords do not match.</span>
          )}
        </div>

        <button type="submit" className="btn btn-primary btn-lg btn-block" disabled={busy}>
          {busy ? (
            <>
              <span className="spinner" /> Saving…
            </>
          ) : (
            'Update password'
          )}
        </button>
      </form>
    </AuthLayout>
  );
}
