import { useCallback, useEffect, useRef, useState } from "react";
import type { Workout, WorkoutSegment } from "../types";

export type TimerStatus = "idle" | "countdown" | "running" | "paused" | "finished";
export type TimerPhase = "work" | "rest";

/** "3-2-1-go" lead-in so athletes can get set before the clock starts. */
export const COUNTDOWN_SECONDS = 10;

export interface TimerState {
  status: TimerStatus;
  elapsedSeconds: number;
  /** Seconds left in the current countdown/interval leg; null when the clock counts up. */
  remainingSeconds: number | null;
  round: number;
  totalRounds: number | null;
  phase: TimerPhase;
  overCap: boolean;
  /** Human-readable label for whatever should be happening right now. */
  currentMovement: string | null;
  /** Position within the current round's rotation of movements, if there's more than one. */
  legNumber: number | null;
  totalLegs: number | null;
  /**
   * The time cap / window, in seconds, for count-up modes (for_time, amrap).
   * Drives the elapsed-vs-cap progress bar; null when there's no cap to show.
   */
  capSeconds: number | null;
  /** Seconds left in the pre-workout lead-in; null unless counting down. */
  countdownRemaining: number | null;
}

const TICK_MS = 100;

function movementLabel(segment: WorkoutSegment | undefined): string | null {
  if (!segment) return null;
  const text = segment.movements
    .map((m) => [m.reps, m.name, m.distance, m.load].filter(Boolean).join(" "))
    .filter(Boolean)
    .join(", ");
  return text || segment.label || null;
}

function isIntervalMode(workout: Workout): boolean {
  return workout.mode === "emom" || workout.mode === "tabata" || workout.mode === "interval";
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
interface Leg {
  segment: WorkoutSegment | undefined;
  work: number;
  rest: number;
  /** Offset of this leg's start from the beginning of its round. */
  start: number;
}

interface RotationPlan {
  kind: "rotation";
  legs: Leg[];
  roundLength: number;
  totalRounds: number | null;
}

interface BlockPlan {
  kind: "blocks";
  blocks: { leg: Leg; rounds: number; blockLength: number; cumStart: number }[];
  totalLength: number | null;
}

type IntervalPlan = RotationPlan | BlockPlan;

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
    const leg = { segment, work, rest, start };
    start += work + rest;
    return leg;
  });
}

function buildPlan(workout: Workout): IntervalPlan {
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
function legAt(legs: Leg[], offset: number): { leg: Leg; index: number; intoLeg: number } {
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
function planTotalDuration(plan: IntervalPlan): number | null {
  if (plan.kind === "blocks") return plan.totalLength;
  return plan.totalRounds ? plan.totalRounds * plan.roundLength : null;
}

function deriveIntervalState(plan: IntervalPlan, elapsedSeconds: number): Omit<TimerState, "status" | "countdownRemaining"> {
  if (plan.kind === "rotation") {
    const { legs, roundLength, totalRounds } = plan;
    const legCount = legs.length;
    const round = Math.floor(elapsedSeconds / roundLength) + 1;
    const intoRound = elapsedSeconds % roundLength;
    const { leg, index, intoLeg } = legAt(legs, intoRound);
    const inWork = intoLeg < leg.work;
    const remaining = inWork ? leg.work - intoLeg : leg.work + leg.rest - intoLeg;
    return {
      elapsedSeconds,
      remainingSeconds: Math.max(0, Math.ceil(remaining)),
      round: totalRounds ? Math.min(round, totalRounds) : round,
      totalRounds,
      phase: inWork ? "work" : "rest",
      overCap: false,
      currentMovement: inWork ? movementLabel(leg.segment) : null,
      legNumber: legCount > 1 ? index + 1 : null,
      totalLegs: legCount > 1 ? legCount : null,
      capSeconds: null,
    };
  }

  const { blocks } = plan;
  const lastBlock = blocks[blocks.length - 1];
  const totalLength = lastBlock ? lastBlock.cumStart + lastBlock.blockLength : 0;
  const clamped = Math.min(elapsedSeconds, Math.max(0, totalLength - 0.001));
  const blockIndex = Math.max(
    0,
    blocks.findIndex((b) => clamped < b.cumStart + b.blockLength),
  );
  const block = blocks[blockIndex] ?? lastBlock;
  const intoBlock = block ? elapsedSeconds - block.cumStart : 0;
  const legLength = block ? block.leg.work + block.leg.rest || 1 : 1;
  const subRound = Math.min(block?.rounds ?? 1, Math.floor(intoBlock / legLength) + 1);
  const intoLeg = intoBlock % legLength;
  const inWork = block ? intoLeg < block.leg.work : true;
  const remaining = inWork && block ? block.leg.work - intoLeg : legLength - intoLeg;

  return {
    elapsedSeconds,
    remainingSeconds: Math.max(0, Math.ceil(remaining)),
    round: blockIndex + 1,
    totalRounds: blocks.length || null,
    phase: inWork ? "work" : "rest",
    overCap: false,
    currentMovement: block
      ? [movementLabel(block.leg.segment), `${subRound}/${block.rounds}`].filter(Boolean).join(" — ")
      : null,
    legNumber: null,
    totalLegs: null,
    capSeconds: null,
  };
}

function deriveState(workout: Workout, elapsedSeconds: number): Omit<TimerState, "status" | "countdownRemaining"> {
  if (isIntervalMode(workout)) {
    return deriveIntervalState(buildPlan(workout), elapsedSeconds);
  }

  // for_time and amrap are single continuous efforts against a cap/window.
  // They count *up* — each athlete reads their own elapsed time as they
  // finish — with a bar tracking progress toward the cap rather than a
  // countdown. amrap always has a window; for_time's cap is optional.
  const cap = workout.time_cap_seconds ?? null;
  return {
    elapsedSeconds,
    remainingSeconds: null,
    round: 1,
    totalRounds: null,
    phase: "work",
    overCap: cap !== null && elapsedSeconds >= cap,
    currentMovement: null,
    legNumber: null,
    totalLegs: null,
    capSeconds: cap,
  };
}

export function useTimerEngine(workout: Workout) {
  const [status, setStatus] = useState<TimerStatus>("idle");
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [countdownRemaining, setCountdownRemaining] = useState<number | null>(null);
  const startedAtRef = useRef<number | null>(null);
  const accumulatedRef = useRef(0);
  const countdownStartedAtRef = useRef<number | null>(null);

  /** Hand off from the lead-in to the workout clock. */
  const beginWorkout = useCallback(() => {
    countdownStartedAtRef.current = null;
    setCountdownRemaining(null);
    accumulatedRef.current = 0;
    setElapsedSeconds(0);
    startedAtRef.current = performance.now();
    setStatus("running");
  }, []);

  useEffect(() => {
    if (status !== "countdown") return;
    const id = window.setInterval(() => {
      if (countdownStartedAtRef.current === null) return;
      const elapsed = (performance.now() - countdownStartedAtRef.current) / 1000;
      const remaining = COUNTDOWN_SECONDS - elapsed;
      if (remaining <= 0) {
        beginWorkout();
      } else {
        setCountdownRemaining(remaining);
      }
    }, TICK_MS);
    return () => window.clearInterval(id);
  }, [status, beginWorkout]);

  const tick = useCallback(() => {
    if (startedAtRef.current === null) return;
    const elapsed = (performance.now() - startedAtRef.current) / 1000 + accumulatedRef.current;
    setElapsedSeconds(elapsed);

    if (isIntervalMode(workout)) {
      const total = planTotalDuration(buildPlan(workout));
      if (total !== null && elapsed >= total) {
        setStatus("finished");
        return;
      }
    }
    if (workout.mode === "amrap" && workout.time_cap_seconds && elapsed >= workout.time_cap_seconds) {
      setStatus("finished");
      return;
    }
  }, [workout]);

  useEffect(() => {
    if (status !== "running") return;
    const id = window.setInterval(tick, TICK_MS);
    return () => window.clearInterval(id);
  }, [status, tick]);

  const start = useCallback(() => {
    countdownStartedAtRef.current = performance.now();
    setCountdownRemaining(COUNTDOWN_SECONDS);
    setStatus("countdown");
  }, []);

  const pause = useCallback(() => {
    if (startedAtRef.current !== null) {
      accumulatedRef.current += (performance.now() - startedAtRef.current) / 1000;
      startedAtRef.current = null;
    }
    setStatus("paused");
  }, []);

  const resume = useCallback(() => {
    startedAtRef.current = performance.now();
    setStatus("running");
  }, []);

  const reset = useCallback(() => {
    startedAtRef.current = null;
    countdownStartedAtRef.current = null;
    accumulatedRef.current = 0;
    setElapsedSeconds(0);
    setCountdownRemaining(null);
    setStatus("idle");
  }, []);

  const finish = useCallback(() => {
    if (startedAtRef.current !== null) {
      accumulatedRef.current += (performance.now() - startedAtRef.current) / 1000;
      startedAtRef.current = null;
    }
    setStatus("finished");
  }, []);

  const derived = deriveState(workout, elapsedSeconds);

  return {
    state: { ...derived, status, countdownRemaining } as TimerState,
    controls: { start, pause, resume, reset, finish, skipCountdown: beginWorkout },
  };
}
