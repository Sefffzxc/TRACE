import { useEffect, useMemo, useState } from 'react';
import { Modal, ErrorNote } from './ui';
import { studentApi } from '../../api/endpoints';
import { useToast } from '../../context/ToastContext';

const BLANK = {
  first_name: '',
  middle_name: '',
  last_name: '',
  birth_date: '',
  gender: 'Male',
  address: '',
  guardian_first_name: '',
  guardian_mid_name: '',
  guardian_last_name: '',
  guardian_contact: '',
  guardian_relationship: 'Parent',
  guardian_email: '',
  grade_level_id: '',
  section_id: '',
};

/**
 * Add or edit a student. On creation the enrolment (grade level + section) is
 * written in the same request, which is how the office actually works: nobody
 * is admitted without being placed in a section.
 */
export default function StudentFormModal({ student, gradeLevels, sections, onClose, onSaved }) {
  const toast = useToast();
  const isEdit = Boolean(student?.student_id);

  const [form, setForm] = useState(BLANK);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!student) return;
    setForm({
      ...BLANK,
      ...Object.fromEntries(
        Object.keys(BLANK).map((key) => [key, student[key] ?? BLANK[key]]),
      ),
      birth_date: student.birth_date || '',
      guardian_email: student.guardian_email || '',
      grade_level_id: student.grade_level_id || '',
      section_id: student.section_id || '',
    });
  }, [student]);

  const sectionOptions = useMemo(
    () =>
      form.grade_level_id
        ? sections.filter((section) => String(section.grade_level_id) === String(form.grade_level_id))
        : [],
    [sections, form.grade_level_id],
  );

  const set = (field) => (event) =>
    setForm((current) => ({ ...current, [field]: event.target.value }));

  const submit = async (event) => {
    event.preventDefault();
    setError('');

    if (!form.first_name.trim() || !form.last_name.trim()) {
      setError("The student's first and last name are required.");
      return;
    }
    if (!isEdit && (!form.grade_level_id || !form.section_id)) {
      setError('Choose a grade level and section so the student can be enrolled.');
      return;
    }

    const payload = {
      first_name: form.first_name.trim(),
      middle_name: form.middle_name.trim() || null,
      last_name: form.last_name.trim(),
      birth_date: form.birth_date || null,
      gender: form.gender,
      address: form.address.trim(),
      guardian_first_name: form.guardian_first_name.trim(),
      guardian_mid_name: form.guardian_mid_name.trim() || null,
      guardian_last_name: form.guardian_last_name.trim(),
      guardian_contact: form.guardian_contact.trim(),
      guardian_relationship: form.guardian_relationship,
      guardian_email: form.guardian_email.trim() || null,
    };

    setBusy(true);
    try {
      if (isEdit) {
        await studentApi.update(student.student_id, payload);
        toast.success('Student record updated.');
      } else {
        await studentApi.create({
          ...payload,
          grade_level_id: Number(form.grade_level_id),
          section_id: Number(form.section_id),
        });
        toast.success('Student added and enrolled.');
      }
      onSaved();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title={isEdit ? 'Edit student' : 'Add a student'}
      subtitle={
        isEdit
          ? `Updating ${student.full_name}`
          : 'The student is enrolled in the current school year on save.'
      }
      size="lg"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" form="student-form" className="btn btn-primary" disabled={busy}>
            {busy ? 'Saving…' : isEdit ? 'Save changes' : 'Add student'}
          </button>
        </>
      }
    >
      <form id="student-form" onSubmit={submit} className="stack" noValidate>
        <ErrorNote message={error} />

        <div className="section-title">Student details</div>
        <div className="form-grid">
          <div className="field">
            <label className="field-label" htmlFor="s_first">
              First name <span className="req">*</span>
            </label>
            <input id="s_first" className="input" value={form.first_name} onChange={set('first_name')} required />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="s_middle">
              Middle name
            </label>
            <input id="s_middle" className="input" value={form.middle_name} onChange={set('middle_name')} />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="s_last">
              Last name <span className="req">*</span>
            </label>
            <input id="s_last" className="input" value={form.last_name} onChange={set('last_name')} required />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="s_birth">
              Date of birth
            </label>
            <input
              id="s_birth"
              className="input"
              type="date"
              value={form.birth_date}
              onChange={set('birth_date')}
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="s_gender">
              Gender
            </label>
            <select id="s_gender" className="select" value={form.gender} onChange={set('gender')}>
              <option>Male</option>
              <option>Female</option>
            </select>
          </div>
          <div className="field span-2">
            <label className="field-label" htmlFor="s_address">
              Home address
            </label>
            <input id="s_address" className="input" value={form.address} onChange={set('address')} />
          </div>
        </div>

        {!isEdit && (
          <>
            <div className="section-title">Enrolment</div>
            <div className="form-grid">
              <div className="field">
                <label className="field-label" htmlFor="s_grade">
                  Grade level <span className="req">*</span>
                </label>
                <select
                  id="s_grade"
                  className="select"
                  value={form.grade_level_id}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      grade_level_id: event.target.value,
                      section_id: '',
                    }))
                  }
                  required
                >
                  <option value="">Choose a grade level…</option>
                  {gradeLevels.map((level) => (
                    <option key={level.grade_level_id} value={level.grade_level_id}>
                      {level.name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label className="field-label" htmlFor="s_section">
                  Section <span className="req">*</span>
                </label>
                <select
                  id="s_section"
                  className="select"
                  value={form.section_id}
                  onChange={set('section_id')}
                  disabled={!form.grade_level_id}
                  required
                >
                  <option value="">
                    {form.grade_level_id ? 'Choose a section…' : 'Pick a grade level first'}
                  </option>
                  {sectionOptions.map((section) => (
                    <option key={section.section_id} value={section.section_id}>
                      {section.name} ({section.enrolled_count}/{section.capacity})
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </>
        )}

        <div className="section-title">Parent or guardian</div>
        <div className="form-grid">
          <div className="field">
            <label className="field-label" htmlFor="g_first">
              First name
            </label>
            <input
              id="g_first"
              className="input"
              value={form.guardian_first_name}
              onChange={set('guardian_first_name')}
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="g_last">
              Last name
            </label>
            <input
              id="g_last"
              className="input"
              value={form.guardian_last_name}
              onChange={set('guardian_last_name')}
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="g_rel">
              Relationship
            </label>
            <select
              id="g_rel"
              className="select"
              value={form.guardian_relationship}
              onChange={set('guardian_relationship')}
            >
              <option>Parent</option>
              <option>Guardian</option>
              <option>Relative</option>
              <option>Other</option>
            </select>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="g_contact">
              Contact number
            </label>
            <input
              id="g_contact"
              className="input"
              placeholder="0917 000 0000"
              value={form.guardian_contact}
              onChange={set('guardian_contact')}
            />
          </div>
          <div className="field span-2">
            <label className="field-label" htmlFor="g_email">
              Email address
            </label>
            <input
              id="g_email"
              className="input"
              type="email"
              placeholder="Optional"
              value={form.guardian_email}
              onChange={set('guardian_email')}
            />
          </div>
        </div>
      </form>
    </Modal>
  );
}
