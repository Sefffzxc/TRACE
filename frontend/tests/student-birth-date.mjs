import assert from 'node:assert/strict';
import { birthDateError, studentToday } from '../src/lib/studentBirthDate.js';

assert.equal(studentToday(new Date('2026-11-22T15:59:59Z')), '2026-11-22');
assert.equal(studentToday(new Date('2026-11-22T16:00:00Z')), '2026-11-23');
for (const value of ['', '1900-01-01', '2016-02-29', '2026-11-23']) {
  assert.equal(birthDateError(value, '2026-11-23'), '', value);
}
for (const value of ['2028-01-01', '2026-11-24']) {
  assert.match(birthDateError(value, '2026-11-23'), /cannot be in the future/, value);
}
assert.match(birthDateError('1899-12-31', '2026-11-23'), /1900/);
for (const value of ['2026-02-30', '2015-02-29', 'invalid', '2026-13-01']) {
  assert.match(birthDateError(value, '2026-11-23'), /valid date/, value);
}
process.stdout.write('Passed: Philippine date boundary, future/historical birth dates and real calendar dates.\n');
