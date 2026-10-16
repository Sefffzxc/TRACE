import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Eye, EyeOff, Lock, LogIn, Mail } from 'lucide-react';
import AuthLayout from './AuthLayout';
import { authApi } from '../../api/endpoints';
import { useAuth } from '../../context/AuthContext';
import { ErrorNote } from '../../components/common/ui';

export default function LoginPage() {
  const navigate = useNavigate();
  const { signIn } = useAuth();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    setError('');
    setBusy(true);
    try {
      const result = await authApi.login(email.trim(), password);

      if (result.requires_2fa) {
        // Unknown device — the server has issued a 6-digit code.
        navigate('/verify', {
          state: { userId: result.user_id, email: email.trim(), debugOtp: result.debug_otp },
        });
        return;
      }

      const profile = signIn(result);
      navigate(profile?.requires_password_change ? '/account?change=1' : '/dashboard', {
        replace: true,
      });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthLayout>
      <h2>Welcome back</h2>
      <p className="auth-lede">Sign in to continue monitoring your students&rsquo; progress.</p>

      <form className="auth-form" onSubmit={submit} noValidate>
        <ErrorNote message={error} />

        <div className="field">
          <label className="field-label" htmlFor="email">
            School email
          </label>
          <div className="input-group">
            <span className="input-icon">
              <Mail size={16} />
            </span>
            <input
              id="email"
              className="input"
              type="email"
              autoComplete="username"
              placeholder="you@school.edu.ph"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
              autoFocus
            />
          </div>
        </div>

        <div className="field">
          <label className="field-label" htmlFor="password">
            Password
          </label>
          <div className="input-group">
            <span className="input-icon">
              <Lock size={16} />
            </span>
            <input
              id="password"
              className="input"
              type={showPassword ? 'text' : 'password'}
              autoComplete="current-password"
              placeholder="Enter your password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
            <button
              type="button"
              className="input-affix"
              onClick={() => setShowPassword((value) => !value)}
              aria-label={showPassword ? 'Hide password' : 'Show password'}
            >
              {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
        </div>

        <div className="auth-row">
          <span className="faint" style={{ fontSize: 'var(--text-xs)' }}>
            Protected by two-factor sign-in
          </span>
          <button type="button" className="link-button" onClick={() => navigate('/forgot')}>
            Forgot password?
          </button>
        </div>

        <button type="submit" className="btn btn-primary btn-lg btn-block" disabled={busy}>
          {busy ? (
            <>
              <span className="spinner" /> Signing in…
            </>
          ) : (
            <>
              <LogIn size={18} /> Sign in
            </>
          )}
        </button>
      </form>

      <div className="auth-divider">New to the system?</div>

      <p className="auth-switch">
        <button type="button" className="link-button" onClick={() => navigate('/register')}>
          Request a staff account
        </button>{' '}
        — an administrator reviews every request.
      </p>
    </AuthLayout>
  );
}
