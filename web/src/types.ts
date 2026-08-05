export type WorkoutMode = "for_time" | "amrap" | "emom" | "tabata" | "interval" | "custom";

/**
 * Which way the clock runs *inside* one leg of an interval workout. Counting
 * down suits an EMOM — the number is how long you have left. Counting up is
 * for sets scored by their finish time ("Every 3:00 x 5 sets, score = slowest
 * set"), where athletes finish at different moments and each needs to read
 * their own split.
 */
export type IntervalClock = "count_down" | "count_up";

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
  /** Read for the interval modes only; for_time/amrap already count up. */
  interval_clock?: IntervalClock | null;
  segments: WorkoutSegment[];
  created_at: string;
  updated_at: string;
}

/** One page of the library listing. `total` counts every match, not just this page. */
export interface WorkoutPage {
  items: Workout[];
  total: number;
  limit: number;
  offset: number;
}

export interface WodEntry {
  date: string;
  title: string;
  text: string;
  url: string;
  saved_workout_id?: string | null;
}

/** One day's workout from the user's configured gym. */
export interface GymWodEntry {
  provider: GymProvider;
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

/** One way of reaching one gym platform. Mirrors the server's `GymProvider`. */
export type GymProvider = "wodify_member" | "wodify_owner" | "sugarwod_owner";

/** How to render one of a provider's two generic text fields. */
export interface GymFieldInfo {
  label: string;
  placeholder: string;
  required: boolean;
}

/**
 * A connectable gym platform, as the server describes it.
 *
 * Settings renders entirely from these, so adding a platform is a server-only
 * change — which is the whole point of asking for them rather than hardcoding
 * a form per provider. `location` and `program` are null when that provider
 * doesn't use the field.
 */
export interface GymProviderInfo {
  provider: GymProvider;
  platform: string;
  label: string;
  blurb: string;
  link_label: string;
  credential_label: string;
  credential_hint: string;
  help_text: string;
  location?: GymFieldInfo | null;
  program?: GymFieldInfo | null;
}

/** A stored gym connection. The credential is never sent back, only described. */
export interface GymConnection {
  provider: GymProvider;
  credential: SecretStatus;
  location?: string | null;
  program?: string | null;
  enabled: boolean;
}

/**
 * Whether a connection is actually working.
 *
 * Fetchers swallow their errors so one bad day can't empty a feed, which means
 * a wrong credential and a gym that simply didn't post look identical from the
 * outside. `last_fetched_at: null` is the only thing that tells them apart.
 */
export interface GymConnectionHealth {
  provider: GymProvider;
  last_fetched_at?: string | null;
  cached_days: number;
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
  gyms: GymConnection[];
  feeds: FeedPref[];
}

export type Role = "user" | "staff" | "admin";

export interface Me {
  id: string;
  email: string | null;
  email_verified: boolean;
  role: Role;
  display_name: string;
  config: UserConfig;
  /** False when the server has no encryption keys, so credentials can't be saved. */
  secrets_available: boolean;
}

/** What the register screen learns about an invite before asking for a password. */
export interface InviteCheck {
  valid: boolean;
  /** Set for an address-bound invite: the form pre-fills and locks this. */
  email: string | null;
  reason: string | null;
}

export interface Invite {
  id: string;
  email: string | null;
  role: Role;
  created_by: string;
  created_at: string;
  expires_at: string;
  redeemed_at: string | null;
  redeemed_by: string | null;
}

/** The one and only response carrying an invite token. */
export interface InviteCreated {
  invite: Invite;
  token: string;
  link: string;
  emailed: boolean;
}

export type ApiTokenScope = "library:read" | "library:write";

export interface ApiToken {
  id: string;
  user_id: string;
  name: string;
  scopes: ApiTokenScope[];
  /** Not secret — the only way to tell two tokens apart in a list. */
  prefix: string;
  created_at: string;
  last_used_at: string | null;
}

/** The one and only response carrying a token's value. */
export interface ApiTokenCreated {
  api_token: ApiToken;
  token: string;
}

export interface SessionView {
  created_at: string | null;
  last_seen_at: string | null;
  user_agent: string | null;
}

/**
 * A requested config change. Every field is optional and omitted means "leave
 * it alone" — which is how a credential survives an edit to the fields around
 * it without the browser ever holding the secret. An empty string clears it.
 */
export interface GymConnectionUpdate {
  /** Omitted keeps the stored key; "" clears it; a value replaces it. */
  credential?: string;
  location?: string | null;
  program?: string | null;
  enabled?: boolean;
}

export interface UserConfigUpdate {
  /** Keyed by provider; only the providers named are touched. */
  gyms?: Partial<Record<GymProvider, GymConnectionUpdate>>;
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
