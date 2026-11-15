import { useCallback, useEffect, useState } from 'react';
import {
  BookOpen,
  CalendarDays,
  Layers,
  Pencil,
  Plus,
  Trash2,
  UserCheck,
} from 'lucide-react';
import {
  ConfirmDialog,
  EmptyState,
  ErrorNote,
  Modal,
  PageHead,
  TableSkeleton,
} from '../../components/common/ui';
import {
  adviserApi,
  assignmentApi,
  gradeLevelApi,
  schoolYearApi,
  sectionApi,
  staffApi,
  subjectApi,
} from '../../api/endpoints';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { GradeSectionsModal, GradeSectionsTable } from './GradeSections';

const TABS = [
  { key: 'years', label: 'School years', icon: CalendarDays },
  { key: 'levels', label: 'Grades & sections', icon: Layers },
  { key: 'subjects', label: 'Subjects', icon: BookOpen },
  { key: 'advisers', label: 'Section advisers', icon: UserCheck },
  { key: 'assignments', label: 'Subject teachers', icon: BookOpen },
];

/* Each tab describes its own table and form, so adding a new one later means
   adding a descriptor rather than another page. */
export default function ClassManagementPage() {
  const toast = useToast();

  // A dashboard card can open a tab directly (?tab=sections).
  const [tab, setTab] = useState(() => {
    const wanted = new URLSearchParams(window.location.search).get('tab');
    if (wanted === 'sections') return 'levels';
    return TABS.some((item) => item.key === wanted) ? wanted : 'years';
  });
  const [data, setData] = useState({
    years: [],
    levels: [],
    sections: [],
    subjects: [],
    assignments: [],
    advisers: [],
    teachers: [],
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(null);
  const [deleting, setDeleting] = useState(null);
  const [reviewing, setReviewing] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [years, levels, sections, subjects, assignments, teachers, advisers] = await Promise.all([
        schoolYearApi.list(),
        gradeLevelApi.list(),
        sectionApi.list(),
        subjectApi.list(),
        assignmentApi.list(),
        staffApi.list({ role: 'teacher' }),
        adviserApi.list(),
      ]);
      setData({
        years: years || [],
        levels: levels || [],
        sections: sections || [],
        subjects: subjects || [],
        assignments: assignments || [],
        teachers: teachers || [],
        advisers: advisers || [],
      });
      setError('');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const apiFor = {
    years: schoolYearApi,
    levels: { ...gradeLevelApi, create: gradeLevelApi.createWithSections, update: gradeLevelApi.updateWithSections },
    subjects: subjectApi,
    assignments: assignmentApi,
    advisers: adviserApi,
  }[tab];

  const idField = {
    years: 'school_year_id',
    levels: 'grade_level_id',
    subjects: 'subject_id',
    assignments: 'assignment_id',
    advisers: 'section_id',
  }[tab];

  const save = async (payload, existing) => {
    // Several assignments at once: create them one by one and stop at the
    // first problem, telling the form how many went through.
    if (Array.isArray(payload)) {
      setBusy(true);
      let created = 0;
      try {
        for (const item of payload) {
          await apiFor.create(item);
          created += 1;
        }
        toast.success(created === 1 ? 'Created.' : `${created} assignments created.`);
        setEditing(null);
      } catch (err) {
        toast.error(
          created
            ? `${created} created, but the next one failed: ${err.message} Fix the remaining rows and try again.`
            : err.message,
        );
      } finally {
        setBusy(false);
        load();
      }
      return created;
    }
    setBusy(true);
    try {
      if (tab === 'advisers') {
        await adviserApi.set(existing?.section_id || payload.section_id, { teacher_id: payload.teacher_id });
        toast.success(existing ? 'Section adviser updated.' : 'Section adviser assigned.');
      } else if (existing) {
        await apiFor.update(existing[idField], payload);
        toast.success('Changes saved.');
      } else {
        await apiFor.create(payload);
        toast.success('Created.');
      }
      setEditing(null);
      load();
    } catch (err) {
      toast.error(err.message);
      if (tab === 'advisers') load();
    } finally {
      setBusy(false);
    }
  };

  const review = async (subjectIds) => {
    setBusy(true);
    try {
      await assignmentApi.review(reviewing.assignment_id, { subject_ids: subjectIds });
      toast.success('Teaching subjects confirmed. Existing grades and scores were preserved.');
      setReviewing(null);
      load();
    } catch (err) {
      toast.error(err.message);
      load();
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await apiFor.remove(deleting[idField]);
      toast.success('Removed.');
      setDeleting(null);
      load();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusy(false);
    }
  };

  const counts = {
    years: data.years.length,
    levels: data.levels.length,
    subjects: data.subjects.length,
    assignments: data.assignments.length,
    advisers: data.advisers.length,
  };

  return (
    <div className="page">
      <PageHead
        title="Class management"
        subtitle="Manage academic structure, section advisers and subject teachers."
        actions={
          <button type="button" className="btn btn-primary" onClick={() => setEditing({})}>
            <Plus size={16} /> Add{' '}
            {{ years: 'school year', levels: 'grade & sections', subjects: 'subject', assignments: 'subject teacher', advisers: 'section adviser' }[tab]}
          </button>
        }
      />

      <ErrorNote message={error} />

      <div className="setup-layout">
        <nav className="setup-nav">
          {TABS.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.key}
                type="button"
                className={tab === item.key ? 'is-active' : ''}
                onClick={() => setTab(item.key)}
              >
                <Icon size={16} />
                {item.label}
                <span className="count">{counts[item.key]}</span>
              </button>
            );
          })}
        </nav>

        <div className="card">
          {tab === 'advisers' && <div className="card-body"><p className="muted">Advisers can view every subject in their section. They can edit scores only for subjects assigned separately under Subject teachers.</p></div>}
          {tab === 'assignments' && <div className="card-body"><p className="muted">Assign one teacher to each section and subject. Adviser access is managed separately and does not reserve any teaching subjects.</p>{data.assignments.some((row) => row.needs_subject_review || row.subject_id == null) && <ErrorNote message="Older All subjects assignments need review. Confirm the actual teaching subjects; existing grades and scores will be kept." />}</div>}
          <div className="card-body flush">
            {loading ? (
              <TableSkeleton rows={5} columns={4} />
            ) : (
              <SetupTable
                tab={tab}
                data={data}
                onEdit={setEditing}
                onDelete={setDeleting}
                onReview={setReviewing}
              />
            )}
          </div>
        </div>
      </div>

      {editing && tab === 'levels' && (
        <GradeSectionsModal
          record={editing.grade_level_id ? editing : null}
          data={data} busy={busy} onSave={save} onClose={() => setEditing(null)}
        />
      )}

      {editing && tab !== 'levels' && (
        <SetupModal
          tab={tab}
          record={editing[idField] ? editing : null}
          data={data}
          busy={busy}
          onSave={save}
          onClose={() => setEditing(null)}
        />
      )}

      {deleting && (
        <ConfirmDialog
          title="Remove this record?"
          message={tab === 'advisers' ? 'This removes advisory viewing access. Subject teaching assignments and saved grades will remain.' : tab === 'assignments' ? 'This removes this teaching assignment. Advisory access and saved grades will remain.' : 'This is blocked if anything still uses it.'}
          confirmLabel="Remove"
          busy={busy}
          onConfirm={remove}
          onClose={() => setDeleting(null)}
        />
      )}
      {reviewing && <AssignmentReviewModal record={reviewing} data={data} busy={busy} onSave={review} onClose={() => setReviewing(null)} />}
    </div>
  );
}

/* ── Tables ──────────────────────────────────────────────────────────────── */
function SetupTable({ tab, data, onEdit, onDelete, onReview }) {
  // Booklets per subject only mean something at a school that tracks units.
  const { tracksUnits, unitLabelPlural } = useAuth();
  const wrap = (headers, rows) => (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            {headers.map((header) => (
              <th key={header} className={header === 'Actions' ? 'actions' : ''}>
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
  );

  const actions = (record) => (
    <td className="actions">
      {tab !== 'assignments' && (
        <button type="button" className="btn-icon" title="Edit" onClick={() => onEdit(record)}>
          <Pencil size={15} />
        </button>
      )}
      <button type="button" className="btn-icon" title="Remove" onClick={() => onDelete(record)}>
        <Trash2 size={15} />
      </button>
    </td>
  );

  if (tab === 'years') {
    if (!data.years.length) {
      return <EmptyState icon={CalendarDays} title="No school years yet" description="Add one to start enrolling students." />;
    }
    return wrap(
      ['School year', 'Status', 'Actions'],
      data.years.map((row) => (
        <tr key={row.school_year_id}>
          <td className="cell-strong">{row.year}</td>
          <td>
            <span className={`badge ${row.is_current ? 'badge-low' : 'badge-neutral'}`}>
              {row.is_current ? 'Current' : 'Past'}
            </span>
          </td>
          {actions(row)}
        </tr>
      )),
    );
  }

  if (tab === 'levels') {
    return <GradeSectionsTable data={data} onEdit={onEdit} onDelete={onDelete} />;
  }

  if (tab === 'subjects') {
    if (!data.subjects.length) return <EmptyState icon={BookOpen} title="No subjects yet" />;
    return wrap(
      ['Subject', 'Grade level', ...(tracksUnits ? [unitLabelPlural] : []), 'Status', 'Actions'],
      data.subjects.map((row) => (
        <tr key={row.subject_id}>
          <td className="cell-strong">{row.subject_name}</td>
          <td>{row.grade_level_name}</td>
          {tracksUnits && <td>{row.total_paces}</td>}
          <td>
            <span className={`badge ${row.is_active ? 'badge-low' : 'badge-neutral'}`}>
              {row.is_active ? 'In use' : 'Not in use'}
            </span>
          </td>
          {actions(row)}
        </tr>
      )),
    );
  }

  if (tab === 'advisers') {
    if (!data.advisers.length) return <EmptyState icon={UserCheck} title="No section advisers yet" description="Choose an adviser to give them viewing access to all subjects in their section." />;
    return wrap(['Section', 'Adviser', 'Access', 'Actions'], data.advisers.map((row) => (
      <tr key={row.section_id}>
        <td className="cell-strong">{row.section_name}{row.grade_level_name ? ` — ${row.grade_level_name}` : ''}</td>
        <td>{row.teacher_name}</td>
        <td><span className="badge badge-neutral">View all subjects</span></td>
        {actions(row)}
      </tr>
    )));
  }

  if (!data.assignments.length) {
    return (
      <EmptyState
        icon={UserCheck}
        title="No subject teachers assigned yet"
        description="Assign the teacher who handles each subject. Section advisers are managed separately."
      />
    );
  }
  return wrap(
    ['Teacher', 'Section', 'Subject', 'Access', 'Actions'],
    data.assignments.map((row) => (
      <tr key={row.assignment_id}>
        <td className="cell-strong">{row.teacher_name}</td>
        <td>{row.section_name}{row.grade_level_name ? ` — ${row.grade_level_name}` : ''}</td>
        <td className="muted">{row.subject_name || 'All subjects'}</td>
        <td>
          <span className={`badge ${row.subject_id == null ? 'badge-brand' : 'badge-neutral'}`}>
            {row.subject_id == null ? 'Review needed' : 'Edit assigned subject'}
          </span>
        </td>
        <td className="actions">
          {(row.needs_subject_review || row.subject_id == null) && <button type="button" className="btn btn-secondary btn-sm" onClick={() => onReview(row)}>Review</button>}
          <button type="button" className="btn-icon" title="Remove" onClick={() => onDelete(row)}><Trash2 size={15} /></button>
        </td>
      </tr>
    )),
  );
}

/* ── Forms ───────────────────────────────────────────────────────────────── */
function SetupModal({ tab, record, data, busy, onSave, onClose }) {
  const { tracksUnits, unitLabelPlural } = useAuth();
  const [form, setForm] = useState(() => defaultsFor(tab, record));
  const eligibleTeachers = data.teachers.filter((teacher) => teacher.role === 'teacher'
    && teacher.is_active && teacher.is_approved && teacher.account_status === 'approved');
  const conflictFor = (row, sectionId = row.section_id, subjectId = row.subject_id) => {
    if (!sectionId || !subjectId) return null;
    const existing = data.assignments.find((assignment) => String(assignment.section_id) === String(sectionId)
      && !(assignment.subject_id == null && assignment.is_adviser)
      && (assignment.subject_id == null || String(assignment.subject_id) === String(subjectId)));
    if (existing) {
      const teacher = data.teachers.find((person) => String(person.id) === String(existing.teacher_id));
      const name = existing.teacher_name || (teacher ? `${teacher.first_name} ${teacher.last_name}` : 'another teacher');
      return { label: `Assigned to ${name}`, message: `${existing.subject_name || 'All subjects in this section'} is already assigned to ${name}. Remove the existing assignment before assigning this class again.` };
    }
    const selected = form.rows.find((other) => other.key !== row.key && other.section_id && other.subject_id
      && String(other.section_id) === String(sectionId)
      && String(other.subject_id) === String(subjectId));
    return selected ? { label: 'Already selected in another row', message: 'This class overlaps another assignment in this form. Choose a different class or remove the duplicate row.' } : null;
  };
  const set = (field) => (event) => {
    const value =
      event.target.type === 'checkbox' ? event.target.checked : event.target.value;
    setForm((current) => ({ ...current, [field]: value }));
  };

  const submit = async (event) => {
    event.preventDefault();
    if (busy || (tab === 'assignments' && form.rows.some((row) => conflictFor(row)))) return;
    const payload = serialise(tab, form);
    const created = await onSave(payload, record);
    // If only some assignments were saved, keep just the ones still to do.
    if (Array.isArray(payload) && created > 0 && created < payload.length) {
      setForm((current) => ({ ...current, rows: current.rows.slice(created) }));
    }
  };

  const setRow = (key, patch) =>
    setForm((current) => ({
      ...current,
      rows: current.rows.map((row) => (row.key === key ? { ...row, ...patch } : row)),
    }));

  const sectionOf = (sectionId) =>
    data.sections.find((section) => String(section.section_id) === String(sectionId));

  // A subject can be kept only when its grade and ownership fit the new section.
  const changeSection = (row) => (event) => {
    const section = sectionOf(event.target.value);
    const subject = data.subjects.find((item) => String(item.subject_id) === String(row.subject_id));
    const keep = subject && section && String(subject.grade_level_id) === String(section.grade_level_id)
      && !conflictFor(row, event.target.value, row.subject_id);
    setRow(row.key, { section_id: event.target.value, subject_id: keep ? row.subject_id : '' });
  };

  const addRow = () =>
    setForm((current) => ({ ...current, rows: [...current.rows, newAssignmentRow()] }));

  const removeRow = (key) =>
    setForm((current) => ({ ...current, rows: current.rows.filter((row) => row.key !== key) }));

  const assignmentCount = tab === 'assignments' ? form.rows.length : 0;
  const lastRow = tab === 'assignments' ? form.rows[form.rows.length - 1] : null;
  const conflictingRows = tab === 'assignments' && form.rows.some((row) => conflictFor(row));

  const titles = {
    years: 'school year',
    subjects: 'subject',
    assignments: 'teacher assignment',
    advisers: 'section adviser',
  };

  return (
    <Modal
      title={`${record ? 'Edit' : 'Add'} ${titles[tab]}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" form="setup-form" className="btn btn-primary" disabled={busy || conflictingRows || (['assignments', 'advisers'].includes(tab) && eligibleTeachers.length === 0)}>
            {busy
              ? 'Saving…'
              : record
                ? 'Save changes'
                : assignmentCount > 1
                  ? `Create ${assignmentCount} assignments`
                  : 'Create'}
          </button>
        </>
      }
    >
      <form id="setup-form" className="stack" onSubmit={submit}>
        {tab === 'advisers' && (
          <>
            <p className="muted">This grants viewing access to all subjects in the section. Assign teaching subjects separately to allow score editing.</p>
            <div className="field">
              <label className="field-label" htmlFor="adviser_section">Section <span className="req">*</span></label>
              <select id="adviser_section" className="select" value={form.section_id} onChange={set('section_id')} required disabled={Boolean(record)}>
                <option value="">Choose a section…</option>
                {data.sections.map((section) => {
                  const current = data.advisers.find((item) => String(item.section_id) === String(section.section_id));
                  return <option key={section.section_id} value={section.section_id} disabled={Boolean(current) && !record}>
                    {section.name}{section.grade_level_name ? ` — ${section.grade_level_name}` : ''}{current ? ` — ${current.teacher_name}` : ''}
                  </option>;
                })}
              </select>
              <span className="field-hint">Each section has one adviser. Use Edit to replace an existing adviser.</span>
            </div>
            <div className="field">
              <label className="field-label" htmlFor="adviser_teacher">Adviser <span className="req">*</span></label>
              <select id="adviser_teacher" className="select" value={form.teacher_id} onChange={set('teacher_id')} required>
                <option value="">Choose a teacher…</option>
                {eligibleTeachers.map((teacher) => <option key={teacher.id} value={teacher.id}>{teacher.first_name} {teacher.last_name}</option>)}
              </select>
              <span className="field-hint">Only approved, active teachers can receive advisory access.</span>
              {eligibleTeachers.length === 0 && <ErrorNote message="There are no approved, active teachers to assign. Review account approvals first." />}
            </div>
            {record && <p className="field-hint">Replacing {record.teacher_name} updates advisory viewing access. Subject teaching assignments and saved grades remain unchanged.</p>}
          </>
        )}
        {tab === 'years' && (
          <>
            <div className="field">
              <label className="field-label" htmlFor="y_year">
                School year <span className="req">*</span>
              </label>
              <input
                id="y_year"
                className="input"
                placeholder="2026-2027"
                value={form.year}
                onChange={set('year')}
                required
              />
              <span className="field-hint">For example 2026-2027.</span>
            </div>
            <label className="checkbox">
              <input type="checkbox" checked={form.is_current} onChange={set('is_current')} />
              Make this the current school year
            </label>
          </>
        )}

        {tab === 'subjects' && (
          <>
            <div className="field">
              <label className="field-label" htmlFor="sub_grade">
                Grade level <span className="req">*</span>
              </label>
              <select id="sub_grade" className="select" value={form.grade_level_id} onChange={set('grade_level_id')} required>
                <option value="">Choose…</option>
                {data.levels.map((level) => (
                  <option key={level.grade_level_id} value={level.grade_level_id}>
                    {level.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-grid">
              <div className="field">
                <label className="field-label" htmlFor="sub_name">
                  Subject name <span className="req">*</span>
                </label>
                <input id="sub_name" className="input" placeholder="Mathematics" value={form.subject_name} onChange={set('subject_name')} required />
              </div>
              {tracksUnits && (
                <div className="field">
                  <label className="field-label" htmlFor="sub_paces">
                    {unitLabelPlural} per year
                  </label>
                  <input id="sub_paces" className="input" type="number" min="1" value={form.total_paces} onChange={set('total_paces')} />
                </div>
              )}
            </div>
            {/* A new subject is always in use; the switch is for retiring one later. */}
            {record && (
              <div className="field">
                <label className="checkbox">
                  <input type="checkbox" checked={form.is_active} onChange={set('is_active')} />
                  In use
                </label>
                <span className="field-hint">Untick to hide this subject without deleting its grades.</span>
              </div>
            )}
          </>
        )}

        {tab === 'assignments' && (
          <>
            <div className="field">
              <label className="field-label" htmlFor="a_teacher">
                Teacher <span className="req">*</span>
              </label>
              <select id="a_teacher" className="select" value={form.teacher_id} onChange={set('teacher_id')} required>
                <option value="">Choose…</option>
                {eligibleTeachers.map((teacher) => (
                  <option key={teacher.id} value={teacher.id}>
                    {teacher.first_name} {teacher.last_name}
                  </option>
                ))}
              </select>
              <span className="field-hint">Only approved, active teachers can receive classes.</span>
              {eligibleTeachers.length === 0 && <ErrorNote message="There are no approved, active teachers to assign. Review account approvals first." />}
            </div>

            {form.rows.map((row, index) => {
              const section = sectionOf(row.section_id);
              const subjects = section
                ? data.subjects.filter((subject) => String(subject.grade_level_id) === String(section.grade_level_id) && subject.is_active !== false)
                : [];
              const conflict = conflictFor(row);
              const assigned = section ? data.assignments.filter((assignment) => String(assignment.section_id) === String(section.section_id) && !(assignment.subject_id == null && assignment.is_adviser)) : [];
              const adviser = section && data.advisers.find((item) => String(item.section_id) === String(section.section_id));
              return (
                <div
                  key={row.key}
                  className="stack"
                  style={
                    form.rows.length > 1
                      ? {
                          border: '1px solid var(--border-subtle)',
                          borderRadius: 'var(--radius-md)',
                          padding: '0.9rem',
                          background: 'var(--surface-sunken)',
                        }
                      : undefined
                  }
                >
                  {form.rows.length > 1 && (
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <strong style={{ fontSize: 'var(--text-sm)', color: 'var(--text-strong)' }}>
                        Assignment {index + 1}
                      </strong>
                      <button
                        type="button"
                        className="btn-icon"
                        title="Remove this assignment"
                        onClick={() => removeRow(row.key)}
                      >
                        <Trash2 size={15} />
                      </button>
                    </div>
                  )}
                  <div className="field">
                    <label className="field-label" htmlFor={`a_section_${row.key}`}>
                      Section <span className="req">*</span>
                    </label>
                    <select
                      id={`a_section_${row.key}`}
                      className="select"
                      value={row.section_id}
                      onChange={changeSection(row)}
                      required
                    >
                      <option value="">Choose…</option>
                      {data.sections.map((item) => {
                        const wildcard = data.assignments.find((assignment) => String(assignment.section_id) === String(item.section_id) && assignment.subject_id == null && !assignment.is_adviser);
                        return <option key={item.section_id} value={item.section_id} disabled={Boolean(wildcard)}>
                          {item.name}{item.grade_level_name ? ` — ${item.grade_level_name}` : ''}{wildcard ? ` — Review ${wildcard.teacher_name || 'existing teacher'}’s All subjects assignment first` : ''}
                        </option>;
                      })}
                    </select>
                  </div>
                  <div className="field">
                    <label className="field-label" htmlFor={`a_subject_${row.key}`}>
                      Subject <span className="req">*</span>
                    </label>
                    {conflict && <div className="field-error" role="alert" style={{ marginBottom: '.4rem' }}>{conflict.message}</div>}
                    <select
                      id={`a_subject_${row.key}`}
                      className="select"
                      value={row.subject_id}
                      onChange={(event) => setRow(row.key, { subject_id: event.target.value })}
                      required
                      disabled={!section}
                      aria-invalid={Boolean(conflict) || undefined}
                    >
                      <option value="">{section ? 'Choose a subject…' : 'Choose a section first…'}</option>
                      {subjects.map((subject) => {
                        const unavailable = conflictFor(row, row.section_id, String(subject.subject_id));
                        return <option key={subject.subject_id} value={subject.subject_id} disabled={Boolean(unavailable)}>
                          {subject.subject_name}{unavailable ? ` — ${unavailable.label}` : ''}
                        </option>;
                      })}
                    </select>
                    <span className="field-hint">Each section and subject has one teacher. Assigned classes are unavailable until their existing assignment is removed.</span>
                    {adviser && <span className="field-hint" style={{ display: 'block', marginTop: '.4rem' }}>Section adviser: {adviser.teacher_name}. Advisory access does not reserve any subject.</span>}
                    {assigned.length > 0 && <ul className="field-hint" style={{ paddingLeft: '1.1rem', marginTop: '.4rem', overflowWrap: 'anywhere' }}>
                      {assigned.map((assignment) => <li key={assignment.assignment_id}>{assignment.subject_name || 'All subjects'} — {assignment.teacher_name || 'Assigned teacher'}</li>)}
                    </ul>}
                  </div>
                </div>
              );
            })}

            {lastRow && lastRow.subject_id && (
              <div>
                <button type="button" className="btn btn-secondary btn-sm" onClick={addRow}>
                  <Plus size={14} /> Add another subject
                </button>
                <span className="field-hint" style={{ display: 'block', marginTop: '0.4rem' }}>
                  For teachers who handle other subjects or grade levels.
                </span>
              </div>
            )}
            <span className="field-hint">To give an adviser viewing access, use Section advisers instead.</span>
          </>
        )}
      </form>
    </Modal>
  );
}

function AssignmentReviewModal({ record, data, busy, onSave, onClose }) {
  const [subjectIds, setSubjectIds] = useState([]);
  const [confirmed, setConfirmed] = useState(false);
  const section = data.sections.find((item) => String(item.section_id) === String(record.section_id));
  const adviser = data.advisers.find((item) => String(item.section_id) === String(record.section_id)
    && String(item.teacher_id) === String(record.teacher_id));
  const hasAdvisoryAccess = Boolean(adviser);
  const subjects = data.subjects.filter((subject) => section
    && String(subject.grade_level_id) === String(section.grade_level_id) && subject.is_active !== false);
  const ownerOf = (subjectId) => data.assignments.find((assignment) => assignment.assignment_id !== record.assignment_id
    && String(assignment.section_id) === String(record.section_id)
    && String(assignment.teacher_id) !== String(record.teacher_id)
    && !(assignment.subject_id == null && assignment.is_adviser)
    && (assignment.subject_id == null || String(assignment.subject_id) === String(subjectId)));
  const conflicts = subjectIds.some((id) => ownerOf(id));
  const toggle = (id) => (event) => {
    setSubjectIds((current) => event.target.checked ? [...current, id] : current.filter((value) => value !== id));
    setConfirmed(false);
  };
  return (
    <Modal title="Review All subjects assignment" onClose={onClose} footer={
      <>
        <button type="button" className="btn btn-secondary" onClick={onClose} disabled={busy}>Cancel</button>
        <button type="submit" form="review-assignment-form" className="btn btn-primary" disabled={busy || !confirmed || conflicts}>{busy ? 'Saving…' : 'Confirm teaching subjects'}</button>
      </>
    }>
      <form id="review-assignment-form" className="stack" onSubmit={(event) => { event.preventDefault(); if (!busy && confirmed && !conflicts) onSave(subjectIds); }}>
        <p><strong>{record.teacher_name}</strong> — {section?.grade_level_name ? `${section.grade_level_name} ` : ''}{record.section_name}</p>
        <p className="muted">Select only the subjects this teacher actually teaches. This replaces the older All subjects assignment with specific teaching assignments. Existing grades and scores will be kept.</p>
        <fieldset className="stack" style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
          <legend className="field-label" style={{ marginBottom: '.75rem' }}>Teaching subjects</legend>
          {subjects.map((subject) => {
            const owner = ownerOf(subject.subject_id);
            const alreadyAssigned = data.assignments.some((assignment) => String(assignment.section_id) === String(record.section_id)
              && String(assignment.teacher_id) === String(record.teacher_id) && String(assignment.subject_id) === String(subject.subject_id));
            return <label className="checkbox" key={subject.subject_id} style={{ alignItems: 'flex-start', overflowWrap: 'anywhere' }}>
              <input type="checkbox" value={subject.subject_id} checked={subjectIds.includes(subject.subject_id)} disabled={busy || (Boolean(owner) && !subjectIds.includes(subject.subject_id))} onChange={toggle(subject.subject_id)} />
              <span>{subject.subject_name}{owner && <span className="field-hint" style={{ display: 'block' }}>Already assigned to {owner.teacher_name || 'another teacher'}</span>}{alreadyAssigned && !owner && <span className="field-hint" style={{ display: 'block' }}>Already assigned to this teacher; this assignment will be kept.</span>}</span>
            </label>;
          })}
          {!subjects.length && <p className="muted">No active subjects are available for this section’s grade level.</p>}
        </fieldset>
        {conflicts && <ErrorNote message="One of the selected subjects was assigned to another teacher. Refresh your selection before confirming." />}
        <p className="field-hint">{hasAdvisoryAccess ? 'Advisory viewing access to all subjects will remain. Leave every subject unchecked if this older assignment was only for advisory access.' : 'Leaving every subject unchecked removes this older teaching assignment. This teacher will have no access through this assignment.'} Existing specific-subject assignments remain even when unchecked.</p>
        <label className="checkbox" style={{ alignItems: 'flex-start' }}>
          <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} disabled={busy} />
          <span>{subjectIds.length ? `I confirm these ${subjectIds.length} teaching subject${subjectIds.length === 1 ? '' : 's'} for this teacher.` : hasAdvisoryAccess ? 'I confirm this older assignment was for advisory access only. Keep any separate teaching assignments.' : 'I confirm no teaching subjects need to be added from this older assignment.'}</span>
        </label>
      </form>
    </Modal>
  );
}

let assignmentRowKey = 0;
function newAssignmentRow() {
  assignmentRowKey += 1;
  return { key: assignmentRowKey, section_id: '', subject_id: '' };
}

function defaultsFor(tab, record) {
  const shapes = {
    years: { year: '', is_current: false },
    subjects: {
      grade_level_id: '',
      subject_name: '',
      total_paces: 12,
      is_active: true,
    },
    assignments: { teacher_id: '', rows: [newAssignmentRow()] },
    advisers: { section_id: '', teacher_id: '' },
  };
  const base = shapes[tab];
  if (!record) return base;
  return Object.fromEntries(
    Object.keys(base).map((key) => [key, record[key] ?? base[key]]),
  );
}

function serialise(tab, form) {
  // The dates of a school year are worked out from its label on the server.
  if (tab === 'years') return { year: form.year.trim(), is_current: Boolean(form.is_current) };
  if (tab === 'subjects') {
    return {
      ...form,
      grade_level_id: Number(form.grade_level_id),
      total_paces: Number(form.total_paces) || 12,
    };
  }
  if (tab === 'assignments') {
    // One request per row; the teacher is shared by all of them.
    return form.rows.map((row) => ({
      teacher_id: form.teacher_id,
      section_id: Number(row.section_id),
      subject_id: Number(row.subject_id),
    }));
  }
  if (tab === 'advisers') return { section_id: Number(form.section_id), teacher_id: form.teacher_id };
  return form;
}
