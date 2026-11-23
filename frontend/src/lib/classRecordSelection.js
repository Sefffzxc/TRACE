/** A teacher's last open sheet belongs to one account, school and school year. */
export function classRecordSelectionKey(user, yearId) {
  return user?.id && user?.school_id != null && yearId != null
    ? `trace.class-record.v1:${user.id}:${user.school_id}:${yearId}` : '';
}

export function readClassRecordSelection(key) {
  try {
    const value = key ? JSON.parse(localStorage.getItem(key)) : null;
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}

export function saveClassRecordSelection(key, { sectionId, subjectId, term }) {
  if (!key || !sectionId || !subjectId || !Number.isInteger(term)) return;
  try {
    localStorage.setItem(key, JSON.stringify({ sectionId, subjectId, term }));
  } catch {
    // A browser that disables storage can still open and edit its class records.
  }
}

export function subjectsForSection(section, subjects, access) {
  if (!section) return [];
  const forLevel = subjects.filter((subject) => subject.grade_level_id === section.grade_level_id);
  if (access.unrestricted || access.advisory?.some((id) => String(id) === String(section.section_id))) return forLevel;
  const taught = access.grade?.[String(section.section_id)] || [];
  return taught.includes('*') ? forLevel : forLevel.filter((subject) => taught.includes(subject.subject_name));
}
