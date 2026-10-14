import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  ArrowDown,
  ArrowUp,
  ChevronsUpDown,
  Download,
  Pencil,
  Search,
  Trash2,
  UserPlus,
  Users,
} from 'lucide-react';
import {
  Avatar,
  ConfirmDialog,
  EmptyState,
  ErrorNote,
  PageHead,
  Pagination,
  RiskBadge,
  TableSkeleton,
} from '../../components/common/ui';
import StudentFormModal from '../../components/common/StudentFormModal';
import { gradeLevelApi, sectionApi, studentApi } from '../../api/endpoints';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

const PAGE_SIZE = 12;

const RISK_FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'critical', label: 'Critical' },
  { key: 'high', label: 'At risk' },
  { key: 'moderate', label: 'Watch' },
  { key: 'low', label: 'On track' },
];

export default function StudentsPage() {
  const navigate = useNavigate();
  const toast = useToast();
  // Standing is a grade, or a grade-shaped figure: the term grade once a
  // record is complete, and the coursework banked before that. One axis for
  // both, so a part-finished term and a finished one can sit in the same
  // column without the reader having to know which is which.
  const { isAdmin } = useAuth();
  const [params, setParams] = useSearchParams();

  const [students, setStudents] = useState([]);
  const [gradeLevels, setGradeLevels] = useState([]);
  const [sections, setSections] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [search, setSearch] = useState(params.get('q') || '');
  const [gradeFilter, setGradeFilter] = useState('');
  const [sectionFilter, setSectionFilter] = useState('');
  const [riskFilter, setRiskFilter] = useState('all');
  const [sort, setSort] = useState({ key: 'last_name', direction: 'asc' });
  const [page, setPage] = useState(1);

  const [editing, setEditing] = useState(null);
  const [deleting, setDeleting] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [studentRows, levels, sectionRows] = await Promise.all([
        studentApi.list(),
        gradeLevelApi.list(),
        sectionApi.list(),
      ]);
      setStudents(studentRows || []);
      setGradeLevels(levels || []);
      setSections(sectionRows || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Keep the URL in step with the search box so links stay shareable.
  useEffect(() => {
    const next = new URLSearchParams(params);
    if (search) next.set('q', search);
    else next.delete('q');
    setParams(next, { replace: true });
    setPage(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  const sectionOptions = useMemo(
    () => (gradeFilter ? sections.filter((s) => String(s.grade_level_id) === gradeFilter) : sections),
    [sections, gradeFilter],
  );

  const riskCounts = useMemo(() => {
    const counts = { all: students.length, critical: 0, high: 0, moderate: 0, low: 0 };
    students.forEach((student) => {
      counts[student.risk_level] = (counts[student.risk_level] || 0) + 1;
    });
    return counts;
  }, [students]);

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    return students.filter((student) => {
      if (term) {
        const haystack = `${student.full_name || ''} ${student.login_id || ''}`.toLowerCase();
        if (!haystack.includes(term)) return false;
      }
      if (gradeFilter && String(student.grade_level_id) !== gradeFilter) return false;
      if (sectionFilter && String(student.section_id) !== sectionFilter) return false;
      if (riskFilter !== 'all' && student.risk_level !== riskFilter) return false;
      return true;
    });
  }, [students, search, gradeFilter, sectionFilter, riskFilter]);

  const sorted = useMemo(() => {
    const rows = [...filtered];
    const { key, direction } = sort;
    rows.sort((a, b) => {
      let left = a[key];
      let right = b[key];
      if (key === 'last_name') {
        left = `${a.last_name} ${a.first_name}`;
        right = `${b.last_name} ${b.first_name}`;
      }
      if (typeof left === 'number' && typeof right === 'number') {
        return direction === 'asc' ? left - right : right - left;
      }
      const result = String(left ?? '').localeCompare(String(right ?? ''), undefined, {
        numeric: true,
      });
      return direction === 'asc' ? result : -result;
    });
    return rows;
  }, [filtered, sort]);

  const pageCount = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const visible = sorted.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  useEffect(() => {
    if (page > pageCount) setPage(1);
  }, [page, pageCount]);

  const toggleSort = (key) =>
    setSort((current) =>
      current.key === key
        ? { key, direction: current.direction === 'asc' ? 'desc' : 'asc' }
        : { key, direction: 'asc' },
    );

  const SortIcon = ({ column }) => {
    if (sort.key !== column) return <ChevronsUpDown size={12} style={{ opacity: 0.4 }} />;
    return sort.direction === 'asc' ? <ArrowUp size={12} /> : <ArrowDown size={12} />;
  };

  const exportCsv = () => {
    const header = [
      'Student ID',
      'Last name',
      'First name',
      'Grade level',
      'Section',
      'Year average',
      'Weakest subject',
      'Needs from here',
      'Subjects flagged',
      'Subjects tracked',
      'Risk',
    ];
    const lines = sorted.map((student) =>
      [
        student.login_id || '',
        student.last_name,
        student.first_name,
        student.grade_level_name || '',
        student.section_name || '',
        student.lowest_standing ?? '',
        student.weakest_subject ?? '',
        student.required_in_weakest ?? '',
        student.subjects_flagged ?? 0,
        student.subjects_tracked ?? 0,
        student.risk_level,
      ]
        .map((cell) => `"${String(cell).replace(/"/g, '""')}"`)
        .join(','),
    );
    const blob = new Blob([[header.join(','), ...lines].join('\n')], {
      type: 'text/csv;charset=utf-8;',
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `lbca-students-${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    URL.revokeObjectURL(url);
    toast.success(`Exported ${sorted.length} student records.`);
  };

  const removeStudent = async () => {
    setBusy(true);
    try {
      await studentApi.remove(deleting.student_id);
      toast.success(`${deleting.full_name} removed.`);
      setDeleting(null);
      load();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page">
      <PageHead
        title="Students"
        subtitle="Every learner being monitored, and what each would need across the terms that remain."
        actions={
          <>
            <button type="button" className="btn btn-secondary" onClick={exportCsv} disabled={!sorted.length}>
              <Download size={16} /> Export CSV
            </button>
            <button type="button" className="btn btn-primary" onClick={() => setEditing({})}>
              <UserPlus size={16} /> Add student
            </button>
          </>
        }
      />

      <ErrorNote message={error} />

      <div className="toolbar">
        <div className="search">
          <Search size={15} />
          <input
            className="input"
            type="search"
            placeholder="Search by name or student ID…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>

        <select
          className="select"
          value={gradeFilter}
          onChange={(event) => {
            setGradeFilter(event.target.value);
            setSectionFilter('');
            setPage(1);
          }}
          aria-label="Filter by grade level"
        >
          <option value="">All grade levels</option>
          {gradeLevels.map((level) => (
            <option key={level.grade_level_id} value={level.grade_level_id}>
              {level.name}
            </option>
          ))}
        </select>

        <select
          className="select"
          value={sectionFilter}
          onChange={(event) => {
            setSectionFilter(event.target.value);
            setPage(1);
          }}
          aria-label="Filter by section"
        >
          <option value="">All sections</option>
          {sectionOptions.map((section) => (
            <option key={section.section_id} value={section.section_id}>
              {section.name}
            </option>
          ))}
        </select>

        <div className="filter-chips">
          {RISK_FILTERS.map((filter) => (
            <button
              key={filter.key}
              type="button"
              className={`chip ${riskFilter === filter.key ? 'is-on' : ''}`}
              onClick={() => {
                setRiskFilter(filter.key);
                setPage(1);
              }}
            >
              {filter.label}
              <span className="chip-count">{riskCounts[filter.key] || 0}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="card">
        <div className="card-body flush">
          {loading ? (
            <TableSkeleton rows={8} columns={7} />
          ) : visible.length === 0 ? (
            <EmptyState
              icon={Users}
              title="No students match these filters"
              description="Try clearing the search box or choosing a different section."
              action={
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => {
                    setSearch('');
                    setGradeFilter('');
                    setSectionFilter('');
                    setRiskFilter('all');
                  }}
                >
                  Clear filters
                </button>
              }
            />
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th className="sortable" onClick={() => toggleSort('last_name')}>
                      <span className="th-inner">
                        Student <SortIcon column="last_name" />
                      </span>
                    </th>
                    <th>Section</th>
                    {/* The weakest subject, not an average. A learner doing
                        well in three subjects and failing the fourth still
                        needs the conversation, and a mean would hide them. */}
                    <th className="right sortable" onClick={() => toggleSort('lowest_standing')}>
                      <span className="th-inner">
                        Year average <SortIcon column="lowest_standing" />
                      </span>
                    </th>
                    <th>Weakest subject</th>
                    {/* The number a teacher can say out loud. "At risk"
                        is not advice; "needs 84 from here" is. */}
                    <th className="right sortable" onClick={() => toggleSort('required_in_weakest')}>
                      <span className="th-inner">
                        Needs from here <SortIcon column="required_in_weakest" />
                      </span>
                    </th>
                    <th className="right sortable" onClick={() => toggleSort('subjects_flagged')}>
                      <span className="th-inner">
                        Subjects flagged <SortIcon column="subjects_flagged" />
                      </span>
                    </th>
                    <th className="sortable" onClick={() => toggleSort('risk_score')}>
                      <span className="th-inner">
                        Risk <SortIcon column="risk_score" />
                      </span>
                    </th>
                    {isAdmin && <th className="actions">Actions</th>}
                  </tr>
                </thead>
                <tbody>
                  {visible.map((student) => (
                    <tr
                      key={student.student_id}
                      className="clickable"
                      onClick={() => navigate(`/students/${student.student_id}`)}
                    >
                      <td>
                        <div className="cell-name">
                          <Avatar name={student.full_name} src={student.photo} size="sm" />
                          <div>
                            <div className="cell-strong">{student.full_name}</div>
                            <div className="mono faint">{student.login_id}</div>
                          </div>
                        </div>
                      </td>
                      <td>
                        <div>{student.section_name || '—'}</div>
                        <div className="faint" style={{ fontSize: 'var(--text-2xs)' }}>
                          {student.grade_level_name || 'Not enrolled'}
                        </div>
                      </td>
                      <td className="right">
                        {student.lowest_standing === null ||
                        student.lowest_standing === undefined ? (
                          <span className="faint">—</span>
                        ) : (
                          <>
                            <span className="cell-strong">
                              {student.lowest_standing.toFixed(0)}
                            </span>
                            {/* A standing taken from coursework rather than
                                from a finished record is provisional, and
                                saying so is the difference between a grade
                                and an estimate. */}
                            {student.lowest_term_grade === null ||
                            student.lowest_term_grade === undefined ? (
                              <span className="faint" title="No term grade yet — this is the coursework banked so far">
                                {' '}*
                              </span>
                            ) : null}
                          </>
                        )}
                      </td>
                      <td>
                        <span className="faint">{student.weakest_subject || '—'}</span>
                      </td>
                      <td className="right">
                        {student.required_in_weakest === null ||
                        student.required_in_weakest === undefined ? (
                          <span
                            className="faint"
                            title={
                              student.terms_done
                                ? 'The year is complete — there are no terms left to average'
                                : 'No term finished yet'
                            }
                          >
                            —
                          </span>
                        ) : student.required_in_weakest > 100 ? (
                          // Above 100 is not an achievable average, so the
                          // line is already out of reach. Saying that is
                          // more use than printing an impossible target.
                          <span
                            className="cell-strong"
                            style={{ color: 'var(--crit-500)' }}
                            title={`Would need ${student.required_in_weakest.toFixed(0)} — out of reach`}
                          >
                            out of reach
                          </span>
                        ) : (
                          <span className="cell-strong">
                            {student.required_in_weakest.toFixed(0)}
                          </span>
                        )}
                      </td>
                      <td className="right">
                        {student.subjects_flagged ? (
                          <span className="cell-strong">
                            {student.subjects_flagged} of {student.subjects_tracked}
                          </span>
                        ) : (
                          <span className="faint">
                            none of {student.subjects_tracked || 0}
                          </span>
                        )}
                      </td>
                      <td>
                        <RiskBadge level={student.risk_level} />
                      </td>
                      {isAdmin && (
                        <td className="actions" onClick={(event) => event.stopPropagation()}>
                          <button
                            type="button"
                            className="btn-icon"
                            title="Edit student"
                            onClick={() => setEditing(student)}
                          >
                            <Pencil size={15} />
                          </button>
                          <button
                            type="button"
                            className="btn-icon"
                            title="Remove student"
                            onClick={() => setDeleting(student)}
                          >
                            <Trash2 size={15} />
                          </button>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {!loading && sorted.length > 0 && (
          <Pagination
            page={page}
            pageCount={pageCount}
            total={sorted.length}
            pageSize={PAGE_SIZE}
            onChange={setPage}
          />
        )}
      </div>

      {editing && (
        <StudentFormModal
          student={editing.student_id ? editing : null}
          gradeLevels={gradeLevels}
          sections={sections}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            load();
          }}
        />
      )}

      {deleting && (
        <ConfirmDialog
          title="Remove this student?"
          message={`${deleting.full_name} and all of their records will be permanently deleted. This cannot be undone.`}
          confirmLabel="Remove student"
          busy={busy}
          onConfirm={removeStudent}
          onClose={() => setDeleting(null)}
        />
      )}
    </div>
  );
}
