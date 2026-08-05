import { useEffect, useState } from "react";
import {
  ApiError,
  changePassword,
  endOtherSessions,
  listSessions,
  resendVerification,
} from "../api";
import type { Me, SessionView } from "../types";

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : "Could not reach the server.";
}

function when(value: string | null): string {
  if (!value) return "unknown";
  return new Date(value).toLocaleString();
}

/**
 * The account half of Settings: who you are, your password, and where you're
 * signed in.
 *
 * Nothing here can read a credential back — the same rule as the gym keys
 * above it. Changing a password requires the current one even though you're
 * already signed in, because sessions are long-lived and a borrowed laptop
 * shouldn't be enough to lock the owner out.
 */
export function Account({ me }: { me: Me }) {
  const [sessions, setSessions] = useState<SessionView[]>([]);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function refreshSessions() {
    setSessions(await listSessions().catch(() => []));
  }

  useEffect(() => {
    void refreshSessions();
  }, []);

  async function handleChangePassword(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setStatus(null);
    try {
      await changePassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      setStatus("Password changed. Every other device has been signed out.");
      await refreshSessions();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleEndOthers() {
    setError(null);
    setStatus(null);
    try {
      await endOtherSessions();
      setStatus("Signed out everywhere else.");
      await refreshSessions();
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function handleResend() {
    setError(null);
    setStatus(null);
    try {
      await resendVerification();
      setStatus("Confirmation email sent.");
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <div className="settings-section">
      <h2>Account</h2>

      <p className="field-hint">
        Signed in as <strong>{me.email}</strong>
        {me.role !== "user" && ` · ${me.role}`}
      </p>

      {!me.email_verified && (
        <p className="field-hint">
          Your email address isn't confirmed yet.{" "}
          <button type="button" className="link-button" onClick={handleResend}>
            Resend the confirmation email
          </button>
        </p>
      )}

      <form onSubmit={handleChangePassword}>
        <h3>Change password</h3>
        <label className="field">
          <span className="field-label">Current password</span>
          <input
            type="password"
            autoComplete="current-password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
          />
        </label>
        <label className="field">
          <span className="field-label">New password</span>
          <input
            type="password"
            autoComplete="new-password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
          />
          <span className="field-hint">At least 12 characters.</span>
        </label>
        <button
          type="submit"
          className="primary-button"
          disabled={saving || !currentPassword || newPassword.length < 12}
        >
          {saving ? "Saving…" : "Change password"}
        </button>
      </form>

      <h3>Signed-in devices</h3>
      {sessions.length === 0 ? (
        <p className="field-hint">No other sessions.</p>
      ) : (
        <ul className="session-list">
          {sessions.map((session, i) => (
            <li key={i}>
              <span>{session.user_agent ?? "Unknown device"}</span>
              <span className="field-hint">Last used {when(session.last_seen_at)}</span>
            </li>
          ))}
        </ul>
      )}
      {sessions.length > 1 && (
        <button type="button" className="secondary-button" onClick={handleEndOthers}>
          Sign out everywhere else
        </button>
      )}

      {error && <p className="error">{error}</p>}
      {status && <p className="field-hint">{status}</p>}
    </div>
  );
}
