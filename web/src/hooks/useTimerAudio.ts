import { useCallback, useEffect, useRef } from "react";
import type { TimerState } from "./useTimerEngine";

/**
 * Audio cues for the timer, aimed at the wall-display / TV target where an
 * athlete reads the clock from across the room and needs to hear — without
 * looking — the last few seconds tick away and what just happened when a leg
 * or round rolls over.
 *
 * Everything is synthesised with the Web Audio API (no asset files, works
 * offline), so a "sound" is just a short pattern of oscillator beeps. Each cue
 * gets a distinct pitch/pattern so a leg change is audibly different from a
 * round change, rest, or the time cap.
 */

interface Beep {
  freq: number;
  durationMs: number;
  type?: OscillatorType;
  gain?: number;
}

/**
 * The cue palette. `tick` is the uniform 3-2-1 warning pip; the others are the
 * longer tone that marks what actually happened at the boundary.
 */
const CUES = {
  /** 3-2-1 warning pip before any leg/round/countdown/cap boundary. */
  tick: [{ freq: 800, durationMs: 90 }],
  /** Lead-in handoff: the workout clock starts. Bright and long. */
  go: [{ freq: 1200, durationMs: 380 }],
  /** Next leg within the same round (e.g. :30 work → :15 rest). Two quick mid pips. */
  leg: [
    { freq: 900, durationMs: 80 },
    { freq: 900, durationMs: 80 },
  ],
  /** Rest begins. A single lower "relax" tone. */
  rest: [{ freq: 500, durationMs: 320 }],
  /** A new round begins. Rising two-tone so it reads as "step up". */
  round: [
    { freq: 700, durationMs: 150 },
    { freq: 1050, durationMs: 220 },
  ],
  /** Time cap reached. Urgent triple beep. */
  cap: [
    { freq: 1200, durationMs: 120 },
    { freq: 1200, durationMs: 120 },
    { freq: 1200, durationMs: 240 },
  ],
  /** Workout finished (no cap). Descending three-note flourish. */
  finish: [
    { freq: 1046, durationMs: 200 },
    { freq: 784, durationMs: 200 },
    { freq: 523, durationMs: 360 },
  ],
} satisfies Record<string, Beep[]>;

type CueName = keyof typeof CUES;

/** Schedule a cue's beeps back-to-back on the audio context timeline. */
function playCue(ctx: AudioContext, beeps: Beep[]) {
  let t = ctx.currentTime;
  for (const beep of beeps) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = beep.type ?? "sine";
    osc.frequency.value = beep.freq;

    const dur = beep.durationMs / 1000;
    const peak = beep.gain ?? 0.18;
    // A short attack/release envelope keeps the beep from clicking.
    gain.gain.setValueAtTime(0, t);
    gain.gain.linearRampToValueAtTime(peak, t + 0.008);
    gain.gain.setValueAtTime(peak, Math.max(t + 0.008, t + dur - 0.02));
    gain.gain.linearRampToValueAtTime(0, t + dur);

    osc.connect(gain).connect(ctx.destination);
    osc.start(t);
    osc.stop(t + dur);
    t += dur + 0.04; // small gap between beeps in a pattern
  }
}

/**
 * How many seconds are left before the current thing ends, unified across the
 * count-down legs (interval modes) and the count-up-toward-a-cap modes
 * (for_time / amrap), so the 3-2-1 ticks work the same either way. Returns null
 * when there's nothing counting down.
 */
function warningValue(state: TimerState): number | null {
  if (state.status === "countdown") {
    return Math.max(0, Math.ceil(state.countdownRemaining ?? 0));
  }
  if (state.status !== "running") return null;
  if (state.remainingSeconds !== null) return state.remainingSeconds;
  if (state.capSeconds !== null) {
    return Math.ceil(state.capSeconds - state.elapsedSeconds);
  }
  return null;
}

interface Snapshot {
  status: TimerState["status"];
  phase: TimerState["phase"];
  round: number;
  remainingSeconds: number | null;
  overCap: boolean;
  warn: number | null;
}

function snapshot(state: TimerState): Snapshot {
  return {
    status: state.status,
    phase: state.phase,
    round: state.round,
    remainingSeconds: state.remainingSeconds,
    overCap: state.overCap,
    warn: warningValue(state),
  };
}

export function useTimerAudio(state: TimerState, muted: boolean) {
  const ctxRef = useRef<AudioContext | null>(null);
  const prevRef = useRef<Snapshot | null>(null);
  const mutedRef = useRef(muted);
  mutedRef.current = muted;

  const ensureCtx = useCallback(() => {
    if (ctxRef.current === null) {
      const AC = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (AC) ctxRef.current = new AC();
    }
    const ctx = ctxRef.current;
    if (ctx && ctx.state === "suspended") ctx.resume().catch(() => {});
    return ctx;
  }, []);

  /**
   * Prime and resume the audio context. Must be called from a user gesture
   * (the Start button) to satisfy browser autoplay policy; every later beep
   * rides on the context unlocked here.
   */
  const unlock = useCallback(() => {
    ensureCtx();
  }, [ensureCtx]);

  const play = useCallback((cue: CueName) => {
    if (mutedRef.current) return;
    const ctx = ctxRef.current;
    if (!ctx || ctx.state !== "running") return;
    playCue(ctx, CUES[cue]);
  }, []);

  useEffect(() => {
    const curr = snapshot(state);
    const prev = prevRef.current;
    prevRef.current = curr;
    if (!prev) return;

    // 3-2-1 warning pips: fire once each time the countdown steps down into
    // 3, 2 or 1. Guarded on a *decrease* so a leg reset (which bumps the value
    // back up) never pips.
    if (
      curr.warn !== null &&
      prev.warn !== null &&
      curr.warn < prev.warn &&
      curr.warn >= 1 &&
      curr.warn <= 3
    ) {
      play("tick");
    }

    // The lead-in handing off to the workout clock. Only the countdown→running
    // edge counts as "go" — a paused→running resume must stay silent.
    if (prev.status === "countdown" && curr.status === "running") {
      play("go");
      return;
    }

    // Workout ended: cap tone if it ran against a cap/window, else a finish
    // flourish.
    if (prev.status !== "finished" && curr.status === "finished") {
      play(state.capSeconds !== null ? "cap" : "finish");
      return;
    }

    if (curr.status !== "running" || prev.status !== "running") return;

    // for_time crosses its cap but keeps counting up (it doesn't auto-finish),
    // so the cap tone fires on the flip here rather than on a status change.
    if (!prev.overCap && curr.overCap) {
      play("cap");
      return;
    }

    // Boundary tones, most-significant first. A round bump wins over a
    // leg/rest change on the same tick.
    if (curr.round > prev.round) {
      play("round");
    } else if (curr.phase === "rest" && prev.phase !== "rest") {
      play("rest");
    } else if (curr.phase === "work" && prev.phase === "rest") {
      // Work resuming after a rest, within the same round (block plans).
      play("leg");
    } else if (
      curr.phase === "work" &&
      curr.remainingSeconds !== null &&
      prev.remainingSeconds !== null &&
      curr.remainingSeconds > prev.remainingSeconds
    ) {
      // Rotation leg→leg: the per-leg clock reset without a phase or round
      // change, i.e. we stepped to the next movement in the round.
      play("leg");
    }
  }, [state, play]);

  return { unlock };
}
