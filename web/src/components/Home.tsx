import { useEffect, useState } from "react";
import {
  ApiError,
  getMe,
  getWorkout,
  listConcept2Wods,
  listGymWods,
  listHybridWods,
  listWods,
  loadWorkoutFromText,
  parseWorkout,
} from "../api";
import { FEED_SPECS } from "../feeds";
import { FeedSection } from "./FeedSection";
import type { EditTarget } from "./WorkoutBuilder";
import type { FeedEntry } from "../feeds";
import type { FeedKind, Me, Workout } from "../types";

/** What one feed resolved to once fetched. */
interface FeedState {
  entries: FeedEntry[];
  /** Gym only: whether a usable gym connection exists at all. */
  configured: boolean;
  error: string | null;
}

const EMPTY: FeedState = { entries: [], configured: true, error: null };

/**
 * Why the gym feed is empty, in the user's terms.
 *
 * The API reports a single `configured` flag, which collapses "no gym
 * connected" and "gym connected but switched off". The user's own config
 * distinguishes them, and they need different actions, so the copy is decided
 * here rather than server-side.
 *
 * Deliberately platform-agnostic: which platform the user connected is a
 * Settings concern, and naming one here would go stale the next time a
 * provider is added.
 */
function gymEmptyReason(me: Me, state: FeedState): string {
  const stored = me.config.gyms.filter((gym) => gym.credential.is_set);

  if (stored.length === 0) {
    return "No gym connected yet. Add your gym's key in Settings and its programming shows up here.";
  }
  if (!stored.some((gym) => gym.enabled)) {
    return "Your gym connection is saved but switched off. Turn it back on in Settings.";
  }
  if (!state.configured) {
    return "Your gym connection is incomplete — Settings will show which fields it still needs.";
  }
  // Connected, switched on, and simply quiet — the ordinary empty case.
  return FEED_SPECS.gym.emptyMessage;
}

export function Home({
  onLoad,
  onEdit,
  onOpenSettings,
}: {
  onLoad: (workout: Workout) => void;
  onEdit: (target: EditTarget) => void;
  onOpenSettings: () => void;
}) {
  const [me, setMe] = useState<Me | null>(null);
  const [feeds, setFeeds] = useState<Partial<Record<FeedKind, FeedState>>>({});
  const [error, setError] = useState<string | null>(null);
  const [loadingDate, setLoadingDate] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      let profile: Me;
      try {
        profile = await getMe();
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Could not load your profile.");
        }
        return;
      }
      if (cancelled) return;
      setMe(profile);

      // Only fetch what's actually going to be rendered — a disabled feed
      // shouldn't cost a request, which is the whole point of the registry.
      const wanted = profile.config.feeds.filter((feed) => feed.enabled);
      const results = await Promise.all(
        wanted.map(async (feed): Promise<[FeedKind, FeedState]> => {
          try {
            switch (feed.kind) {
              case "crossfit":
                return [feed.kind, { ...EMPTY, entries: await listWods() }];
              case "concept2":
                return [feed.kind, { ...EMPTY, entries: await listConcept2Wods() }];
              case "hybrid":
                return [feed.kind, { ...EMPTY, entries: await listHybridWods() }];
              case "gym": {
                const gym = await listGymWods();
                return [feed.kind, { entries: gym.wods, configured: gym.configured, error: null }];
              }
            }
          } catch (err) {
            const message =
              err instanceof ApiError ? err.message : "Could not reach this feed right now.";
            return [feed.kind, { ...EMPTY, error: message }];
          }
        }),
      );
      if (!cancelled) setFeeds(Object.fromEntries(results));
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleLoad(entry: FeedEntry) {
    setLoadingDate(entry.date);
    setError(null);
    try {
      onLoad(await loadWorkoutFromText(entry.text, entry.title));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load that workout.");
      setLoadingDate(null);
    }
  }

  async function handleEdit(entry: FeedEntry) {
    setLoadingDate(entry.date);
    setError(null);
    try {
      // If it's already in the library, edit that record in place. Otherwise
      // parse it (without saving) so the coach can tweak it before it lands.
      const target: EditTarget = entry.saved_workout_id
        ? { workout: await getWorkout(entry.saved_workout_id), saved: true }
        : { workout: await parseWorkout(entry.text, entry.title), saved: false };
      onEdit(target);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not open that workout.");
    } finally {
      setLoadingDate(null);
    }
  }

  if (error && !me) {
    return (
      <div className="panel">
        <p className="error">{error}</p>
      </div>
    );
  }

  if (!me) {
    return (
      <div className="panel">
        <p className="section-sub">Loading your workouts…</p>
      </div>
    );
  }

  const enabled = me.config.feeds.filter((feed) => feed.enabled);

  return (
    <div className="panel wod-panel">
      {error && <p className="error">{error}</p>}

      {enabled.length === 0 ? (
        <div className="empty-state home-welcome">
          <h2>Nothing on your home page yet</h2>
          <p className="section-sub">
            Turn on a workout feed to see daily programming here, or go straight to building a
            workout of your own.
          </p>
          <div className="builder-actions">
            <button className="primary-button" onClick={onOpenSettings}>
              Choose your feeds
            </button>
          </div>
        </div>
      ) : (
        enabled.map((feed) => {
          const spec = FEED_SPECS[feed.kind];
          const state = feeds[feed.kind];
          return (
            <FeedSection
              key={feed.kind}
              spec={spec}
              entries={state?.entries ?? []}
              loadingDate={loadingDate}
              onLoad={handleLoad}
              onEdit={handleEdit}
              emptyState={
                <div className="empty-state">
                  {!state ? (
                    "Loading…"
                  ) : state.error ? (
                    <span className="error">{state.error}</span>
                  ) : feed.kind === "gym" ? (
                    <>
                      <p>{gymEmptyReason(me, state)}</p>
                      <button className="secondary-button" onClick={onOpenSettings}>
                        Open Settings
                      </button>
                    </>
                  ) : (
                    spec.emptyMessage
                  )}
                </div>
              }
            />
          );
        })
      )}
    </div>
  );
}
