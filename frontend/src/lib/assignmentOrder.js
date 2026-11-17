import { formatPersonName } from './personName';

const textOrder = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });
const SORT_FIELDS = ['grade', 'section', 'subject', 'teacher'];

function gradeRank(level, name) {
  for (const value of [level, name]) {
    const label = String(value ?? '').trim();
    if (/^(?:k|kg|kinder|kindergarten)$/i.test(label)) return 0;
    const number = label.match(/\d+/);
    if (number) return Number(number[0]);
  }
  return null;
}

/** Arrange display rows without changing saved assignments or their IDs. */
export function arrangeAssignments(assignments, sections, levels, sortBy = 'grade', direction = 'asc') {
  const sectionById = new Map(sections.map((section) => [String(section.section_id), section]));
  const levelById = new Map(levels.map((level) => [String(level.grade_level_id), level]));
  const multiplier = direction === 'desc' ? -1 : 1;
  const primary = SORT_FIELDS.includes(sortBy) ? sortBy : 'grade';
  const fields = [primary, ...SORT_FIELDS.filter((field) => field !== primary)];
  const rows = assignments.map((assignment) => {
    const section = sectionById.get(String(assignment.section_id));
    const grade = levelById.get(String(section?.grade_level_id));
    const gradeName = grade?.name || section?.grade_level_name || assignment.grade_level_name || '';
    return {
      row: { ...assignment, grade_level_name: gradeName, section_name: assignment.section_name || section?.name || '' },
      grade: gradeName,
      rank: gradeRank(grade?.level, gradeName),
      section: assignment.section_name || section?.name || '',
      subject: assignment.subject_name || 'All subjects',
      teacher: formatPersonName(assignment.teacher_name),
    };
  });

  rows.sort((left, right) => {
    for (const field of fields) {
      // Unavailable labels stay at the bottom in either direction.
      if (Boolean(left[field]) !== Boolean(right[field])) return left[field] ? -1 : 1;
      if (field === 'grade') {
        if ((left.rank == null) !== (right.rank == null)) return left.rank == null ? 1 : -1;
        if (left.rank !== right.rank) return (left.rank - right.rank) * multiplier;
      }
      const compared = textOrder.compare(left[field], right[field]);
      if (compared) return compared * multiplier;
    }
    return textOrder.compare(String(left.row.assignment_id), String(right.row.assignment_id));
  });
  return rows.map(({ row }) => row);
}
