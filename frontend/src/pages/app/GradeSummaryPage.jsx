import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Brain, ClipboardList, GraduationCap, RefreshCw, Search } from 'lucide-react';
import { aiApi, assignmentApi, classRecordApi, sectionApi, subjectApi } from '../../api/endpoints';
import { EmptyState, ErrorNote, PageHead, RiskBadge, TableSkeleton } from '../../components/common/ui';
import { AiPredictionBasis } from '../../components/common/ai';
import { useAuth } from '../../context/AuthContext';
import { useSchoolYear } from '../../context/SchoolYearContext';
import { useToast } from '../../context/ToastContext';
import { assignedSections, sectionLabel } from '../../lib/classAccess';
import { classRecordSelectionKey, readClassRecordSelection, subjectsForSection } from '../../lib/classRecordSelection';
import { periodName, show } from '../../lib/grading';
import { formatPersonName } from '../../lib/personName';

export default function GradeSummaryPage() {
  const toast = useToast();
  const { user, schoolReady, periodLabel } = useAuth();
  const { year, yearId, ready: yearReady } = useSchoolYear();
  const [params, setParams] = useSearchParams();
  const [catalog, setCatalog] = useState(null);
  const [catalogError, setCatalogError] = useState('');
  const [snapshot, setSnapshot] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [search, setSearch] = useState('');
  const [updatingForecasts, setUpdatingForecasts] = useState(false);
  const selectionKey = classRecordSelectionKey(user, yearId);

  useEffect(() => {
    if (!schoolReady || !yearId) return undefined;
    let live = true;
    setCatalogError('');
    Promise.all([sectionApi.list(), subjectApi.list({ is_active: true }), assignmentApi.mine()])
      .then(([sections, subjects, access]) => {
        if (live) setCatalog({ key: selectionKey, sections: assignedSections(sections, access),
          subjects: subjects || [], access: access || { grade: {}, advisory: [] },
          saved: readClassRecordSelection(selectionKey) });
      })
      .catch((err) => { if (live) setCatalogError(err.message); });
    return () => { live = false; };
  }, [schoolReady, yearId, selectionKey, refresh]);

  const readyCatalog = catalog?.key === selectionKey ? catalog : null;
  const sections = readyCatalog?.sections || [];
  const requestedSection = params.get('section_id') || readyCatalog?.saved.sectionId;
  const section = sections.find((row) => String(row.section_id) === String(requestedSection)) || sections[0];
  const subjects = readyCatalog ? subjectsForSection(section, readyCatalog.subjects, readyCatalog.access) : [];
  const requestedSubject = params.get('subject_id') || readyCatalog?.saved.subjectId;
  const subject = subjects.find((row) => String(row.subject_id) === String(requestedSubject)) || subjects[0];
  const sectionId = section?.section_id;
  const subjectId = subject?.subject_id;
  const requestKey = yearId && sectionId && subjectId ? `${yearId}:${sectionId}:${subjectId}` : '';
  const summary = snapshot?.key === requestKey ? snapshot.data : null;

  useEffect(() => {
    setError('');
    setLoading(Boolean(requestKey));
    if (!requestKey) return undefined;
    let live = true;
    classRecordApi.summary({ section_id: sectionId, subject_id: subjectId, school_year_id: yearId })
      .then((data) => { if (live) setSnapshot({ key: requestKey, data }); })
      .catch((err) => { if (live) { setSnapshot(null); setError(err.message); } })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [requestKey, sectionId, subjectId, yearId, refresh]);

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return (summary?.rows || []).filter((row) => !needle ||
      `${row.student_name} ${row.login_id || ''}`.toLowerCase().includes(needle));
  }, [summary, search]);
  const busy = loading || Boolean(requestKey && !summary && !error);
  const recordPath = sectionId && subjectId
    ? `/class-record?section_id=${sectionId}&subject_id=${subjectId}` : '/class-record';
  const changeSection = (value) => setParams({ section_id: value }, { replace: true });
  const changeSubject = (value) => setParams({ section_id: String(sectionId), subject_id: value }, { replace: true });
  const finished = (summary?.rows || []).filter((row) => row.final_grade != null).length;
  const updateForecasts = async () => {
    setUpdatingForecasts(true);
    try {
      const result = await aiApi.rescore({ section_id: sectionId, subject: subject.subject_name, limit: 2000 });
      if (!result.scored) toast.info('Complete the first period before requesting a forecast.');
      else if (result.source === 'model') toast.success(`Updated ${result.scored} forecast${result.scored === 1 ? '' : 's'}.`);
      else toast.warning('Some forecasts used the rule-based estimate because the AI could not provide them.');
      setRefresh((n) => n + 1);
    } catch (err) { toast.error(err.message); }
    finally { setUpdatingForecasts(false); }
  };

  return (
    <div className="page grade-summary-page">
      <PageHead title="Grade summary"
        subtitle={`Recorded grades for ${year?.year || 'the selected school year'}. Averages use completed periods.`}
        actions={<>
          <Link className="btn btn-secondary" to={recordPath}><ClipboardList size={16} /> Open class record</Link>
          <button type="button" className="btn btn-secondary" onClick={() => setRefresh((n) => n + 1)}
            disabled={busy || updatingForecasts || (!requestKey && !catalogError)} title="Reload saved grades and forecasts"><RefreshCw size={16} /> Refresh</button>
          <button type="button" className="btn btn-primary" onClick={updateForecasts}
            disabled={!requestKey || busy || updatingForecasts}><Brain size={16} /> {updatingForecasts ? 'Updating...' : 'Update predictions'}</button>
        </>}
      />
      <ErrorNote message={catalogError || error} />
      <div className="toolbar">
        <select className="select" value={sectionId || ''} onChange={(e) => changeSection(e.target.value)} aria-label="Section"
          disabled={!readyCatalog || !sections.length || updatingForecasts}>
          {!sections.length && <option value="">No assigned sections</option>}
          {sections.map((row) => <option key={row.section_id} value={row.section_id}>{sectionLabel(row, sections, true)}</option>)}
        </select>
        <select className="select" value={subjectId || ''} onChange={(e) => changeSubject(e.target.value)} aria-label="Subject"
          disabled={!subjects.length || updatingForecasts}>
          {!subjects.length && <option value="">No assigned subjects</option>}
          {subjects.map((row) => <option key={row.subject_id} value={row.subject_id}>{row.subject_name}</option>)}
        </select>
        <div className="search"><Search size={15} /><input className="input" type="search" placeholder="Find a learner..."
          aria-label="Find a learner" value={search} onChange={(e) => setSearch(e.target.value)} /></div>
      </div>
      {summary && !busy && <p className="faint gs-note">
        {summary.rows.length} learner{summary.rows.length === 1 ? '' : 's'} · {finished} final grade{finished === 1 ? '' : 's'} ready · Passing mark {show(summary.passing_mark)}.
        {' '}Final grades appear after all {summary.period_count} periods are complete. Forecasts are estimates.
      </p>}
      {yearReady && !yearId ? <EmptyState icon={GraduationCap} title="No current school year"
        description="Ask your administrator to select a school year." /> :
        !readyCatalog && !catalogError ? <TableSkeleton rows={8} /> : busy ? <TableSkeleton rows={8} /> :
        !section || !subject ? <EmptyState icon={GraduationCap} title="No assigned classes"
          description="Your grade summaries appear when a section and subject are assigned to you." /> :
        error || catalogError ? null : !rows.length ? <EmptyState icon={GraduationCap}
          title={search ? 'No learner matches your search' : 'No learners enrolled'}
          description={search ? 'Try another name or student ID.' : 'Add learners to this section to see their grade summary.'} /> :
        <div className="card"><div className="table-wrap gs-scroll"><table className="table gs-table">
          <caption className="gs-caption">{summary?.section_name} - {summary?.subject} grade summary, {year?.year}</caption>
          <thead><tr>
            <th scope="col">Student</th>
            {Array.from({ length: summary.period_count }, (_, n) => <th key={n} scope="col" className="right">{periodName(n + 1, summary.period_label || periodLabel)}</th>)}
            <th scope="col" className="right">Current average</th>
            <th scope="col">AI predicted final grade</th>
            <th scope="col" className="right">Final grade</th>
            <th scope="col">Result</th>
          </tr></thead>
          <tbody>{rows.map((row) => <tr key={row.enrollment_id}>
            <th scope="row" className="gs-student">
              <Link to={`/students/${row.student_id}`}>{formatPersonName(row.student_name)}</Link>
              {row.login_id && <div className="faint">{row.login_id}</div>}
              {row.risk_level ? <RiskBadge level={row.risk_level} /> : <span className="faint">No standing yet</span>}
            </th>
            {row.term_grades.map((grade, n) => <td key={n} className="right" title={grade == null ? 'This period is not complete.' : undefined}>{show(grade)}</td>)}
            <td className="right cell-strong">{show(row.current_average)}<div className="faint gs-small">{row.completed_periods}/{summary.period_count} complete</div></td>
            <td className="gs-forecast"><ForecastCell prediction={row.ai} completed={row.final_grade != null} /></td>
            <td className={`right gs-final ${row.final_grade != null ? 'is-complete' : ''}`}>
              {row.final_grade == null ? <span className="faint">Pending</span> : <><strong>{show(row.final_grade)}</strong><div className="faint gs-small">{row.descriptor}</div></>}
            </td>
            <td>{row.result === 'Pending' ? <span className="faint">Pending</span> :
              <span className={`badge badge-${row.result === 'Passed' ? 'low' : 'critical'}`}>{row.result}</span>}</td>
          </tr>)}</tbody>
        </table></div></div>}
    </div>
  );
}

function ForecastCell({ prediction, completed }) {
  if (!prediction || prediction.predicted_final == null) return <span className="faint">No forecast yet</span>;
  const fallback = prediction.source === 'fallback';
  return <div className={completed ? 'faint' : ''}>
    <div className="gs-forecast-value">{show(prediction.predicted_final, 1)}
      {prediction.range_low != null && prediction.range_high != null &&
        <span className="faint gs-small"> ({show(prediction.range_low, 1)}–{show(prediction.range_high, 1)})</span>}
    </div>
    <span className={`badge badge-plain ${fallback ? 'badge-neutral' : 'badge-brand'}`} title={fallback ? prediction.error || 'AI service unavailable.' : prediction.model_version}>
      {fallback ? 'Rule-based estimate' : 'AI prediction'}
    </span>
    <div><AiPredictionBasis prediction={prediction} /></div>
    {prediction.created_at && <div className="gs-small faint">Forecast saved {new Date(prediction.created_at).toLocaleString()}</div>}
    {completed && <div className="gs-small faint">Earlier forecast · actual final grade available</div>}
  </div>;
}
