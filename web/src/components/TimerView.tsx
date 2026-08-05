import { useEffect, useRef, useState } from "react";
import { markWorkoutCompleted, markWorkoutStarted } from "../api";
import { useTimerEngine } from "../hooks/useTimerEngine";
import { useTimerAudio } from "../hooks/useTimerAudio";
import { useWakeLock } from "../hooks/useWakeLock";
import { WorkoutTimeline } from "./WorkoutTimeline";
import type { Workout } from "../types";
import { isUntimed, MODE_LABELS } from "../types";
import { formatClock, movementLabel } from "../timerPlan";

const MUTED_KEY = "short-timer:muted";

/**
 * Untimed sessions: work through the list and tick things off.
 *
 * Checked state is deliberately session-local. It's a scratchpad for "where am
 * I right now", not a training log — persisting it would imply a history the
 * app doesn't keep, and would need answers for what a checkmark means the next
 * day.
 */
function UntimedSession({ workout }: { workout: Workout }) {
  const items = workout.segments
    .map((segment) => {
      // A note usually restates the set count in the source's own words
      // ("2-3 sets"), which is truer than the single number the parser had to
      // round it to — so prefer it, and fall back to `rounds` when absent.
      // Showing both gives you "3 sets — 2-3 sets", which contradicts itself.
      const note = segment.movements.find((m) => m.notes)?.notes ?? null;
      const sets = segment.rounds ? `${segment.rounds} sets` : null;
      return {
        title: movementLabel(segment) ?? "",
        detail: note ?? sets ?? "",
      };
    })
    .filter((item) => item.title);

  const [done, setDone] = useState<boolean[]>(() => items.map(() => false));
  const completed = done.filter(Boolean).length;

  return (
    <div className="timer-view untimed-view">
      <div className="timer-header">
        <h2>{workout.name}</h2>
        <p className="mode-badge">{MODE_LABELS[workout.mode]}</p>
      </div>

      {workout.description && <p className="section-sub">{workout.description}</p>}

      {items.length === 0 ? (
        <p className="section-sub untimed-empty">
          Nothing to time here — take the day as it comes.
        </p>
      ) : (
        <>
          <p className="untimed-progress" aria-live="polite">
            {completed} of {items.length} done
          </p>
          <ul className="untimed-list">
            {items.map((item, i) => (
              <li key={i}>
                <label className={done[i] ? "untimed-item done" : "untimed-item"}>
                  <input
                    type="checkbox"
                    checked={done[i]}
                    onChange={(e) =>
                      setDone((prev) => prev.map((v, j) => (j === i ? e.target.checked : v)))
                    }
                  />
                  <span className="untimed-item-text">
                    <strong>{item.title}</strong>
                    {item.detail && <em className="untimed-detail">{item.detail}</em>}
                  </span>
                </label>
              </li>
            ))}
          </ul>
          <div className="timer-controls">
            <button onClick={() => setDone(items.map(() => false))}>Reset</button>
          </div>
        </>
      )}
    </div>
  );
}

export function TimerView({ workout }: { workout: Workout }) {
  // Dispatch before any timer hook runs, so the clock view's hooks stay
  // unconditional rather than being called for a workout with no clock.
  return isUntimed(workout) ? (
    <UntimedSession workout={workout} />
  ) : (
    <ClockView workout={workout} />
  );
}

function ClockView({ workout }: { workout: Workout }) {
  const { state, controls } = useTimerEngine(workout);

  // The engine reaches "finished" three ways — the Finish button, an interval
  // plan running out, and an AMRAP hitting its cap — so this watches the state
  // rather than hanging off any one of them.
  //
  // The guard is cleared on the way *out* of "finished" rather than keyed on
  // the workout id. Keying on the id looks equivalent and isn't: it makes the
  // flag stick for the lifetime of the component, so a second run of the same
  // workout — reset and try again, or simply doing Helen twice — records its
  // start and silently drops its finish.
  const reportedRef = useRef(false);
  useEffect(() => {
    if (state.status !== "finished") {
      reportedRef.current = false;
      return;
    }
    if (reportedRef.current || !workout.id) return;
    reportedRef.current = true;
    // Telemetry: unawaited, failures ignored. Nothing about finishing a
    // workout should depend on the network.
    void markWorkoutCompleted(workout.id, state.elapsedSeconds).catch(() => {});
  }, [state.status, state.elapsedSeconds, workout.id]);
  // Audio cues on the wall display — remembered per browser so a muted gym
  // stays muted between sessions.
  const [muted, setMuted] = useState(() => {
    try {
      return localStorage.getItem(MUTED_KEY) === "1";
    } catch {
      return false;
    }
  });
  const audio = useTimerAudio(state, muted, workout.mode);
  // "TV mode": the timer takes over the whole screen with oversized elements
  // so it reads from across a gym. It also requests true browser fullscreen
  // when available, but the CSS layout is the source of truth so it still
  // works where the Fullscreen API is blocked (e.g. embedded previews).
  const [tv, setTv] = useState(false);

  // Hold the screen awake whenever the display is the point: a clock that's
  // counting (paused included — that's a workout mid-flight, not an idle
  // page), or TV mode, which is someone saying this screen is a wall display
  // before they've pressed Start.
  useWakeLock(
    tv || state.status === "countdown" || state.status === "running" || state.status === "paused",
  );

  useEffect(() => {
    try {
      localStorage.setItem(MUTED_KEY, muted ? "1" : "0");
    } catch {
      /* storage unavailable (private mode) — muting still works this session. */
    }
  }, [muted]);

  useEffect(() => {
    function onFullscreenChange() {
      // Leaving fullscreen (e.g. via Esc) should drop TV mode too.
      if (!document.fullscreenElement) setTv(false);
    }
    document.addEventListener("fullscreenchange", onFullscreenChange);
    return () => {
      document.removeEventListener("fullscreenchange", onFullscreenChange);
      if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
    };
  }, []);

  function toggleTv() {
    const next = !tv;
    setTv(next);
    try {
      if (next) {
        document.documentElement.requestFullscreen?.().catch(() => {});
      } else if (document.fullscreenElement) {
        document.exitFullscreen?.().catch(() => {});
      }
    } catch {
      /* Fullscreen unavailable — CSS TV mode still applies. */
    }
  }

  const counting = state.status === "countdown";
  // During the lead-in the clock shows a bare seconds count, not mm:ss.
  // Otherwise the big number is whichever reading the workout is run by: an
  // up-counting leg (each athlete's own set time), the time left in a leg, or
  // total elapsed for the single-effort modes.
  const bigNumber = counting
    ? String(Math.max(0, Math.ceil(state.countdownRemaining ?? 0)))
    : state.legElapsedSeconds !== null
      ? formatClock(state.legElapsedSeconds)
      : state.remainingSeconds !== null
        ? formatClock(state.remainingSeconds)
        : formatClock(state.elapsedSeconds);

  // A count-up set still runs inside a window, and the athlete who has already
  // finished wants to know when the next one starts — so the countdown the big
  // clock gave up stays on as a subtitle.
  const windowRemaining =
    state.legElapsedSeconds !== null && state.remainingSeconds !== null
      ? state.remainingSeconds
      : null;

  const roundPct = state.totalRounds
    ? (Math.min(state.round, state.totalRounds) / state.totalRounds) * 100
    : 0;

  const legPct = state.totalLegs ? ((state.legNumber ?? 0) / state.totalLegs) * 100 : 0;

  const timePct = state.capSeconds
    ? Math.min(100, (state.elapsedSeconds / state.capSeconds) * 100)
    : 0;

  return (
    <div className={`timer-view ${tv ? "tv" : ""}`}>
      <div className="timer-toolbar">
        <button
          className="tv-toggle"
          onClick={() => setMuted((m) => !m)}
          aria-pressed={!muted}
          title={muted ? "Enable timer sounds" : "Mute timer sounds"}
        >
          {muted ? "🔇 Sound off" : "🔊 Sound on"}
        </button>
        <button
          className="tv-toggle"
          onClick={toggleTv}
          aria-pressed={tv}
          title={tv ? "Exit full-screen display" : "Fill the screen for a wall display"}
        >
          {tv ? "✕ Exit" : "⛶ Fill screen"}
        </button>
      </div>

      <div className="timer-header">
        <h2>{workout.name}</h2>
        <p className="mode-badge">{MODE_LABELS[workout.mode]}</p>
      </div>

      {counting && <p className="phase-label phase-countdown">Get ready</p>}
      {state.status !== "idle" && !counting && (
        <p className={`phase-label phase-${state.phase}`}>{state.phase}</p>
      )}

      <div
        className={`clock ${counting ? "countdown" : state.phase} ${
          state.overCap && !counting ? "over-cap" : ""
        }`}
      >
        {bigNumber}
      </div>

      {windowRemaining !== null && (
        <p className="clock-subtitle">{formatClock(windowRemaining)} left in the set</p>
      )}

      {state.currentMovement && <p className="current-movement">{state.currentMovement}</p>}

      {state.overCap && <p className="error">Time cap reached</p>}

      {state.capSeconds != null && (
        <div
          className="time-bar"
          aria-label={`Time elapsed toward ${formatClock(state.capSeconds)} cap`}
        >
          <div className="time-track">
            <div
              className={`time-fill ${state.overCap ? "over-cap" : ""}`}
              style={{ width: `${timePct}%` }}
            />
          </div>
          <span className="time-cap-label">{formatClock(state.capSeconds)}</span>
        </div>
      )}

      {state.totalLegs != null && (
        <div className="leg-bar" aria-label={`Movement ${state.legNumber} of ${state.totalLegs}`}>
          <span className="leg-number">{state.legNumber}</span>
          <div className="leg-track">
            <div className="leg-fill" style={{ width: `${legPct}%` }} />
          </div>
          <span className="leg-number leg-total">{state.totalLegs}</span>
        </div>
      )}

      {state.totalRounds != null && (
        <div className="round-bar" aria-label={`Round ${state.round} of ${state.totalRounds}`}>
          <span className="round-number">{Math.min(state.round, state.totalRounds)}</span>
          <div className="round-track">
            <div className="round-fill" style={{ width: `${roundPct}%` }} />
          </div>
          <span className="round-number round-total">{state.totalRounds}</span>
        </div>
      )}

      <div className="timer-controls">
        {state.status === "idle" && (
          <button
            onClick={() => {
              // Start is a user gesture — the only reliable moment to unlock
              // the audio context so later beeps are allowed to play.
              audio.unlock();
              controls.start();
              // Telemetry, deliberately unawaited and with failures ignored:
              // nothing about a running clock should depend on it. Only saved
              // workouts have an id the server can attribute.
              if (workout.id) void markWorkoutStarted(workout.id).catch(() => {});
            }}
          >
            Start
          </button>
        )}
        {counting && <button onClick={controls.skipCountdown}>Skip countdown</button>}
        {state.status === "running" && <button onClick={controls.pause}>Pause</button>}
        {state.status === "paused" && <button onClick={controls.resume}>Resume</button>}
        {(state.status === "running" || state.status === "paused") && (
          <button onClick={controls.finish}>Finish</button>
        )}
        {state.status === "finished" && <p>Done — elapsed {formatClock(state.elapsedSeconds)}</p>}
        <button onClick={controls.reset}>Reset</button>
      </div>

      {/* The plan, colour-coded, with the live position marked — so the next
          leg (and whether it's rest) is readable without waiting for it. */}
      <WorkoutTimeline
        workout={workout}
        elapsedSeconds={state.status === "idle" || counting ? null : state.elapsedSeconds}
      />
    </div>
  );
}
