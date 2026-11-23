export const MIN_STUDENT_BIRTH_DATE = '1900-01-01';

export function studentToday(now = new Date()) {
  const parts = new Intl.DateTimeFormat('en', {
    timeZone: 'Asia/Manila', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(now);
  const fields = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  return `${fields.year}-${fields.month}-${fields.day}`;
}

export function birthDateError(value, today = studentToday()) {
  if (!value) return '';
  const parsed = new Date(`${value}T00:00:00Z`);
  if (!/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(value) || Number.isNaN(parsed.getTime())
    || parsed.toISOString().slice(0, 10) !== value) return 'Enter a valid date of birth.';
  if (value < MIN_STUDENT_BIRTH_DATE) return 'Date of birth must be on or after January 1, 1900.';
  if (value > today) return 'Date of birth cannot be in the future.';
  return '';
}
