import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, CheckCircle2, Eye, EyeOff, UserPlus } from 'lucide-react';
import AuthLayout from './AuthLayout';
import { authApi } from '../../api/endpoints';
import { ErrorNote } from '../../components/common/ui';

const BLANK = {
  first_name: '',
  middle_name: '',
  last_name: '',
  email: '',
  contact_number: '',
  employee_no: '',
  department: '',
  role: 'teacher',
  password: '',
  confirm: '',
};

/** Mirrors the server's password policy so the UI can warn before submitting. */
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

export default function RegisterPage() {
  const navigate = useNavigate();
  const [form, setForm] = useState(BLANK);
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(null);

  const strength = useMemo(() => scorePassword(form.password), [form.password]);

  const set = (field) => (event) => setForm((current) => ({ ...current, [field]: event.target.value }));

  const submit = async (event) => {
    event.preventDefault();
    setError('');

    if (form.password !== form.confirm) {
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
      const { confirm, ...payload } = form;
      const result = await authApi.register({
        ...payload,
        middle_name: payload.middle_name || null,
        employee_no: payload.employee_no || null,
        department: payload.department || null,
      });
      setDone(result);
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
          <h2>{done.is_approved ? 'Account ready' : 'Registration received'}</h2>
          <p className="muted">{done.message}</p>
          {!done.is_approved && (
            <div className="alert alert-info" style={{ marginTop: '0.75rem', textAlign: 'left' }}>
              <span>
                You will be able to sign in as soon as an administrator approves your account.
                They usually review new requests within one school day.
              </span>
            </div>
          )}
          <button
            type="button"
            className="btn btn-primary btn-block"
            style={{ marginTop: '1.25rem' }}
            onClick={() => navigate('/login')}
          >
            Back to sign in
          </button>
        </div>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout>
      <button type="button" className="auth-back" onClick={() => navigate('/login')}>
        <ArrowLeft size={15} /> Back to sign in
      </button>

      <h2>Request a staff account</h2>
      <p className="auth-lede">
        Fill in your details. An administrator approves each request before the account
        becomes active.
      </p>

      <form className="auth-form" onSubmit={submit} noValidate>
        <ErrorNote message={error} />

        <div className="form-grid">
          <div className="field">
            <label className="field-label" htmlFor="first_name">
              First name <span className="req">*</span>
            </label>
            <input
              id="first_name"
              className="input"
              value={form.first_name}
              onChange={set('first_name')}
              required
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="last_name">
              Last name <span className="req">*</span>
            </label>
            <input
              id="last_name"
              className="input"
              value={form.last_name}
              onChange={set('last_name')}
              required
            />
          </div>
        </div>

        <div className="field">
          <label className="field-label" htmlFor="reg_email">
            School email <span className="req">*</span>
          </label>
          <input
            id="reg_email"
            className="input"
            type="email"
            placeholder="you@lbca.edu.ph"
            value={form.email}
            onChange={set('email')}
            required
          />
        </div>

        <div className="form-grid">
          <div className="field">
            <label className="field-label" htmlFor="contact">
              Contact number <span className="req">*</span>
            </label>
            <input
              id="contact"
              className="input"
              placeholder="0917 000 0000"
              value={form.contact_number}
              onChange={set('contact_number')}
              required
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="role">
              Role
            </label>
            <select id="role" className="select" value={form.role} onChange={set('role')}>
              <option value="teacher">Teacher</option>
              <option value="admin">Administrator</option>
            </select>
          </div>
        </div>

        <div className="form-grid">
          <div className="field">
            <label className="field-label" htmlFor="employee_no">
              Employee number
            </label>
            <input
              id="employee_no"
              className="input"
              placeholder="Optional"
              value={form.employee_no}
              onChange={set('employee_no')}
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="department">
              Department
            </label>
            <input
              id="department"
              className="input"
              placeholder="Optional"
              value={form.department}
              onChange={set('department')}
            />
          </div>
        </div>

        <div className="field">
          <label className="field-label" htmlFor="reg_password">
            Password <span className="req">*</span>
          </label>
          <div className="input-group">
            <input
              id="reg_password"
              className="input"
              style={{ paddingLeft: '0.75rem' }}
              type={showPassword ? 'text' : 'password'}
              autoComplete="new-password"
              value={form.password}
              onChange={set('password')}
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
          <label className="field-label" htmlFor="confirm">
            Confirm password <span className="req">*</span>
          </label>
          <input
            id="confirm"
            className={`input ${form.confirm && form.confirm !== form.password ? 'has-error' : ''}`}
            type={showPassword ? 'text' : 'password'}
            autoComplete="new-password"
            value={form.confirm}
            onChange={set('confirm')}
            required
          />
          {form.confirm && form.confirm !== form.password && (
            <span className="field-error">The passwords do not match.</span>
          )}
        </div>

        <button type="submit" className="btn btn-primary btn-lg btn-block" disabled={busy}>
          {busy ? (
            <>
              <span className="spinner" /> Submitting…
            </>
          ) : (
            <>
              <UserPlus size={18} /> Submit request
            </>
          )}
        </button>
      </form>
    </AuthLayout>
  );
}
