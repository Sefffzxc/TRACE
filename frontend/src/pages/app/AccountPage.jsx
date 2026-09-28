import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Camera, Eye, EyeOff, Save, ShieldCheck, Trash2 } from 'lucide-react';
import { Avatar, ErrorNote, PageHead } from '../../components/common/ui';
import { staffApi } from '../../api/endpoints';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

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

export default function AccountPage() {
  const { user, refreshProfile } = useAuth();
  const toast = useToast();
  const [params] = useSearchParams();
  const fileRef = useRef(null);

  const [tab, setTab] = useState(
    params.get('tab') === 'security' || params.get('change') ? 'security' : 'profile',
  );

  const [profile, setProfile] = useState({
    first_name: '',
    middle_name: '',
    last_name: '',
    contact_number: '',
    department: '',
  });
  const [profileBusy, setProfileBusy] = useState(false);
  const [profileError, setProfileError] = useState('');

  const [passwords, setPasswords] = useState({ current: '', next: '', confirm: '' });
  const [showPassword, setShowPassword] = useState(false);
  const [passwordBusy, setPasswordBusy] = useState(false);
  const [passwordError, setPasswordError] = useState('');

  const strength = useMemo(() => scorePassword(passwords.next), [passwords.next]);

  useEffect(() => {
    if (!user) return;
    setProfile({
      first_name: user.first_name || '',
      middle_name: user.middle_name || '',
      last_name: user.last_name || '',
      contact_number: user.contact_number || '',
      department: user.department || '',
    });
  }, [user]);

  const saveProfile = async (event) => {
    event.preventDefault();
    setProfileError('');
    setProfileBusy(true);
    try {
      await staffApi.updateMe({
        ...profile,
        middle_name: profile.middle_name || null,
        department: profile.department || null,
      });
      await refreshProfile();
      toast.success('Profile updated.');
    } catch (err) {
      setProfileError(err.message);
    } finally {
      setProfileBusy(false);
    }
  };

  const changePassword = async (event) => {
    event.preventDefault();
    setPasswordError('');

    if (passwords.next !== passwords.confirm) {
      setPasswordError('The two new passwords do not match.');
      return;
    }
    if (strength.score < 4) {
      setPasswordError(
        'Password must be at least 8 characters with an uppercase letter, a lowercase letter, a number and a symbol.',
      );
      return;
    }

    setPasswordBusy(true);
    try {
      await staffApi.changePassword(passwords.current, passwords.next);
      await refreshProfile();
      setPasswords({ current: '', next: '', confirm: '' });
      toast.success('Password changed.');
    } catch (err) {
      setPasswordError(err.message);
    } finally {
      setPasswordBusy(false);
    }
  };

  const uploadPhoto = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      await staffApi.uploadPhoto(file);
      await refreshProfile();
      toast.success('Photo updated.');
    } catch (err) {
      toast.error(err.message);
    } finally {
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const removePhoto = async () => {
    try {
      await staffApi.removePhoto();
      await refreshProfile();
      toast.success('Photo removed.');
    } catch (err) {
      toast.error(err.message);
    }
  };

  return (
    <div className="page">
      <PageHead title="Account settings" subtitle="Your details, photo and password." />

      {user?.requires_password_change && (
        <div className="alert alert-warning">
          <ShieldCheck size={17} />
          <span>
            An administrator issued you a temporary password. Please set a new one before
            carrying on.
          </span>
        </div>
      )}

      <div className="tabs">
        <button
          type="button"
          className={`tab ${tab === 'profile' ? 'is-active' : ''}`}
          onClick={() => setTab('profile')}
        >
          Profile
        </button>
        <button
          type="button"
          className={`tab ${tab === 'security' ? 'is-active' : ''}`}
          onClick={() => setTab('security')}
        >
          Security
        </button>
      </div>

      {tab === 'profile' && (
        <section className="grid" style={{ gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1.4fr)' }}>
          <div className="card">
            <div className="card-header">
              <h2>Photo</h2>
            </div>
            <div className="card-body">
              <div className="photo-editor">
                <Avatar
                  name={`${user?.first_name || ''} ${user?.last_name || ''}`}
                  src={user?.profile_pic}
                  size="xl"
                />
                <div className="stack" style={{ gap: '.6rem' }}>
                  <div className="row" style={{ gap: '.5rem' }}>
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() => fileRef.current?.click()}
                    >
                      <Camera size={15} /> Upload
                    </button>
                    {user?.profile_pic && (
                      <button type="button" className="btn btn-ghost btn-sm" onClick={removePhoto}>
                        <Trash2 size={15} /> Remove
                      </button>
                    )}
                  </div>
                  <span className="hint">PNG, JPG, WEBP or GIF, up to 4 MB. Square images look best.</span>
                </div>
              </div>
              <input
                ref={fileRef}
                type="file"
                accept="image/png,image/jpeg,image/webp,image/gif"
                className="visually-hidden"
                onChange={uploadPhoto}
              />
            </div>
          </div>

          <div className="card">
            <div className="card-header">
              <h2>Your details</h2>
            </div>
            <form className="card-body" onSubmit={saveProfile}>
              <ErrorNote message={profileError} />
              <div className="form-grid" style={{ marginTop: profileError ? '1rem' : 0 }}>
                <div className="field">
                  <label className="field-label" htmlFor="p_first">
                    First name
                  </label>
                  <input
                    id="p_first"
                    className="input"
                    value={profile.first_name}
                    onChange={(event) => setProfile((c) => ({ ...c, first_name: event.target.value }))}
                  />
                </div>
                <div className="field">
                  <label className="field-label" htmlFor="p_middle">
                    Middle name
                  </label>
                  <input
                    id="p_middle"
                    className="input"
                    value={profile.middle_name}
                    onChange={(event) => setProfile((c) => ({ ...c, middle_name: event.target.value }))}
                  />
                </div>
                <div className="field">
                  <label className="field-label" htmlFor="p_last">
                    Last name
                  </label>
                  <input
                    id="p_last"
                    className="input"
                    value={profile.last_name}
                    onChange={(event) => setProfile((c) => ({ ...c, last_name: event.target.value }))}
                  />
                </div>
                <div className="field">
                  <label className="field-label" htmlFor="p_contact">
                    Contact number
                  </label>
                  <input
                    id="p_contact"
                    className="input"
                    value={profile.contact_number}
                    onChange={(event) => setProfile((c) => ({ ...c, contact_number: event.target.value }))}
                  />
                </div>
                <div className="field">
                  <label className="field-label" htmlFor="p_dept">
                    Department
                  </label>
                  <input
                    id="p_dept"
                    className="input"
                    value={profile.department}
                    onChange={(event) => setProfile((c) => ({ ...c, department: event.target.value }))}
                  />
                </div>
                <div className="field">
                  <label className="field-label" htmlFor="p_email">
                    Email (cannot be changed here)
                  </label>
                  <input id="p_email" className="input" value={user?.email || ''} disabled />
                </div>
              </div>

              <div className="row-end" style={{ marginTop: '1.25rem' }}>
                <button type="submit" className="btn btn-primary" disabled={profileBusy}>
                  <Save size={16} /> {profileBusy ? 'Saving…' : 'Save changes'}
                </button>
              </div>
            </form>
          </div>
        </section>
      )}

      {tab === 'security' && (
        <section className="grid grid-2">
          <div className="card">
            <div className="card-header">
              <div>
                <h2>Change password</h2>
                <div className="card-subtitle">
                  At least 8 characters with upper and lower case, a number and a symbol.
                </div>
              </div>
            </div>
            <form className="card-body stack" onSubmit={changePassword}>
              <ErrorNote message={passwordError} />

              <div className="field">
                <label className="field-label" htmlFor="cur_pw">
                  Current password
                </label>
                <div className="input-group">
                  <input
                    id="cur_pw"
                    className="input"
                    style={{ paddingLeft: '0.75rem' }}
                    type={showPassword ? 'text' : 'password'}
                    autoComplete="current-password"
                    value={passwords.current}
                    onChange={(event) => setPasswords((c) => ({ ...c, current: event.target.value }))}
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

              <div className="field">
                <label className="field-label" htmlFor="new_pw">
                  New password
                </label>
                <input
                  id="new_pw"
                  className="input"
                  type={showPassword ? 'text' : 'password'}
                  autoComplete="new-password"
                  value={passwords.next}
                  onChange={(event) => setPasswords((c) => ({ ...c, next: event.target.value }))}
                  required
                />
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
                <label className="field-label" htmlFor="conf_pw">
                  Confirm new password
                </label>
                <input
                  id="conf_pw"
                  className={`input ${
                    passwords.confirm && passwords.confirm !== passwords.next ? 'has-error' : ''
                  }`}
                  type={showPassword ? 'text' : 'password'}
                  autoComplete="new-password"
                  value={passwords.confirm}
                  onChange={(event) => setPasswords((c) => ({ ...c, confirm: event.target.value }))}
                  required
                />
              </div>

              <div className="row-end">
                <button type="submit" className="btn btn-primary" disabled={passwordBusy}>
                  {passwordBusy ? 'Updating…' : 'Update password'}
                </button>
              </div>
            </form>
          </div>

          <div className="card">
            <div className="card-header">
              <h2>Account status</h2>
            </div>
            <div className="card-body">
              <div className="detail-list">
                <div className="detail-row">
                  <span className="k">Role</span>
                  <span className="v" style={{ textTransform: 'capitalize' }}>
                    {user?.role}
                  </span>
                </div>
                <div className="detail-row">
                  <span className="k">Status</span>
                  <span className="v">
                    <span className="badge badge-low">{user?.account_status}</span>
                  </span>
                </div>
                <div className="detail-row">
                  <span className="k">Employee number</span>
                  <span className="v">{user?.employee_no || '—'}</span>
                </div>
                <div className="detail-row">
                  <span className="k">Last sign-in</span>
                  <span className="v">
                    {user?.last_login_at ? new Date(user.last_login_at).toLocaleString() : '—'}
                  </span>
                </div>
                <div className="detail-row">
                  <span className="k">Member since</span>
                  <span className="v">
                    {user?.created_at ? new Date(user.created_at).toLocaleDateString() : '—'}
                  </span>
                </div>
              </div>

              <div className="alert alert-info" style={{ marginTop: '1.25rem' }}>
                <ShieldCheck size={17} />
                <span>
                  Signing in from a new device asks for a six-digit code. That device is then
                  trusted for 90 days.
                </span>
              </div>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
