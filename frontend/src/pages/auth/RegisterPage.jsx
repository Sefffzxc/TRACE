import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ArrowLeft,
  Building2,
  CheckCircle2,
  Clock,
  Eye,
  EyeOff,
  Search,
  UserPlus,
} from 'lucide-react';
import AuthLayout from './AuthLayout';
import { authApi, schoolApi } from '../../api/endpoints';
import { ErrorNote } from '../../components/common/ui';

/* One deployment now serves many schools, so the first thing this form has to
   settle is which of two completely different things is happening: somebody
   joining a school that already exists, or somebody bringing a new school into
   the system. The two cannot be merged — a teacher picks a school from a list
   and waits for their own administrator, while a school head describes a school
   that nobody has described yet and waits for the system operator. */
const BLANK_TEACHER = {
  school_id: '',
  first_name: '',
  middle_name: '',
  last_name: '',
  email: '',
  contact_number: '',
  password: '',
  confirm: '',
};

/* `school_type` and `tracks_units` start unanswered rather than at the server's
   defaults. Both are questions only the school can answer, and a pre-ticked
   radio is an answer nobody gave. */
const BLANK_SCHOOL = {
  name: '',
  school_type: '',
  address: '',
  city: '',
  province: '',
  region: '',
  contact_number: '',
  contact_email: '',
  tracks_units: null,
  unit_label_singular: '',
  unit_label_plural: '',
  default_units_per_subject: '12',
  passing_mark: '75',
  grading_periods: '4',
  grading_period_label: 'Quarter',
};

const BLANK_ADMIN = {
  first_name: '',
  middle_name: '',
  last_name: '',
  email: '',
  contact_number: '',
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

/* The server wants both the singular and the plural name for a lesson unit,
   but asking twice for what is almost always the same word with an "s" reads
   like a form designed by the database. So the plural is what we ask for and
   the singular is guessed from it — then shown, editable, because the guess is
   wrong for anything irregular. */
function singularise(plural) {
  const value = plural.trim();
  if (!value) return '';
  if (/ies$/i.test(value)) return `${value.slice(0, -3)}y`;
  if (/(ses|xes|zes|ches|shes)$/i.test(value)) return value.slice(0, -2);
  if (/s$/i.test(value) && !/ss$/i.test(value)) return value.slice(0, -1);
  return value;
}

/* A 409 is the one failure the server words better than we could: it knows
   whether the email address is taken or the school is already registered, and
   in the second case it also says what to do instead. Show it verbatim. */
function readSubmitError(err) {
  if (err?.status === 409) return err.message;
  return err?.message || 'Something went wrong. Please try again.';
}

export default function RegisterPage() {
  const navigate = useNavigate();

  const [mode, setMode] = useState('teacher');
  const [teacher, setTeacher] = useState(BLANK_TEACHER);
  const [school, setSchool] = useState(BLANK_SCHOOL);
  const [admin, setAdmin] = useState(BLANK_ADMIN);

  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(null);

  /* `null` means "not fetched yet", which is different from an empty list: the
     first shows a loading note, the second means no school has been approved
     yet and the only sensible path is the administrator one. */
  const [directory, setDirectory] = useState(null);
  const [directoryError, setDirectoryError] = useState('');
  const [schoolFilter, setSchoolFilter] = useState('');

  /* Once the singular has been typed into by hand, stop overwriting it. */
  const [singularTouched, setSingularTouched] = useState(false);

  const password = mode === 'teacher' ? teacher.password : admin.password;
  const confirm = mode === 'teacher' ? teacher.confirm : admin.confirm;
  const strength = useMemo(() => scorePassword(password), [password]);

  /* Fetched lazily, but the teacher path is the default so in practice this
     runs on mount. The whole directory comes down once and is filtered in the
     browser — it is a short public list of names and cities, and a request per
     keystroke would be a worse trade than holding it in memory. */
  useEffect(() => {
    if (mode !== 'teacher' || directory !== null) return undefined;
    let cancelled = false;
    schoolApi
      .directory()
      .then((rows) => {
        if (!cancelled) setDirectory(rows || []);
      })
      .catch((err) => {
        if (!cancelled) {
          setDirectory([]);
          setDirectoryError(err.message);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [mode, directory]);

  const setTeacherField = (field) => (event) =>
    setTeacher((current) => ({ ...current, [field]: event.target.value }));
  const setSchoolField = (field) => (event) =>
    setSchool((current) => ({ ...current, [field]: event.target.value }));
  const setAdminField = (field) => (event) =>
    setAdmin((current) => ({ ...current, [field]: event.target.value }));

  const switchMode = (next) => {
    setMode(next);
    setError('');
  };

  const visibleSchools = useMemo(() => {
    const term = schoolFilter.trim().toLowerCase();
    const rows = (directory || []).filter((row) =>
      term ? `${row.name} ${row.city}`.toLowerCase().includes(term) : true,
    );
    return rows.sort((a, b) => a.name.localeCompare(b.name));
  }, [directory, schoolFilter]);

  const chosenSchool =
    (directory || []).find((row) => String(row.school_id) === String(teacher.school_id)) || null;

  /* Keep an already-chosen school in the dropdown even when the filter no
     longer matches it, so narrowing the search never silently loses the
     answer somebody already gave. */
  const schoolOptions =
    chosenSchool && !visibleSchools.some((row) => row.school_id === chosenSchool.school_id)
      ? [chosenSchool, ...visibleSchools]
      : visibleSchools;

  const setPlural = (event) => {
    const value = event.target.value;
    setSchool((current) => ({
      ...current,
      unit_label_plural: value,
      unit_label_singular: singularTouched ? current.unit_label_singular : singularise(value),
    }));
  };

  const submitTeacher = async () => {
    const result = await authApi.register({
      school_id: Number(teacher.school_id),
      role: 'teacher',
      email: teacher.email.trim(),
      password: teacher.password,
      first_name: teacher.first_name.trim(),
      middle_name: teacher.middle_name.trim() || null,
      last_name: teacher.last_name.trim(),
      contact_number: teacher.contact_number.trim(),
    });
    setDone({
      kind: 'teacher',
      ...result,
      school_name: result?.school_name || chosenSchool?.name || 'your school',
    });
  };

  const submitSchool = async () => {
    const tracks = school.tracks_units === true;
    const result = await schoolApi.register({
      /* The school's own fields are nested, because this one call creates two
         records — the school and the person who will run it — and keeping them
         apart in the payload is what makes that readable on the server. */
      school: {
        name: school.name.trim(),
        school_type: school.school_type,
        address: school.address.trim(),
        city: school.city.trim(),
        province: school.province.trim() || null,
        region: school.region.trim() || null,
        contact_number: school.contact_number.trim() || null,
        contact_email: school.contact_email.trim() || null,
        tracks_units: tracks,
        /* A school that answered "no" never sees these words anywhere, so the
           server's own defaults are the honest thing to store. */
        unit_label_singular: tracks ? school.unit_label_singular.trim() : 'Module',
        unit_label_plural: tracks ? school.unit_label_plural.trim() : 'Modules',
        default_units_per_subject: tracks ? Number(school.default_units_per_subject) : 12,
        passing_mark: Number(school.passing_mark),
        grading_periods: Number(school.grading_periods),
        grading_period_label: school.grading_period_label.trim() || 'Quarter',
      },
      first_name: admin.first_name.trim(),
      middle_name: admin.middle_name.trim() || null,
      last_name: admin.last_name.trim(),
      email: admin.email.trim(),
      contact_number: admin.contact_number.trim(),
      password: admin.password,
    });
    setDone({ kind: 'school', ...result, school_name: school.name.trim() });
  };

  const submit = async (event) => {
    event.preventDefault();
    setError('');

    if (mode === 'teacher' && !teacher.school_id) {
      setError('Please choose your school from the list.');
      return;
    }
    if (mode === 'school') {
      if (!school.school_type) {
        setError('Please say whether the school is public or private.');
        return;
      }
      if (school.tracks_units === null) {
        setError('Please answer whether teachers track progress through numbered booklets.');
        return;
      }
      if (school.tracks_units && !school.unit_label_plural.trim()) {
        setError('Please say what your school calls its booklets or modules.');
        return;
      }
    }
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
      if (mode === 'teacher') await submitTeacher();
      else await submitSchool();
    } catch (err) {
      setError(readSubmitError(err));
    } finally {
      setBusy(false);
    }
  };

  /* ── Waiting states ─────────────────────────────────────────────────────── */
  if (done?.kind === 'school') {
    return (
      <AuthLayout>
        <div className="auth-success">
          <span className="success-mark">
            <Clock size={34} />
          </span>
          <h2>Awaiting approval</h2>
          <p className="muted">{done.message}</p>
          <div className="alert alert-info" style={{ marginTop: '0.75rem', textAlign: 'left' }}>
            <Clock size={17} />
            <span>
              {done.school_name} has been registered and is now waiting for the system
              operator to approve it. Your administrator account was created alongside it and
              is activated the moment the school is approved — sign in then with the email
              address and password you just chose.
            </span>
          </div>
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
                You will be able to sign in as soon as an administrator at {done.school_name}{' '}
                approves your account. They usually review new requests within one school day.
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

  /* ── The form ───────────────────────────────────────────────────────────── */
  return (
    <AuthLayout>
      <button type="button" className="auth-back" onClick={() => navigate('/login')}>
        <ArrowLeft size={15} /> Back to sign in
      </button>

      <h2>{mode === 'school' ? 'Register your school' : 'Request a staff account'}</h2>
      <p className="auth-lede">
        {mode === 'school'
          ? 'Register your school and your administrator account.'
          : 'Your school administrator approves new accounts.'}
      </p>

      <form className="auth-form" onSubmit={submit} noValidate>
        <ErrorNote message={error} />

        <fieldset className="field" style={{ border: 0, margin: 0, padding: 0, minWidth: 0 }}>
          <legend className="field-label" style={{ padding: 0 }}>
            I am registering as
          </legend>
          <div className="stack" style={{ gap: '0.45rem', marginTop: '0.4rem' }}>
            <label className="checkbox">
              <input
                type="radio"
                name="register_mode"
                value="teacher"
                checked={mode === 'teacher'}
                onChange={() => switchMode('teacher')}
              />
              A teacher
            </label>
            <label className="checkbox">
              <input
                type="radio"
                name="register_mode"
                value="school"
                checked={mode === 'school'}
                onChange={() => switchMode('school')}
              />
              A school administrator
            </label>
          </div>
          <span className="field-hint">Administrator is only for registering a new school.</span>
        </fieldset>

        {mode === 'teacher' ? (
          <>
            <div className="field">
              <label className="field-label" htmlFor="school_filter">
                Your school <span className="req">*</span>
              </label>
              <div className="input-group">
                <span className="input-icon">
                  <Search size={16} />
                </span>
                <input
                  id="school_filter"
                  className="input"
                  placeholder="Search by name or city"
                  value={schoolFilter}
                  onChange={(event) => setSchoolFilter(event.target.value)}
                />
              </div>
              <select
                id="school_id"
                className="select"
                value={teacher.school_id}
                onChange={setTeacherField('school_id')}
                required
                aria-label="Your school"
              >
                <option value="">
                  {directory === null ? 'Loading schools…' : 'Choose your school…'}
                </option>
                {schoolOptions.map((row) => (
                  <option key={row.school_id} value={String(row.school_id)}>
                    {row.name} — {row.city || 'city not given'}
                  </option>
                ))}
              </select>
              <span className="field-hint">Not listed? Your school head must register it first.</span>
              {directory !== null && directory.length === 0 && !directoryError && (
                <div className="alert alert-info">
                  <Building2 size={17} />
                  <span>No schools yet. Register yours as a school administrator.</span>
                </div>
              )}
              <ErrorNote message={directoryError} />
              {schoolFilter.trim() && directory !== null && (
                <span className="field-hint">
                  {visibleSchools.length}{' '}
                  {visibleSchools.length === 1 ? 'school matches' : 'schools match'}{' '}
                  &ldquo;{schoolFilter.trim()}&rdquo;.
                </span>
              )}
            </div>

            <div className="form-grid">
              <div className="field">
                <label className="field-label" htmlFor="first_name">
                  First name <span className="req">*</span>
                </label>
                <input
                  id="first_name"
                  className="input"
                  value={teacher.first_name}
                  onChange={setTeacherField('first_name')}
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
                  value={teacher.last_name}
                  onChange={setTeacherField('last_name')}
                  required
                />
              </div>
            </div>

            <div className="field">
              <label className="field-label" htmlFor="middle_name">
                Middle name
              </label>
              <input
                id="middle_name"
                className="input"
                placeholder="Optional"
                value={teacher.middle_name}
                onChange={setTeacherField('middle_name')}
              />
            </div>

            <div className="field">
              <label className="field-label" htmlFor="reg_email">
                Email <span className="req">*</span>
              </label>
              <input
                id="reg_email"
                className="input"
                type="email"
                placeholder="you@school.edu.ph"
                value={teacher.email}
                onChange={setTeacherField('email')}
                required
              />
            </div>

            <div className="field">
              <label className="field-label" htmlFor="contact">
                Contact number <span className="req">*</span>
              </label>
              <input
                id="contact"
                className="input"
                placeholder="0917 000 0000"
                value={teacher.contact_number}
                onChange={setTeacherField('contact_number')}
                required
              />
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
                  value={teacher.password}
                  onChange={setTeacherField('password')}
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
                className={`input ${
                  teacher.confirm && teacher.confirm !== teacher.password ? 'has-error' : ''
                }`}
                type={showPassword ? 'text' : 'password'}
                autoComplete="new-password"
                value={teacher.confirm}
                onChange={setTeacherField('confirm')}
                required
              />
              {teacher.confirm && teacher.confirm !== teacher.password && (
                <span className="field-error">The passwords do not match.</span>
              )}
            </div>
          </>
        ) : (
          <>
            {/* ── About the school ───────────────────────────────────────── */}
            <div className="section-title">About the school</div>

            <div className="field">
              <label className="field-label" htmlFor="s_name">
                School name <span className="req">*</span>
              </label>
              <input
                id="s_name"
                className="input"
                placeholder="Saint Mary's Academy"
                value={school.name}
                onChange={setSchoolField('name')}
                required
              />
            </div>

            <fieldset className="field" style={{ border: 0, margin: 0, padding: 0, minWidth: 0 }}>
              <legend className="field-label" style={{ padding: 0 }}>
                Is the school public or private? <span className="req">*</span>
              </legend>
              <div className="stack" style={{ gap: '0.45rem', marginTop: '0.4rem' }}>
                <label className="checkbox">
                  <input
                    type="radio"
                    name="school_type"
                    value="public"
                    checked={school.school_type === 'public'}
                    onChange={setSchoolField('school_type')}
                  />
                  Public
                </label>
                <label className="checkbox">
                  <input
                    type="radio"
                    name="school_type"
                    value="private"
                    checked={school.school_type === 'private'}
                    onChange={setSchoolField('school_type')}
                  />
                  Private
                </label>
              </div>
            </fieldset>

            <div className="field">
              <label className="field-label" htmlFor="s_address">
                Address
              </label>
              <input
                id="s_address"
                className="input"
                placeholder="Street and barangay"
                value={school.address}
                onChange={setSchoolField('address')}
              />
            </div>

            <div className="form-grid">
              <div className="field">
                <label className="field-label" htmlFor="s_city">
                  City or municipality <span className="req">*</span>
                </label>
                <input
                  id="s_city"
                  className="input"
                  value={school.city}
                  onChange={setSchoolField('city')}
                  required
                />
              </div>
              <div className="field">
                <label className="field-label" htmlFor="s_province">
                  Province
                </label>
                <input
                  id="s_province"
                  className="input"
                  value={school.province}
                  onChange={setSchoolField('province')}
                />
              </div>
            </div>

            <div className="field">
              <label className="field-label" htmlFor="s_region">
                Region
              </label>
              <input
                id="s_region"
                className="input"
                placeholder="Region IV-A — optional"
                value={school.region}
                onChange={setSchoolField('region')}
              />
            </div>

            <div className="form-grid">
              <div className="field">
                <label className="field-label" htmlFor="s_contact">
                  School contact number
                </label>
                <input
                  id="s_contact"
                  className="input"
                  placeholder="(049) 000 0000 — optional"
                  value={school.contact_number}
                  onChange={setSchoolField('contact_number')}
                />
              </div>
              <div className="field">
                <label className="field-label" htmlFor="s_email">
                  School contact email
                </label>
                <input
                  id="s_email"
                  className="input"
                  type="email"
                  placeholder="office@school.edu.ph"
                  value={school.contact_email}
                  onChange={setSchoolField('contact_email')}
                />
              </div>
            </div>

            {/* ── How your school teaches ────────────────────────────────────
                The one question on this form that changes what the app looks
                like afterwards, which is why it gets this much explanation. */}
            <div className="section-title">How your school teaches</div>

            <fieldset className="field" style={{ border: 0, margin: 0, padding: 0, minWidth: 0 }}>
              <legend className="field-label" style={{ padding: 0 }}>
                Do teachers track progress through numbered booklets or modules?{' '}
                <span className="req">*</span>
              </legend>
              <div className="stack" style={{ gap: '0.45rem', marginTop: '0.4rem' }}>
                <label className="checkbox">
                  <input
                    type="radio"
                    name="tracks_units"
                    checked={school.tracks_units === false}
                    onChange={() => setSchool((current) => ({ ...current, tracks_units: false }))}
                  />
                  No — we record grades and attendance only
                </label>
                <label className="checkbox">
                  <input
                    type="radio"
                    name="tracks_units"
                    checked={school.tracks_units === true}
                    onChange={() => setSchool((current) => ({ ...current, tracks_units: true }))}
                  />
                  Yes — and we call them…
                </label>
              </div>

              {school.tracks_units === true && (
                <div className="stack" style={{ gap: '0.6rem', marginTop: '0.7rem' }}>
                  <div
                    className="row"
                    style={{ gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}
                  >
                    <input
                      className="input"
                      style={{ maxWidth: '11rem' }}
                      placeholder="PACEs"
                      aria-label="What your school calls its booklets"
                      value={school.unit_label_plural}
                      onChange={setPlural}
                      required
                    />
                    <span className="faint">,</span>
                    <input
                      className="input"
                      style={{ maxWidth: '5.5rem' }}
                      type="number"
                      min="1"
                      max="200"
                      aria-label="How many per subject"
                      value={school.default_units_per_subject}
                      onChange={setSchoolField('default_units_per_subject')}
                      required
                    />
                    <span className="faint">per subject</span>
                  </div>
                  <div className="field">
                    <label className="field-label" htmlFor="s_unit_singular">
                      And just one of them is a… <span className="req">*</span>
                    </label>
                    <input
                      id="s_unit_singular"
                      className="input"
                      style={{ maxWidth: '11rem' }}
                      placeholder="PACE"
                      value={school.unit_label_singular}
                      onChange={(event) => {
                        setSingularTouched(true);
                        setSchoolField('unit_label_singular')(event);
                      }}
                      required
                    />

                  </div>
                </div>
              )}

              <span className="field-hint">
                Most schools answer <strong>No</strong>. Choose Yes only for booklet-based
                schools such as School of Tomorrow (PACEs). You can change this later.
              </span>
            </fieldset>

            {/* ── Grading ────────────────────────────────────────────────── */}
            <div className="section-title">Grading</div>

            <div className="form-grid">
              <div className="field">
                <label className="field-label" htmlFor="s_passing">
                  Passing mark <span className="req">*</span>
                </label>
                <input
                  id="s_passing"
                  className="input"
                  type="number"
                  min="1"
                  max="100"
                  step="1"
                  value={school.passing_mark}
                  onChange={setSchoolField('passing_mark')}
                  required
                />
                <span className="field-hint">A grade below this counts as failing.</span>
              </div>
              <div className="field">
                <label className="field-label" htmlFor="s_periods">
                  Grading periods in a year <span className="req">*</span>
                </label>
                <input
                  id="s_periods"
                  className="input"
                  type="number"
                  min="1"
                  max="6"
                  step="1"
                  value={school.grading_periods}
                  onChange={setSchoolField('grading_periods')}
                  required
                />
              </div>
            </div>

            <div className="field">
              <label className="field-label" htmlFor="s_period_label">
                What you call one of them <span className="req">*</span>
              </label>
              <input
                id="s_period_label"
                className="input"
                placeholder="Quarter"
                value={school.grading_period_label}
                onChange={setSchoolField('grading_period_label')}
                required
              />
              <span className="field-hint">As on your report cards, e.g. Quarter.</span>
            </div>

            {/* ── Your account ───────────────────────────────────────────── */}
            <div className="section-title">Your account</div>

            <div className="form-grid">
              <div className="field">
                <label className="field-label" htmlFor="a_first">
                  First name <span className="req">*</span>
                </label>
                <input
                  id="a_first"
                  className="input"
                  value={admin.first_name}
                  onChange={setAdminField('first_name')}
                  required
                />
              </div>
              <div className="field">
                <label className="field-label" htmlFor="a_last">
                  Last name <span className="req">*</span>
                </label>
                <input
                  id="a_last"
                  className="input"
                  value={admin.last_name}
                  onChange={setAdminField('last_name')}
                  required
                />
              </div>
            </div>

            <div className="field">
              <label className="field-label" htmlFor="a_middle">
                Middle name
              </label>
              <input
                id="a_middle"
                className="input"
                placeholder="Optional"
                value={admin.middle_name}
                onChange={setAdminField('middle_name')}
              />
            </div>

            <div className="field">
              <label className="field-label" htmlFor="a_email">
                Your email <span className="req">*</span>
              </label>
              <input
                id="a_email"
                className="input"
                type="email"
                placeholder="you@school.edu.ph"
                value={admin.email}
                onChange={setAdminField('email')}
                required
              />
              <span className="field-hint">You sign in with this.</span>
            </div>

            <div className="field">
              <label className="field-label" htmlFor="a_contact">
                Contact number <span className="req">*</span>
              </label>
              <input
                id="a_contact"
                className="input"
                placeholder="0917 000 0000"
                value={admin.contact_number}
                onChange={setAdminField('contact_number')}
                required
              />
            </div>

            <div className="field">
              <label className="field-label" htmlFor="a_password">
                Password <span className="req">*</span>
              </label>
              <div className="input-group">
                <input
                  id="a_password"
                  className="input"
                  style={{ paddingLeft: '0.75rem' }}
                  type={showPassword ? 'text' : 'password'}
                  autoComplete="new-password"
                  value={admin.password}
                  onChange={setAdminField('password')}
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
              <label className="field-label" htmlFor="a_confirm">
                Confirm password <span className="req">*</span>
              </label>
              <input
                id="a_confirm"
                className={`input ${
                  admin.confirm && admin.confirm !== admin.password ? 'has-error' : ''
                }`}
                type={showPassword ? 'text' : 'password'}
                autoComplete="new-password"
                value={admin.confirm}
                onChange={setAdminField('confirm')}
                required
              />
              {admin.confirm && admin.confirm !== admin.password && (
                <span className="field-error">The passwords do not match.</span>
              )}
            </div>
          </>
        )}

        <button type="submit" className="btn btn-primary btn-lg btn-block" disabled={busy}>
          {busy ? (
            <>
              <span className="spinner" /> Submitting…
            </>
          ) : (
            <>
              {mode === 'school' ? <Building2 size={18} /> : <UserPlus size={18} />}
              {mode === 'school' ? 'Register school' : 'Submit request'}
            </>
          )}
        </button>
      </form>
    </AuthLayout>
  );
}
