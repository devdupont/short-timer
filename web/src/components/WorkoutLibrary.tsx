import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  deleteWorkout,
  listWorkoutCategories,
  listWorkouts,
  seedBenchmarks,
} from "../api";
import type { Workout, WorkoutMode } from "../types";
import { MODE_LABELS } from "../types";

const PAGE_SIZE = 20;
//: Long enough that a typed word is one request rather than one per keystroke.
const SEARCH_DEBOUNCE_MS = 300;

export function WorkoutLibrary({
  refreshKey,
  onSelect,
  onEdit,
}: {
  refreshKey: number;
  onSelect: (workout: Workout) => void;
  onEdit: (workout: Workout) => void;
}) {
  const [items, setItems] = useState<Workout[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [mode, setMode] = useState<WorkoutMode | "">("");
  const [category, setCategory] = useState("");
  const [categories, setCategories] = useState<string[]>([]);
  const [page, setPage] = useState(1);
  const [seeding, setSeeding] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  // Bumped by anything that changes the library under us (delete, seed) to
  // re-read the current page from the server rather than patching it locally.
  const [reloadKey, setReloadKey] = useState(0);

  const filtering = Boolean(search || mode || category);
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  // Each filter change resets the page in the *same* update that applies the
  // filter. Doing it in a follow-up effect instead would fetch twice — once at
  // the old offset, which briefly renders an empty page, then again at 0.
  useEffect(() => {
    const id = setTimeout(() => {
      setSearch(query.trim());
      setPage(1);
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(id);
  }, [query]);

  function changeMode(next: WorkoutMode | "") {
    setMode(next);
    setPage(1);
  }

  function changeCategory(next: string) {
    setCategory(next);
    setPage(1);
  }

  // Responses can land out of order once typing drives the requests; only the
  // newest one may write to state.
  const latestRequest = useRef(0);

  useEffect(() => {
    const seq = ++latestRequest.current;
    setLoading(true);
    listWorkouts({
      q: search || undefined,
      mode: mode || undefined,
      category: category || undefined,
      limit: PAGE_SIZE,
      offset: (page - 1) * PAGE_SIZE,
    })
      .then((result) => {
        if (seq !== latestRequest.current) return;
        setItems(result.items);
        setTotal(result.total);
      })
      .finally(() => {
        if (seq === latestRequest.current) setLoading(false);
      });
  }, [refreshKey, reloadKey, search, mode, category, page]);

  useEffect(() => {
    listWorkoutCategories().then(setCategories);
  }, [refreshKey, reloadKey]);

  // Deleting the last row on the last page leaves us past the end.
  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

  async function handleDelete(id: string) {
    await deleteWorkout(id);
    setReloadKey((k) => k + 1);
  }

  async function handleSeed() {
    setSeeding(true);
    setNotice(null);
    try {
      const { added, skipped } = await seedBenchmarks();
      setReloadKey((k) => k + 1);
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

  const libraryEmpty = !filtering && total === 0;
  const subtitle = loading && items.length === 0
    ? "Loading…"
    : libraryEmpty
      ? "Nothing saved yet."
      : filtering
        ? `${total} match${total === 1 ? "" : "es"}.`
        : `${total} saved workout${total === 1 ? "" : "s"}. Select one to load it into the timer.`;

  const firstShown = (page - 1) * PAGE_SIZE + 1;
  const lastShown = Math.min(page * PAGE_SIZE, total);

  return (
    <div className="panel">
      <div className="panel-intro">
        <h2>Library</h2>
        <p className="section-sub">{subtitle}</p>
      </div>

      {!libraryEmpty && (
        <div className="library-filters">
          <input
            className="library-search"
            type="search"
            placeholder="Search by name, movement, mode, or category…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search workouts"
          />
          <select
            className="library-filter"
            value={mode}
            onChange={(e) => changeMode(e.target.value as WorkoutMode | "")}
            aria-label="Filter by mode"
          >
            <option value="">All modes</option>
            {Object.entries(MODE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          {categories.length > 0 && (
            <select
              className="library-filter"
              value={category}
              onChange={(e) => changeCategory(e.target.value)}
              aria-label="Filter by category"
            >
              <option value="">All categories</option>
              {categories.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          )}
        </div>
      )}

      {notice && <p className="section-sub">{notice}</p>}

      {!loading && libraryEmpty && (
        <div className="empty-state library-empty">
          <p>No saved workouts yet — start with the classic benchmarks, or load a WOD.</p>
          <button className="primary-button" onClick={handleSeed} disabled={seeding}>
            {seeding ? "Adding…" : "Add benchmark WODs"}
          </button>
          <p className="field-hint">Murph, Cindy, Fran, Helen, DT and 10 more.</p>
        </div>
      )}

      {!loading && filtering && total === 0 && (
        <div className="empty-state">No workouts match these filters.</div>
      )}

      {items.length > 0 && (
        <ul className="library-list">
          {items.map((workout) => (
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

      {totalPages > 1 && (
        <nav className="library-pagination" aria-label="Library pages">
          <button
            className="secondary-button page-button"
            onClick={() => setPage(page - 1)}
            disabled={page === 1 || loading}
          >
            ← Prev
          </button>
          <span className="page-status" aria-live="polite">
            {firstShown}–{lastShown} of {total}
          </span>
          <button
            className="secondary-button page-button"
            onClick={() => setPage(page + 1)}
            disabled={page === totalPages || loading}
          >
            Next →
          </button>
        </nav>
      )}

      {!libraryEmpty && (
        <div className="library-footer">
          <button className="secondary-button" onClick={handleSeed} disabled={seeding}>
            {seeding ? "Adding…" : "+ Add benchmark WODs"}
          </button>
        </div>
      )}
    </div>
  );
}
