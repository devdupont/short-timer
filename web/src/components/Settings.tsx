import { useEffect, useState } from "react";
import { ApiError, getGymHealth, getMe, listGymProviders, updateConfig } from "../api";
import { Account } from "./Account";
import { FEED_SPECS } from "../feeds";
import type {
  FeedPref,
  GymConnection,
  GymConnectionHealth,
  GymProvider,
  GymProviderInfo,
  Me,
  UserConfigUpdate,
} from "../types";

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
interface ConnectionDraft {
  credential: string;
  location: string;
  program: string;
  enabled: boolean;
}

type Drafts = Partial<Record<GymProvider, ConnectionDraft>>;

function draftFor(connection: GymConnection | undefined): ConnectionDraft {
  return {
    credential: "",
    location: connection?.location ?? "",
    program: connection?.program ?? "",
    enabled: connection?.enabled ?? false,
  };
}

function draftsFrom(me: Me, providers: GymProviderInfo[]): Drafts {
  const drafts: Drafts = {};
  for (const info of providers) {
    drafts[info.provider] = draftFor(me.config.gyms.find((g) => g.provider === info.provider));
  }
  return drafts;
}

function since(iso: string): string {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 2) return "just now";
  if (minutes < 60) return `${minutes} minutes ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  return `${Math.round(hours / 24)} days ago`;
}

/**
 * The one line that distinguishes "your key is wrong" from "your gym is quiet".
 *
 * Fetchers deliberately swallow their errors so a single bad day can't empty a
 * feed, which leaves those two cases looking identical from the outside. A
 * connection that is switched on and has *never* fetched is the actionable one.
 */
function ConnectionStatus({
  connection,
  health,
  active,
}: {
  connection: GymConnection | undefined;
  health: GymConnectionHealth | undefined;
  active: boolean;
}) {
  if (!connection?.credential.is_set) {
    return <span className="field-hint">Not connected.</span>;
  }
  if (!connection.enabled) {
    return <span className="field-hint">Saved, but switched off — nothing is fetched.</span>;
  }
  if (!health?.last_fetched_at) {
    return (
      <span className="error">
        Switched on, but this gym has never fetched successfully. Check the key and any required
        fields below.
      </span>
    );
  }
  return (
    <span className="field-hint">
      {active ? "Feeding your home page. " : "Ready, but another connection is in use. "}
      Last fetched {since(health.last_fetched_at)} · {health.cached_days} day
      {health.cached_days === 1 ? "" : "s"} cached.
    </span>
  );
}

function ProviderCard({
  info,
  connection,
  health,
  draft,
  active,
  saving,
  secretsOff,
  onPatch,
  onSave,
  onClear,
}: {
  info: GymProviderInfo;
  connection: GymConnection | undefined;
  health: GymConnectionHealth | undefined;
  draft: ConnectionDraft;
  active: boolean;
  saving: boolean;
  secretsOff: boolean;
  onPatch: (changes: Partial<ConnectionDraft>) => void;
  onSave: () => void;
  onClear: () => void;
}) {
  const stored = connection?.credential;
  return (
    <section className="form-card">
      <div className="builder-section-head">
        <h3>{info.label}</h3>
        <p className="section-sub">{info.blurb}</p>
      </div>

      <p className="connection-status">
        <ConnectionStatus connection={connection} health={health} active={active} />
      </p>

      <label className="field">
        <span className="field-label">
          {info.credential_label}
          {stored?.is_set && <span className="category-badge">Saved {stored.masked}</span>}
        </span>
        <input
          type="password"
          autoComplete="off"
          value={draft.credential}
          disabled={secretsOff}
          onChange={(e) => onPatch({ credential: e.target.value })}
          placeholder={stored?.is_set ? "Leave blank to keep the saved key" : "Not set"}
        />
        <span className="field-hint">
          {info.credential_hint}
          {stored?.is_set && (
            <>
              {" "}
              <button type="button" className="text-remove" onClick={onClear}>
                Remove saved key
              </button>
            </>
          )}
        </span>
      </label>

      {(info.location || info.program) && (
        <div className="field-grid">
          {info.location && (
            <label className="field">
              <span className="field-label">
                {info.location.label}{" "}
                {!info.location.required && <span className="optional">optional</span>}
              </span>
              <input
                value={draft.location}
                placeholder={info.location.placeholder}
                onChange={(e) => onPatch({ location: e.target.value })}
              />
            </label>
          )}
          {info.program && (
            <label className="field">
              <span className="field-label">
                {info.program.label}{" "}
                {!info.program.required && <span className="optional">optional</span>}
              </span>
              <input
                value={draft.program}
                placeholder={info.program.placeholder}
                onChange={(e) => onPatch({ program: e.target.value })}
              />
            </label>
          )}
        </div>
      )}

      <label className="field checkbox-field">
        <input
          type="checkbox"
          checked={draft.enabled}
          onChange={(e) => onPatch({ enabled: e.target.checked })}
        />
        {/* Selects which credential fetches the gym, not whether the feed is on
            the home page — that's the "Home page feeds" list above. */}
        <span className="field-label">Use this connection</span>
      </label>

      <p className="field-hint">{info.help_text}</p>

      <button type="button" className="primary-button" disabled={saving} onClick={onSave}>
        {saving ? "Saving…" : "Save"}
      </button>
    </section>
  );
}

export function Settings() {
  const [me, setMe] = useState<Me | null>(null);
  const [providers, setProviders] = useState<GymProviderInfo[]>([]);
  const [health, setHealth] = useState<GymConnectionHealth[]>([]);
  const [drafts, setDrafts] = useState<Drafts>({});
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        // Health is best-effort: it only enriches the status line, so a
        // failure there shouldn't stop someone from editing their settings.
        const [loaded, offered] = await Promise.all([getMe(), listGymProviders()]);
        if (cancelled) return;
        setMe(loaded);
        setProviders(offered);
        setDrafts(draftsFrom(loaded, offered));
        const reported = await getGymHealth().catch(() => []);
        if (!cancelled) setHealth(reported);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Could not reach the server.");
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  function patch(provider: GymProvider, changes: Partial<ConnectionDraft>) {
    setDrafts((current) => {
      const existing = current[provider];
      return existing ? { ...current, [provider]: { ...existing, ...changes } } : current;
    });
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
      setDrafts(draftsFrom(next, providers));
      setHealth(await getGymHealth().catch(() => health));
      setStatus(message);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the server.");
    } finally {
      setSaving(false);
    }
  }

  if (error && !me) return <p className="error">{error}</p>;
  if (!me) return <p className="empty-state">Loading…</p>;

  const secretsOff = !me.secrets_available;
  const connectionFor = (provider: GymProvider) =>
    me.config.gyms.find((g) => g.provider === provider);

  // Only one connection actually feeds the home page. Providers arrive in the
  // server's own priority order, so the first usable one is the answer — and
  // saying so beats letting someone wonder why their second gym is ignored.
  const activeProvider = providers.find((info) => {
    const connection = connectionFor(info.provider);
    if (!connection?.credential.is_set || !connection.enabled) return false;
    if (info.location?.required && !connection.location) return false;
    if (info.program?.required && !connection.program) return false;
    return true;
  })?.provider;

  // Grouped so two routes onto the same platform read as one choice.
  const platforms = [...new Set(providers.map((info) => info.platform))];

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

      {platforms.map((platform) => (
        <div key={platform} className="platform-group">
          <div className="builder-section-head">
            <h3 className="platform-heading">{platform}</h3>
          </div>
          {providers
            .filter((info) => info.platform === platform)
            .map((info) => {
              const draft = drafts[info.provider];
              if (!draft) return null;
              return (
                <ProviderCard
                  key={info.provider}
                  info={info}
                  connection={connectionFor(info.provider)}
                  health={health.find((h) => h.provider === info.provider)}
                  draft={draft}
                  active={activeProvider === info.provider}
                  saving={saving}
                  secretsOff={secretsOff}
                  onPatch={(changes) => patch(info.provider, changes)}
                  onClear={() =>
                    save(
                      { gyms: { [info.provider]: { credential: "" } } },
                      `${info.credential_label} removed.`,
                    )
                  }
                  onSave={() =>
                    save(
                      {
                        gyms: {
                          [info.provider]: {
                            // Omitted when blank, so the stored key survives.
                            ...(draft.credential ? { credential: draft.credential } : {}),
                            ...(info.location ? { location: draft.location } : {}),
                            ...(info.program ? { program: draft.program } : {}),
                            enabled: draft.enabled,
                          },
                        },
                      },
                      `${info.label} saved.`,
                    )
                  }
                />
              );
            })}
        </div>
      ))}

      {error && <p className="error">{error}</p>}
      {status && <p className="field-hint">{status}</p>}

      {me && <Account me={me} />}
    </div>
  );
}
