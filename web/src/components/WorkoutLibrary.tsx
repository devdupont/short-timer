import { useEffect, useMemo, useState } from "react";
import { ApiError, deleteWorkout, listWorkouts, seedBenchmarks } from "../api";
import type { Workout } from "../types";
import { MODE_LABELS } from "../types";

/** Everything about a workout worth matching a search against, lowercased. */
function searchHaystack(workout: Workout): string {
  const parts = [workout.name, workout.category ?? "", MODE_LABELS[workout.mode]];
  for (const segment of workout.segments) {
    if (segment.label) parts.push(segment.label);
    for (const m of segment.movements) if (m.name) parts.push(m.name);
  }
  return parts.join(" ").toLowerCase();
}

export function WorkoutLibrary({
  refreshKey,
  onSelect,
  onEdit,
}: {
  refreshKey: number;
  onSelect: (workout: Workout) => void;
  onEdit: (workout: Workout) => void;
}) {
  const [workouts, setWorkouts] = useState<Workout[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [seeding, setSeeding] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    listWorkouts()
      .then(setWorkouts)
      .finally(() => setLoading(false));
  }, [refreshKey]);

  async function handleDelete(id: string) {
    await deleteWorkout(id);
    setWorkouts((prev) => prev.filter((w) => w.id !== id));
  }

  async function handleSeed() {
    setSeeding(true);
    setNotice(null);
    try {
      const { added, skipped } = await seedBenchmarks();
      setWorkouts(await listWorkouts());
      setNotice(
        added === 0
          ? "All benchmark workouts are already in your library."
          : `Added ${added} benchmark workout${added === 1 ? "" : "s"}${
              skipped ? ` (${skipped} already saved)` : ""
            }.`,
      );
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "Could not add benchmark workouts.");
    } finally {
      setSeeding(false);
    }
  }

  const filtered = useMemo(() => {
    const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
    if (terms.length === 0) return workouts;
    return workouts.filter((w) => {
      const haystack = searchHaystack(w);
      return terms.every((t) => haystack.includes(t));
    });
  }, [workouts, query]);

  const subtitle = loading
    ? "Loading…"
    : workouts.length === 0
      ? "Nothing saved yet."
      : `${workouts.length} saved workout${workouts.length === 1 ? "" : "s"}. Select one to load it into the timer.`;

  return (
    <div className="panel">
      <div className="panel-intro">
        <h2>Library</h2>
        <p className="section-sub">{subtitle}</p>
      </div>

      {workouts.length > 0 && (
        <input
          className="library-search"
          type="search"
          placeholder="Search by name, movement, mode, or category…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search workouts"
        />
      )}

      {notice && <p className="section-sub">{notice}</p>}

      {!loading && workouts.length === 0 && (
        <div className="empty-state library-empty">
          <p>No saved workouts yet — start with the classic benchmarks, or load a WOD.</p>
          <button className="primary-button" onClick={handleSeed} disabled={seeding}>
            {seeding ? "Adding…" : "Add benchmark WODs"}
          </button>
          <p className="field-hint">Murph, Cindy, Fran, Helen, DT and 10 more.</p>
        </div>
      )}

      {workouts.length > 0 && filtered.length === 0 && (
        <div className="empty-state">No workouts match “{query}”.</div>
      )}

      {filtered.length > 0 && (
        <ul className="library-list">
          {filtered.map((workout) => (
            <li className="library-row" key={workout.id}>
              <button className="library-item" onClick={() => onSelect(workout)}>
                <span className="library-item-name">{workout.name}</span>
                <span className="badge-row">
                  <span className="mode-badge">{MODE_LABELS[workout.mode]}</span>
                  {workout.category && <span className="category-badge">{workout.category}</span>}
                </span>
              </button>
              <button
                className="row-action"
                aria-label={`Edit ${workout.name}`}
                onClick={() => onEdit(workout)}
              >
                Edit
              </button>
              <button
                className="delete-button"
                aria-label={`Delete ${workout.name}`}
                onClick={() => handleDelete(workout.id)}
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}

      {workouts.length > 0 && (
        <div className="library-footer">
          <button className="secondary-button" onClick={handleSeed} disabled={seeding}>
            {seeding ? "Adding…" : "+ Add benchmark WODs"}
          </button>
        </div>
      )}
    </div>
  );
}
