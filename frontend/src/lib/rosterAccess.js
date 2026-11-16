/** Roster creation belongs to teachers assigned to teach or advise a section. */
export function rosterSections(sections, access, role) {
  if (role !== 'teacher' || !access) return [];
  const allowed = new Set([
    ...Object.keys(access.grade || {}),
    ...(access.advisory || []).map(String),
  ]);
  return (sections || []).filter((section) => allowed.has(String(section.section_id)));
}

export function rosterGrades(gradeLevels, sections) {
  const allowed = new Set(sections.map((section) => String(section.grade_level_id)));
  return (gradeLevels || []).filter((level) => allowed.has(String(level.grade_level_id)));
}
