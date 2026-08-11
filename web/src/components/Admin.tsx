import { useEffect, useState } from "react";
import { ApiError, createInvite, listInvites, revokeInvite } from "../api";
import { inviteState } from "../invites";
import type { InviteStatus } from "../invites";
import type { Invite, Role } from "../types";

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : "Could not reach the server.";
}

function when(value: string | null): string {
  if (!value) return "unknown";
  return new Date(value).toLocaleString();
}

/** The one line under an invite saying where it stands. */
function stateLabel(status: InviteStatus, at: string): string {
  if (status === "redeemed") return `Redeemed ${when(at)}`;
  if (status === "expired") return `Expired ${when(at)}`;
  return `Pending, expires ${when(at)}`;
}

/**
 * Invite administration, for admins only.
 *
 * Registration is invite-only and invites come from admins, so this is the
 * only way to let a new person in short of shell access to the server (see
 * `scripts/create_admin.py`). The endpoints behind it 404 rather than 403 for
 * everyone else, so the tab is hidden for non-admins to match — a visible tab
 * that always errors would read as a bug.
 *
 * A minted link is shown once and stays on screen until dismissed. Unlike an
 * API token this isn't because the server forgets it — the token is recoverable
 * in principle — but the response is the only place it's ever handed over, and
 * re-rendering the list would drop it.
 */
export function Admin() {
  const [invites, setInvites] = useState<Invite[]>([]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("user");
  const [minted, setMinted] = useState<{ link: string; emailed: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function refresh() {
    setInvites(await listInvites().catch(() => []));
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      // An empty field means an open invite anyone holding the link may
      // redeem, which the API spells as a null address rather than "".
      const created = await createInvite(email.trim() || null, role);
      setMinted({ link: created.link, emailed: created.emailed });
      setEmail("");
      setRole("user");
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
      await revokeInvite(id);
      await refresh();
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <section className="panel">
      <h2>Admin</h2>
      <p className="section-sub">
        Registration is invite-only. Mint a link here and send it to whoever should have an
        account.
      </p>

      <h3>Invites</h3>

      {minted && (
        <div className="form-card">
          <p className="field-hint">
            {minted.emailed
              ? "Emailed to the address on the invite. The link is here too, in case it doesn't arrive."
              : "Send this link to the person you're inviting."}
          </p>
          <code className="token-value">{minted.link}</code>
          <button type="button" className="secondary-button" onClick={() => setMinted(null)}>
            Done
          </button>
        </div>
      )}

      {invites.length > 0 && (
        <ul className="session-list">
          {invites.map((invite) => {
            const { status, at, revocable } = inviteState(invite);
            return (
              <li key={invite.id}>
                <span>
                  {invite.email ?? "Anyone with the link"} · {invite.role}
                </span>
                <span className="field-hint">{stateLabel(status, at)}</span>
                {revocable && (
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() => handleRevoke(invite.id)}
                  >
                    Revoke
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}

      <form onSubmit={handleCreate}>
        <label className="field">
          <span className="field-label">Email optional</span>
          <input
            type="email"
            placeholder="athlete@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <span className="field-hint">
            Naming an address locks the invite to it and skips the confirmation email, since
            delivery already proves they own the mailbox. Leave it blank for a link anyone can
            redeem once.
          </span>
        </label>
        <label className="field">
          <span className="field-label">Role</span>
          <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
            <option value="user">user</option>
            <option value="staff">staff</option>
            <option value="admin">admin</option>
          </select>
        </label>
        <button type="submit" className="primary-button" disabled={saving}>
          {saving ? "Creating…" : "Create invite"}
        </button>
      </form>

      {error && <p className="error">{error}</p>}
    </section>
  );
}
