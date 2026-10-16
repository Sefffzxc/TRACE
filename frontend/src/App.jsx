import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { GraduationCap } from 'lucide-react';

import { AuthProvider, useAuth } from './context/AuthContext';
import { ThemeProvider } from './context/ThemeContext';
import { ToastProvider } from './context/ToastContext';

import AppShell from './components/layout/AppShell';

import LoginPage from './pages/auth/LoginPage';
import RegisterPage from './pages/auth/RegisterPage';
import VerifyOtpPage from './pages/auth/VerifyOtpPage';
import ForgotPasswordPage from './pages/auth/ForgotPasswordPage';
import ResetPasswordPage from './pages/auth/ResetPasswordPage';

import DashboardPage from './pages/app/DashboardPage';
import StudentsPage from './pages/app/StudentsPage';
import StudentProfilePage from './pages/app/StudentProfilePage';
import RosterPage from './pages/app/RosterPage';
import ClassRecordPage from './pages/app/ClassRecordPage';
import EarlyWarningPage from './pages/app/EarlyWarningPage';
import AiInsightsPage from './pages/app/AiInsightsPage';
import TeachersPage from './pages/app/TeachersPage';
import ApprovalsPage from './pages/app/ApprovalsPage';
import ClassManagementPage from './pages/app/ClassManagementPage';
import SchoolsAdminPage from './pages/app/SchoolsAdminPage';
import SchoolSettingsPage from './pages/app/SchoolSettingsPage';
import AccountPage from './pages/app/AccountPage';
import NotFoundPage from './pages/app/NotFoundPage';

import './styles/tokens.css';
import './styles/base.css';
import './styles/auth.css';
import './styles/layout.css';
import './styles/pages.css';

function Splash({ message = 'Loading…' }) {
  return (
    <div className="loading-screen">
      <div className="lm">
        <span className="seal">
          <GraduationCap size={26} />
        </span>
        <span>{message}</span>
      </div>
    </div>
  );
}

/** Only lets signed-in staff through; everyone else lands on the sign-in page. */
function RequireAuth({ children }) {
  const { user, booting } = useAuth();
  if (booting) return <Splash message="Restoring your session…" />;
  if (!user) return <Navigate to="/login" replace />;
  return children;
}

/** Keeps signed-in staff out of the sign-in screens. */
function RequireGuest({ children }) {
  const { user, booting } = useAuth();
  if (booting) return <Splash />;
  if (user) return <Navigate to="/dashboard" replace />;
  return children;
}

/**
 * Teacher-only routes: the monitoring screens. The principal runs the school
 * and its teachers; following individual learners is the teachers' job, so the
 * route itself is closed to them, not just its menu entry.
 */
function RequireTeacher({ children }) {
  const { isAdmin } = useAuth();
  if (isAdmin) return <Navigate to="/dashboard" replace />;
  return children;
}

/** Administrator-only routes. */
function RequireAdmin({ children }) {
  const { isAdmin } = useAuth();
  if (!isAdmin) return <Navigate to="/dashboard" replace />;
  return children;
}

/**
 * System-operator-only routes. The mirror of RequireAdmin one level up: the
 * operator's console is about schools, not students, and nobody attached to a
 * school has any use for it.
 */
function RequireSuperAdmin({ children }) {
  const { isSuperAdmin } = useAuth();
  if (!isSuperAdmin) return <Navigate to="/dashboard" replace />;
  return children;
}

/**
 * The operator has no school, so every monitoring screen — the dashboard
 * included — would be empty for them. Send them where their work actually is.
 */
function Landing({ children }) {
  const { isSuperAdmin } = useAuth();
  if (isSuperAdmin) return <Navigate to="/schools" replace />;
  return children;
}

/**
 * Lesson-unit progress is not merely hidden from a school that does not track
 * units — the route itself goes away, so a bookmark or a typed URL cannot reach
 * a screen that has nothing to record.
 */
function RequireUnitTracking({ children }) {
  const { school, tracksUnits } = useAuth();
  /* Decide only once the school's settings have actually arrived. While the
     lookup is still in flight `tracksUnits` is false for want of an answer, and
     bouncing a teacher off their own screen on every refresh would be worse
     than showing it a moment early. */
  if (school && !tracksUnits) return <Navigate to="/dashboard" replace />;
  return children;
}

/**
 * The class record and the AI forecast belong to schools that grade by class
 * record. A school that tracks units records booklet progress instead and has
 * no grades for the model to read, so the routes close for it, not just the
 * menu entries. Decided only once the school's settings have arrived.
 */
function RequireGradeRecord({ children }) {
  const { school, tracksUnits } = useAuth();
  if (school && tracksUnits) return <Navigate to="/dashboard" replace />;
  return children;
}

function Router() {
  return (
    <Routes>
      {/* Signed out */}
      <Route
        path="/login"
        element={
          <RequireGuest>
            <LoginPage />
          </RequireGuest>
        }
      />
      <Route
        path="/register"
        element={
          <RequireGuest>
            <RegisterPage />
          </RequireGuest>
        }
      />
      <Route
        path="/verify"
        element={
          <RequireGuest>
            <VerifyOtpPage />
          </RequireGuest>
        }
      />
      <Route
        path="/forgot"
        element={
          <RequireGuest>
            <ForgotPasswordPage />
          </RequireGuest>
        }
      />
      <Route
        path="/reset"
        element={
          <RequireGuest>
            <ResetPasswordPage />
          </RequireGuest>
        }
      />

      {/* Signed in */}
      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route
          path="/dashboard"
          element={
            <Landing>
              <DashboardPage />
            </Landing>
          }
        />
        <Route
          path="/students"
          element={
            <RequireTeacher>
              <StudentsPage />
            </RequireTeacher>
          }
        />
        <Route
          path="/students/:studentId"
          element={
            <RequireTeacher>
              <StudentProfilePage />
            </RequireTeacher>
          }
        />
        {/* The class record exists at EVERY school: attendance and assessment
            marks are what the risk model reads, and a school that tracks no
            lesson units still has both. It used to be a tab inside the screen
            below, which meant a DepEd school could not reach it at all. */}
        <Route
          path="/class-record"
          element={
            <RequireTeacher>
              <RequireGradeRecord>
                <ClassRecordPage />
              </RequireGradeRecord>
            </RequireTeacher>
          }
        />
        <Route
          path="/pace"
          element={
            <RequireTeacher>
              <RequireUnitTracking>
                <RosterPage mode="units" />
              </RequireUnitTracking>
            </RequireTeacher>
          }
        />
        <Route
          path="/early-warning"
          element={
            <RequireTeacher>
              <EarlyWarningPage />
            </RequireTeacher>
          }
        />
        <Route
          path="/ai-risk"
          element={
            <RequireTeacher>
              <RequireGradeRecord>
                <AiInsightsPage />
              </RequireGradeRecord>
            </RequireTeacher>
          }
        />
        <Route path="/account" element={<AccountPage />} />
        <Route
          path="/schools"
          element={
            <RequireSuperAdmin>
              <SchoolsAdminPage />
            </RequireSuperAdmin>
          }
        />
        <Route
          path="/school-settings"
          element={
            <RequireAdmin>
              <SchoolSettingsPage />
            </RequireAdmin>
          }
        />
        <Route
          path="/teachers"
          element={
            <RequireAdmin>
              <TeachersPage />
            </RequireAdmin>
          }
        />
        <Route
          path="/approvals"
          element={
            <RequireAdmin>
              <ApprovalsPage />
            </RequireAdmin>
          }
        />
        <Route
          path="/class-management"
          element={
            <RequireAdmin>
              <ClassManagementPage />
            </RequireAdmin>
          }
        />
        <Route path="*" element={<NotFoundPage />} />
      </Route>

      <Route path="/" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <ThemeProvider>
      <ToastProvider>
        <AuthProvider>
          <BrowserRouter>
            <Router />
          </BrowserRouter>
        </AuthProvider>
      </ToastProvider>
    </ThemeProvider>
  );
}
