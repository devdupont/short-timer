import { useEffect, useState } from "react";
import { ApiError, deleteWorkout, listWorkouts, seedBenchmarks } from "../api";
import type { Workout } from "../types";
import { MODE_LABELS } from "../types";

/** Rows per page. The server caps what it will hand back at 100. */
const PAGE_SIZE = 25;

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
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  // What's actually been sent to the server — `query` lags behind it while typing.
  const [search, setSearch] = useState("");
  // Whether the library holds anything at all, which only an unfiltered count
  // can answer: zero results for a search says nothing about an empty library.
  const [hasAny, setHasAny] = useState(false);
  const [seeding, setSeeding] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  // Bumped to re-fetch the current page in place, after a delete or a seed.
  const [reloadKey, setReloadKey] = useState(0);

  // Searching is a round trip now, so wait for a pause in typing rather than
  // firing a request per keystroke.
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(query.trim());
      // A new search starts at the first page; page 4 of the old results is
      // usually past the end of the new ones.
      setOffset(0);
    }, 250);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    listWorkouts({ limit: PAGE_SIZE, offset, q: search })
      .then((page) => {
        if (cancelled) return;
        setWorkouts(page.items);
        setTotal(page.total);
        if (!search) setHasAny(page.total > 0);
        // Deleting the last row of the last page leaves the offset past the
        // end; step back rather than showing an empty page with a Prev button.
        if (page.items.length === 0 && offset > 0) {
          setOffset(Math.max(0, Math.ceil(page.total / PAGE_SIZE) - 1) * PAGE_SIZE);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey, reloadKey, search, offset]);

  async function handleDelete(id: string) {
    await deleteWorkout(id);
    // Refetch instead of splicing locally, so the row pulled up from the next
    // page fills the gap and the count stays honest.
    setReloadKey((key) => key + 1);
  }

  async function handleSeed() {
    setSeeding(true);
    setNotice(null);
    try {
      const { added, skipped } = await seedBenchmarks();
      setQuery("");
      setSearch("");
      setOffset(0);
      setReloadKey((key) => key + 1);
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

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const pageNumber = Math.floor(offset / PAGE_SIZE) + 1;
  const firstShown = total === 0 ? 0 : offset + 1;
  const lastShown = offset + workouts.length;

  const subtitle = loading
    ? "Loading…"
    : !hasAny
      ? "Nothing saved yet."
      : search
        ? `${total} matching workout${total === 1 ? "" : "s"}.`
        : `${total} saved workout${total === 1 ? "" : "s"}. Select one to load it into the timer.`;

  return (
    <div className="panel">
      <div className="panel-intro">
        <h2>Library</h2>
        <p className="section-sub">{subtitle}</p>
      </div>

      {hasAny && (
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

      {!loading && !hasAny && (
        <div className="empty-state library-empty">
          <p>No saved workouts yet — start with the classic benchmarks, or load a WOD.</p>
          <button className="primary-button" onClick={handleSeed} disabled={seeding}>
            {seeding ? "Adding…" : "Add benchmark WODs"}
          </button>
          <p className="field-hint">Murph, Cindy, Fran, Helen, DT and 10 more.</p>
        </div>
      )}

      {!loading && hasAny && total === 0 && (
        <div className="empty-state">No workouts match “{query}”.</div>
      )}

      {workouts.length > 0 && (
        <ul className="library-list">
          {workouts.map((workout) => (
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

      {total > PAGE_SIZE && (
        <nav className="library-pager" aria-label="Library pages">
          <button
            className="secondary-button"
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            disabled={loading || offset === 0}
          >
            ← Prev
          </button>
          <span className="library-pager-status" aria-live="polite">
            {firstShown}–{lastShown} of {total} · page {pageNumber} of {pageCount}
          </span>
          <button
            className="secondary-button"
            onClick={() => setOffset(offset + PAGE_SIZE)}
            disabled={loading || lastShown >= total}
          >
            Next →
          </button>
        </nav>
      )}

      {hasAny && (
        <div className="library-footer">
          <button className="secondary-button" onClick={handleSeed} disabled={seeding}>
            {seeding ? "Adding…" : "+ Add benchmark WODs"}
          </button>
        </div>
      )}
    </div>
  );
}
