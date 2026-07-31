import type { Workout, WorkoutSegment } from "./types";

/**
 * How a workout turns into a running clock.
 *
 * This is the timer's reading of a workout, split out from the engine so that
 * anything else that needs to know what the clock *will* do — the visualizer,
 * most of all — reads the same plan the clock runs rather than a second
 * interpretation that can drift from it.
 */

/** Seconds as m:ss — how a clock, a cap and a leg length are all written here. */
export function formatClock(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function isIntervalMode(workout: Workout): boolean {
  return workout.mode === "emom" || workout.mode === "tabata" || workout.mode === "interval";
}

/**
 * Whether the clock counts up through each interval leg instead of down.
 *
 * The plan is identical either way — same legs, same lengths, same rest. All
 * that changes is the number the athlete reads: their own elapsed time within
 * the set, which is what a workout scored by set times needs, since everyone
 * finishes at a different moment and a shared countdown tells them nothing.
 */
export function countsUp(workout: Workout): boolean {
  return isIntervalMode(workout) && workout.interval_clock === "count_up";
}

/** A leg that is itself the recovery, e.g. an EMOM's "Minute 5: Rest". */
export function isRestSegment(segment: WorkoutSegment | undefined): boolean {
  return Boolean(segment?.is_rest);
}

export function movementLabel(segment: WorkoutSegment | undefined): string | null {
  if (!segment) return null;
  const text = segment.movements
    .map((m) =>
      // Calories are the count for an erg piece the way reps are for a
      // barbell — without them "16 Calorie Row" reads as a bare "Row".
      [m.reps, m.calories ? `${m.calories} cal` : null, m.name, m.distance, m.load]
        .filter(Boolean)
        .join(" "),
    )
    .filter(Boolean)
    .join(", ");
  if (text) return text;
  if (segment.label) return segment.label;
  // A rest leg names nothing on purpose — the rest *is* the instruction.
  return isRestSegment(segment) ? "Rest" : null;
}

/**
 * Interval workouts come in two shapes we need to tell apart:
 *
 * - Rotation: every segment is one leg of a single round (e.g. Chelsea's
 *   pull-ups/push-ups/air-squats all inside one minute, or an EMOM that
 *   assigns a different movement to each minute of a 3-minute set). Rest
 *   happens once, after the round finishes.
 * - Blocks: a segment carries its own `rounds`, meaning it's a fully
 *   self-contained sub-workout (e.g. tabata run separately on each of four
 *   movements). Blocks run back to back.
 *
 * A segment with its own `rounds` is what distinguishes the two.
 */
function isBlockPlan(workout: Workout): boolean {
  return workout.segments.some((s) => (s.rounds ?? 0) > 0);
}

/**
 * One leg of a round, with its duration already resolved.
 *
 * Segments may carry their own `work_seconds`/`rest_seconds` — that's how a
 * "5/4/3/2/1 minutes" ladder says each leg is a different length. Resolving
 * them once here means the arithmetic below never has to care whether a
 * duration came from the segment or from the workout.
 */
export interface Leg {
  segment: WorkoutSegment | undefined;
  work: number;
  rest: number;
  /** Offset of this leg's start from the beginning of its round. */
  start: number;
  /** The whole leg is recovery — its `work` time runs as rest. */
  isRest: boolean;
}

export interface RotationPlan {
  kind: "rotation";
  legs: Leg[];
  roundLength: number;
  totalRounds: number | null;
}

export interface BlockPlan {
  kind: "blocks";
  blocks: { leg: Leg; rounds: number; blockLength: number; cumStart: number }[];
  totalLength: number | null;
}

export type IntervalPlan = RotationPlan | BlockPlan;

/**
 * Resolve each segment's work/rest into a flat list of legs.
 *
 * A segment that names its own `rest_seconds` always rests where it says —
 * that's what a ladder needs, since every rung has its own recovery. Where a
 * segment is silent, the default depends on the plan shape, which is why
 * `restAfterEveryLeg` has to be passed in:
 *
 * - In a rotation the legs are movements *within* one round, so the round
 *   rests once at the end — only the last leg inherits the workout's rest.
 * - In a block plan each leg is a self-contained sub-workout that repeats
 *   internally, so every one of them inherits it.
 */
function resolveLegs(workout: Workout, restAfterEveryLeg: boolean): Leg[] {
  const workSeconds = workout.work_seconds ?? 60;
  const restSeconds = workout.rest_seconds ?? 0;
  // No segments at all still means one leg, so an interval workout with
  // nothing but a work/rest pair on it still runs.
  const segments: (WorkoutSegment | undefined)[] = workout.segments.length
    ? workout.segments
    : [undefined];

  let start = 0;
  return segments.map((segment, index) => {
    const work = segment?.work_seconds ?? workSeconds;
    const inheritsRest = restAfterEveryLeg || index === segments.length - 1;
    const rest = segment?.rest_seconds ?? (inheritsRest ? restSeconds : 0);
    const leg = { segment, work, rest, start, isRest: isRestSegment(segment) };
    start += work + rest;
    return leg;
  });
}

export function buildPlan(workout: Workout): IntervalPlan {
  const asBlocks = isBlockPlan(workout);
  const legs = resolveLegs(workout, asBlocks);

  if (asBlocks) {
    let cumStart = 0;
    const blocks = legs.map((leg) => {
      const rounds = leg.segment?.rounds ?? workout.rounds ?? 1;
      const blockLength = rounds * (leg.work + leg.rest);
      const block = { leg, rounds, blockLength, cumStart };
      cumStart += blockLength;
      return block;
    });
    return { kind: "blocks", blocks, totalLength: blocks.length ? cumStart : null };
  }

  const last = legs[legs.length - 1];
  return {
    kind: "rotation",
    legs,
    roundLength: last.start + last.work + last.rest,
    totalRounds: workout.rounds ?? null,
  };
}

/** The leg covering `offset` within a round, and how far into it we are. */
export function legAt(legs: Leg[], offset: number): { leg: Leg; index: number; intoLeg: number } {
  let index = 0;
  for (let i = legs.length - 1; i >= 0; i--) {
    if (offset >= legs[i].start) {
      index = i;
      break;
    }
  }
  return { leg: legs[index], index, intoLeg: offset - legs[index].start };
}

/** Total workout duration, if bounded; null means it runs until stopped. */
export function planTotalDuration(plan: IntervalPlan): number | null {
  if (plan.kind === "blocks") return plan.totalLength;
  return plan.totalRounds ? plan.totalRounds * plan.roundLength : null;
}

// --- Timeline -------------------------------------------------------------
// The same plan, flattened into something drawable: bars of coloured blocks,
// each block a stretch of clock time doing one thing.

/** How many colours the palette assigns before movements fold into one neutral. */
export const PALETTE_SLOTS = 6;

export interface TimelineBlock {
  kind: "work" | "rest";
  label: string;
  seconds: number;
  /** Offset from the start of its bar. */
  start: number;
  /**
   * Which palette colour this block wears. Null for rest, and for movements
   * past the palette's slots — a cycled hue would claim two movements are the
   * same, so the overflow shares one neutral instead.
   */
  colorIndex: number | null;
}

export interface TimelineBar {
  label: string | null;
  blocks: TimelineBlock[];
  /** Length of one repeat of this bar. */
  seconds: number;
  /** How many times it repeats; null when the workout is unbounded. */
  repeats: number | null;
  /** Offset of the bar's first repeat from the start of the workout. */
  start: number;
}

export interface Timeline {
  bars: TimelineBar[];
  totalSeconds: number | null;
}

/**
 * Which palette colour each segment wears, keyed on what it says to do.
 *
 * Two segments naming the same movement get the same colour, so a rotation
 * that comes back around is visibly the same thing coming back around.
 */
export function segmentColors(workout: Workout): (number | null)[] {
  const slots = new Map<string, number>();
  return workout.segments.map((segment) => {
    if (isRestSegment(segment)) return null;
    const key = (movementLabel(segment) ?? "").toLowerCase();
    if (!key) return null;
    const existing = slots.get(key);
    if (existing !== undefined) return existing;
    if (slots.size >= PALETTE_SLOTS) return null;
    slots.set(key, slots.size);
    return slots.size - 1;
  });
}

function restBlock(seconds: number, start: number): TimelineBlock {
  return { kind: "rest", label: "Rest", seconds, start, colorIndex: null };
}

function legBlocks(leg: Leg, colorIndex: number | null): TimelineBlock[] {
  // The leg's work time *is* the rest, so a rest leg draws as one rest block.
  if (leg.isRest) return [restBlock(leg.work + leg.rest, 0)];

  const label = movementLabel(leg.segment) ?? "Work";
  const blocks: TimelineBlock[] = [];
  if (leg.work > 0) blocks.push({ kind: "work", label, seconds: leg.work, start: 0, colorIndex });
  if (leg.rest > 0) blocks.push(restBlock(leg.rest, leg.work));
  return blocks;
}

/** Flatten a workout into drawable bars. Interval modes only — others have no clock shape. */
export function buildTimeline(workout: Workout): Timeline {
  const plan = buildPlan(workout);
  const colors = segmentColors(workout);
  const colorFor = (index: number) => colors[index] ?? null;

  if (plan.kind === "rotation") {
    const blocks = plan.legs.flatMap((leg, index) =>
      legBlocks(leg, colorFor(index)).map((block) => ({ ...block, start: leg.start + block.start })),
    );
    const bar: TimelineBar = {
      label: null,
      blocks,
      seconds: plan.roundLength,
      repeats: plan.totalRounds,
      start: 0,
    };
    return { bars: [bar], totalSeconds: planTotalDuration(plan) };
  }

  const bars = plan.blocks.map(({ leg, rounds, cumStart }, index) => ({
    label: movementLabel(leg.segment),
    blocks: legBlocks(leg, colorFor(index)),
    seconds: leg.work + leg.rest,
    repeats: rounds,
    start: cumStart,
  }));
  return { bars, totalSeconds: planTotalDuration(plan) };
}

/** Where `elapsedSeconds` lands on the timeline, for the live position marker. */
export function locateBlock(
  timeline: Timeline,
  elapsedSeconds: number,
): { bar: number; block: number; repeat: number } | null {
  for (let i = timeline.bars.length - 1; i >= 0; i--) {
    const bar = timeline.bars[i];
    if (elapsedSeconds < bar.start && i > 0) continue;
    if (bar.seconds <= 0) return null;
    const intoBar = Math.max(0, elapsedSeconds - bar.start);
    const repeat = Math.floor(intoBar / bar.seconds) + 1;
    const offset = intoBar % bar.seconds;
    for (let b = bar.blocks.length - 1; b >= 0; b--) {
      if (offset >= bar.blocks[b].start) {
        return { bar: i, block: b, repeat: bar.repeats ? Math.min(repeat, bar.repeats) : repeat };
      }
    }
    return null;
  }
  return null;
}
