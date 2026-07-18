import { useCallback, useEffect, useRef, useState } from "react";
import type { Workout } from "../types";

export type TimerStatus = "idle" | "running" | "paused" | "finished";
export type TimerPhase = "work" | "rest";

export interface TimerState {
  status: TimerStatus;
  elapsedSeconds: number;
  /** Seconds left in the current countdown/interval leg; null for a plain stopwatch. */
  remainingSeconds: number | null;
  round: number;
  totalRounds: number | null;
  phase: TimerPhase;
  overCap: boolean;
}

const TICK_MS = 100;

function intervalLength(workout: Workout): number {
  return (workout.work_seconds ?? 0) + (workout.rest_seconds ?? 0);
}

function isIntervalMode(workout: Workout): boolean {
  return workout.mode === "emom" || workout.mode === "tabata" || workout.mode === "interval";
}

function deriveState(workout: Workout, elapsedSeconds: number): Omit<TimerState, "status"> {
  if (isIntervalMode(workout)) {
    const legLength = intervalLength(workout) || 60;
    const workLength = workout.work_seconds ?? legLength;
    const round = Math.floor(elapsedSeconds / legLength) + 1;
    const intoLeg = elapsedSeconds % legLength;
    const phase: TimerPhase = intoLeg < workLength ? "work" : "rest";
    const remaining = phase === "work" ? workLength - intoLeg : legLength - intoLeg;
    return {
      elapsedSeconds,
      remainingSeconds: Math.max(0, Math.ceil(remaining)),
      round,
      totalRounds: workout.rounds ?? null,
      phase,
      overCap: false,
    };
  }

  if (workout.mode === "amrap") {
    const cap = workout.time_cap_seconds ?? 0;
    return {
      elapsedSeconds,
      remainingSeconds: Math.max(0, Math.ceil(cap - elapsedSeconds)),
      round: 1,
      totalRounds: null,
      phase: "work",
      overCap: elapsedSeconds >= cap,
    };
  }

  const cap = workout.time_cap_seconds ?? null;
  return {
    elapsedSeconds,
    remainingSeconds: cap === null ? null : Math.max(0, Math.ceil(cap - elapsedSeconds)),
    round: 1,
    totalRounds: null,
    phase: "work",
    overCap: cap !== null && elapsedSeconds >= cap,
  };
}

export function useTimerEngine(workout: Workout) {
  const [status, setStatus] = useState<TimerStatus>("idle");
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const startedAtRef = useRef<number | null>(null);
  const accumulatedRef = useRef(0);

  const tick = useCallback(() => {
    if (startedAtRef.current === null) return;
    const elapsed = (performance.now() - startedAtRef.current) / 1000 + accumulatedRef.current;
    setElapsedSeconds(elapsed);

    const totalRounds = workout.rounds;
    if (isIntervalMode(workout) && totalRounds) {
      const legLength = intervalLength(workout) || 60;
      if (elapsed >= legLength * totalRounds) {
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
    startedAtRef.current = performance.now();
    setStatus("running");
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
    accumulatedRef.current = 0;
    setElapsedSeconds(0);
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
    state: { ...derived, status } as TimerState,
    controls: { start, pause, resume, reset, finish },
  };
}
