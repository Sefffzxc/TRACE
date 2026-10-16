/**
 * Every call the UI makes to the API, named after what it does rather than
 * where it lives — so a route change on the server is a one-line edit here.
 */
import { api, deviceId } from './client';

/* ── Schools ─────────────────────────────────────────────────────────────────
 * One deployment, many schools. `directory` and `register` are the only two
 * calls here that work without signing in — a teacher has to be able to find
 * their school before they have an account, and a school head has to be able
 * to register one before anyone does.
 */
export const schoolApi = {
  // Public: approved schools only, name and city only.
  directory: (search) => api.get('/api/schools/directory', { search }, { auth: false }),
  register: (payload) => api.post('/api/schools/register', payload, { auth: false }),

  // The signed-in user's own school, and its settings.
  mine: () => api.get('/api/schools/me'),
  updateMine: (payload) => api.patch('/api/schools/me', payload),

  // System operator only.
  list: (params) => api.get('/api/schools', params),
  decide: (schoolId, action, reason) =>
    api.patch(`/api/schools/${schoolId}`, { action, reason }),
};

/* ── Authentication ──────────────────────────────────────────────────────── */
export const authApi = {
  register: (payload) => api.post('/api/auth/register', payload, { auth: false }),

  login: (email, password) =>
    api.post(
      '/api/auth/login',
      {
        email,
        password,
        device_id: deviceId(),
        device_name: navigator.userAgent.slice(0, 120),
      },
      { auth: false },
    ),

  verifyOtp: (userId, code) =>
    api.post(
      '/api/auth/verify-otp',
      {
        user_id: userId,
        code,
        device_id: deviceId(),
        device_name: navigator.userAgent.slice(0, 120),
      },
      { auth: false },
    ),

  forgotPassword: (email) => api.post('/api/auth/forgot-password', { email }, { auth: false }),

  verifyResetOtp: (email, code) =>
    api.post('/api/auth/verify-reset-otp', { email, code }, { auth: false }),

  resetPassword: (token, newPassword, confirmPassword) =>
    api.post(
      '/api/auth/reset-password',
      { token, new_password: newPassword, confirm_password: confirmPassword },
      { auth: false },
    ),

  logout: () => api.post('/api/auth/logout'),
  me: () => api.get('/api/auth/me'),
};

/* ── Staff ───────────────────────────────────────────────────────────────── */
export const staffApi = {
  list: (params) => api.get('/api/staff', params),
  teachers: () => api.get('/api/staff/teachers'),
  pending: () => api.get('/api/staff/pending'),
  auditLogs: (params) => api.get('/api/staff/audit-logs', params),
  me: () => api.get('/api/staff/me'),
  updateMe: (payload) => api.patch('/api/staff/me', payload),
  changePassword: (currentPassword, newPassword) =>
    api.post('/api/staff/me/password', {
      current_password: currentPassword,
      new_password: newPassword,
    }),
  uploadPhoto: (file) => {
    const form = new FormData();
    form.append('file', file);
    return api.post('/api/staff/me/photo', form);
  },
  removePhoto: () => api.delete('/api/staff/me/photo'),
  adminAction: (staffId, action, extra = {}) =>
    api.patch(`/api/staff/${staffId}`, { action, ...extra }),
  remove: (staffId) => api.delete(`/api/staff/${staffId}`),
};

/* ── Class management ────────────────────────────────────────────────────── */
export const schoolYearApi = {
  list: () => api.get('/api/school-years'),
  current: () => api.get('/api/school-years/current'),
  create: (payload) => api.post('/api/school-years', payload),
  update: (id, payload) => api.patch(`/api/school-years/${id}`, payload),
  remove: (id) => api.delete(`/api/school-years/${id}`),
};

export const gradeLevelApi = {
  list: () => api.get('/api/grade-levels'),
  create: (payload) => api.post('/api/grade-levels', payload),
  update: (id, payload) => api.patch(`/api/grade-levels/${id}`, payload),
  remove: (id) => api.delete(`/api/grade-levels/${id}`),
};

export const sectionApi = {
  list: (params) => api.get('/api/sections', params),
  create: (payload) => api.post('/api/sections', payload),
  update: (id, payload) => api.patch(`/api/sections/${id}`, payload),
  remove: (id) => api.delete(`/api/sections/${id}`),
};

export const subjectApi = {
  list: (params) => api.get('/api/subjects', params),
  create: (payload) => api.post('/api/subjects', payload),
  update: (id, payload) => api.patch(`/api/subjects/${id}`, payload),
  remove: (id) => api.delete(`/api/subjects/${id}`),
};

export const assignmentApi = {
  list: (params) => api.get('/api/teacher-assignments', params),
  create: (payload) => api.post('/api/teacher-assignments', payload),
  remove: (id) => api.delete(`/api/teacher-assignments/${id}`),
};

/* ── Students ────────────────────────────────────────────────────────────── */
export const studentApi = {
  list: (params) => api.get('/api/students', params),
  get: (id) => api.get(`/api/students/${id}`),
  profile: (id) => api.get(`/api/students/${id}/profile`),
  create: (payload) => api.post('/api/students', payload),
  update: (id, payload) => api.patch(`/api/students/${id}`, payload),
  remove: (id) => api.delete(`/api/students/${id}`),
};

export const enrollmentApi = {
  list: (params) => api.get('/api/enrollments', params),
  create: (payload) => api.post('/api/enrollments', payload),
  update: (id, payload) => api.patch(`/api/enrollments/${id}`, payload),
  remove: (id) => api.delete(`/api/enrollments/${id}`),
};

/* ── PACE & early warning ────────────────────────────────────────────────── */
export const paceApi = {
  list: (params) => api.get('/api/paces', params),
  create: (payload) => api.post('/api/paces', payload),
  update: (id, payload) => api.patch(`/api/paces/${id}`, payload),
  // `context` carries the section, subject and grading period of the class
  // record being saved. The server needs them to look up the highest possible
  // scores and the weights, which it will not accept from the client.
  bulkSave: (entries, context = {}) =>
    api.post('/api/paces/bulk', { entries, ...context }),
  remove: (id) => api.delete(`/api/paces/${id}`),
};

/** The class record's header: the HIGHEST POSSIBLE SCORE row and the weights. */
export const classRecordApi = {
  getSetup: (params) => api.get('/api/class-record/setup', params),
  saveSetup: (params, payload) =>
    api.put(
      `/api/class-record/setup?section_id=${params.section_id}` +
        `&subject_id=${params.subject_id}&grading_period=${params.grading_period}`,
      payload,
    ),
};

export const warningApi = {
  list: (params) => api.get('/api/early-warnings', params),
  summary: (params) => api.get('/api/early-warnings/summary', params),
  update: (id, payload) => api.patch(`/api/early-warnings/${id}`, payload),
};

/* ── Dashboard ───────────────────────────────────────────────────────────── */
export const dashboardApi = {
  load: () => api.get('/api/dashboard'),
  /* The principal's view: the school and its teachers. */
  admin: () => api.get('/api/dashboard/admin'),
};

/* ── AI risk classification ──────────────────────────────────────────────────
 * The trained model runs as its own service; the backend calls it and logs the
 * answer. `status` doubles as the warm-up ping — on free hosting the model
 * service sleeps after 15 minutes and takes about a minute to wake, so the app
 * pings it on load rather than letting the first save time out.
 */
export const aiApi = {
  status: (wake = true) => api.get('/api/ai/status', { wake }),
  modelCard: () => api.get('/api/ai/model-card'),
  predictions: (params) => api.get('/api/ai/predictions', params),
  prediction: (id) => api.get(`/api/ai/predictions/${id}`),
  summary: () => api.get('/api/ai/summary'),
  rescore: (payload = {}) => api.post('/api/ai/rescore', payload),
};
