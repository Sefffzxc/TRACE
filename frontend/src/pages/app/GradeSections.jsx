import { useState } from 'react';
import { GraduationCap, Layers, Pencil, Plus, Trash2 } from 'lucide-react';
import { EmptyState, Modal } from '../../components/common/ui';

export function GradeSectionsTable({ data, onEdit, onDelete }) {
  if (!data.levels.length) {
    return <EmptyState icon={Layers} title="No grades yet" description="Add a grade and its sections together." />;
  }
  return (
    <div className="grade-groups">
      {data.levels.map((grade) => {
        const sections = data.sections.filter((section) => section.grade_level_id === grade.grade_level_id);
        const hasSubjects = data.subjects.some((subject) => subject.grade_level_id === grade.grade_level_id);
        return (
          <section className="grade-group" key={grade.grade_level_id} aria-label={grade.name}>
            <div className="grade-group-header">
              <div>
                <h2><GraduationCap size={19} /> {grade.name}</h2>
                <p>{sections.length} section{sections.length === 1 ? '' : 's'} &middot; {grade.student_count} student{grade.student_count === 1 ? '' : 's'}</p>
              </div>
              <div className="grade-group-actions">
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => onEdit(grade)}>
                  <Pencil size={14} /> Edit grade &amp; sections
                </button>
                <button
                  type="button" className="btn-icon"
                  aria-label={`Remove ${grade.name}`}
                  title={sections.length || hasSubjects ? 'Remove sections and subjects before removing this grade' : 'Remove grade'}
                  disabled={sections.length > 0 || hasSubjects}
                  onClick={() => onDelete(grade)}
                >
                  <Trash2 size={15} />
                </button>
              </div>
            </div>
            {sections.length ? (
              <div className="table-wrap">
                <table className="table">
                  <thead><tr><th>Section</th><th>Adviser</th><th>Enrolled / capacity</th></tr></thead>
                  <tbody>
                    {sections.map((section) => (
                      <tr key={section.section_id}>
                        <td className="cell-strong">{section.name}</td>
                        <td className="muted">{section.adviser_name || 'Unassigned'}</td>
                        <td>{section.enrolled_count} / {section.capacity}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="grade-group-empty">
                <span>No sections added yet.</span>
                <button type="button" className="link-button" onClick={() => onEdit(grade)}>
                  Add sections
                </button>
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}

let sectionKey = 0;
function sectionRow(section = {}) {
  sectionKey += 1;
  return { key: sectionKey, name: '', capacity: 40, ...section };
}

export function GradeSectionsModal({ record, data, busy, onSave, onClose }) {
  const [form, setForm] = useState(() => ({
    level: record?.level || '',
    name: record?.name || '',
    sections: record
      ? data.sections.filter((section) => section.grade_level_id === record.grade_level_id).map(sectionRow)
      : [sectionRow()],
  }));
  const setGrade = (field) => (event) => setForm((current) => ({ ...current, [field]: event.target.value }));
  const setSection = (key, field, value) => setForm((current) => ({
    ...current, sections: current.sections.map((section) => section.key === key ? { ...section, [field]: value } : section),
  }));
  const removeSection = (key) => setForm((current) => ({
    ...current, sections: current.sections.filter((section) => section.key !== key),
  }));
  const submit = (event) => {
    event.preventDefault();
    onSave({
      level: form.level.trim(), name: form.name.trim(),
      sections: form.sections.map((section) => ({
        ...(section.section_id ? { section_id: section.section_id } : {}),
        name: section.name.trim(), capacity: Number(section.capacity),
      })),
    }, record);
  };

  return (
    <Modal
      title={record ? 'Edit grade & sections' : 'Add grade & sections'}
      subtitle="Set the grade first, then add its sections below."
      size="lg" onClose={busy ? undefined : onClose}
      footer={(
        <>
          <button type="button" className="btn btn-secondary" disabled={busy} onClick={onClose}>Cancel</button>
          <button type="submit" form="grade-sections-form" className="btn btn-primary" disabled={busy}>
            {busy ? 'Saving…' : record ? 'Save changes' : 'Create grade & sections'}
          </button>
        </>
      )}
    >
      <form id="grade-sections-form" className="stack" onSubmit={submit}>
        <fieldset className="grade-form-fields stack" disabled={busy}>
          <div className="grade-form-heading"><h3>Grade level</h3></div>
          <div className="form-grid">
            <div className="field">
              <label className="field-label" htmlFor="grade-level">Level <span className="req">*</span></label>
              <input id="grade-level" className="input" placeholder="7" value={form.level} onChange={setGrade('level')} maxLength={10} required />
            </div>
            <div className="field">
              <label className="field-label" htmlFor="grade-name">Display name <span className="req">*</span></label>
              <input id="grade-name" className="input" placeholder="Grade 7" value={form.name} onChange={setGrade('name')} maxLength={30} required />
            </div>
          </div>
          <div className="grade-form-heading grade-form-sections-heading">
            <div><h3>Sections</h3><p>Enter a name and capacity for each section in this grade.</p></div>
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => setForm((current) => ({ ...current, sections: [...current.sections, sectionRow()] }))}>
              <Plus size={14} /> Add section
            </button>
          </div>
          {!form.sections.length && <p className="muted">No sections added. You can add them now or edit this grade later.</p>}
          {form.sections.map((section, index) => {
            const inUse = section.enrolled_count > 0 || data.assignments.some((assignment) => assignment.section_id === section.section_id);
            return (
              <div className="grade-section-row" key={section.key}>
                <div className="field">
                  <label className="field-label" htmlFor={`section-name-${section.key}`}>Section {index + 1} <span className="req">*</span></label>
                  <input id={`section-name-${section.key}`} className="input" placeholder="Faith" maxLength={40} value={section.name} onChange={(event) => setSection(section.key, 'name', event.target.value)} required />
                </div>
                <div className="field">
                  <label className="field-label" htmlFor={`section-capacity-${section.key}`}>Capacity <span className="req">*</span></label>
                  <input id={`section-capacity-${section.key}`} className="input" type="number" min="1" value={section.capacity} onChange={(event) => setSection(section.key, 'capacity', event.target.value)} required />
                </div>
                <button
                  type="button" className="btn-icon grade-section-remove" disabled={inUse}
                  aria-label={`Remove section ${index + 1}`}
                  title={inUse ? 'This section has enrollments or teacher assignments' : 'Remove section'}
                  onClick={() => removeSection(section.key)}
                ><Trash2 size={16} /></button>
                {inUse && <p className="field-hint grade-section-note">This section is in use. Its name and capacity can still be updated.</p>}
              </div>
            );
          })}
        </fieldset>
      </form>
    </Modal>
  );
}
