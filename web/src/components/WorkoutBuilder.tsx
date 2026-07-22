import { useState } from "react";
import { ApiError, createWorkout, updateWorkout } from "../api";
import type { Movement, Workout, WorkoutMode, WorkoutSegment } from "../types";
import { MODE_HINTS, MODE_LABELS } from "../types";

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

/**
 * A duration entered as separate minutes and seconds boxes, stored as total
 * seconds. Coaches think in mm:ss ("a 20-minute cap"), not raw seconds.
 */
function DurationField({
  label,
  optional,
  hint,
  seconds,
  onChange,
}: {
  label: string;
  optional?: boolean;
  hint?: string;
  seconds: number | null | undefined;
  onChange: (seconds: number | null) => void;
}) {
  const hasValue = seconds !== null && seconds !== undefined;
  const mins = hasValue ? Math.floor(seconds / 60) : "";
  const secs = hasValue ? seconds % 60 : "";

  function update(nextMins: string, nextSecs: string) {
    const m = Math.max(0, Number.parseInt(nextMins, 10) || 0);
    const s = Math.min(59, Math.max(0, Number.parseInt(nextSecs, 10) || 0));
    const total = m * 60 + s;
    // A zero-length duration means "unset" rather than an instant timer.
    onChange(total === 0 ? null : total);
  }

  return (
    <div className="field">
      <span className="field-label">
        {label} {optional && <span className="optional">(optional)</span>}
      </span>
      <div className="duration-input">
        <input
          type="number"
          min={0}
          inputMode="numeric"
          placeholder="min"
          aria-label={`${label} minutes`}
          value={mins}
          onChange={(e) => update(e.target.value, String(secs))}
        />
        <span className="duration-sep">:</span>
        <input
          type="number"
          min={0}
          max={59}
          inputMode="numeric"
          placeholder="sec"
          aria-label={`${label} seconds`}
          value={secs}
          onChange={(e) => update(String(mins), e.target.value)}
        />
      </div>
      {hint && <span className="field-hint">{hint}</span>}
    </div>
  );
}

export interface EditTarget {
  workout: Workout;
  /** True when this workout already exists in the library (so we update in place). */
  saved: boolean;
}

export function WorkoutBuilder({
  onSaved,
  editTarget,
  onCancelEdit,
}: {
  onSaved: (workout: Workout) => void;
  editTarget?: EditTarget | null;
  onCancelEdit?: () => void;
}) {
  const [workout, setWorkout] = useState<Workout>(editTarget?.workout ?? emptyWorkout());
  const [repSchemeText, setRepSchemeText] = useState(
    (editTarget?.workout.rep_scheme ?? []).join(", "),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
    setError(null);
    const payload = { ...workout, rep_scheme: parseRepScheme(repSchemeText) };
    try {
      // An already-saved workout must be updated in place: creating would hit
      // the source-text dedup and hand back the original, dropping the edits.
      const saved = editTarget?.saved
        ? await updateWorkout(payload.id, payload)
        : await createWorkout(payload);
      onSaved(saved);
      if (!editTarget) {
        setWorkout(emptyWorkout());
        setRepSchemeText("");
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save that workout.");
    } finally {
      setSaving(false);
    }
  }

  const showIntervalFields =
    workout.mode === "emom" || workout.mode === "tabata" || workout.mode === "interval";
  const showTimeCap = workout.mode === "for_time" || workout.mode === "amrap";
  const nameMissing = !workout.name.trim();

  /** Start over: drop any loaded workout and reset the form to blank. */
  function handleNew() {
    setWorkout(emptyWorkout());
    setRepSchemeText("");
    setError(null);
    // Leaving edit mode also remounts this form via its key in App.
    onCancelEdit?.();
  }

  return (
    <div className="panel builder">
      <div className="builder-intro">
        <div className="builder-heading">
          <h2>{editTarget ? "Edit workout" : "Build a workout"}</h2>
          <button className="secondary-button builder-new" onClick={handleNew}>
            New
          </button>
        </div>
        <p className="section-sub">
          {editTarget
            ? `Adjust anything — a time cap, rounds, loads — then send it to the timer. Saving ${
                editTarget.saved ? "updates it in your library" : "adds it to your library"
              }.`
            : "Assemble a workout by hand: name it, choose how the clock runs, then add segments and movements."}
        </p>
      </div>

      <section className="form-card">
        <label className="field">
          <span className="field-label">
            Workout name <span className="required">*</span>
          </span>
          <input
            placeholder="e.g. Fran"
            value={workout.name}
            onChange={(e) => setWorkout({ ...workout, name: e.target.value })}
          />
        </label>

        <label className="field">
          <span className="field-label">Mode</span>
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
          <span className="field-hint">{MODE_HINTS[workout.mode]}</span>
        </label>

        {showTimeCap && (
          <DurationField
            label={workout.mode === "amrap" ? "Time window" : "Time cap"}
            optional={workout.mode !== "amrap"}
            seconds={workout.time_cap_seconds}
            onChange={(time_cap_seconds) => setWorkout({ ...workout, time_cap_seconds })}
          />
        )}

        {showIntervalFields && (
          <div className="field-grid three">
            <label className="field">
              <span className="field-label">Rounds</span>
              <input
                type="number"
                inputMode="numeric"
                placeholder="—"
                value={workout.rounds ?? ""}
                onChange={(e) => setWorkout({ ...workout, rounds: parseIntOrNull(e.target.value) })}
              />
            </label>
            <DurationField
              label="Work"
              seconds={workout.work_seconds}
              onChange={(work_seconds) => setWorkout({ ...workout, work_seconds })}
            />
            <DurationField
              label="Rest"
              seconds={workout.rest_seconds}
              onChange={(rest_seconds) => setWorkout({ ...workout, rest_seconds })}
            />
          </div>
        )}

        <label className="field">
          <span className="field-label">
            Rep scheme <span className="optional">(optional)</span>
          </span>
          <input
            placeholder="e.g. 21, 15, 9"
            value={repSchemeText}
            onChange={(e) => setRepSchemeText(e.target.value)}
          />
          <span className="field-hint">Comma-separated reps for a descending/ascending ladder.</span>
        </label>
      </section>

      <div className="builder-section-head">
        <h3 className="section-title">Segments</h3>
        <p className="section-sub">
          Each segment is a group of movements. Add more for chippers or multi-part workouts.
        </p>
      </div>

      {workout.segments.map((segment, segIndex) => (
        <section className="segment-card" key={segIndex}>
          <div className="segment-card-head">
            <span className="segment-badge">Segment {segIndex + 1}</span>
            {workout.segments.length > 1 && (
              <button
                type="button"
                className="text-remove"
                onClick={() => removeSegment(segIndex)}
              >
                Remove
              </button>
            )}
          </div>

          <div className="field-grid two">
            <label className="field">
              <span className="field-label">
                Label <span className="optional">(optional)</span>
              </span>
              <input
                placeholder="e.g. Buy-in"
                value={segment.label ?? ""}
                onChange={(e) => updateSegment(segIndex, { label: e.target.value })}
              />
            </label>
            <label className="field">
              <span className="field-label">
                Rounds <span className="optional">(optional)</span>
              </span>
              <input
                type="number"
                inputMode="numeric"
                placeholder="—"
                value={segment.rounds ?? ""}
                onChange={(e) => updateSegment(segIndex, { rounds: parseIntOrNull(e.target.value) })}
              />
            </label>
          </div>

          {/* Per-leg durations, for ladders and pyramids whose legs differ in
              length. Left blank they inherit the workout-level work/rest
              above, which is all a uniform interval workout needs. */}
          {showIntervalFields && (
            <div className="field-grid two">
              <DurationField
                label="Leg work"
                optional
                hint="Overrides the work/rest above for this leg only — how a 5/4/3/2/1 ladder is built."
                seconds={segment.work_seconds}
                onChange={(work_seconds) => updateSegment(segIndex, { work_seconds })}
              />
              <DurationField
                label="Leg rest"
                optional
                seconds={segment.rest_seconds}
                onChange={(rest_seconds) => updateSegment(segIndex, { rest_seconds })}
              />
            </div>
          )}

          <div className="movement-list">
            {segment.movements.map((movement, movIndex) => (
              <div className="movement-item" key={movIndex}>
                <div className="movement-item-head">
                  <span className="movement-item-title">Movement {movIndex + 1}</span>
                  {segment.movements.length > 1 && (
                    <button
                      type="button"
                      className="icon-remove"
                      aria-label={`Remove movement ${movIndex + 1}`}
                      onClick={() => removeMovement(segIndex, movIndex)}
                    >
                      ×
                    </button>
                  )}
                </div>
                <label className="field">
                  <span className="field-label">Name</span>
                  <input
                    placeholder="e.g. Pull-up"
                    value={movement.name ?? ""}
                    onChange={(e) => updateMovement(segIndex, movIndex, { name: e.target.value })}
                  />
                </label>
                <div className="field-grid three">
                  <label className="field">
                    <span className="field-label">Reps</span>
                    <input
                      type="number"
                      inputMode="numeric"
                      placeholder="—"
                      value={movement.reps ?? ""}
                      onChange={(e) =>
                        updateMovement(segIndex, movIndex, { reps: parseIntOrNull(e.target.value) })
                      }
                    />
                  </label>
                  <label className="field">
                    <span className="field-label">Load</span>
                    <input
                      placeholder="e.g. 95/65 lb"
                      value={movement.load ?? ""}
                      onChange={(e) => updateMovement(segIndex, movIndex, { load: e.target.value })}
                    />
                  </label>
                  <label className="field">
                    <span className="field-label">Distance</span>
                    <input
                      placeholder="e.g. 400 m"
                      value={movement.distance ?? ""}
                      onChange={(e) =>
                        updateMovement(segIndex, movIndex, { distance: e.target.value })
                      }
                    />
                  </label>
                </div>
              </div>
            ))}
          </div>

          <button type="button" className="add-button" onClick={() => addMovement(segIndex)}>
            + Add movement
          </button>
        </section>
      ))}

      <button type="button" className="add-button add-segment" onClick={addSegment}>
        + Add segment
      </button>

      <div className="builder-actions">
        <button className="primary-button" onClick={handleSave} disabled={saving || nameMissing}>
          {saving
            ? "Saving…"
            : editTarget
              ? `${editTarget.saved ? "Save changes" : "Save"} & start`
              : "Save workout"}
        </button>
        {nameMissing && <span className="field-hint">Add a workout name to save.</span>}
        {error && <span className="error">{error}</span>}
      </div>
    </div>
  );
}
