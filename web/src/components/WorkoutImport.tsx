import { useState } from "react";
import { ApiError, createWorkout, parseWorkout } from "../api";
import type { Workout } from "../types";
import { MODE_LABELS } from "../types";

export function WorkoutImport({ onSaved }: { onSaved: (workout: Workout) => void }) {
  const [text, setText] = useState("");
  const [preview, setPreview] = useState<Workout | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

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
    const saved = await createWorkout(preview);
    setPreview(null);
    setText("");
    onSaved(saved);
  }

  return (
    <div className="panel">
      <h2>Paste a workout</h2>
      <textarea
        rows={8}
        placeholder={"Murph\nFor time:\n1 mile run\n100 pull-ups\n200 push-ups\n300 air squats\n1 mile run"}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <button onClick={handleParse} disabled={loading || !text.trim()}>
        {loading ? "Parsing…" : "Parse with LLM"}
      </button>
      {error && <p className="error">{error}</p>}

      {preview && (
        <div className="preview">
          <h3>{preview.name}</h3>
          <p className="mode-badge">{MODE_LABELS[preview.mode]}</p>
          {preview.description && <p>{preview.description}</p>}
          <ul>
            {preview.segments.map((segment, i) => (
              <li key={i}>
                {segment.label && <strong>{segment.label}: </strong>}
                {segment.rounds && <em>{segment.rounds} rounds — </em>}
                {segment.movements
                  .map((m) => [m.reps, m.name, m.load].filter(Boolean).join(" "))
                  .join(", ")}
              </li>
            ))}
          </ul>
          <button onClick={handleSave}>Save to library</button>
        </div>
      )}
    </div>
  );
}
