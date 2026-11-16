import { useEffect, useMemo, useState } from 'react';
import { Modal, ErrorNote } from './ui';
import ImportClassRecord from './ImportClassRecord';
import { assignmentApi } from '../../api/endpoints';
import { useAuth } from '../../context/AuthContext';
import { rosterGrades, rosterSections } from '../../lib/rosterAccess';

/** Select the assigned section first; importing its roster never writes scores. */
export default function ImportStudentsModal({ gradeLevels, sections, onClose, onDone }) {
  const { user, periodLabel } = useAuth();
  const [access, setAccess] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [grade, setGrade] = useState('');
  const [sectionId, setSectionId] = useState('');
  const [target, setTarget] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setAccess(null);
    setError('');
    if (user?.role !== 'teacher') {
      setLoading(false);
      return;
    }
    assignmentApi.mine().then((result) => {
      if (!cancelled) setAccess(result);
    }).catch((err) => {
      if (!cancelled) setError(err.message);
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [user?.role, retry]);

  const eligibleSections = useMemo(() => rosterSections(sections, access, user?.role), [sections, access, user?.role]);
  const levels = useMemo(() => rosterGrades(gradeLevels, eligibleSections), [gradeLevels, eligibleSections]);
  const options = useMemo(() => eligibleSections.filter((section) => String(section.grade_level_id) === grade), [eligibleSections, grade]);
  const selected = options.find((section) => String(section.section_id) === sectionId);

  useEffect(() => {
    if (!loading) setGrade((current) => levels.some((level) => String(level.grade_level_id) === current)
      ? current : levels.length === 1 ? String(levels[0].grade_level_id) : '');
  }, [levels, loading]);
  useEffect(() => {
    setSectionId((current) => options.some((section) => String(section.section_id) === current)
      ? current : options.length === 1 ? String(options[0].section_id) : '');
  }, [options]);

  if (target) return (
    <ImportClassRecord section={target} rosterOnly periodLabel={periodLabel} onClose={onClose} onDone={onDone} />
  );

  return (
    <Modal title="Import students" subtitle="Add learners to a section where you teach or serve as adviser."
      onClose={onClose} footer={<>
        <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="btn btn-primary" disabled={!selected || loading || Boolean(error)}
          onClick={() => { if (selected) setTarget(selected); }}>Choose workbook</button>
      </>}>
      <div className="stack">
        <ErrorNote message={error} />
        {error && <button type="button" className="btn btn-secondary" onClick={() => setRetry((value) => value + 1)}>Retry assigned sections</button>}
        {loading && <p className="faint">Loading your assigned sections…</p>}
        {!loading && !error && !eligibleSections.length && <p className="alert alert-info">You need a teaching or adviser assignment before importing students. Ask your principal to assign your section.</p>}
        <div className="field">
          <label className="field-label" htmlFor="roster_grade">Grade level</label>
          <select id="roster_grade" className="select" value={grade} disabled={loading || Boolean(error)}
            onChange={(event) => { setGrade(event.target.value); setSectionId(''); }}>
            <option value="">Choose a grade level…</option>
            {levels.map((level) => <option key={level.grade_level_id} value={level.grade_level_id}>{level.name}</option>)}
          </select>
        </div>
        <div className="field">
          <label className="field-label" htmlFor="roster_section">Section</label>
          <select id="roster_section" className="select" value={sectionId} disabled={!grade || loading || Boolean(error)}
            onChange={(event) => setSectionId(event.target.value)}>
            <option value="">Choose a section…</option>
            {options.map((section) => <option key={section.section_id} value={section.section_id}>{section.name}</option>)}
          </select>
        </div>
        <p className="alert alert-info">This imports student names and enrollment only. It does not import subject scores or assessment settings.</p>
      </div>
    </Modal>
  );
}
