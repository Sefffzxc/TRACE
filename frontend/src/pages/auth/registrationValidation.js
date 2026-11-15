/** Validate the controls actually shown in the selected registration form. */
export function validateRegistration(form, { mode, school, password, confirm, passwordScore }) {
  const errors = {};
  for (const input of form.querySelectorAll('input[id], select[id]')) {
    if (!input.willValidate || input.type === 'radio') continue;
    const label = (input.labels?.[0]?.textContent || input.getAttribute('aria-label') || 'This field')
      .replace(/\*/g, '').trim();
    const value = input.autocomplete === 'new-password' ? input.value : input.value.trim();
    if (input.required && !value) {
      errors[input.id] = `${label} is required.`;
    } else if (input.validity.typeMismatch) {
      errors[input.id] = 'Please enter a valid email address.';
    } else if (input.validity.badInput) {
      errors[input.id] = 'Please enter a valid number.';
    } else if (input.validity.rangeUnderflow || input.validity.rangeOverflow) {
      errors[input.id] = `Enter a value between ${input.min} and ${input.max}.`;
    } else if (input.validity.stepMismatch) {
      errors[input.id] = 'Please enter a whole number.';
    } else if (value && input.minLength > 0 && value.length < input.minLength) {
      errors[input.id] = `${label} must have at least ${input.minLength} characters.`;
    } else if (input.maxLength > 0 && value.length > input.maxLength) {
      errors[input.id] = `${label} must have no more than ${input.maxLength} characters.`;
    }
  }

  if (mode === 'teacher' && !form.elements.school_id.value) {
    errors.school_id = 'Please choose your school from the list.';
  }
  if (mode === 'school') {
    if (!school.school_type) errors.s_school_type = 'Please say whether the school is public or private.';
    if (!school.region) errors.s_province = 'Please choose the province from the list.';
    if (school.region && !school.city.trim()) errors.s_city = 'Please choose the city or municipality.';
    if (school.address.trim().length > 255) errors.s_street = 'Keep the combined street and barangay address within 255 characters.';
    if (school.tracks_units === null) errors.s_tracks = 'Please answer whether teachers track progress through numbered booklets.';
  }
  const passwordId = mode === 'teacher' ? 'reg_password' : 'a_password';
  const confirmId = mode === 'teacher' ? 'confirm' : 'a_confirm';
  if (password && password.length <= 128 && passwordScore < 4) {
    errors[passwordId] = 'Use at least 8 characters with an uppercase letter, a lowercase letter, a number and a symbol.';
  }
  if (confirm && password !== confirm) errors[confirmId] = 'The passwords do not match.';
  return errors;
}

/** Only known field failures belong beside a control; other errors stay global. */
export function registrationApiErrors(error, mode) {
  const account = mode === 'teacher'
    ? { school_id: 'school_id', first_name: 'first_name', middle_name: 'middle_name', last_name: 'last_name', email: 'reg_email', contact_number: 'contact', password: 'reg_password' }
    : { first_name: 'a_first', middle_name: 'a_middle', last_name: 'a_last', email: 'a_email', contact_number: 'a_contact', password: 'a_password' };
  const school = { name: 's_name', school_type: 's_school_type', address: 's_street', city: 's_city', province: 's_province', region: 's_province', contact_number: 's_contact', contact_email: 's_email', tracks_units: 's_tracks', unit_label_plural: 's_unit_plural', unit_label_singular: 's_unit_singular', default_units_per_subject: 's_unit_count', passing_mark: 's_passing', grading_periods: 's_periods', grading_period_label: 's_period_label' };
  const fields = {};
  let unmapped = false;
  if (error?.status === 422 && Array.isArray(error.payload?.detail)) {
    for (const issue of error.payload.detail) {
      const location = Array.isArray(issue.loc) ? issue.loc.filter((part) => part !== 'body') : [];
      const id = location[0] === 'captcha_token' ? 'captcha' :
        location[0] === 'school' && mode === 'school' ? school[location[1]] : account[location[0]];
      if (id) fields[id] = (issue.msg || 'Please check this value.').replace(/^Value error, /, '');
      else unmapped = true;
    }
  } else if (error?.status === 409 && error.message === 'An account with that email address already exists.') {
    fields[account.email] = error.message;
  } else if (mode === 'school' && error?.status === 409 && error.message?.includes('is already registered or awaiting approval.')) {
    fields.s_name = error.message;
  } else if (mode === 'teacher' && error?.status === 400 && error.message === 'Please choose a school from the list.') {
    fields.school_id = error.message;
  }
  return { fields, unmapped };
}
