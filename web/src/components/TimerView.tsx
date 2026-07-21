import { useEffect, useState } from "react";
import { useTimerEngine } from "../hooks/useTimerEngine";
import { useTimerAudio } from "../hooks/useTimerAudio";
import type { Workout } from "../types";
import { MODE_LABELS } from "../types";

const MUTED_KEY = "short-timer:muted";

function formatClock(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function TimerView({ workout }: { workout: Workout }) {
  const { state, controls } = useTimerEngine(workout);
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
  const bigNumber = counting
    ? String(Math.max(0, Math.ceil(state.countdownRemaining ?? 0)))
    : state.remainingSeconds !== null
      ? formatClock(state.remainingSeconds)
      : formatClock(state.elapsedSeconds);

  const roundPct = state.totalRounds
    ? (Math.min(state.round, state.totalRounds) / state.totalRounds) * 100
    : 0;

  const legPct = state.totalLegs ? ((state.legNumber ?? 0) / state.totalLegs) * 100 : 0;

  const timePct = state.capSeconds
    ? Math.min(100, (state.elapsedSeconds / state.capSeconds) * 100)
    : 0;

  const segmentLines = workout.segments
    .map((segment) => ({
      segment,
      movementText: segment.movements
        .map((m) => [m.reps, m.name, m.distance, m.load].filter(Boolean).join(" "))
        .filter(Boolean)
        .join(", "),
    }))
    .filter(({ segment, movementText }) => segment.label || segment.rounds || movementText);

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

      {segmentLines.length > 0 && (
        <ol className="segment-list">
          {segmentLines.map(({ segment, movementText }, i) => (
            <li key={i}>
              {segment.label && <strong>{segment.label}: </strong>}
              {segment.rounds && <em>{segment.rounds} rounds — </em>}
              {movementText}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
