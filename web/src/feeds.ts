/**
 * Per-source display rules for the home page.
 *
 * The server deliberately keeps `Wod` and `GymWod` as separate types even
 * though they currently share a shape, because their lifecycles differ —
 * crossfit.com has rest days and a public permalink, a gym has neither. This
 * module respects that: the two stay separate on the wire, and only the bits
 * that actually differ in *presentation* live here, so one card component can
 * render both without either source leaking assumptions into the other.
 */

import type {
  Concept2WodEntry,
  FeedKind,
  GymWodEntry,
  HybridWodEntry,
  WodEntry,
} from "./types";

/** What the cards need from an entry, common to every source. */
export interface FeedEntry {
  date: string;
  title: string;
  text: string;
  /** Empty when the source has no page we can link to — the card drops the link. */
  url: string;
  saved_workout_id?: string | null;
  /**
   * Overrides the spec's `linkLabel` for this entry. The gym feed sets it,
   * because "your gym" is whichever platform you connected and only the server
   * knows which — see the provider registry.
   */
  link_label?: string;
}

export interface FeedSpec {
  kind: FeedKind;
  heading: string;
  blurb: string;
  /** Label for the "see it at the source" link on each card. */
  linkLabel: string;
  /** Trim a raw entry down to what belongs on a card. */
  body(text: string): string;
  /** Sources that program scheduled rest have nothing to send to the timer. */
  isRestDay(text: string): boolean;
  /**
   * Copy for "this feed loaded fine and simply has nothing today". A feed that
   * can be empty for a *configuration* reason says so nearer the user's
   * config — see `gymEmptyReason` — and falls back to this.
   */
  emptyMessage: string;
}

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

function isCrossfitRestDay(text: string): boolean {
  return /^\s*rest day/i.test(cleanWodText(text));
}

/** Safety cap so an unexpectedly long entry can't dominate the page. */
function truncate(body: string): string {
  if (body.length <= 500) return body;
  return `${body.slice(0, 500).replace(/\s+\S*$/, "")}…`;
}

function crossfitBody(text: string): string {
  const clean = cleanWodText(text);
  const match = clean.match(DETAIL_MARKER);
  const body = (match ? clean.slice(0, match.index) : clean).trim();
  // Rest days are followed by a hero/story bio with no detail marker to cut on.
  if (isCrossfitRestDay(body)) return "Rest Day";
  return truncate(body);
}

export const CROSSFIT_SPEC: FeedSpec = {
  kind: "crossfit",
  heading: "CrossFit.com",
  blurb: "Today's Workout of the Day from crossfit.com, plus recent days.",
  linkLabel: "View on crossfit.com ↗",
  body: crossfitBody,
  isRestDay: isCrossfitRestDay,
  emptyMessage: "No workouts available from crossfit.com right now.",
};

/**
 * Concept2 sends the workout as a headline plus a sentence expanding it, and
 * the headline is already the card's title — so the body is everything after
 * it. Both halves still go to the parser: "2/3/2/3/2/3/2 minutes with 1 minute
 * rest" only means seven intervals if you read the sentence underneath.
 */
function concept2Body(text: string): string {
  const clean = text.replace(/\r\n/g, "\n").trim();
  const rest = clean.slice(clean.indexOf("\n") + 1).trim();
  // A day with no description is just the headline; show it rather than nothing.
  return truncate(rest && clean.includes("\n") ? rest : clean);
}

export const CONCEPT2_SPEC: FeedSpec = {
  kind: "concept2",
  heading: "Concept2",
  blurb: "The daily erg workout for the RowErg, SkiErg and BikeErg.",
  linkLabel: "View on Concept2 ↗",
  body: concept2Body,
  // Concept2 programs an interval workout every single day — there is no rest
  // day to recognise.
  isRestDay: () => false,
  emptyMessage: "No workouts available from Concept2 right now.",
};

/**
 * Hybrid Calisthenics is a fixed rotation of untimed sets, so unlike the other
 * feeds it has no clock to advertise and no prose to trim — the two lines the
 * server stores *are* the workout.
 */
export const HYBRID_SPEC: FeedSpec = {
  kind: "hybrid",
  heading: "Hybrid Calisthenics",
  blurb: "Today's bodyweight session from the free Hybrid Routine. No clock — just sets.",
  linkLabel: "View on hybridcalisthenics.com ↗",
  body: (text) => truncate(text.replace(/\r\n/g, "\n").trim()),
  isRestDay: (text) => /^\s*(a day of rest|rest day)/i.test(text),
  emptyMessage: "No workouts available from Hybrid Calisthenics right now.",
};

export const GYM_SPEC: FeedSpec = {
  kind: "gym",
  heading: "Your gym",
  blurb: "Programming from the gym you connected in Settings.",
  // Only a fallback: each entry carries its own label naming the platform it
  // actually came from.
  linkLabel: "View at the source ↗",
  // A gym platform returns the workout as the gym wrote it — no prose to strip.
  body: (text) => truncate(text.replace(/\r\n/g, "\n").trim()),
  // A gym that isn't running a class simply publishes nothing that day, so
  // there's no rest-day text to recognise.
  isRestDay: () => false,
  emptyMessage: "Your gym hasn't published anything for the last few days.",
};

export const FEED_SPECS: Record<FeedKind, FeedSpec> = {
  crossfit: CROSSFIT_SPEC,
  gym: GYM_SPEC,
  concept2: CONCEPT2_SPEC,
  hybrid: HYBRID_SPEC,
};

/** Every wire type satisfies `FeedEntry`; this documents that rather than casting at call sites. */
export type AnyFeedEntry = WodEntry | GymWodEntry | Concept2WodEntry | HybridWodEntry;
