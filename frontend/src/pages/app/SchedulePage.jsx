import { useCallback, useEffect, useMemo, useState } from 'react';
import { CalendarDays, Plus, Trash2 } from 'lucide-react';
import {
  ConfirmDialog,
  EmptyState,
  ErrorNote,
  Modal,
  PageHead,
} from '../../components/common/ui';
import {
  scheduleApi,
  schoolYearApi,
  sectionApi,
  staffApi,
  subjectApi,
} from '../../api/endpoints';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'];
const PERIODS = [
  ['07:30', '08:30'],
  ['08:30', '09:30'],
  ['09:45', '10:45'],
  ['10:45', '11:45'],
  ['13:00', '14:00'],
  ['14:00', '15:00'],
  ['15:15', '16:15'],
];

/**
 * The weekly timetable. Administrators build it; teachers see their own.
 * Clashes (same teacher, same section or same room at overlapping times) are
 * rejected by the server, and the message is surfaced as-is.
 */
export default function SchedulePage() {
  const toast = useToast();
  const { isAdmin, user } = useAuth();

  const [schedules, setSchedules] = useState([]);
  const [sections, setSections] = useState([]);
  const [subjects, setSubjects] = useState([]);
  const [teachers, setTeachers] = useState([]);
  const [year, setYear] = useState(null);
  const [sectionId, setSectionId] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [adding, setAdding] = useState(null);
  const [deleting, setDeleting] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [scheduleRows, sectionRows, subjectRows, teacherRows, currentYear] = await Promise.all([
        scheduleApi.list(isAdmin ? {} : { teacher_id: user.id }),
        sectionApi.list(),
        subjectApi.list({ is_active: true }),
        staffApi.list({ role: 'teacher' }),
        schoolYearApi.current(),
      ]);
      setSchedules(scheduleRows || []);
      setSections(sectionRows || []);
      setSubjects(subjectRows || []);
      setTeachers(teacherRows || []);
      setYear(currentYear);
      if (isAdmin && sectionRows?.length && !sectionId) {
        setSectionId(String(sectionRows[0].section_id));
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAdmin, user?.id]);

  useEffect(() => {
    load();
  }, [load]);

  const visible = useMemo(
    () =>
      isAdmin && sectionId
        ? schedules.filter((row) => String(row.section_id) === sectionId)
        : schedules,
    [schedules, sectionId, isAdmin],
  );

  const grid = useMemo(() => {
    const map = {};
    visible.forEach((row) => {
      map[`${row.day_of_week}|${row.start_time}`] = row;
    });
    return map;
  }, [visible]);

  const save = async (payload) => {
    setBusy(true);
    try {
      await scheduleApi.create(payload);
      toast.success('Class added to the timetable.');
      setAdding(null);
      load();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await scheduleApi.remove(deleting.schedule_id);
      toast.success('Class removed.');
      setDeleting(null);
      load();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="page">
        <div className="skeleton" style={{ height: 420, borderRadius: 'var(--radius-lg)' }} />
      </div>
    );
  }

  return (
    <div className="page">
      <PageHead
        title={isAdmin ? 'Timetable' : 'My timetable'}
        subtitle={
          isAdmin
            ? 'Weekly class schedule. The system blocks double-booked teachers, sections and rooms.'
            : 'Your weekly teaching schedule.'
        }
        actions={
          isAdmin && (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => setAdding({})}
              disabled={!year || !sections.length}
            >
              <Plus size={16} /> Add class
            </button>
          )
        }
      />

      <ErrorNote message={error} />

      {isAdmin && sections.length > 0 && (
        <div className="toolbar">
          <select
            className="select"
            value={sectionId}
            onChange={(event) => setSectionId(event.target.value)}
            aria-label="Section"
          >
            {sections.map((section) => (
              <option key={section.section_id} value={section.section_id}>
                {section.name}
              </option>
            ))}
          </select>
          <span className="spacer" />
          <span className="result-count">
            {visible.length} class{visible.length === 1 ? '' : 'es'} scheduled
          </span>
        </div>
      )}

      <div className="card">
        <div className="card-body">
          {visible.length === 0 ? (
            <EmptyState
              icon={CalendarDays}
              title="Nothing scheduled yet"
              description={
                isAdmin
                  ? 'Add classes to build the weekly timetable for this section.'
                  : 'Once an administrator schedules your classes they will appear here.'
              }
            />
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table className="timetable">
                <thead>
                  <tr>
                    <th style={{ width: 92 }}>Time</th>
                    {DAYS.map((day) => (
                      <th key={day}>{day}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {PERIODS.map(([start, end]) => (
                    <tr key={start}>
                      <th style={{ whiteSpace: 'nowrap' }}>
                        {start}–{end}
                      </th>
                      {DAYS.map((day) => {
                        const row = grid[`${day}|${start}`];
                        return (
                          <td key={day}>
                            {row ? (
                              <div className="slot filled">
                                <span className="s">{row.subject_name}</span>
                                <span className="m">{isAdmin ? row.teacher_name : row.section_name}</span>
                                <span className="m">{row.room}</span>
                                {isAdmin && (
                                  <button
                                    type="button"
                                    className="btn-icon"
                                    style={{ padding: 2, marginTop: 2 }}
                                    title="Remove"
                                    onClick={() => setDeleting(row)}
                                  >
                                    <Trash2 size={12} />
                                  </button>
                                )}
                              </div>
                            ) : (
                              <div className="slot">—</div>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {adding && (
        <ScheduleModal
          year={year}
          sections={sections}
          subjects={subjects}
          teachers={teachers}
          defaultSectionId={sectionId}
          busy={busy}
          onSave={save}
          onClose={() => setAdding(null)}
        />
      )}

      {deleting && (
        <ConfirmDialog
          title="Remove this class?"
          message={`${deleting.subject_name} on ${deleting.day_of_week} at ${deleting.start_time} will be removed from the timetable.`}
          confirmLabel="Remove"
          busy={busy}
          onConfirm={remove}
          onClose={() => setDeleting(null)}
        />
      )}
    </div>
  );
}

function ScheduleModal({ year, sections, subjects, teachers, defaultSectionId, busy, onSave, onClose }) {
  const [form, setForm] = useState({
    section_id: defaultSectionId || '',
    subject_id: '',
    teacher_id: '',
    day_of_week: DAYS[0],
    period: 0,
    room: '',
  });

  const set = (field) => (event) => setForm((current) => ({ ...current, [field]: event.target.value }));

  const section = sections.find((item) => String(item.section_id) === String(form.section_id));
  const subjectOptions = section
    ? subjects.filter((item) => item.grade_level_id === section.grade_level_id)
    : subjects;

  const submit = (event) => {
    event.preventDefault();
    const [start, end] = PERIODS[Number(form.period)];
    onSave({
      school_year_id: year.school_year_id,
      section_id: Number(form.section_id),
      subject_id: Number(form.subject_id),
      teacher_id: form.teacher_id,
      day_of_week: form.day_of_week,
      start_time: start,
      end_time: end,
      room: form.room || section?.room || 'TBA',
    });
  };

  return (
    <Modal
      title="Add a class"
      subtitle={year ? `School year ${year.year}` : undefined}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" form="schedule-form" className="btn btn-primary" disabled={busy}>
            {busy ? 'Saving…' : 'Add to timetable'}
          </button>
        </>
      }
    >
      <form id="schedule-form" className="stack" onSubmit={submit}>
        <div className="field">
          <label className="field-label" htmlFor="sch_section">
            Section <span className="req">*</span>
          </label>
          <select id="sch_section" className="select" value={form.section_id} onChange={set('section_id')} required>
            <option value="">Choose…</option>
            {sections.map((item) => (
              <option key={item.section_id} value={item.section_id}>
                {item.name}
              </option>
            ))}
          </select>
        </div>

        <div className="form-grid">
          <div className="field">
            <label className="field-label" htmlFor="sch_subject">
              Subject <span className="req">*</span>
            </label>
            <select id="sch_subject" className="select" value={form.subject_id} onChange={set('subject_id')} required>
              <option value="">Choose…</option>
              {subjectOptions.map((item) => (
                <option key={item.subject_id} value={item.subject_id}>
                  {item.subject_name}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="sch_teacher">
              Teacher <span className="req">*</span>
            </label>
            <select id="sch_teacher" className="select" value={form.teacher_id} onChange={set('teacher_id')} required>
              <option value="">Choose…</option>
              {teachers.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.first_name} {item.last_name}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="sch_day">
              Day <span className="req">*</span>
            </label>
            <select id="sch_day" className="select" value={form.day_of_week} onChange={set('day_of_week')}>
              {DAYS.map((day) => (
                <option key={day}>{day}</option>
              ))}
            </select>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="sch_period">
              Period <span className="req">*</span>
            </label>
            <select id="sch_period" className="select" value={form.period} onChange={set('period')}>
              {PERIODS.map(([start, end], index) => (
                <option key={start} value={index}>
                  {start}–{end}
                </option>
              ))}
            </select>
          </div>
          <div className="field span-2">
            <label className="field-label" htmlFor="sch_room">
              Room
            </label>
            <input
              id="sch_room"
              className="input"
              placeholder={section?.room || 'e.g. Room 701'}
              value={form.room}
              onChange={set('room')}
            />
          </div>
        </div>
      </form>
    </Modal>
  );
}
