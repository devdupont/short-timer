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

import type { FeedKind, GymWodEntry, WodEntry } from "./types";

/** What the cards need from an entry, common to every source. */
export interface FeedEntry {
  date: string;
  title: string;
  text: string;
  url: string;
  saved_workout_id?: string | null;
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
};

export const GYM_SPEC: FeedSpec = {
  kind: "gym",
  heading: "Your gym",
  blurb: "Programming from the gym you connected in Settings.",
  linkLabel: "View on Wodify ↗",
  // Wodify returns the workout as the gym wrote it — no wrapper prose to strip.
  body: (text) => truncate(text.replace(/\r\n/g, "\n").trim()),
  // A gym that isn't running a class simply publishes nothing that day, so
  // there's no rest-day text to recognise.
  isRestDay: () => false,
};

export const FEED_SPECS: Record<FeedKind, FeedSpec> = {
  crossfit: CROSSFIT_SPEC,
  gym: GYM_SPEC,
};

/** Both wire types satisfy `FeedEntry`; this documents that rather than casting at call sites. */
export type AnyFeedEntry = WodEntry | GymWodEntry;
