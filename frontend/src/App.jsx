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
import PacePage from './pages/app/PacePage';
import EarlyWarningPage from './pages/app/EarlyWarningPage';
import AiInsightsPage from './pages/app/AiInsightsPage';
import TeachersPage from './pages/app/TeachersPage';
import ApprovalsPage from './pages/app/ApprovalsPage';
import ClassManagementPage from './pages/app/ClassManagementPage';
import SchedulePage from './pages/app/SchedulePage';
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

/** Administrator-only routes. */
function RequireAdmin({ children }) {
  const { isAdmin } = useAuth();
  if (!isAdmin) return <Navigate to="/dashboard" replace />;
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
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/students" element={<StudentsPage />} />
        <Route path="/students/:studentId" element={<StudentProfilePage />} />
        <Route path="/pace" element={<PacePage />} />
        <Route path="/early-warning" element={<EarlyWarningPage />} />
        <Route path="/ai-risk" element={<AiInsightsPage />} />
        <Route path="/schedule" element={<SchedulePage />} />
        <Route path="/account" element={<AccountPage />} />
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
