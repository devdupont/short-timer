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

interface RotationPlan {
  kind: "rotation";
  legs: WorkoutSegment[];
  workSeconds: number;
  restSeconds: number;
  roundLength: number;
  totalRounds: number | null;
}

interface BlockPlan {
  kind: "blocks";
  blocks: { segment: WorkoutSegment; rounds: number; blockLength: number; cumStart: number }[];
  workSeconds: number;
  restSeconds: number;
  totalLength: number | null;
}

type IntervalPlan = RotationPlan | BlockPlan;

function buildPlan(workout: Workout): IntervalPlan {
  const workSeconds = workout.work_seconds ?? 60;
  const restSeconds = workout.rest_seconds ?? 0;

  if (isBlockPlan(workout)) {
    let cumStart = 0;
    const blocks = workout.segments.map((segment) => {
      const rounds = segment.rounds ?? workout.rounds ?? 1;
      const blockLength = rounds * (workSeconds + restSeconds);
      const block = { segment, rounds, blockLength, cumStart };
      cumStart += blockLength;
      return block;
    });
    return {
      kind: "blocks",
      blocks,
      workSeconds,
      restSeconds,
      totalLength: blocks.length ? cumStart : null,
    };
  }

  const legs = workout.segments.length ? workout.segments : [];
  const legCount = legs.length || 1;
  const roundLength = legCount * workSeconds + restSeconds;
  return {
    kind: "rotation",
    legs,
    workSeconds,
    restSeconds,
    roundLength,
    totalRounds: workout.rounds ?? null,
  };
}

/** Total workout duration, if bounded; null means it runs until stopped. */
function planTotalDuration(plan: IntervalPlan): number | null {
  if (plan.kind === "blocks") return plan.totalLength;
  return plan.totalRounds ? plan.totalRounds * plan.roundLength : null;
}

function deriveIntervalState(plan: IntervalPlan, elapsedSeconds: number): Omit<TimerState, "status" | "countdownRemaining"> {
  if (plan.kind === "rotation") {
    const { legs, workSeconds, restSeconds, roundLength, totalRounds } = plan;
    const legCount = legs.length || 1;
    const roundWorkLength = legCount * workSeconds;
    const round = Math.floor(elapsedSeconds / roundLength) + 1;
    const intoRound = elapsedSeconds % roundLength;
    const inWork = intoRound < roundWorkLength;
    const legIndex = Math.min(legCount - 1, Math.floor(intoRound / workSeconds));
    const remaining = inWork
      ? workSeconds - (intoRound % workSeconds)
      : roundLength - intoRound || restSeconds;
    return {
      elapsedSeconds,
      remainingSeconds: Math.max(0, Math.ceil(remaining)),
      round: totalRounds ? Math.min(round, totalRounds) : round,
      totalRounds,
      phase: inWork ? "work" : "rest",
      overCap: false,
      currentMovement: inWork ? movementLabel(legs[legIndex]) : null,
      legNumber: legCount > 1 ? (inWork ? legIndex + 1 : legCount) : null,
      totalLegs: legCount > 1 ? legCount : null,
      capSeconds: null,
    };
  }

  const { blocks, workSeconds, restSeconds } = plan;
  const last = blocks[blocks.length - 1];
  const totalLength = last ? last.cumStart + last.blockLength : 0;
  const clamped = Math.min(elapsedSeconds, Math.max(0, totalLength - 0.001));
  const blockIndex = Math.max(
    0,
    blocks.findIndex((b) => clamped < b.cumStart + b.blockLength),
  );
  const block = blocks[blockIndex] ?? blocks[blocks.length - 1];
  const intoBlock = block ? elapsedSeconds - block.cumStart : 0;
  const legLength = workSeconds + restSeconds || 1;
  const subRound = Math.min(block?.rounds ?? 1, Math.floor(intoBlock / legLength) + 1);
  const intoLeg = intoBlock % legLength;
  const inWork = intoLeg < workSeconds;
  const remaining = inWork ? workSeconds - intoLeg : legLength - intoLeg || restSeconds;

  return {
    elapsedSeconds,
    remainingSeconds: Math.max(0, Math.ceil(remaining)),
    round: blockIndex + 1,
    totalRounds: blocks.length || null,
    phase: inWork ? "work" : "rest",
    overCap: false,
    currentMovement: block
      ? [movementLabel(block.segment), `${subRound}/${block.rounds}`].filter(Boolean).join(" — ")
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
