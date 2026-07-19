import { useState } from "react";
import { ApiError, createWorkout, parseWorkout } from "../api";
import type { Workout } from "../types";
import { MODE_LABELS } from "../types";

export function WorkoutImport({
  onSaved,
  onLoad,
}: {
  onSaved: (workout: Workout) => void;
  onLoad: (workout: Workout) => void;
}) {
  const [text, setText] = useState("");
  const [preview, setPreview] = useState<Workout | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  async function handleParse() {
    setLoading(true);
    setError(null);
    setPreview(null);
    try {
      setPreview(await parseWorkout(text));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not parse that workout.");
    } finally {
      setLoading(false);
    }
  }

  async function handleSave() {
    if (!preview) return;
    setSaving(true);
    setError(null);
    try {
      const saved = await createWorkout(preview);
      setPreview(null);
      setText("");
      onSaved(saved);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save that workout.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="panel">
      <div className="panel-intro">
        <h2>Paste a workout</h2>
        <p className="section-sub">
          Paste any workout description and the parser turns it into a timer-ready format.
        </p>
      </div>

      <section className="form-card">
        <label className="field">
          <span className="field-label">Workout text</span>
          <textarea
            rows={8}
            placeholder={
              "Murph\nFor time:\n1 mile run\n100 pull-ups\n200 push-ups\n300 air squats\n1 mile run"
            }
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <span className="field-hint">
            Include the name, mode (For Time, AMRAP, EMOM…), and the movements.
          </span>
        </label>
        <div className="builder-actions">
          <button
            className="primary-button"
            onClick={handleParse}
            disabled={loading || !text.trim()}
          >
            {loading ? "Parsing…" : "Parse with LLM"}
          </button>
          {error && <span className="error">{error}</span>}
        </div>
      </section>

      {preview && (
        <section className="form-card preview-card">
          <div className="preview-head">
            <h3 className="section-title">{preview.name}</h3>
            <div className="badge-row">
              <span className="mode-badge">{MODE_LABELS[preview.mode]}</span>
              {preview.category && <span className="category-badge">{preview.category}</span>}
            </div>
          </div>
          {preview.description && <p className="section-sub">{preview.description}</p>}
          <ol className="segment-list">
            {preview.segments.map((segment, i) => (
              <li key={i}>
                {segment.label && <strong>{segment.label}: </strong>}
                {segment.rounds && <em>{segment.rounds} rounds — </em>}
                {segment.movements
                  .map((m) => [m.reps, m.name, m.load].filter(Boolean).join(" "))
                  .filter(Boolean)
                  .join(", ")}
              </li>
            ))}
          </ol>
          <div className="builder-actions">
            <button className="primary-button" onClick={handleSave} disabled={saving}>
              {saving ? "Saving…" : "Save to library"}
            </button>
            <button className="secondary-button" onClick={() => onLoad(preview)} disabled={saving}>
              Load without saving
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
