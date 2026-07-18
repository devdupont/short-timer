import { useTimerEngine } from "../hooks/useTimerEngine";
import type { Workout } from "../types";
import { MODE_LABELS } from "../types";

function formatClock(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function TimerView({ workout }: { workout: Workout }) {
  const { state, controls } = useTimerEngine(workout);

  const bigNumber =
    state.remainingSeconds !== null ? formatClock(state.remainingSeconds) : formatClock(state.elapsedSeconds);

  return (
    <div className="panel timer-view">
      <h2>{workout.name}</h2>
      <p className="mode-badge">{MODE_LABELS[workout.mode]}</p>

      <div className={`clock ${state.phase} ${state.overCap ? "over-cap" : ""}`}>{bigNumber}</div>

      {state.totalRounds && (
        <p className="round-indicator">
          Round {Math.min(state.round, state.totalRounds)} / {state.totalRounds} — {state.phase}
        </p>
      )}
      {state.overCap && <p className="error">Time cap reached</p>}

      <div className="timer-controls">
        {state.status === "idle" && <button onClick={controls.start}>Start</button>}
        {state.status === "running" && <button onClick={controls.pause}>Pause</button>}
        {state.status === "paused" && <button onClick={controls.resume}>Resume</button>}
        {(state.status === "running" || state.status === "paused") && (
          <button onClick={controls.finish}>Finish</button>
        )}
        {state.status === "finished" && <p>Done — elapsed {formatClock(state.elapsedSeconds)}</p>}
        <button onClick={controls.reset}>Reset</button>
      </div>

      <ol className="segment-list">
        {workout.segments.map((segment, i) => (
          <li key={i}>
            {segment.label && <strong>{segment.label}: </strong>}
            {segment.rounds && <em>{segment.rounds} rounds — </em>}
            {segment.movements
              .map((m) => [m.reps, m.name, m.distance, m.load].filter(Boolean).join(" "))
              .join(", ")}
          </li>
        ))}
      </ol>
    </div>
  );
}
