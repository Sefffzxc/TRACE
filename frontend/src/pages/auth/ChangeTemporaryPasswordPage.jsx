import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Eye, EyeOff, Lock, LogOut, ShieldCheck } from 'lucide-react';
import AuthLayout from './AuthLayout';
import { staffApi } from '../../api/endpoints';
import { useAuth } from '../../context/AuthContext';
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
  const labels = ['Too short', 'Weak — add a capital letter', 'Fair — add numbers', 'Good — add a symbol', 'Strong'];
  return { score, label: labels[score] };
}

export default function ChangeTemporaryPasswordPage() {
  const navigate = useNavigate();
  const { user, signOut, updateProfile } = useAuth();
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [show, setShow] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const strength = useMemo(() => scorePassword(password), [password]);

  const submit = async (event) => {
    event.preventDefault();
    if (busy || signingOut) return;
    setError('');
    if (password !== confirm) {
      setError('The two passwords do not match.');
      return;
    }
    if (strength.score < 4) {
      setError('Use 8+ characters with upper and lower case, a number and a symbol.');
      return;
    }
    if (password.length > 128) {
      setError('Password must be no more than 128 characters.');
      return;
    }
    setBusy(true);
    try {
      const result = await staffApi.completeRequiredPasswordChange(password, confirm);
      updateProfile(result.user);
      navigate('/dashboard', { replace: true });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const logout = async () => {
    if (busy || signingOut) return;
    setSigningOut(true);
    await signOut();
    navigate('/login', { replace: true });
  };

  return (
    <AuthLayout>
      <h2>Change your temporary password</h2>
      <p className="auth-lede">
        Your administrator issued a temporary password for <strong>{user?.email}</strong>.
        {' '}Create your own password before accessing TRACE.
      </p>

      <form className="auth-form" onSubmit={submit} noValidate>
        <ErrorNote message={error} />

        <div className="field">
          <label className="field-label" htmlFor="new_password">New password</label>
          <div className="input-group">
            <span className="input-icon"><Lock size={16} /></span>
            <input
              id="new_password"
              className="input"
              type={show ? 'text' : 'password'}
              autoComplete="new-password"
              maxLength={128}
              value={password}
              onChange={(event) => { setPassword(event.target.value); setError(''); }}
              disabled={busy || signingOut}
              required
              autoFocus
              aria-describedby="password_requirements"
            />
            <button
              type="button"
              className="input-affix"
              onClick={() => setShow((value) => !value)}
              aria-label={show ? 'Hide passwords' : 'Show passwords'}
            >
              {show ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
          <div className="strength" data-score={strength.score}>
            <div className="strength-bars"><i /><i /><i /><i /></div>
            <span className="strength-label">{strength.label}</span>
          </div>
          <span className="field-hint" id="password_requirements">
            Use at least 8 characters, including uppercase and lowercase letters, a number and a symbol.
          </span>
        </div>

        <div className="field">
          <label className="field-label" htmlFor="confirm_password">Confirm new password</label>
          <input
            id="confirm_password"
            className={`input ${confirm && confirm !== password ? 'has-error' : ''}`}
            type={show ? 'text' : 'password'}
            autoComplete="new-password"
            maxLength={128}
            value={confirm}
            onChange={(event) => { setConfirm(event.target.value); setError(''); }}
            disabled={busy || signingOut}
            required
            aria-invalid={Boolean(confirm && confirm !== password)}
            aria-describedby={confirm && confirm !== password ? 'password_mismatch' : undefined}
          />
          {confirm && confirm !== password && (
            <span className="field-error" id="password_mismatch">The passwords do not match.</span>
          )}
        </div>

        <button type="submit" className="btn btn-primary btn-lg btn-block" disabled={busy || signingOut}>
          {busy ? <><span className="spinner" /> Saving…</> : <><ShieldCheck size={18} /> Change password and continue</>}
        </button>
        <button type="button" className="btn btn-secondary btn-block" onClick={logout} disabled={busy || signingOut}>
          <LogOut size={16} /> {signingOut ? 'Logging out…' : 'Log out'}
        </button>
      </form>
    </AuthLayout>
  );
}
