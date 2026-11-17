// Kept outside React so a request captures the year visible when it starts.
// A stale tab must never silently write its old record into a newly opened year.
let currentId = null;

export function setSchoolYearScope(id) {
  currentId = id == null ? null : Number(id);
}

export function getSchoolYearScope() {
  return currentId;
}
