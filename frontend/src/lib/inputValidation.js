export const MOBILE_NUMBER_PATTERN = '09[0-9]{9}';
export const MOBILE_NUMBER_MESSAGE = 'Enter an 11-digit Philippine mobile number starting with 09.';
export const SCHOOL_NAME_MESSAGE = 'School name must contain letters, not just numbers or symbols.';

export function isMobileNumber(value) {
  return /^09[0-9]{9}$/.test(value.trim());
}

export function hasSchoolNameLetters(value) {
  return /\p{L}/u.test(value);
}

export function formatSchoolName(value) {
  return value.trim().replace(/\s+/g, ' ').toLowerCase()
    .replace(/(^|[\s\-/(])(\p{L})/gu, (_, separator, letter) => separator + letter.toUpperCase());
}
