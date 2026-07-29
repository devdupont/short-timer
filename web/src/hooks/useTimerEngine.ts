import { useCallback, useEffect, useRef, useState } from "react";
import type { Workout } from "../types";
import type { IntervalPlan } from "../timerPlan";
import { buildPlan, isIntervalMode, legAt, movementLabel, planTotalDuration } from "../timerPlan";

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

function deriveIntervalState(plan: IntervalPlan, elapsedSeconds: number): Omit<TimerState, "status" | "countdownRemaining"> {
  if (plan.kind === "rotation") {
    const { legs, roundLength, totalRounds } = plan;
    const legCount = legs.length;
    const round = Math.floor(elapsedSeconds / roundLength) + 1;
    const intoRound = elapsedSeconds % roundLength;
    const { leg, index, intoLeg } = legAt(legs, intoRound);
    // A rest leg is rest for its whole length — there's no work half of it to
    // be inside. Everything downstream (the phase colour, the rest tone, the
    // movement readout) follows from this one flag.
    const inWork = !leg.isRest && intoLeg < leg.work;
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
  const inWork = block ? !block.leg.isRest && intoLeg < block.leg.work : true;
  const remaining = inWork && block ? block.leg.work - intoLeg : legLength - intoLeg;

  return {
    elapsedSeconds,
    remainingSeconds: Math.max(0, Math.ceil(remaining)),
    round: blockIndex + 1,
    totalRounds: blocks.length || null,
    phase: inWork ? "work" : "rest",
    overCap: false,
    // Kept up during a block's rest so you can see which movement's block
    // you're in and how far through it — but a rest *leg* has nothing to name.
    currentMovement:
      block && !block.leg.isRest
        ? [movementLabel(block.leg.segment), `${subRound}/${block.rounds}`]
            .filter(Boolean)
            .join(" — ")
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
