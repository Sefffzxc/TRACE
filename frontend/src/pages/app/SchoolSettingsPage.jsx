import { useEffect, useMemo, useState } from 'react';
import { BookOpen, Building2, GraduationCap, Info, Save, ShieldCheck } from 'lucide-react';
import AddressFields from '../../components/common/AddressFields';
import { EmptyState, ErrorNote, PageHead } from '../../components/common/ui';
import { schoolApi } from '../../api/endpoints';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

/* Same mapping the staff list uses for account status, so a pending school and
   a pending teacher look the same shade of amber wherever they appear. */
const STATUS_BADGE = {
  approved: 'badge-low',
  pending: 'badge-moderate',
  rejected: 'badge-critical',
  deactivated: 'badge-neutral',
};

/* Fields the server stores as NULL rather than an empty string: blanking one
   out has to clear it, not write "" into a column that means "unknown". */
const NULLABLE = new Set([
  'short_name',
  'province',
  'region',
  'contact_number',
  'contact_email',
]);

/* Held as strings while they are being typed — a half-typed number is not a
   number — and converted on the way out. */
const NUMERIC = new Set(['default_units_per_subject', 'passing_mark', 'grading_periods']);

function formFromSchool(school) {
  return {
    name: school?.name || '',
    short_name: school?.short_name || '',
    address: school?.address || '',
    city: school?.city || '',
    province: school?.province || '',
    region: school?.region || '',
    contact_number: school?.contact_number || '',
    contact_email: school?.contact_email || '',
    tracks_units: Boolean(school?.tracks_units),
    unit_label_singular: school?.unit_label_singular || 'Module',
    unit_label_plural: school?.unit_label_plural || 'Modules',
    default_units_per_subject: String(school?.default_units_per_subject ?? 12),
    passing_mark: String(school?.passing_mark ?? 75),
    grading_periods: String(school?.grading_periods ?? 4),
    grading_period_label: school?.grading_period_label || 'Quarter',
  };
}

/** Turns the form's strings into the types the API expects. */
function serialise(form) {
  const out = {};
  Object.entries(form).forEach(([key, value]) => {
    if (key === 'tracks_units') out[key] = Boolean(value);
    else if (NUMERIC.has(key)) out[key] = Number(value);
    else {
      const trimmed = String(value).trim();
      out[key] = NULLABLE.has(key) ? trimmed || null : trimmed;
    }
  });
  return out;
}

export default function SchoolSettingsPage() {
  const { school, loadSchool, isSuperAdmin } = useAuth();
  const toast = useToast();

  const [form, setForm] = useState(() => formFromSchool(school));
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  /* The loaded school is the baseline the form is measured against, so the
     diff resets itself when `loadSchool()` brings back the saved version. */
  const baseline = useMemo(() => formFromSchool(school), [school]);
  useEffect(() => {
    setForm(baseline);
  }, [baseline]);

  /* Only what actually moved is sent. The server records a settings change in
     the audit log by field name, so an untouched field in the payload would
     read as an edit nobody made. */
  const changes = useMemo(() => {
    const next = serialise(form);
    const previous = serialise(baseline);
    const diff = {};
    Object.keys(next).forEach((key) => {
      if (next[key] !== previous[key]) diff[key] = next[key];
    });
    return diff;
  }, [form, baseline]);

  const dirtyCount = Object.keys(changes).length;

  const set = (field) => (event) =>
    setForm((current) => ({ ...current, [field]: event.target.value }));

  const save = async (event) => {
    event.preventDefault();
    setError('');

    if (form.name.trim().length < 3) {
      setError('School name: at least 3 characters.');
      return;
    }
    if (form.city.trim().length < 2) {
      setError('Please give the city or municipality.');
      return;
    }
    if (form.tracks_units && (!form.unit_label_singular.trim() || !form.unit_label_plural.trim())) {
      setError('Enter what you call your booklets (singular and plural).');
      return;
    }
    const passing = Number(form.passing_mark);
    if (!Number.isFinite(passing) || passing < 1 || passing > 100) {
      setError('Passing mark: 1 to 100.');
      return;
    }
    const periods = Number(form.grading_periods);
    if (!Number.isInteger(periods) || periods < 1 || periods > 6) {
      setError('Grading periods: 1 to 6.');
      return;
    }
    const units = Number(form.default_units_per_subject);
    if (form.tracks_units && (!Number.isInteger(units) || units < 1 || units > 200)) {
      setError('Units per subject: 1 to 200.');
      return;
    }
    if (!dirtyCount) {
      toast.info('Nothing has changed yet.');
      return;
    }

    setBusy(true);
    try {
      await schoolApi.updateMine(changes);
      await loadSchool();
      toast.success('School settings saved.');
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  /* The system operator has no school on purpose — they approve schools and
     never look inside one — so there is nothing on this page for them. */
  if (!school) {
    return (
      <div className="page">
        <PageHead title="School settings" />
        <div className="card">
          <div className="card-body">
            <EmptyState
              icon={Building2}
              title={isSuperAdmin ? 'No school to edit' : 'School settings are not available'}
              description={
                isSuperAdmin
                  ? 'Your account manages schools. Open the Schools list.'
                  : 'Could not load your school. Try again.'
              }
            />
          </div>
        </div>
      </div>
    );
  }

  const unitPlural = form.unit_label_plural.trim() || 'units';

  return (
    <div className="page">
      <PageHead
        title="School settings"
        subtitle="Your school's details and grading."
      />

      <form className="stack" onSubmit={save} noValidate>
        <ErrorNote message={error} />

        {/* ── Identity and contact ─────────────────────────────────────────── */}
        <div className="card">
          <div className="card-header">
            <div>
              <h2>School details</h2>
              <div className="card-subtitle">Name and address</div>
            </div>
          </div>
          <div className="card-body stack">
            <div className="form-grid">
              <div className="field span-2">
                <label className="field-label" htmlFor="sc_name">
                  School name <span className="req">*</span>
                </label>
                <input
                  id="sc_name"
                  className="input"
                  value={form.name}
                  onChange={set('name')}
                  required
                />
              </div>
              <div className="field">
                <label className="field-label" htmlFor="sc_short">
                  Short name
                </label>
                <input
                  id="sc_short"
                  className="input"
                  placeholder="Used in headers"
                  value={form.short_name}
                  onChange={set('short_name')}
                />
              </div>
            </div>
            <AddressFields
              idPrefix="sc"
              value={form}
              onChange={(patch) => setForm((current) => ({ ...current, ...patch }))}
            />
            <div className="form-grid">
              <div className="field">
                <label className="field-label" htmlFor="sc_contact">
                  Contact number
                </label>
                <input
                  id="sc_contact"
                  className="input"
                  value={form.contact_number}
                  onChange={set('contact_number')}
                />
              </div>
              <div className="field">
                <label className="field-label" htmlFor="sc_email">
                  Contact email
                </label>
                <input
                  id="sc_email"
                  className="input"
                  type="email"
                  value={form.contact_email}
                  onChange={set('contact_email')}
                />
              </div>
            </div>
          </div>
        </div>

        {/* ── The one setting that changes what the app looks like ─────────── */}
        <div className="card">
          <div className="card-header">
            <div>
              <h2>How your school teaches</h2>
              <div className="card-subtitle">Booklets or modules (e.g. PACEs)</div>
            </div>
          </div>
          <div className="card-body stack">
            <label className="checkbox">
              <input
                type="checkbox"
                checked={form.tracks_units}
                onChange={(event) =>
                  setForm((current) => ({ ...current, tracks_units: event.target.checked }))
                }
              />
              We use numbered booklets or modules
            </label>

            {/* Saying what the switch does, in the two states it can be in, is
                the whole point of this card: it is the difference between a
                teacher having a lesson-progress screen and not having one. */}
            {form.tracks_units ? (
              <div className="alert alert-info">
                <BookOpen size={17} />
                <span>
                  Teachers record progress in {unitPlural}.
                </span>
              </div>
            ) : (
              <div className="alert alert-info">
                <Info size={17} />
                <span>
                  Teachers record grades only.
                </span>
              </div>
            )}

            {baseline.tracks_units && !form.tracks_units && (
              <div className="alert alert-warning">
                <Info size={17} />
                <span>
                  This hides progress screens. Saved progress is kept.
                </span>
              </div>
            )}

            {form.tracks_units && (
              <div className="form-grid">
                <div className="field">
                  <label className="field-label" htmlFor="sc_unit_singular">
                    You call one of them a <span className="req">*</span>
                  </label>
                  <input
                    id="sc_unit_singular"
                    className="input"
                    placeholder="PACE"
                    value={form.unit_label_singular}
                    onChange={set('unit_label_singular')}
                    required
                  />
                </div>
                <div className="field">
                  <label className="field-label" htmlFor="sc_unit_plural">
                    And several of them <span className="req">*</span>
                  </label>
                  <input
                    id="sc_unit_plural"
                    className="input"
                    placeholder="PACEs"
                    value={form.unit_label_plural}
                    onChange={set('unit_label_plural')}
                    required
                  />
                </div>
                <div className="field">
                  <label className="field-label" htmlFor="sc_units_per">
                    How many per subject <span className="req">*</span>
                  </label>
                  <input
                    id="sc_units_per"
                    className="input"
                    type="number"
                    min="1"
                    max="200"
                    step="1"
                    value={form.default_units_per_subject}
                    onChange={set('default_units_per_subject')}
                    required
                  />
                  <span className="field-hint">Default for new subjects.</span>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* ── Grading ──────────────────────────────────────────────────────── */}
        <div className="card">
          <div className="card-header">
            <div>
              <h2>Grading</h2>
              <div className="card-subtitle">Passing mark and grading periods</div>
            </div>
          </div>
          <div className="card-body">
            <div className="form-grid">
              <div className="field">
                <label className="field-label" htmlFor="sc_passing">
                  Passing mark <span className="req">*</span>
                </label>
                <input
                  id="sc_passing"
                  className="input"
                  type="number"
                  min="1"
                  max="100"
                  step="1"
                  value={form.passing_mark}
                  onChange={set('passing_mark')}
                  required
                />
                <span className="field-hint">
                  A grade below this is flagged as failing across the app.
                </span>
              </div>
              <div className="field">
                <label className="field-label" htmlFor="sc_periods">
                  Grading periods in a year <span className="req">*</span>
                </label>
                <input
                  id="sc_periods"
                  className="input"
                  type="number"
                  min="1"
                  max="6"
                  step="1"
                  value={form.grading_periods}
                  onChange={set('grading_periods')}
                  required
                />
              </div>
              <div className="field">
                <label className="field-label" htmlFor="sc_period_label">
                  What you call one of them <span className="req">*</span>
                </label>
                <input
                  id="sc_period_label"
                  className="input"
                  placeholder="Quarter"
                  value={form.grading_period_label}
                  onChange={set('grading_period_label')}
                  required
                />
                <span className="field-hint">
                  &ldquo;Quarter&rdquo;, &ldquo;Grading&rdquo;, &ldquo;Semester&rdquo;.
                </span>
              </div>
            </div>
          </div>
        </div>

        <div className="row-end">
          {dirtyCount > 0 && (
            <span className="muted" style={{ fontSize: 'var(--text-sm)' }}>
              {dirtyCount} unsaved {dirtyCount === 1 ? 'change' : 'changes'}
            </span>
          )}
          <button type="submit" className="btn btn-primary" disabled={busy || !dirtyCount}>
            <Save size={16} /> {busy ? 'Saving…' : 'Save changes'}
          </button>
        </div>
      </form>

      {/* ── Facts this page cannot change ──────────────────────────────────── */}
      <div className="card">
        <div className="card-header">
          <div>
            <h2>Registration</h2>
            <div className="card-subtitle">Only the system operator can change these.</div>
          </div>
        </div>
        <div className="card-body">
          <div className="detail-list">
            <div className="detail-row">
              <span className="k">Public or private</span>
              <span className="v" style={{ textTransform: 'capitalize' }}>
                {school.school_type || '—'}
              </span>
            </div>
            <div className="detail-row">
              <span className="k">Approval status</span>
              <span className="v">
                <span className={`badge ${STATUS_BADGE[school.status] || 'badge-neutral'}`}>
                  {school.status}
                </span>
              </span>
            </div>
            {school.rejection_reason && (
              <div className="detail-row">
                <span className="k">Operator&rsquo;s note</span>
                <span className="v">{school.rejection_reason}</span>
              </div>
            )}
            <div className="detail-row">
              <span className="k">Registered</span>
              <span className="v">
                {school.created_at ? new Date(school.created_at).toLocaleDateString() : '—'}
              </span>
            </div>
            <div className="detail-row">
              <span className="k">Approved</span>
              <span className="v">
                {school.approved_at ? new Date(school.approved_at).toLocaleDateString() : '—'}
              </span>
            </div>
          </div>

        </div>
      </div>

    </div>
  );
}
