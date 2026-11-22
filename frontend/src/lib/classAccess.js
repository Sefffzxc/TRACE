/** Match the monitoring pickers to the classes the API lets this user see. */
export function assignedSections(sections, access) {
  const rows = sections || [];
  if (access?.unrestricted === true) return rows;
  const allowed = new Set([
    ...Object.keys(access?.grade || {}),
    ...(access?.advisory || []).map(String),
  ]);
  return rows.filter((section) => allowed.has(String(section.section_id)));
}

/** IDs keep identically named sections in different grades separate. */
export function matchesSection(row, section) {
  if (!section) return true;
  return row.section_id != null
    ? String(row.section_id) === String(section.section_id)
    : row.section_name === section.name;
}

export function sectionLabel(section, sections, alwaysIncludeGrade = false) {
  const duplicate = sections.some((other) => other.section_id !== section.section_id && other.name === section.name);
  return (alwaysIncludeGrade || duplicate) && section.grade_level_name
    ? `${section.grade_level_name} - ${section.name}` : section.name;
}
