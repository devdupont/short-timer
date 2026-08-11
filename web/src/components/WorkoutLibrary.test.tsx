import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { WorkoutLibrary } from "./WorkoutLibrary";
import type { Workout, WorkoutPage } from "../types";

/**
 * The saved-workout list: searching, filtering, paging, and seeding.
 *
 * Two distinctions carry most of the weight here. "The library is empty" and
 * "nothing matched your search" are different states with different offers —
 * only the first should push the benchmark seed. And the page has to survive
 * deleting the last row of the last page, which otherwise leaves the offset
 * past the end and renders a blank page with a Prev button.
 */

const api = vi.hoisted(() => ({
  listWorkouts: vi.fn(),
  listWorkoutCategories: vi.fn(),
  deleteWorkout: vi.fn(),
  seedBenchmarks: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, ...api };
});

function workout(over: Partial<Workout> = {}): Workout {
  return {
    id: "w1",
    name: "Fran",
    mode: "for_time",
    segments: [],
    created_at: "",
    updated_at: "",
    ...over,
  };
}

function page(items: Workout[], total = items.length): WorkoutPage {
  return { items, total, limit: 25, offset: 0 };
}

/** 25 rows, so the pager appears. */
const FULL_PAGE = Array.from({ length: 25 }, (_, i) =>
  workout({ id: `w${i}`, name: `Workout ${i}` }),
);

const onSelect = vi.fn();
const onEdit = vi.fn();

function view() {
  return render(<WorkoutLibrary refreshKey={0} onSelect={onSelect} onEdit={onEdit} />);
}

beforeEach(() => {
  vi.clearAllMocks();
  api.listWorkouts.mockResolvedValue(page([workout()]));
  api.listWorkoutCategories.mockResolvedValue([]);
  api.deleteWorkout.mockResolvedValue(undefined);
  api.seedBenchmarks.mockResolvedValue({ added: 15, skipped: 0 });
});

describe("an empty library", () => {
  it("offers the benchmarks rather than a search box", async () => {
    api.listWorkouts.mockResolvedValue(page([]));

    view();

    expect(await screen.findByText("Nothing saved yet.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add benchmark WODs" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Search workouts")).not.toBeInTheDocument();
  });

  it("fills up when the benchmarks are added", async () => {
    api.listWorkouts.mockResolvedValueOnce(page([]));
    const user = userEvent.setup();
    view();

    await user.click(await screen.findByRole("button", { name: "Add benchmark WODs" }));

    expect(await screen.findByText("Added 15 benchmark workouts.")).toBeInTheDocument();
  });

  it("says so plainly when there was nothing new to add", async () => {
    api.listWorkouts.mockResolvedValueOnce(page([]));
    api.seedBenchmarks.mockResolvedValue({ added: 0, skipped: 15 });
    const user = userEvent.setup();
    view();

    await user.click(await screen.findByRole("button", { name: "Add benchmark WODs" }));

    expect(
      await screen.findByText("All benchmark workouts are already in your library."),
    ).toBeInTheDocument();
  });

  it("counts one added workout in the singular", async () => {
    api.listWorkouts.mockResolvedValueOnce(page([]));
    api.seedBenchmarks.mockResolvedValue({ added: 1, skipped: 14 });
    const user = userEvent.setup();
    view();

    await user.click(await screen.findByRole("button", { name: "Add benchmark WODs" }));

    expect(await screen.findByText("Added 1 benchmark workout (14 already saved).")).toBeInTheDocument();
  });

  it("reports a seed that failed", async () => {
    api.listWorkouts.mockResolvedValueOnce(page([]));
    api.seedBenchmarks.mockRejectedValue(new Error("nope"));
    const user = userEvent.setup();
    view();

    await user.click(await screen.findByRole("button", { name: "Add benchmark WODs" }));

    expect(await screen.findByText("Could not add benchmark workouts.")).toBeInTheDocument();
  });
});

describe("listing what's saved", () => {
  it("shows each workout with its mode", async () => {
    api.listWorkouts.mockResolvedValue(page([workout({ name: "Fran", category: "benchmark" })]));

    view();

    // Scoped to the row: the mode filter lists every mode as an option too.
    const row = (await screen.findByText("Fran")).closest("li") as HTMLElement;
    expect(within(row).getByText("For Time")).toBeInTheDocument();
    expect(within(row).getByText("benchmark")).toBeInTheDocument();
  });

  it("counts what it is showing", async () => {
    api.listWorkouts.mockResolvedValue(page([workout(), workout({ id: "w2" })], 2));

    view();

    expect(
      await screen.findByText("2 saved workouts. Select one to load it into the timer."),
    ).toBeInTheDocument();
  });

  it("loads one into the timer when it is picked", async () => {
    const fran = workout({ name: "Fran" });
    api.listWorkouts.mockResolvedValue(page([fran]));
    const user = userEvent.setup();
    view();

    await user.click(await screen.findByText("Fran"));

    expect(onSelect).toHaveBeenCalledWith(fran);
  });

  it("opens one for editing", async () => {
    const fran = workout({ name: "Fran" });
    api.listWorkouts.mockResolvedValue(page([fran]));
    const user = userEvent.setup();
    view();

    await user.click(await screen.findByRole("button", { name: "Edit Fran" }));

    expect(onEdit).toHaveBeenCalledWith(fran);
  });
});

describe("deleting", () => {
  it("deletes the row that was clicked", async () => {
    api.listWorkouts.mockResolvedValue(page([workout({ id: "abc", name: "Fran" })]));
    const user = userEvent.setup();
    view();

    await user.click(await screen.findByRole("button", { name: "Delete Fran" }));

    expect(api.deleteWorkout).toHaveBeenCalledWith("abc");
  });

  it("refetches rather than splicing the row out locally", async () => {
    // A refetch pulls up the row that was on the next page and keeps the count
    // honest; splicing locally would leave a short page and a stale total.
    api.listWorkouts.mockResolvedValue(page([workout({ name: "Fran" })]));
    const user = userEvent.setup();
    view();
    await screen.findByText("Fran");

    await user.click(screen.getByRole("button", { name: "Delete Fran" }));

    await waitFor(() => expect(api.listWorkouts).toHaveBeenCalledTimes(2));
  });
});

describe("narrowing the list", () => {
  it("waits for a pause before searching", async () => {
    // One request per keystroke would be a round trip per letter.
    const user = userEvent.setup();
    view();
    await screen.findByText("Fran");
    api.listWorkouts.mockClear();

    await user.type(screen.getByLabelText("Search workouts"), "murph");

    await waitFor(() => expect(api.listWorkouts).toHaveBeenCalledTimes(1), { timeout: 2000 });
    expect(api.listWorkouts).toHaveBeenCalledWith(
      expect.objectContaining({ q: "murph", offset: 0 }),
    );
  });

  it("filters by mode", async () => {
    const user = userEvent.setup();
    view();
    await screen.findByText("Fran");

    await user.selectOptions(screen.getByLabelText("Filter by mode"), "amrap");

    await waitFor(() =>
      expect(api.listWorkouts).toHaveBeenCalledWith(expect.objectContaining({ mode: "amrap" })),
    );
  });

  it("only offers a category filter once there are categories", async () => {
    view();
    await screen.findByText("Fran");

    expect(screen.queryByLabelText("Filter by category")).not.toBeInTheDocument();
  });

  it("filters by category when there are some", async () => {
    api.listWorkoutCategories.mockResolvedValue(["benchmark", "hero"]);
    const user = userEvent.setup();
    view();

    await user.selectOptions(await screen.findByLabelText("Filter by category"), "hero");

    await waitFor(() =>
      expect(api.listWorkouts).toHaveBeenCalledWith(expect.objectContaining({ category: "hero" })),
    );
  });

  it("distinguishes no matches from an empty library", async () => {
    // The library has something; this search just didn't find it. Offering the
    // benchmark seed here would be answering a question nobody asked.
    api.listWorkouts.mockResolvedValueOnce(page([workout()])).mockResolvedValue(page([], 0));
    const user = userEvent.setup();
    view();
    await screen.findByText("Fran");

    await user.type(screen.getByLabelText("Search workouts"), "nothing");

    expect(await screen.findByText(/No workouts match/, {}, { timeout: 2000 })).toBeInTheDocument();
    expect(screen.queryByText("Nothing saved yet.")).not.toBeInTheDocument();
  });
});

describe("paging", () => {
  it("stays hidden while everything fits on one page", async () => {
    view();
    await screen.findByText("Fran");

    expect(screen.queryByLabelText("Library pages")).not.toBeInTheDocument();
  });

  it("says which page is showing", async () => {
    api.listWorkouts.mockResolvedValue(page(FULL_PAGE, 60));

    view();

    const pager = await screen.findByLabelText("Library pages");
    expect(within(pager).getByText("1–25 of 60 · page 1 of 3")).toBeInTheDocument();
  });

  it("cannot go back from the first page", async () => {
    api.listWorkouts.mockResolvedValue(page(FULL_PAGE, 60));

    view();

    const pager = await screen.findByLabelText("Library pages");
    expect(within(pager).getByRole("button", { name: "← Prev" })).toBeDisabled();
  });

  it("asks for the next page's rows", async () => {
    api.listWorkouts.mockResolvedValue(page(FULL_PAGE, 60));
    const user = userEvent.setup();
    view();

    await user.click(await screen.findByRole("button", { name: "Next →" }));

    await waitFor(() =>
      expect(api.listWorkouts).toHaveBeenCalledWith(expect.objectContaining({ offset: 25 })),
    );
  });

  it("steps back when the last row of the last page is deleted", async () => {
    // Otherwise the offset sits past the end of the results and the view is a
    // blank page with a Prev button on it.
    api.listWorkouts.mockResolvedValueOnce(page(FULL_PAGE, 60));
    const user = userEvent.setup();
    view();
    await user.click(await screen.findByRole("button", { name: "Next →" }));

    api.listWorkouts.mockResolvedValue(page([], 25));
    await waitFor(() =>
      expect(api.listWorkouts).toHaveBeenCalledWith(expect.objectContaining({ offset: 0 })),
    );
  });
});
