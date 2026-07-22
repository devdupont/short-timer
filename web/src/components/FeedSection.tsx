import type { FeedEntry, FeedSpec } from "../feeds";

function formatFeedDate(iso: string): string {
  const date = new Date(`${iso}T00:00:00`);
  return date.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
}

export function FeedCard({
  entry,
  spec,
  featured,
  loading,
  onLoad,
  onEdit,
}: {
  entry: FeedEntry;
  spec: FeedSpec;
  featured: boolean;
  loading: boolean;
  onLoad: (entry: FeedEntry) => void;
  onEdit: (entry: FeedEntry) => void;
}) {
  const saved = Boolean(entry.saved_workout_id);
  const restDay = spec.isRestDay(entry.text);
  return (
    <section className={`form-card wod-card ${featured ? "wod-featured" : ""}`}>
      <div className="wod-card-head">
        <div>
          <p className="wod-date">{formatFeedDate(entry.date)}</p>
          <h3 className="section-title">{entry.title}</h3>
        </div>
        {saved && <span className="category-badge">In library</span>}
      </div>
      <pre className="wod-text">{spec.body(entry.text)}</pre>
      <div className="builder-actions">
        {restDay ? (
          <span className="field-hint">Rest day — nothing to load.</span>
        ) : (
          <>
            <button className="primary-button" onClick={() => onLoad(entry)} disabled={loading}>
              {loading ? "Loading…" : saved ? "Load into timer" : "Load & save"}
            </button>
            <button className="secondary-button" onClick={() => onEdit(entry)} disabled={loading}>
              Edit first
            </button>
          </>
        )}
        <a className="wod-link" href={entry.url} target="_blank" rel="noreferrer">
          {spec.linkLabel}
        </a>
      </div>
    </section>
  );
}

/**
 * One source's block on the home page: today featured, earlier days below.
 *
 * `emptyState` is supplied by the caller rather than rendered here, because
 * "empty" means something different per source — a gym feed can be empty
 * because no gym is connected, which is a different message from one that is
 * connected but quiet today.
 */
export function FeedSection({
  spec,
  entries,
  loadingDate,
  emptyState,
  onLoad,
  onEdit,
}: {
  spec: FeedSpec;
  entries: FeedEntry[];
  loadingDate: string | null;
  emptyState?: React.ReactNode;
  onLoad: (entry: FeedEntry) => void;
  onEdit: (entry: FeedEntry) => void;
}) {
  const [today, ...earlier] = entries;

  return (
    <section className="feed-section">
      <div className="panel-intro">
        <h2>{spec.heading}</h2>
        <p className="section-sub">{spec.blurb}</p>
      </div>

      {!today && emptyState}

      {today && (
        <FeedCard
          entry={today}
          spec={spec}
          featured
          loading={loadingDate === today.date}
          onLoad={onLoad}
          onEdit={onEdit}
        />
      )}

      {earlier.length > 0 && (
        <details className="feed-earlier">
          <summary>Earlier this week ({earlier.length})</summary>
          <div className="wod-list">
            {earlier.map((entry) => (
              <FeedCard
                key={entry.date}
                entry={entry}
                spec={spec}
                featured={false}
                loading={loadingDate === entry.date}
                onLoad={onLoad}
                onEdit={onEdit}
              />
            ))}
          </div>
        </details>
      )}
    </section>
  );
}
