import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Brain, ChevronDown, ClipboardList, GraduationCap, RefreshCw, Search } from 'lucide-react';
import { aiApi, assignmentApi, classRecordApi, sectionApi, subjectApi } from '../../api/endpoints';
import { EmptyState, ErrorNote, PageHead, RiskBadge, RiskLegend, TableSkeleton } from '../../components/common/ui';
import { AiPredictionBasis } from '../../components/common/ai';
import { useAuth } from '../../context/AuthContext';
import { useSchoolYear } from '../../context/SchoolYearContext';
import { useToast } from '../../context/ToastContext';
import { assignedSections, sectionLabel } from '../../lib/classAccess';
import { classRecordSelectionKey, readClassRecordSelection, subjectsForSection } from '../../lib/classRecordSelection';
import { show } from '../../lib/grading';
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
        subtitle={`Recorded grades and forecasts for ${year?.year || 'the selected school year'}.`}
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
      {summary && !busy && <div className="gs-meta">
        <p className="faint gs-note">{summary.rows.length} learner{summary.rows.length === 1 ? '' : 's'} · {finished} final grade{finished === 1 ? '' : 's'} ready · Passing mark {show(summary.passing_mark)}</p>
        <RiskLegend />
      </div>}
      {yearReady && !yearId ? <EmptyState icon={GraduationCap} title="No current school year"
        description="Ask your administrator to select a school year." /> :
        !readyCatalog && !catalogError ? <TableSkeleton rows={8} /> : busy ? <TableSkeleton rows={8} /> :
        !section || !subject ? <EmptyState icon={GraduationCap} title="No assigned classes"
          description="Your grade summaries appear when a section and subject are assigned to you." /> :
        error || catalogError ? null : !rows.length ? <EmptyState icon={GraduationCap}
          title={search ? 'No learner matches your search' : 'No learners enrolled'}
          description={search ? 'Try another name or student ID.' : 'Add learners to this section to see their grade summary.'} /> :
        <div className="card"><div className="table-wrap gs-scroll"><table className="table gs-table">
          <caption className="visually-hidden">{summary?.section_name} - {summary?.subject} grade summary, {year?.year}</caption>
          <thead><tr>
            <th scope="col">Student</th>
            {Array.from({ length: summary.period_count }, (_, n) => <th key={n} scope="col" className="right">{summary.period_label || periodLabel} {n + 1}</th>)}
            <th scope="col" className="right" title="Current average of completed periods">Average</th>
            <th scope="col" title="AI predicted final grade. Open a forecast for its basis and saved time.">Predicted final</th>
            <th scope="col" className="right" title={`Actual grade after all ${summary.period_count} periods are complete`}>Final grade</th>
            <th scope="col" title={`Actual final result. Passing mark: ${show(summary.passing_mark)}.`}>Result</th>
          </tr></thead>
          <tbody>{rows.map((row) => <tr key={row.enrollment_id}>
            <th scope="row" className="gs-student">
              <div className="gs-student-name">
                <Link to={`/students/${row.student_id}`} title={[formatPersonName(row.student_name), row.login_id, !row.risk_level && 'No standing yet'].filter(Boolean).join(' · ')}>{formatPersonName(row.student_name)}</Link>
                {row.risk_level ? <RiskBadge level={row.risk_level} compact /> : <span className="visually-hidden">No standing yet</span>}
              </div>
            </th>
            {row.term_grades.map((grade, n) => <td key={n} className="right" title={grade == null ? 'This period is not complete.' : undefined}>{show(grade)}</td>)}
            <td className="right cell-strong" title={`${row.completed_periods} of ${summary.period_count} periods complete`}>{show(row.current_average)}</td>
            <td className="gs-forecast"><ForecastCell prediction={row.ai} completed={row.final_grade != null} studentName={formatPersonName(row.student_name)} /></td>
            <td className={`right gs-final ${row.final_grade != null ? 'is-complete' : ''}`}>
              {row.final_grade == null ? <span className="faint" title="Final grade pending">—</span> : <strong title={row.descriptor}>{show(row.final_grade)}</strong>}
            </td>
            <td>{row.result === 'Pending' ? <span className="faint">Pending</span> :
              <span className={`badge badge-${row.result === 'Passed' ? 'low' : 'critical'}`}>{row.result}</span>}</td>
          </tr>)}</tbody>
        </table></div></div>}
    </div>
  );
}

function ForecastCell({ prediction, completed, studentName }) {
  if (!prediction || prediction.predicted_final == null) return <span className="faint" title="No forecast yet">—</span>;
  const fallback = prediction.source === 'fallback';
  const rangeLabel = prediction.range_low != null && prediction.range_high != null
    ? `, likely range ${show(prediction.range_low, 1)} to ${show(prediction.range_high, 1)}` : '';
  return <details className="gs-forecast-details">
    <summary className="gs-forecast-summary" aria-label={`${fallback ? 'Rule-based' : 'AI'} forecast for ${studentName}: ${show(prediction.predicted_final, 1)}${rangeLabel}. Details`}>
      <strong className={completed ? 'faint' : ''}>{show(prediction.predicted_final, 1)}</strong>
      {prediction.range_low != null && prediction.range_high != null &&
        <span className="faint gs-small" title="Likely range">({show(prediction.range_low, 1)}–{show(prediction.range_high, 1)})</span>}
      <span className={`badge badge-plain gs-source ${fallback ? 'badge-neutral' : 'badge-brand'}`}>
        {fallback ? 'Rule-based' : 'AI'}
      </span>
      <ChevronDown size={13} aria-hidden="true" />
    </summary>
    <div className="gs-forecast-meta">
      <div className="gs-small" title={fallback ? prediction.error || 'AI service unavailable.' : prediction.model_version}>{fallback ? 'Rule-based estimate' : 'AI prediction'} · estimated final grade</div>
      <div><AiPredictionBasis prediction={prediction} /></div>
      {prediction.created_at && <div className="gs-small faint">Forecast saved {new Date(prediction.created_at).toLocaleString()}</div>}
      {completed && <div className="gs-small faint">Earlier forecast · actual final grade available</div>}
    </div>
  </details>;
}
