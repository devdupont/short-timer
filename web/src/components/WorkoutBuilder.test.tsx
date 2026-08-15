import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { WorkoutBuilder } from "./WorkoutBuilder";
import type { Workout } from "../types";

/**
 * Building a workout by hand.
 *
 * The one that matters most is the save path: an already-saved workout has to
 * be *updated*, not created again. Creating hits the source-text dedup, which
 * hands back the original and silently drops every edit — the change appears
 * to save and simply isn't there.
 */

const api = vi.hoisted(() => ({
  createWorkout: vi.fn(),
  updateWorkout: vi.fn(),
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

const onSaved = vi.fn();
const onCancelEdit = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  api.createWorkout.mockImplementation(async (w: Workout) => ({ ...w, id: "new-id" }));
  api.updateWorkout.mockImplementation(async (_id: string, w: Workout) => w);
});

describe("building a new one", () => {
  it("will not save without a name", () => {
    render(<WorkoutBuilder onSaved={onSaved} />);

    expect(screen.getByRole("button", { name: "Save workout" })).toBeDisabled();
    expect(screen.getByText("Add a workout name to save.")).toBeInTheDocument();
  });

  it("treats a name of only spaces as missing", async () => {
    const user = userEvent.setup();
    render(<WorkoutBuilder onSaved={onSaved} />);

    await user.type(screen.getByPlaceholderText("e.g. Fran"), "   ");

    expect(screen.getByRole("button", { name: "Save workout" })).toBeDisabled();
  });

  it("creates it and hands back the saved copy", async () => {
    const user = userEvent.setup();
    render(<WorkoutBuilder onSaved={onSaved} />);

    await user.type(screen.getByPlaceholderText("e.g. Fran"), "Helen");
    await user.click(screen.getByRole("button", { name: "Save workout" }));

    await waitFor(() =>
      expect(api.createWorkout).toHaveBeenCalledWith(expect.objectContaining({ name: "Helen" })),
    );
    expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ id: "new-id" }));
  });

  it("clears the form afterwards, ready for the next one", async () => {
    const user = userEvent.setup();
    render(<WorkoutBuilder onSaved={onSaved} />);

    await user.type(screen.getByPlaceholderText("e.g. Fran"), "Helen");
    await user.click(screen.getByRole("button", { name: "Save workout" }));

    await waitFor(() => expect(screen.getByPlaceholderText("e.g. Fran")).toHaveValue(""));
  });

  it("keeps the form when saving fails", async () => {
    api.createWorkout.mockRejectedValue(new Error("nope"));
    const user = userEvent.setup();
    render(<WorkoutBuilder onSaved={onSaved} />);

    await user.type(screen.getByPlaceholderText("e.g. Fran"), "Helen");
    await user.click(screen.getByRole("button", { name: "Save workout" }));

    expect(await screen.findByText("Could not save that workout.")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("e.g. Fran")).toHaveValue("Helen");
  });
});

describe("editing an existing one", () => {
  it("seeds the form from the workout being edited", () => {
    render(
      <WorkoutBuilder
        onSaved={onSaved}
        editTarget={{ workout: workout({ name: "Fran", rep_scheme: [21, 15, 9] }), saved: true }}
      />,
    );

    expect(screen.getByRole("heading", { name: "Edit workout" })).toBeInTheDocument();
    expect(screen.getByPlaceholderText("e.g. Fran")).toHaveValue("Fran");
    expect(screen.getByPlaceholderText("e.g. 21, 15, 9")).toHaveValue("21, 15, 9");
  });

  it("updates in place rather than creating a second copy", async () => {
    // Creating would hit the source-text dedup, hand back the original, and
    // drop the edits — the save appears to work and changes nothing.
    const user = userEvent.setup();
    render(
      <WorkoutBuilder
        onSaved={onSaved}
        editTarget={{ workout: workout({ id: "abc" }), saved: true }}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Save changes & start" }));

    await waitFor(() => expect(api.updateWorkout).toHaveBeenCalledWith("abc", expect.anything()));
    expect(api.createWorkout).not.toHaveBeenCalled();
  });

  it("creates when the workout was never saved", async () => {
    // A parsed-but-unsaved WOD from the home page: there's nothing to update.
    const user = userEvent.setup();
    render(
      <WorkoutBuilder
        onSaved={onSaved}
        editTarget={{ workout: workout({ id: "" }), saved: false }}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Save & start" }));

    await waitFor(() => expect(api.createWorkout).toHaveBeenCalled());
    expect(api.updateWorkout).not.toHaveBeenCalled();
  });

  it("keeps the edited workout on screen after saving", async () => {
    // Unlike a fresh build, this isn't a form you're about to reuse.
    const user = userEvent.setup();
    render(
      <WorkoutBuilder onSaved={onSaved} editTarget={{ workout: workout(), saved: true }} />,
    );

    await user.click(screen.getByRole("button", { name: "Save changes & start" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(screen.getByPlaceholderText("e.g. Fran")).toHaveValue("Fran");
  });

  it("leaves edit mode when starting a new one", async () => {
    const user = userEvent.setup();
    render(
      <WorkoutBuilder
        onSaved={onSaved}
        editTarget={{ workout: workout(), saved: true }}
        onCancelEdit={onCancelEdit}
      />,
    );

    await user.click(screen.getByRole("button", { name: "New" }));

    expect(onCancelEdit).toHaveBeenCalled();
    expect(screen.getByPlaceholderText("e.g. Fran")).toHaveValue("");
  });
});

describe("fields that depend on the mode", () => {
  it("offers a time cap for the single-effort modes", async () => {
    const user = userEvent.setup();
    render(<WorkoutBuilder onSaved={onSaved} />);

    await user.selectOptions(screen.getByRole("combobox"), "amrap");

    expect(screen.getByLabelText(/minutes/)).toBeInTheDocument();
  });

  it("offers interval fields for the interval modes", async () => {
    const user = userEvent.setup();
    render(<WorkoutBuilder onSaved={onSaved} />);

    await user.selectOptions(screen.getByRole("combobox"), "emom");

    expect(screen.getByLabelText("Count each interval up")).toBeInTheDocument();
  });

  it("switches the interval clock direction", async () => {
    // For sets scored by finish time, where every athlete reads their own.
    const user = userEvent.setup();
    render(<WorkoutBuilder onSaved={onSaved} />);
    await user.selectOptions(screen.getByRole("combobox"), "emom");
    await user.type(screen.getByPlaceholderText("e.g. Fran"), "Sets");

    await user.click(screen.getByLabelText("Count each interval up"));
    await user.click(screen.getByRole("button", { name: "Save workout" }));

    await waitFor(() =>
      expect(api.createWorkout).toHaveBeenCalledWith(
        expect.objectContaining({ interval_clock: "count_up" }),
      ),
    );
  });
});

describe("the rep scheme box", () => {
  async function saveWith(text: string) {
    const user = userEvent.setup();
    render(<WorkoutBuilder onSaved={onSaved} />);
    await user.type(screen.getByPlaceholderText("e.g. Fran"), "Ladder");
    if (text) await user.type(screen.getByPlaceholderText("e.g. 21, 15, 9"), text);
    await user.click(screen.getByRole("button", { name: "Save workout" }));
    await waitFor(() => expect(api.createWorkout).toHaveBeenCalled());
    return api.createWorkout.mock.calls[0][0] as Workout;
  }

  it("reads a comma-separated ladder", async () => {
    expect((await saveWith("21, 15, 9")).rep_scheme).toEqual([21, 15, 9]);
  });

  it("accepts spaces as separators too", async () => {
    expect((await saveWith("21 15 9")).rep_scheme).toEqual([21, 15, 9]);
  });

  it("ignores anything that isn't a number", async () => {
    expect((await saveWith("21, x, 9")).rep_scheme).toEqual([21, 9]);
  });

  it("sends nothing rather than an empty ladder", async () => {
    // An empty array would read as "a ladder with no rungs" downstream.
    expect((await saveWith("")).rep_scheme).toBeNull();
  });
});

describe("segments and movements", () => {
  it("starts with one segment, which can't be removed", () => {
    // A workout with no segments has nothing to run, so the last one stays.
    render(<WorkoutBuilder onSaved={onSaved} />);

    expect(screen.getByText("Segment 1")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
  });

  it("adds and removes segments", async () => {
    const user = userEvent.setup();
    render(<WorkoutBuilder onSaved={onSaved} />);

    await user.click(screen.getByRole("button", { name: "+ Add segment" }));
    expect(screen.getByText("Segment 2")).toBeInTheDocument();

    await user.click(screen.getAllByRole("button", { name: "Remove" })[1]);
    expect(screen.queryByText("Segment 2")).not.toBeInTheDocument();
  });

  it("adds a second movement to a segment", async () => {
    const user = userEvent.setup();
    render(<WorkoutBuilder onSaved={onSaved} />);

    await user.click(screen.getByRole("button", { name: "+ Add movement" }));

    expect(screen.getAllByPlaceholderText("e.g. Pull-up")).toHaveLength(2);
  });

  it("removes a movement once there is more than one", async () => {
    const user = userEvent.setup();
    render(<WorkoutBuilder onSaved={onSaved} />);
    await user.click(screen.getByRole("button", { name: "+ Add movement" }));

    await user.click(screen.getByRole("button", { name: "Remove movement 2" }));

    expect(screen.getAllByPlaceholderText("e.g. Pull-up")).toHaveLength(1);
  });

  it("drops the movement editors when a leg becomes rest", async () => {
    // A rest leg has nothing to perform, so movement fields would be asking
    // for something that can't exist.
    const user = userEvent.setup();
    render(<WorkoutBuilder onSaved={onSaved} />);

    await user.click(screen.getByLabelText("Rest period"));

    expect(screen.queryByPlaceholderText("e.g. Pull-up")).not.toBeInTheDocument();
    expect(screen.getByText(/runs this leg as rest/)).toBeInTheDocument();
  });

  it("brings a blank movement back when it becomes work again", async () => {
    const user = userEvent.setup();
    render(<WorkoutBuilder onSaved={onSaved} />);
    const rest = screen.getByLabelText("Rest period");

    await user.click(rest);
    await user.click(rest);

    expect(screen.getByPlaceholderText("e.g. Pull-up")).toHaveValue("");
  });
});
