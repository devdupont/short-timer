import { useEffect, useState } from "react";
import { deleteWorkout, listWorkouts } from "../api";
import type { Workout } from "../types";
import { MODE_LABELS } from "../types";

export function WorkoutLibrary({
  refreshKey,
  onSelect,
}: {
  refreshKey: number;
  onSelect: (workout: Workout) => void;
}) {
  const [workouts, setWorkouts] = useState<Workout[]>([]);
  const [loading, setLoading] = useState(true);

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

  if (loading) return <div className="panel">Loading library…</div>;

  return (
    <div className="panel">
      <h2>Library</h2>
      {workouts.length === 0 && <p>No saved workouts yet.</p>}
      <ul className="library-list">
        {workouts.map((workout) => (
          <li key={workout.id}>
            <button className="library-item" onClick={() => onSelect(workout)}>
              <strong>{workout.name}</strong>
              <span className="mode-badge">{MODE_LABELS[workout.mode]}</span>
              {workout.category && <span className="category-badge">{workout.category}</span>}
            </button>
            <button className="delete-button" onClick={() => handleDelete(workout.id)}>
              Delete
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
