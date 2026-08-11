import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { WorkoutImport } from "./WorkoutImport";
import type { Workout } from "../types";

/**
 * Pasting a workout in and checking what came back.
 *
 * The preview is the whole point of this screen: an LLM parse can be wrong,
 * and it's far cheaper to notice here than three minutes into a workout. So
 * these care most about the parse being shown before it's committed, and
 * about "load without saving" not writing to the library.
 */

const api = vi.hoisted(() => ({
  parseWorkout: vi.fn(),
  createWorkout: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, ...api };
});

const FRAN: Workout = {
  id: "",
  name: "Fran",
  mode: "for_time",
  category: "benchmark",
  description: "21-15-9 reps for time",
  rep_scheme: [21, 15, 9],
  segments: [{ movements: [{ name: "Thruster", load: "95 lb" }] }],
  created_at: "",
  updated_at: "",
};

const onSaved = vi.fn();
const onLoad = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  api.parseWorkout.mockResolvedValue(FRAN);
  api.createWorkout.mockResolvedValue({ ...FRAN, id: "saved-1" });
});

async function paste(text = "Fran\n21-15-9"): Promise<ReturnType<typeof userEvent.setup>> {
  const user = userEvent.setup();
  render(<WorkoutImport onSaved={onSaved} onLoad={onLoad} />);
  await user.type(screen.getByRole("textbox"), text);
  return user;
}

describe("parsing", () => {
  it("will not parse an empty box", () => {
    render(<WorkoutImport onSaved={onSaved} onLoad={onLoad} />);

    expect(screen.getByRole("button", { name: "Parse with LLM" })).toBeDisabled();
  });

  it("will not parse whitespace either", async () => {
    // Every parse costs a model call, so an accidental space shouldn't buy one.
    const user = userEvent.setup();
    render(<WorkoutImport onSaved={onSaved} onLoad={onLoad} />);

    await user.type(screen.getByRole("textbox"), "   ");

    expect(screen.getByRole("button", { name: "Parse with LLM" })).toBeDisabled();
  });

  it("sends the pasted text to the parser", async () => {
    const user = await paste("Fran\n21-15-9");

    await user.click(screen.getByRole("button", { name: "Parse with LLM" }));

    expect(api.parseWorkout).toHaveBeenCalledWith("Fran\n21-15-9");
  });

  it("shows what came back before anything is saved", async () => {
    const user = await paste();

    await user.click(screen.getByRole("button", { name: "Parse with LLM" }));

    expect(await screen.findByText("Fran")).toBeInTheDocument();
    expect(screen.getByText("For Time")).toBeInTheDocument();
    expect(screen.getByText("benchmark")).toBeInTheDocument();
    expect(api.createWorkout).not.toHaveBeenCalled();
  });

  it("says when a parse failed, and offers nothing to save", async () => {
    api.parseWorkout.mockRejectedValue(new Error("model down"));
    const user = await paste();

    await user.click(screen.getByRole("button", { name: "Parse with LLM" }));

    expect(await screen.findByText("Could not parse that workout.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save to library" })).not.toBeInTheDocument();
  });

  it("clears a stale preview when parsing again", async () => {
    // Otherwise a failed second parse leaves the first result on screen, and
    // the buttons under it would save something the text no longer says.
    const user = await paste();
    await user.click(screen.getByRole("button", { name: "Parse with LLM" }));
    await screen.findByText("Fran");

    api.parseWorkout.mockRejectedValue(new Error("model down"));
    await user.click(screen.getByRole("button", { name: "Parse with LLM" }));

    await waitFor(() => expect(screen.queryByText("Fran")).not.toBeInTheDocument());
  });
});

describe("what to do with the result", () => {
  it("saves it and hands the saved copy back", async () => {
    // The saved copy carries the id the library and timer need; the preview
    // doesn't have one yet.
    const user = await paste();
    await user.click(screen.getByRole("button", { name: "Parse with LLM" }));
    await user.click(await screen.findByRole("button", { name: "Save to library" }));

    await waitFor(() => expect(api.createWorkout).toHaveBeenCalledWith(FRAN));
    expect(onSaved).toHaveBeenCalledWith({ ...FRAN, id: "saved-1" });
  });

  it("empties the box once it has been saved", async () => {
    const user = await paste();
    await user.click(screen.getByRole("button", { name: "Parse with LLM" }));
    await user.click(await screen.findByRole("button", { name: "Save to library" }));

    await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue(""));
    expect(screen.queryByRole("button", { name: "Save to library" })).not.toBeInTheDocument();
  });

  it("loads without saving, leaving the library alone", async () => {
    // For trying a one-off: the timer runs it, nothing is written down.
    const user = await paste();
    await user.click(screen.getByRole("button", { name: "Parse with LLM" }));
    await user.click(await screen.findByRole("button", { name: "Load without saving" }));

    expect(onLoad).toHaveBeenCalledWith(FRAN);
    expect(api.createWorkout).not.toHaveBeenCalled();
  });

  it("keeps the preview when saving fails, so the parse isn't lost", async () => {
    // Re-parsing costs another model call; the result is still on screen and
    // still savable.
    api.createWorkout.mockRejectedValue(new Error("nope"));
    const user = await paste();
    await user.click(screen.getByRole("button", { name: "Parse with LLM" }));
    await user.click(await screen.findByRole("button", { name: "Save to library" }));

    expect(await screen.findByText("Could not save that workout.")).toBeInTheDocument();
    expect(screen.getByText("Fran")).toBeInTheDocument();
    expect(onSaved).not.toHaveBeenCalled();
  });
});
