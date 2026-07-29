export type WorkoutMode = "for_time" | "amrap" | "emom" | "tabata" | "interval" | "custom";

export interface Movement {
  name?: string | null;
  reps?: number | null;
  distance?: string | null;
  calories?: number | null;
  load?: string | null;
  notes?: string | null;
}

export interface WorkoutSegment {
  label?: string | null;
  rounds?: number | null;
  rep_scheme?: number[] | null;
  /**
   * This leg's own work/rest, for interval workouts whose legs differ in
   * length (a "5/4/3/2/1 minutes" ladder). Both fall back to the
   * workout-level values, so uniform intervals leave them unset.
   */
  work_seconds?: number | null;
  rest_seconds?: number | null;
  /**
   * This leg *is* the recovery — an EMOM whose "Minute 5: Rest". Distinct from
   * `rest_seconds`, which appends recovery to a leg of work: here the leg's
   * whole duration runs as rest, and the clock says so.
   */
  is_rest?: boolean | null;
  movements: Movement[];
}

export interface Workout {
  id: string;
  name: string;
  description?: string | null;
  category?: string | null;
  source_text?: string | null;
  source_hash?: string | null;
  mode: WorkoutMode;
  time_cap_seconds?: number | null;
  rounds?: number | null;
  work_seconds?: number | null;
  rest_seconds?: number | null;
  rep_scheme?: number[] | null;
  segments: WorkoutSegment[];
  created_at: string;
  updated_at: string;
}

export interface WodEntry {
  date: string;
  title: string;
  text: string;
  url: string;
  saved_workout_id?: string | null;
}

/** One day's workout from the user's configured gym. Same shape as WodEntry. */
export interface GymWodEntry {
  date: string;
  title: string;
  text: string;
  url: string;
  saved_workout_id?: string | null;
}

/** One day's erg workout from Concept2. Same shape as WodEntry. */
export interface Concept2WodEntry {
  date: string;
  title: string;
  text: string;
  url: string;
  saved_workout_id?: string | null;
}

/**
 * The gym feed. `configured` is false when the user hasn't connected a gym (or
 * has one saved but switched off) — an empty feed for that reason isn't an
 * error, and the UI should say so rather than showing a generic "nothing here".
 */
export interface GymFeed {
  configured: boolean;
  wods: GymWodEntry[];
}

/** A stored credential as the server describes it — never the value itself. */
export interface SecretStatus {
  is_set: boolean;
  masked?: string | null;
}

export interface WodifyOwnerConfig {
  api_key: SecretStatus;
  location?: string | null;
  program?: string | null;
  enabled: boolean;
}

export interface WodifyMemberConfig {
  whiteboard_key: SecretStatus;
  location?: string | null;
  program?: string | null;
  enabled: boolean;
}

/** One day of the Hybrid Calisthenics rotation. Same shape as WodEntry. */
export interface HybridWodEntry {
  date: string;
  title: string;
  text: string;
  url: string;
  saved_workout_id?: string | null;
}

/** A workout source the home page knows how to render. */
export type FeedKind = "gym" | "crossfit" | "concept2" | "hybrid";

/**
 * Whether a feed appears on the home page. Distinct from the `enabled` on a
 * Wodify config, which picks which credential route fetches the gym — this one
 * is purely about display, and list position is the order.
 */
export interface FeedPref {
  kind: FeedKind;
  enabled: boolean;
}

export interface UserConfig {
  wodify_owner: WodifyOwnerConfig;
  wodify_member: WodifyMemberConfig;
  feeds: FeedPref[];
}

export interface Me {
  id: string;
  display_name: string;
  config: UserConfig;
  /** False when the server has no encryption keys, so credentials can't be saved. */
  secrets_available: boolean;
}

/**
 * A requested config change. Every field is optional and omitted means "leave
 * it alone" — which is how a credential survives an edit to the fields around
 * it without the browser ever holding the secret. An empty string clears it.
 */
export interface WodifyConfigUpdate {
  location?: string | null;
  program?: string | null;
  enabled?: boolean;
}

export interface UserConfigUpdate {
  wodify_owner?: WodifyConfigUpdate & { api_key?: string };
  wodify_member?: WodifyConfigUpdate & { whiteboard_key?: string };
  /** Replaced wholesale, not merged — position is the display order. */
  feeds?: FeedPref[];
}

export const MODE_LABELS: Record<WorkoutMode, string> = {
  for_time: "For Time",
  amrap: "AMRAP",
  emom: "EMOM",
  tabata: "Tabata",
  interval: "Interval",
  custom: "Custom",
};

/**
 * Whether this workout has a clock to run at all.
 *
 * A strength session ("Pushups, 2-3 sets, as many as you can") and a rest day
 * are both real workouts with nothing to count. Forcing them through the timer
 * gives you a stopwatch measuring nothing, so they get a checklist instead. A
 * `custom` workout that *does* carry a cap still gets the clock — someone
 * deliberately put a number on it.
 */
export function isUntimed(workout: Workout): boolean {
  return workout.mode === "custom" && !workout.time_cap_seconds;
}

export const MODE_HINTS: Record<WorkoutMode, string> = {
  for_time: "Finish the work as fast as possible, optionally against a time cap.",
  amrap: "As many rounds or reps as possible within a fixed time window.",
  emom: "Every interval on the minute, start the next movement.",
  tabata: "Repeated work/rest intervals — classically 20s work / 10s rest × 8.",
  interval: "Custom work/rest intervals repeated for a set number of rounds.",
  custom: "No fixed clock — a note, rest day, or free-form session.",
};
