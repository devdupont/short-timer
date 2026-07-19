import { useEffect, useState } from "react";
import { ApiError, getWorkout, listWods, loadWorkoutFromText, parseWorkout } from "../api";
import type { EditTarget } from "./WorkoutBuilder";
import type { WodEntry, Workout } from "../types";

/** Strip the markdown links and bold markers crossfit.com embeds in wodRaw. */
function cleanWodText(text: string): string {
  return text
    .replace(/\r\n/g, "\n")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/\*\*/g, "")
    .trim();
}

// crossfit.com appends comment prompts, coaching strategy, and scaling tiers
// after the actual RX workout. Those make cards huge and aren't what the timer
// needs, so we show only the workout up to the first such marker. (The full
// text is still what gets parsed when the workout is loaded.)
const DETAIL_MARKER =
  /\n\s*(post [^\n]*to comments|stimulus and strategy|intermediate option|beginner option|scaling)/i;

/** crossfit.com programs scheduled rest days — there's no workout to time. */
function isRestDay(text: string): boolean {
  return /^\s*rest day/i.test(cleanWodText(text));
}

function workoutBody(text: string): string {
  const clean = cleanWodText(text);
  const match = clean.match(DETAIL_MARKER);
  let body = (match ? clean.slice(0, match.index) : clean).trim();
  // Rest days are followed by a hero/story bio with no detail marker to cut on.
  if (isRestDay(body)) return "Rest Day";
  // Safety cap so an unexpectedly long entry can't dominate the page.
  if (body.length > 500) body = `${body.slice(0, 500).replace(/\s+\S*$/, "")}…`;
  return body;
}

function formatWodDate(iso: string): string {
  const date = new Date(`${iso}T00:00:00`);
  return date.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
}

function WodCard({
  wod,
  featured,
  loading,
  onLoad,
  onEdit,
}: {
  wod: WodEntry;
  featured: boolean;
  loading: boolean;
  onLoad: (wod: WodEntry) => void;
  onEdit: (wod: WodEntry) => void;
}) {
  const saved = Boolean(wod.saved_workout_id);
  const restDay = isRestDay(wod.text);
  return (
    <section className={`form-card wod-card ${featured ? "wod-featured" : ""}`}>
      <div className="wod-card-head">
        <div>
          <p className="wod-date">{formatWodDate(wod.date)}</p>
          <h3 className="section-title">{wod.title}</h3>
        </div>
        {saved && <span className="category-badge">In library</span>}
      </div>
      <pre className="wod-text">{workoutBody(wod.text)}</pre>
      <div className="builder-actions">
        {restDay ? (
          <span className="field-hint">Rest day — nothing to load.</span>
        ) : (
          <>
            <button className="primary-button" onClick={() => onLoad(wod)} disabled={loading}>
              {loading ? "Loading…" : saved ? "Load into timer" : "Load & save"}
            </button>
            <button className="secondary-button" onClick={() => onEdit(wod)} disabled={loading}>
              Edit first
            </button>
          </>
        )}
        <a className="wod-link" href={wod.url} target="_blank" rel="noreferrer">
          View on crossfit.com ↗
        </a>
      </div>
    </section>
  );
}

export function WorkoutWod({
  onLoad,
  onEdit,
}: {
  onLoad: (workout: Workout) => void;
  onEdit: (target: EditTarget) => void;
}) {
  const [wods, setWods] = useState<WodEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingDate, setLoadingDate] = useState<string | null>(null);

  useEffect(() => {
    listWods()
      .then(setWods)
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Could not reach crossfit.com."),
      );
  }, []);

  async function handleLoad(wod: WodEntry) {
    setLoadingDate(wod.date);
    setError(null);
    try {
      onLoad(await loadWorkoutFromText(wod.text, wod.title));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load that workout.");
      setLoadingDate(null);
    }
  }

  async function handleEdit(wod: WodEntry) {
    setLoadingDate(wod.date);
    setError(null);
    try {
      // If it's already in the library, edit that record in place. Otherwise
      // parse it (without saving) so the coach can tweak it before it lands.
      const target: EditTarget = wod.saved_workout_id
        ? { workout: await getWorkout(wod.saved_workout_id), saved: true }
        : { workout: await parseWorkout(wod.text, wod.title), saved: false };
      onEdit(target);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not open that workout.");
    } finally {
      setLoadingDate(null);
    }
  }

  const today = wods?.[0];
  const earlier = wods?.slice(1) ?? [];

  return (
    <div className="panel wod-panel">
      <div className="panel-intro">
        <h2>CrossFit WODs</h2>
        <p className="section-sub">
          Today's Workout of the Day from crossfit.com, plus recent days. Load one to send it
          straight to the timer.
        </p>
      </div>

      {error && <p className="error">{error}</p>}
      {!wods && !error && <p className="section-sub">Loading today's workout…</p>}
      {wods && wods.length === 0 && (
        <div className="empty-state">No workouts available from crossfit.com right now.</div>
      )}

      {today && (
        <WodCard
          wod={today}
          featured
          loading={loadingDate === today.date}
          onLoad={handleLoad}
          onEdit={handleEdit}
        />
      )}

      {earlier.length > 0 && (
        <>
          <h3 className="section-title wod-earlier-heading">Earlier this week</h3>
          <div className="wod-list">
            {earlier.map((wod) => (
              <WodCard
                key={wod.date}
                wod={wod}
                featured={false}
                loading={loadingDate === wod.date}
                onLoad={handleLoad}
                onEdit={handleEdit}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
