import { useEffect, useState } from "react";
import { ApiError, getMe, updateConfig } from "../api";
import { FEED_SPECS } from "../feeds";
import type { FeedPref, Me, UserConfigUpdate } from "../types";

/** Feed order is sent as a whole list, so both edits below return a new one. */
function setEnabled(feeds: FeedPref[], index: number, enabled: boolean): FeedPref[] {
  return feeds.map((feed, i) => (i === index ? { ...feed, enabled } : feed));
}

function move(feeds: FeedPref[], index: number, delta: number): FeedPref[] {
  const next = [...feeds];
  const [moved] = next.splice(index, 1);
  next.splice(index + delta, 0, moved);
  return next;
}

/**
 * Credentials are write-only: the server reports whether one is stored and
 * shows its last four characters, but never sends the value back. So the input
 * starts empty and an empty input means "leave the stored one alone" — the
 * browser only ever holds a credential the user just typed.
 */
type Draft = {
  ownerKey: string;
  ownerLocation: string;
  ownerProgram: string;
  ownerEnabled: boolean;
  memberKey: string;
  memberLocation: string;
  memberProgram: string;
  memberEnabled: boolean;
};

function draftFrom(me: Me): Draft {
  return {
    ownerKey: "",
    ownerLocation: me.config.wodify_owner.location ?? "",
    ownerProgram: me.config.wodify_owner.program ?? "",
    ownerEnabled: me.config.wodify_owner.enabled,
    memberKey: "",
    memberLocation: me.config.wodify_member.location ?? "",
    memberProgram: me.config.wodify_member.program ?? "",
    memberEnabled: me.config.wodify_member.enabled,
  };
}

function CredentialField({
  label,
  hint,
  stored,
  value,
  disabled,
  onChange,
  onClear,
}: {
  label: string;
  hint: string;
  stored: { is_set: boolean; masked?: string | null };
  value: string;
  disabled: boolean;
  onChange: (next: string) => void;
  onClear: () => void;
}) {
  return (
    <label className="field">
      <span className="field-label">
        {label}
        {stored.is_set && <span className="category-badge">Saved {stored.masked}</span>}
      </span>
      <input
        type="password"
        autoComplete="off"
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        placeholder={stored.is_set ? "Leave blank to keep the saved key" : "Not set"}
      />
      <span className="field-hint">
        {hint}
        {stored.is_set && (
          <>
            {" "}
            <button type="button" className="text-remove" onClick={onClear}>
              Remove saved key
            </button>
          </>
        )}
      </span>
    </label>
  );
}

export function Settings() {
  const [me, setMe] = useState<Me | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getMe()
      .then((loaded) => {
        if (cancelled) return;
        setMe(loaded);
        setDraft(draftFrom(loaded));
      })
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Could not reach the server."),
      );
    return () => {
      cancelled = true;
    };
  }, []);

  function patch(changes: Partial<Draft>) {
    setDraft((current) => (current ? { ...current, ...changes } : current));
    setStatus(null);
  }

  async function save(update: UserConfigUpdate, message: string) {
    setSaving(true);
    setError(null);
    setStatus(null);
    try {
      const next = await updateConfig(update);
      setMe(next);
      // Re-seed from the server so cleared credentials and trimmed values are
      // reflected, and the key inputs go back to empty.
      setDraft(draftFrom(next));
      setStatus(message);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the server.");
    } finally {
      setSaving(false);
    }
  }

  if (error && !me) return <p className="error">{error}</p>;
  if (!me || !draft) return <p className="empty-state">Loading…</p>;

  const secretsOff = !me.secrets_available;

  return (
    <div className="settings">
      <div className="builder-heading">
        <h2>Settings</h2>
        <p className="section-sub">
          Connect your gym so its programming shows up alongside the crossfit.com WOD.
        </p>
      </div>

      {secretsOff && (
        <p className="error">
          This server has no encryption keys configured, so API keys can’t be saved. Set
          SECRETS_KEYS and restart. Other settings still save normally.
        </p>
      )}

      <section className="form-card">
        <div className="builder-section-head">
          <h3>Home page feeds</h3>
          <p className="section-sub">
            Which workout sources appear on your home page, and in what order. Hiding a feed
            doesn’t disconnect it — your gym credentials stay saved either way.
          </p>
        </div>

        <ul className="feed-pref-list">
          {me.config.feeds.map((feed, index) => (
            <li key={feed.kind} className="feed-pref-row">
              <label className="feed-pref-toggle">
                <input
                  type="checkbox"
                  checked={feed.enabled}
                  disabled={saving}
                  onChange={(event) =>
                    save(
                      { feeds: setEnabled(me.config.feeds, index, event.target.checked) },
                      event.target.checked
                        ? `${FEED_SPECS[feed.kind].heading} shown on your home page.`
                        : `${FEED_SPECS[feed.kind].heading} hidden.`,
                    )
                  }
                />
                <span>{FEED_SPECS[feed.kind].heading}</span>
              </label>
              <div className="feed-pref-order">
                <button
                  className="secondary-button"
                  aria-label={`Move ${FEED_SPECS[feed.kind].heading} up`}
                  disabled={saving || index === 0}
                  onClick={() => save({ feeds: move(me.config.feeds, index, -1) }, "Order saved.")}
                >
                  ↑
                </button>
                <button
                  className="secondary-button"
                  aria-label={`Move ${FEED_SPECS[feed.kind].heading} down`}
                  disabled={saving || index === me.config.feeds.length - 1}
                  onClick={() => save({ feeds: move(me.config.feeds, index, 1) }, "Order saved.")}
                >
                  ↓
                </button>
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section className="form-card">
        <div className="builder-section-head">
          <h3>My gym’s whiteboard</h3>
          <p className="section-sub">
            For gym members. Works only if your gym has turned on Wodify’s public whiteboard —
            the key comes from the URL they publish.
          </p>
        </div>

        <CredentialField
          label="Whiteboard key"
          hint="The WhiteboardKey value from your gym’s public whiteboard link."
          stored={me.config.wodify_member.whiteboard_key}
          value={draft.memberKey}
          disabled={secretsOff}
          onChange={(memberKey) => patch({ memberKey })}
          onClear={() => save({ wodify_member: { whiteboard_key: "" } }, "Whiteboard key removed.")}
        />

        <div className="field-grid">
          <label className="field">
            <span className="field-label">
              Location <span className="optional">optional</span>
            </span>
            <input
              value={draft.memberLocation}
              onChange={(e) => patch({ memberLocation: e.target.value })}
              placeholder="e.g. Main"
            />
          </label>
          <label className="field">
            <span className="field-label">
              Program <span className="optional">optional</span>
            </span>
            <input
              value={draft.memberProgram}
              onChange={(e) => patch({ memberProgram: e.target.value })}
              placeholder="e.g. CrossFit"
            />
          </label>
        </div>

        <label className="field checkbox-field">
          <input
            type="checkbox"
            checked={draft.memberEnabled}
            onChange={(e) => patch({ memberEnabled: e.target.checked })}
          />
          {/* Selects which credential fetches the gym, not whether the feed is
              on the home page — that's the "Home page feeds" list above. */}
          <span className="field-label">Use this connection</span>
        </label>

        <button
          type="button"
          className="primary-button"
          disabled={saving}
          onClick={() =>
            save(
              {
                wodify_member: {
                  // Omitted when blank, so the stored key survives this save.
                  ...(draft.memberKey ? { whiteboard_key: draft.memberKey } : {}),
                  location: draft.memberLocation,
                  program: draft.memberProgram,
                  enabled: draft.memberEnabled,
                },
              },
              "Whiteboard settings saved.",
            )
          }
        >
          {saving ? "Saving…" : "Save"}
        </button>
      </section>

      <section className="form-card">
        <div className="builder-section-head">
          <h3>Gym owner API key</h3>
          <p className="section-sub">
            For gym owners and admins. Generate a key in Wodify under Automations →
            Integrations → API Keys. Location and program must match your Wodify setup exactly.
          </p>
        </div>

        <CredentialField
          label="API key"
          hint="Sent to Wodify as the x-api-key header. Stored encrypted."
          stored={me.config.wodify_owner.api_key}
          value={draft.ownerKey}
          disabled={secretsOff}
          onChange={(ownerKey) => patch({ ownerKey })}
          onClear={() => save({ wodify_owner: { api_key: "" } }, "API key removed.")}
        />

        <div className="field-grid">
          <label className="field">
            <span className="field-label">Location</span>
            <input
              value={draft.ownerLocation}
              onChange={(e) => patch({ ownerLocation: e.target.value })}
              placeholder="Exact location name in Wodify"
            />
          </label>
          <label className="field">
            <span className="field-label">Program</span>
            <input
              value={draft.ownerProgram}
              onChange={(e) => patch({ ownerProgram: e.target.value })}
              placeholder="Exact program name in Wodify"
            />
          </label>
        </div>

        <label className="field checkbox-field">
          <input
            type="checkbox"
            checked={draft.ownerEnabled}
            onChange={(e) => patch({ ownerEnabled: e.target.checked })}
          />
          {/* Selects which credential fetches the gym, not whether the feed is
              on the home page — that's the "Home page feeds" list above. */}
          <span className="field-label">Use this connection</span>
        </label>

        <button
          type="button"
          className="primary-button"
          disabled={saving}
          onClick={() =>
            save(
              {
                wodify_owner: {
                  ...(draft.ownerKey ? { api_key: draft.ownerKey } : {}),
                  location: draft.ownerLocation,
                  program: draft.ownerProgram,
                  enabled: draft.ownerEnabled,
                },
              },
              "API settings saved.",
            )
          }
        >
          {saving ? "Saving…" : "Save"}
        </button>
      </section>

      {error && <p className="error">{error}</p>}
      {status && <p className="field-hint">{status}</p>}
    </div>
  );
}
