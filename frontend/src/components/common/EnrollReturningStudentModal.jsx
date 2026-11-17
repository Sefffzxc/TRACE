import { useEffect, useMemo, useState } from 'react';
import { enrollmentApi, studentApi } from '../../api/endpoints';
import { useSchoolYear } from '../../context/SchoolYearContext';
import { useToast } from '../../context/ToastContext';
import { ErrorNote, Modal } from './ui';
import { formatPersonName } from '../../lib/personName';

/** Reuse the school profile while creating a separate enrollment for this year. */
export default function EnrollReturningStudentModal({ gradeLevels, sections, onClose, onSaved }) {
  const { year, yearId } = useSchoolYear();
  const toast = useToast();
  const [gradeId, setGradeId] = useState(gradeLevels.length === 1 ? String(gradeLevels[0].grade_level_id) : '');
  const options = useMemo(() => sections.filter((row) => String(row.grade_level_id) === gradeId), [sections, gradeId]);
  const [sectionId, setSectionId] = useState('');
  const [search, setSearch] = useState('');
  const [matches, setMatches] = useState([]);
  const [selectedId, setSelectedId] = useState('');
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [searched, setSearched] = useState(false);

  useEffect(() => {
    setSectionId(options.length === 1 ? String(options[0].section_id) : '');
  }, [options]);

  useEffect(() => {
    let live = true;
    setMatches([]);
    setSelectedId('');
    setSearched(false);
    setError('');
    if (!sectionId || search.trim().length < 2) {
      setLoading(false);
      return undefined;
    }
    setLoading(true);
    const timer = window.setTimeout(async () => {
      try {
        const rows = await studentApi.returning({ section_id: sectionId, search: search.trim() });
        if (live) { setMatches(rows || []); setSearched(true); }
      } catch (err) {
        if (live) setError(err.message);
      } finally {
        if (live) setLoading(false);
      }
    }, 350);
    return () => { live = false; window.clearTimeout(timer); };
  }, [search, sectionId]);

  const submit = async (event) => {
    event.preventDefault();
    if (busy || !selectedId || !sectionId || !yearId) return;
    setBusy(true);
    setError('');
    try {
      await enrollmentApi.create({ student_id: selectedId, grade_level_id: Number(gradeId), section_id: Number(sectionId), school_year_id: yearId });
      toast.success(`Student enrolled for S.Y. ${year.year}. Previous records were preserved.`);
      onSaved();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return <Modal title="Enroll a returning student" subtitle={`Reuse an existing profile for S.Y. ${year?.year || 'the current school year'}.`}
    onClose={busy ? undefined : onClose} footer={<>
      <button type="button" className="btn btn-secondary" onClick={onClose} disabled={busy}>Cancel</button>
      <button type="submit" form="returning-student-form" className="btn btn-primary" disabled={busy || loading || !selectedId || !yearId}>{busy ? 'Enrolling…' : 'Enroll student'}</button>
    </>}>
    <form id="returning-student-form" className="stack" onSubmit={submit}>
      <ErrorNote message={error} />
      <div className="field">
        <label className="field-label" htmlFor="returning_grade">Grade level</label>
        <select id="returning_grade" className="select" value={gradeId} onChange={(event) => setGradeId(event.target.value)} disabled={busy}>
          <option value="">Choose a grade level…</option>
          {gradeLevels.map((row) => <option key={row.grade_level_id} value={row.grade_level_id}>{row.name}</option>)}
        </select>
      </div>
      <div className="field">
        <label className="field-label" htmlFor="returning_section">Enroll into section</label>
        <select id="returning_section" className="select" value={sectionId} onChange={(event) => setSectionId(event.target.value)} disabled={busy || !gradeId}>
          <option value="">Choose a section…</option>
          {options.map((row) => <option key={row.section_id} value={row.section_id}>{row.name}</option>)}
        </select>
      </div>
      <div className="field">
        <label className="field-label" htmlFor="returning_search">Find an existing student</label>
        <input id="returning_search" type="search" className="input" value={search} onChange={(event) => setSearch(event.target.value)} disabled={busy || !sectionId} placeholder="Name or student ID (at least 2 characters)" />
      </div>
      {loading && <p className="faint" role="status">Searching existing student profiles…</p>}
      {searched && !loading && !matches.length && <p className="faint">No matching profiles are available to enroll. Students already enrolled this year are excluded.</p>}
      {matches.length > 0 && <div className="stack" role="radiogroup" aria-label="Matching student profiles">
        {matches.map((row) => <label key={row.student_id} className="checkbox">
          <input type="radio" name="returning_student" value={row.student_id} checked={selectedId === row.student_id} disabled={busy} onChange={() => setSelectedId(row.student_id)} />
          <span>{formatPersonName(row.full_name)}<span className="faint">{row.login_id ? ` · ${row.login_id}` : ''}</span></span>
        </label>)}
      </div>}
      <p className="faint">This adds an enrollment for the current year. Previous scores and enrollments stay in their original school year.</p>
    </form>
  </Modal>;
}
