import { useEffect, useState } from "react";
import {
  ApiError,
  changePassword,
  createApiToken,
  endOtherSessions,
  listApiTokens,
  listSessions,
  resendVerification,
  revokeApiToken,
} from "../api";
import type { ApiToken, ApiTokenScope, Me, SessionView } from "../types";

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

      <ApiTokens />
    </div>
  );
}

/**
 * API tokens, for clients that can't hold a session — the MCP server is the
 * one that needs this.
 *
 * The value is shown once, at creation, and never again: the server keeps only
 * a hash of it. That's why the freshly-minted token stays on screen until it's
 * explicitly dismissed rather than disappearing on the next render.
 */
function ApiTokens() {
  const [tokens, setTokens] = useState<ApiToken[]>([]);
  const [name, setName] = useState("");
  const [canWrite, setCanWrite] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [minted, setMinted] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function refresh() {
    setTokens(await listApiTokens().catch(() => []));
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const scopes: ApiTokenScope[] = canWrite
        ? ["library:read", "library:write"]
        : ["library:read"];
      const created = await createApiToken({ name, scopes, currentPassword });
      setMinted(created.token);
      setName("");
      setCurrentPassword("");
      setCanWrite(false);
      await refresh();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleRevoke(id: string) {
    setError(null);
    try {
      await revokeApiToken(id);
      await refresh();
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <>
      <h3>API tokens</h3>
      <p className="field-hint">
        For clients that can't sign in through a browser — set one as{" "}
        <code>MCP_API_TOKEN</code> to point the MCP server at this library.
      </p>

      {minted && (
        <div className="form-card">
          <p className="field-hint">
            Copy this now. It won't be shown again — only a hash of it is stored.
          </p>
          <code className="token-value">{minted}</code>
          <button type="button" className="secondary-button" onClick={() => setMinted(null)}>
            Done
          </button>
        </div>
      )}

      {tokens.length > 0 && (
        <ul className="session-list">
          {tokens.map((token) => (
            <li key={token.id}>
              <span>
                {token.name} · <code>{token.prefix}…</code>
              </span>
              <span className="field-hint">
                {token.scopes.join(", ")} · last used{" "}
                {token.last_used_at ? when(token.last_used_at) : "never"}
              </span>
              <button
                type="button"
                className="secondary-button"
                onClick={() => handleRevoke(token.id)}
              >
                Revoke
              </button>
            </li>
          ))}
        </ul>
      )}

      <form onSubmit={handleCreate}>
        <label className="field">
          <span className="field-label">Name</span>
          <input
            type="text"
            placeholder="MCP server"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label className="field-inline">
          <input
            type="checkbox"
            checked={canWrite}
            onChange={(e) => setCanWrite(e.target.checked)}
          />
          <span>Allow saving workouts, not just reading them</span>
        </label>
        <label className="field">
          <span className="field-label">Current password</span>
          <input
            type="password"
            autoComplete="current-password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
          />
          <span className="field-hint">
            A token outlives the session that made it, so this asks again.
          </span>
        </label>
        <button
          type="submit"
          className="primary-button"
          disabled={saving || !name || !currentPassword}
        >
          {saving ? "Creating…" : "Create token"}
        </button>
      </form>

      {error && <p className="error">{error}</p>}
    </>
  );
}
