import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TimerView } from "./TimerView";
import type { Workout, WorkoutSegment } from "../types";

/**
 * The timer screen.
 *
 * `useTimerEngine.test.ts` covers the clock itself, so these cover what the
 * screen does around it: which control is offered in which state, the
 * telemetry that must never be allowed to affect a running clock, and the
 * split between a workout with a clock and one without.
 */

const api = vi.hoisted(() => ({
  markWorkoutStarted: vi.fn(),
  markWorkoutCompleted: vi.fn(),
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

function seg(over: Partial<WorkoutSegment> = {}): WorkoutSegment {
  return { movements: [], ...over };
}

/** Two working minutes and a rest, so there are legs and rounds to show. */
const EMOM = workout({
  mode: "emom",
  rounds: 2,
  work_seconds: 60,
  segments: [
    seg({ movements: [{ name: "Row", calories: 16 }] }),
    seg({ movements: [{ name: "Wall Walk", reps: 3 }] }),
  ],
});

/** A strength session: real work, but nothing to count. */
const UNTIMED = workout({
  mode: "custom",
  segments: [
    seg({ movements: [{ name: "Back Squat", notes: "2-3 sets" }] }),
    seg({ movements: [{ name: "Pull-up" }], rounds: 3 }),
  ],
});

function advance(seconds: number): void {
  act(() => {
    vi.advanceTimersByTime(seconds * 1000);
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  api.markWorkoutStarted.mockResolvedValue(undefined);
  api.markWorkoutCompleted.mockResolvedValue(undefined);
  localStorage.clear();
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "performance", "Date"] });
});

afterEach(() => {
  vi.useRealTimers();
});

/** userEvent drives its own clock, which has to be the faked one. */
function ui() {
  return userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
}

describe("a workout with a clock", () => {
  it("starts idle, offering only Start", () => {
    render(<TimerView workout={EMOM} />);

    expect(screen.getByRole("button", { name: "Start" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pause" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Finish" })).not.toBeInTheDocument();
  });

  it("counts the lead-in down before the workout", async () => {
    const user = ui();
    render(<TimerView workout={EMOM} />);

    await user.click(screen.getByRole("button", { name: "Start" }));

    expect(screen.getByText("Get ready")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Skip countdown" })).toBeInTheDocument();
  });

  it("offers Pause and Finish once running", async () => {
    const user = ui();
    render(<TimerView workout={EMOM} />);

    await user.click(screen.getByRole("button", { name: "Start" }));
    await user.click(screen.getByRole("button", { name: "Skip countdown" }));

    expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Finish" })).toBeInTheDocument();
  });

  it("swaps Pause for Resume when paused", async () => {
    const user = ui();
    render(<TimerView workout={EMOM} />);

    await user.click(screen.getByRole("button", { name: "Start" }));
    await user.click(screen.getByRole("button", { name: "Skip countdown" }));
    await user.click(screen.getByRole("button", { name: "Pause" }));

    expect(screen.getByRole("button", { name: "Resume" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pause" })).not.toBeInTheDocument();
  });

  it("reports the elapsed time when finished", async () => {
    const user = ui();
    render(<TimerView workout={EMOM} />);

    await user.click(screen.getByRole("button", { name: "Start" }));
    await user.click(screen.getByRole("button", { name: "Skip countdown" }));
    advance(65);
    await user.click(screen.getByRole("button", { name: "Finish" }));

    expect(screen.getByText(/Done — elapsed/)).toBeInTheDocument();
  });

  it("shows how far through the rounds and legs it is", async () => {
    const user = ui();
    render(<TimerView workout={EMOM} />);

    await user.click(screen.getByRole("button", { name: "Start" }));
    await user.click(screen.getByRole("button", { name: "Skip countdown" }));

    expect(screen.getByLabelText("Round 1 of 2")).toBeInTheDocument();
    expect(screen.getByLabelText("Movement 1 of 2")).toBeInTheDocument();
  });

  it("names the movement under way", async () => {
    const user = ui();
    render(<TimerView workout={EMOM} />);

    await user.click(screen.getByRole("button", { name: "Start" }));
    await user.click(screen.getByRole("button", { name: "Skip countdown" }));

    // Scoped to the readout: the timeline below names every movement too.
    expect(document.querySelector(".current-movement")).toHaveTextContent(/Row/);
  });

  it("says when a cap has been passed", async () => {
    const user = ui();
    render(<TimerView workout={workout({ mode: "for_time", time_cap_seconds: 5 })} />);

    await user.click(screen.getByRole("button", { name: "Start" }));
    await user.click(screen.getByRole("button", { name: "Skip countdown" }));
    advance(8);

    expect(screen.getByText("Time cap reached")).toBeInTheDocument();
  });
});

describe("telemetry", () => {
  it("records a start, without waiting for it", async () => {
    const user = ui();
    render(<TimerView workout={EMOM} />);

    await user.click(screen.getByRole("button", { name: "Start" }));

    expect(api.markWorkoutStarted).toHaveBeenCalledWith("w1");
  });

  it("lets the clock run even when reporting fails", async () => {
    // Nothing about a running clock should depend on the network.
    api.markWorkoutStarted.mockRejectedValue(new Error("offline"));
    const user = ui();
    render(<TimerView workout={EMOM} />);

    await user.click(screen.getByRole("button", { name: "Start" }));

    expect(screen.getByText("Get ready")).toBeInTheDocument();
  });

  it("says nothing about a workout the server has never seen", async () => {
    // An unsaved preview has no id to attribute anything to.
    const user = ui();
    render(<TimerView workout={workout({ id: "" })} />);

    await user.click(screen.getByRole("button", { name: "Start" }));

    expect(api.markWorkoutStarted).not.toHaveBeenCalled();
  });

  it("records a finish once, not on every tick", async () => {
    const user = ui();
    render(<TimerView workout={EMOM} />);

    await user.click(screen.getByRole("button", { name: "Start" }));
    await user.click(screen.getByRole("button", { name: "Skip countdown" }));
    advance(10);
    await user.click(screen.getByRole("button", { name: "Finish" }));
    advance(10);

    expect(api.markWorkoutCompleted).toHaveBeenCalledTimes(1);
  });

  it("records the second run of the same workout too", async () => {
    // The guard clears on the way out of "finished" rather than being keyed to
    // the workout — otherwise doing Helen twice records one finish.
    const user = ui();
    render(<TimerView workout={EMOM} />);

    await user.click(screen.getByRole("button", { name: "Start" }));
    await user.click(screen.getByRole("button", { name: "Skip countdown" }));
    await user.click(screen.getByRole("button", { name: "Finish" }));
    await user.click(screen.getByRole("button", { name: "Reset" }));

    await user.click(screen.getByRole("button", { name: "Start" }));
    await user.click(screen.getByRole("button", { name: "Skip countdown" }));
    await user.click(screen.getByRole("button", { name: "Finish" }));

    expect(api.markWorkoutCompleted).toHaveBeenCalledTimes(2);
  });
});

describe("the wall display", () => {
  it("remembers being muted between sessions", async () => {
    // A gym that muted the timer shouldn't have to mute it again tomorrow.
    const user = ui();
    const { unmount } = render(<TimerView workout={EMOM} />);

    await user.click(screen.getByRole("button", { name: /Sound on/ }));
    expect(screen.getByRole("button", { name: /Sound off/ })).toBeInTheDocument();

    unmount();
    render(<TimerView workout={EMOM} />);

    expect(screen.getByRole("button", { name: /Sound off/ })).toBeInTheDocument();
  });

  it("still mutes for this session when storage is unavailable", async () => {
    // Private browsing throws on write; muting has to keep working anyway.
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("private mode");
    });
    const user = ui();
    render(<TimerView workout={EMOM} />);

    await user.click(screen.getByRole("button", { name: /Sound on/ }));

    expect(screen.getByRole("button", { name: /Sound off/ })).toBeInTheDocument();
    vi.restoreAllMocks();
  });

  it("fills the screen on request", async () => {
    const request = vi.fn().mockResolvedValue(undefined);
    document.documentElement.requestFullscreen = request;
    const user = ui();
    render(<TimerView workout={EMOM} />);

    await user.click(screen.getByRole("button", { name: /Fill screen/ }));

    expect(request).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /Exit/ })).toBeInTheDocument();
  });

  it("stays in TV mode when the browser refuses fullscreen", async () => {
    // The CSS layout is the source of truth, so an embedded preview that
    // blocks the Fullscreen API still gets the wall display.
    document.documentElement.requestFullscreen = vi
      .fn()
      .mockRejectedValue(new Error("blocked"));
    const user = ui();
    render(<TimerView workout={EMOM} />);

    await user.click(screen.getByRole("button", { name: /Fill screen/ }));

    expect(screen.getByRole("button", { name: /Exit/ })).toBeInTheDocument();
  });

  it("drops TV mode when fullscreen is left by any other means", async () => {
    // Pressing Esc exits fullscreen without touching our button.
    document.documentElement.requestFullscreen = vi.fn().mockResolvedValue(undefined);
    const user = ui();
    render(<TimerView workout={EMOM} />);
    await user.click(screen.getByRole("button", { name: /Fill screen/ }));

    act(() => {
      document.dispatchEvent(new Event("fullscreenchange"));
    });

    expect(screen.getByRole("button", { name: /Fill screen/ })).toBeInTheDocument();
  });
});

describe("a workout with nothing to time", () => {
  it("offers a checklist instead of a clock", () => {
    render(<TimerView workout={UNTIMED} />);

    expect(screen.queryByRole("button", { name: "Start" })).not.toBeInTheDocument();
    expect(screen.getByText("0 of 2 done")).toBeInTheDocument();
  });

  it("counts things off as they're ticked", async () => {
    const user = ui();
    render(<TimerView workout={UNTIMED} />);

    await user.click(screen.getAllByRole("checkbox")[0]);

    expect(screen.getByText("1 of 2 done")).toBeInTheDocument();
  });

  it("clears the ticks on reset", async () => {
    const user = ui();
    render(<TimerView workout={UNTIMED} />);
    await user.click(screen.getAllByRole("checkbox")[0]);

    await user.click(screen.getByRole("button", { name: "Reset" }));

    expect(screen.getByText("0 of 2 done")).toBeInTheDocument();
  });

  it("prefers the source's own words over a rounded set count", async () => {
    // "2-3 sets" is truer than the single number the parser had to pick.
    render(<TimerView workout={UNTIMED} />);

    expect(screen.getByText("2-3 sets")).toBeInTheDocument();
    expect(screen.getByText("3 sets")).toBeInTheDocument();
  });

  it("says so plainly when there is nothing listed at all", () => {
    render(<TimerView workout={workout({ mode: "custom", segments: [] })} />);

    expect(screen.getByText(/Nothing to time here/)).toBeInTheDocument();
  });
});
