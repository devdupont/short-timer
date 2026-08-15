import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { WorkoutTimeline } from "./WorkoutTimeline";
import type { Workout, WorkoutSegment } from "../types";

/**
 * The visualizer that draws a parse to scale.
 *
 * Its whole reason to exist is that a bad parse should be visible before the
 * clock starts — a rest minute read as a movement, or a leg the wrong length,
 * shows up as a wrong-shaped bar. `timerPlan.test.ts` covers the plan it draws
 * from; these cover the reading it gives that plan.
 */

function workout(over: Partial<Workout> = {}): Workout {
  return {
    id: "w1",
    name: "test",
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

describe("the summary chips", () => {
  it("describes a bounded interval workout's shape", () => {
    render(
      <WorkoutTimeline
        workout={workout({
          mode: "emom",
          rounds: 3,
          work_seconds: 60,
          segments: [seg({ movements: [{ name: "Row" }] })],
        })}
      />,
    );

    expect(screen.getByText("3 × 1:00 round")).toBeInTheDocument();
    expect(screen.getByText("3:00 total")).toBeInTheDocument();
  });

  it("says when a round repeats without a bound", () => {
    render(
      <WorkoutTimeline
        workout={workout({
          mode: "emom",
          work_seconds: 60,
          segments: [seg({ movements: [{ name: "Row" }] })],
        })}
      />,
    );

    expect(screen.getByText("1:00 round, unbounded")).toBeInTheDocument();
  });

  it("says a set counts up, because the bars can't show direction", () => {
    // "Every 3:00 × 5, score = slowest set" runs the leg clock upward, and
    // that's the one part of how the clock behaves a bar cannot draw.
    render(
      <WorkoutTimeline
        workout={workout({
          mode: "interval",
          rounds: 5,
          work_seconds: 180,
          interval_clock: "count_up",
          segments: [seg({ movements: [{ name: "Row" }] })],
        })}
      />,
    );

    expect(screen.getByText("sets count up")).toBeInTheDocument();
  });

  it("calls an amrap's limit a window and a for-time's a cap", () => {
    // They're the same number and mean opposite things: one is how long you
    // get, the other is when you're cut off.
    const { unmount } = render(
      <WorkoutTimeline workout={workout({ mode: "amrap", time_cap_seconds: 1200 })} />,
    );
    expect(screen.getByText("window 20:00")).toBeInTheDocument();
    unmount();

    render(<WorkoutTimeline workout={workout({ mode: "for_time", time_cap_seconds: 600 })} />);
    expect(screen.getByText("cap 10:00")).toBeInTheDocument();
  });

  it("says so when a for-time has no cap at all", () => {
    render(<WorkoutTimeline workout={workout({ mode: "for_time" })} />);

    expect(screen.getByText("no cap")).toBeInTheDocument();
  });

  it("shows a rep scheme", () => {
    render(<WorkoutTimeline workout={workout({ rep_scheme: [21, 15, 9] })} />);

    expect(screen.getByText("21-15-9")).toBeInTheDocument();
  });

  it("shows a round count for a non-interval workout", () => {
    render(<WorkoutTimeline workout={workout({ mode: "for_time", rounds: 5 })} />);

    expect(screen.getByText("5 rounds")).toBeInTheDocument();
  });
});

describe("workouts with no clock shape", () => {
  it("lists the structure instead of drawing bars", () => {
    // A chipper is "these movements, in this order, as fast as you can" —
    // there's no timeline to draw to scale.
    render(
      <WorkoutTimeline
        workout={workout({
          mode: "for_time",
          segments: [
            seg({ movements: [{ name: "Pull-up", reps: 100 }] }),
            seg({ movements: [{ name: "Push-up", reps: 200 }] }),
          ],
        })}
      />,
    );

    expect(screen.getByText(/Pull-up/)).toBeInTheDocument();
    expect(screen.getByText(/Push-up/)).toBeInTheDocument();
  });

  it("marks a set count on a segment that repeats", () => {
    render(
      <WorkoutTimeline
        workout={workout({
          mode: "custom",
          segments: [seg({ movements: [{ name: "Back Squat" }], rounds: 5 })],
        })}
      />,
    );

    expect(screen.getByText("5×")).toBeInTheDocument();
  });

  it("keeps a label that says more than the movement does", () => {
    render(
      <WorkoutTimeline
        workout={workout({
          mode: "custom",
          segments: [seg({ label: "Strength", movements: [{ name: "Back Squat" }] })],
        })}
      />,
    );

    expect(screen.getByText("Strength:")).toBeInTheDocument();
  });

  it("shows a dash for a segment with nothing named", () => {
    render(
      <WorkoutTimeline workout={workout({ mode: "custom", segments: [seg({})] })} />,
    );

    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("renders nothing at all when there is nothing to say", () => {
    // No chips and no segments: an empty box would just be noise.
    const { container } = render(<WorkoutTimeline workout={workout({ mode: "custom" })} />);

    expect(container).toBeEmptyDOMElement();
  });
});

describe("marking where the clock is", () => {
  const emom = workout({
    mode: "emom",
    rounds: 2,
    work_seconds: 60,
    segments: [
      seg({ movements: [{ name: "Row" }] }),
      seg({ movements: [{ name: "Wall Walk" }] }),
    ],
  });

  it("marks nothing while the clock is idle", () => {
    const { container } = render(<WorkoutTimeline workout={emom} />);

    expect(container.querySelectorAll(".active")).toHaveLength(0);
  });

  it("marks the leg the clock is in", () => {
    const { container } = render(<WorkoutTimeline workout={emom} elapsedSeconds={10} />);

    expect(container.querySelectorAll(".active").length).toBeGreaterThan(0);
  });

  it("moves the mark on as the clock advances", () => {
    const first = render(<WorkoutTimeline workout={emom} elapsedSeconds={10} />);
    const firstActive = first.container.querySelector(".timeline-leg.active")?.textContent;
    first.unmount();

    const second = render(<WorkoutTimeline workout={emom} elapsedSeconds={70} />);
    const secondActive = second.container.querySelector(".timeline-leg.active")?.textContent;

    expect(firstActive).not.toEqual(secondActive);
  });
});
