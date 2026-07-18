import { useState } from "react";
import { createWorkout } from "../api";
import type { Movement, Workout, WorkoutMode, WorkoutSegment } from "../types";
import { MODE_LABELS } from "../types";

function emptyMovement(): Movement {
  return { name: "" };
}

function emptySegment(): WorkoutSegment {
  return { movements: [emptyMovement()] };
}

function emptyWorkout(): Workout {
  return {
    id: "",
    name: "",
    mode: "for_time",
    segments: [emptySegment()],
    created_at: "",
    updated_at: "",
  };
}

function parseIntOrNull(value: string): number | null {
  if (!value.trim()) return null;
  const n = Number.parseInt(value, 10);
  return Number.isNaN(n) ? null : n;
}

function parseRepScheme(value: string): number[] | null {
  const nums = value
    .split(/[,\s]+/)
    .map((s) => Number.parseInt(s, 10))
    .filter((n) => !Number.isNaN(n));
  return nums.length ? nums : null;
}

export function WorkoutBuilder({ onSaved }: { onSaved: (workout: Workout) => void }) {
  const [workout, setWorkout] = useState<Workout>(emptyWorkout());
  const [repSchemeText, setRepSchemeText] = useState("");
  const [saving, setSaving] = useState(false);

  function updateSegment(index: number, patch: Partial<WorkoutSegment>) {
    setWorkout((w) => ({
      ...w,
      segments: w.segments.map((s, i) => (i === index ? { ...s, ...patch } : s)),
    }));
  }

  function updateMovement(segIndex: number, movIndex: number, patch: Partial<Movement>) {
    setWorkout((w) => ({
      ...w,
      segments: w.segments.map((s, i) =>
        i === segIndex
          ? { ...s, movements: s.movements.map((m, j) => (j === movIndex ? { ...m, ...patch } : m)) }
          : s,
      ),
    }));
  }

  function addSegment() {
    setWorkout((w) => ({ ...w, segments: [...w.segments, emptySegment()] }));
  }

  function removeSegment(index: number) {
    setWorkout((w) => ({ ...w, segments: w.segments.filter((_, i) => i !== index) }));
  }

  function addMovement(segIndex: number) {
    setWorkout((w) => ({
      ...w,
      segments: w.segments.map((s, i) =>
        i === segIndex ? { ...s, movements: [...s.movements, emptyMovement()] } : s,
      ),
    }));
  }

  function removeMovement(segIndex: number, movIndex: number) {
    setWorkout((w) => ({
      ...w,
      segments: w.segments.map((s, i) =>
        i === segIndex ? { ...s, movements: s.movements.filter((_, j) => j !== movIndex) } : s,
      ),
    }));
  }

  async function handleSave() {
    setSaving(true);
    try {
      const saved = await createWorkout({ ...workout, rep_scheme: parseRepScheme(repSchemeText) });
      onSaved(saved);
      setWorkout(emptyWorkout());
      setRepSchemeText("");
    } finally {
      setSaving(false);
    }
  }

  const showIntervalFields =
    workout.mode === "emom" || workout.mode === "tabata" || workout.mode === "interval";

  return (
    <div className="panel">
      <h2>Build a workout</h2>

      <label>
        Name
        <input
          value={workout.name}
          onChange={(e) => setWorkout({ ...workout, name: e.target.value })}
        />
      </label>

      <label>
        Mode
        <select
          value={workout.mode}
          onChange={(e) => setWorkout({ ...workout, mode: e.target.value as WorkoutMode })}
        >
          {Object.entries(MODE_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>

      {(workout.mode === "for_time" || workout.mode === "amrap") && (
        <label>
          {workout.mode === "amrap" ? "Time window (seconds)" : "Time cap (seconds, optional)"}
          <input
            type="number"
            value={workout.time_cap_seconds ?? ""}
            onChange={(e) =>
              setWorkout({ ...workout, time_cap_seconds: parseIntOrNull(e.target.value) })
            }
          />
        </label>
      )}

      {showIntervalFields && (
        <div className="inline-fields">
          <label>
            Rounds
            <input
              type="number"
              value={workout.rounds ?? ""}
              onChange={(e) => setWorkout({ ...workout, rounds: parseIntOrNull(e.target.value) })}
            />
          </label>
          <label>
            Work (sec)
            <input
              type="number"
              value={workout.work_seconds ?? ""}
              onChange={(e) =>
                setWorkout({ ...workout, work_seconds: parseIntOrNull(e.target.value) })
              }
            />
          </label>
          <label>
            Rest (sec)
            <input
              type="number"
              value={workout.rest_seconds ?? ""}
              onChange={(e) =>
                setWorkout({ ...workout, rest_seconds: parseIntOrNull(e.target.value) })
              }
            />
          </label>
        </div>
      )}

      <label>
        Rep scheme (e.g. 21, 15, 9)
        <input value={repSchemeText} onChange={(e) => setRepSchemeText(e.target.value)} />
      </label>

      <h3>Segments</h3>
      {workout.segments.map((segment, segIndex) => (
        <div className="segment-editor" key={segIndex}>
          <div className="inline-fields">
            <input
              placeholder="Label (optional)"
              value={segment.label ?? ""}
              onChange={(e) => updateSegment(segIndex, { label: e.target.value })}
            />
            <input
              type="number"
              placeholder="Rounds"
              value={segment.rounds ?? ""}
              onChange={(e) =>
                updateSegment(segIndex, { rounds: parseIntOrNull(e.target.value) })
              }
            />
            <button type="button" onClick={() => removeSegment(segIndex)}>
              Remove segment
            </button>
          </div>
          {segment.movements.map((movement, movIndex) => (
            <div className="inline-fields movement-row" key={movIndex}>
              <input
                placeholder="Movement"
                value={movement.name}
                onChange={(e) => updateMovement(segIndex, movIndex, { name: e.target.value })}
              />
              <input
                type="number"
                placeholder="Reps"
                value={movement.reps ?? ""}
                onChange={(e) =>
                  updateMovement(segIndex, movIndex, { reps: parseIntOrNull(e.target.value) })
                }
              />
              <input
                placeholder="Load (e.g. 95/65 lb)"
                value={movement.load ?? ""}
                onChange={(e) => updateMovement(segIndex, movIndex, { load: e.target.value })}
              />
              <input
                placeholder="Distance"
                value={movement.distance ?? ""}
                onChange={(e) => updateMovement(segIndex, movIndex, { distance: e.target.value })}
              />
              <button type="button" onClick={() => removeMovement(segIndex, movIndex)}>
                ×
              </button>
            </div>
          ))}
          <button type="button" onClick={() => addMovement(segIndex)}>
            + movement
          </button>
        </div>
      ))}
      <button type="button" onClick={addSegment}>
        + segment
      </button>

      <div className="actions">
        <button onClick={handleSave} disabled={saving || !workout.name.trim()}>
          {saving ? "Saving…" : "Save workout"}
        </button>
      </div>
    </div>
  );
}
