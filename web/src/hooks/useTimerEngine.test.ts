import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { COUNTDOWN_SECONDS, useTimerEngine } from "./useTimerEngine";
import type { Workout, WorkoutSegment } from "../types";

/**
 * The clock itself: what the timer counts, and when it stops.
 *
 * `timerPlan.test.ts` covers the *plan* — how a workout becomes legs and how
 * long each one is. This covers the engine that runs that plan against real
 * time, which is where the state a user actually watches comes from.
 *
 * The engine reads `performance.now()` rather than counting ticks, so that a
 * throttled background tab resumes at the right time instead of drifting.
 * That means faking `performance` as well as the interval — advancing one
 * without the other would leave the clock reading zero forever.
 */

function workout(over: Partial<Workout>): Workout {
  return {
    id: "w",
    name: "test",
    mode: "for_time",
    segments: [],
    created_at: "",
    updated_at: "",
    ...over,
  };
}

function seg(over: Partial<WorkoutSegment>): WorkoutSegment {
  return { movements: [], ...over };
}

/** Three minutes of EMOM: two working minutes, then one off. */
const emom = workout({
  mode: "emom",
  rounds: 1,
  work_seconds: 60,
  segments: [
    seg({ movements: [{ name: "Row", calories: 16 }] }),
    seg({ movements: [{ name: "Wall Walk", reps: 3 }] }),
    seg({ is_rest: true }),
  ],
});

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "performance", "Date"] });
});

afterEach(() => {
  vi.useRealTimers();
});

/** Advance the clock, letting the engine's interval fire and re-render. */
function advance(seconds: number): void {
  act(() => {
    vi.advanceTimersByTime(seconds * 1000);
  });
}

/** Start and skip the lead-in, which most of these aren't about. */
function startRunning(controls: { skipCountdown: () => void }): void {
  act(() => {
    controls.skipCountdown();
  });
}

describe("before it starts", () => {
  it("sits idle at zero", () => {
    const { result } = renderHook(() => useTimerEngine(workout({})));

    expect(result.current.state.status).toBe("idle");
    expect(result.current.state.elapsedSeconds).toBe(0);
  });

  it("does not move on its own", () => {
    const { result } = renderHook(() => useTimerEngine(workout({})));

    advance(30);

    expect(result.current.state.status).toBe("idle");
    expect(result.current.state.elapsedSeconds).toBe(0);
  });
});

describe("the lead-in", () => {
  it("counts down before the workout clock starts", () => {
    const { result } = renderHook(() => useTimerEngine(workout({})));

    act(() => {
      result.current.controls.start();
    });

    expect(result.current.state.status).toBe("countdown");
    expect(result.current.state.countdownRemaining).toBe(COUNTDOWN_SECONDS);
  });

  it("ticks the lead-in down without advancing the workout clock", () => {
    const { result } = renderHook(() => useTimerEngine(workout({})));

    act(() => {
      result.current.controls.start();
    });
    advance(4);

    expect(result.current.state.status).toBe("countdown");
    expect(result.current.state.countdownRemaining).toBeCloseTo(COUNTDOWN_SECONDS - 4, 1);
    // The lead-in is time to get set, not part of the effort.
    expect(result.current.state.elapsedSeconds).toBe(0);
  });

  it("hands off to the workout clock at zero", () => {
    const { result } = renderHook(() => useTimerEngine(workout({})));

    act(() => {
      result.current.controls.start();
    });
    advance(COUNTDOWN_SECONDS);

    expect(result.current.state.status).toBe("running");
    expect(result.current.state.countdownRemaining).toBeNull();
    // The workout starts from zero, not from wherever the lead-in ended.
    expect(result.current.state.elapsedSeconds).toBe(0);
  });

  it("can be skipped", () => {
    const { result } = renderHook(() => useTimerEngine(workout({})));

    act(() => {
      result.current.controls.start();
    });
    advance(2);
    startRunning(result.current.controls);

    expect(result.current.state.status).toBe("running");
    expect(result.current.state.elapsedSeconds).toBe(0);
  });
});

describe("running", () => {
  it("counts elapsed time up", () => {
    const { result } = renderHook(() => useTimerEngine(workout({})));

    startRunning(result.current.controls);
    advance(5);

    expect(result.current.state.elapsedSeconds).toBeCloseTo(5, 1);
  });

  it("freezes when paused", () => {
    const { result } = renderHook(() => useTimerEngine(workout({})));

    startRunning(result.current.controls);
    advance(5);
    act(() => {
      result.current.controls.pause();
    });
    advance(60);

    expect(result.current.state.status).toBe("paused");
    expect(result.current.state.elapsedSeconds).toBeCloseTo(5, 1);
  });

  it("picks up where it left off, without swallowing or double-counting the pause", () => {
    // The engine tracks a start instant plus an accumulated total rather than
    // counting ticks; getting that arithmetic wrong is invisible until someone
    // pauses mid-workout, and then their time is simply wrong.
    const { result } = renderHook(() => useTimerEngine(workout({})));

    startRunning(result.current.controls);
    advance(5);
    act(() => {
      result.current.controls.pause();
    });
    advance(60);
    act(() => {
      result.current.controls.resume();
    });
    advance(3);

    expect(result.current.state.status).toBe("running");
    expect(result.current.state.elapsedSeconds).toBeCloseTo(8, 1);
  });

  it("survives being paused and resumed repeatedly", () => {
    const { result } = renderHook(() => useTimerEngine(workout({})));

    startRunning(result.current.controls);
    for (let i = 0; i < 3; i++) {
      advance(2);
      act(() => {
        result.current.controls.pause();
      });
      advance(10);
      act(() => {
        result.current.controls.resume();
      });
    }

    expect(result.current.state.elapsedSeconds).toBeCloseTo(6, 1);
  });

  it("keeps the time on the clock when finished by hand", () => {
    // Finishing is how a for-time score is taken; losing the last segment
    // would quietly shorten it.
    const { result } = renderHook(() => useTimerEngine(workout({})));

    startRunning(result.current.controls);
    advance(7);
    act(() => {
      result.current.controls.finish();
    });

    expect(result.current.state.status).toBe("finished");
    expect(result.current.state.elapsedSeconds).toBeCloseTo(7, 1);
  });

  it("stops counting once finished", () => {
    const { result } = renderHook(() => useTimerEngine(workout({})));

    startRunning(result.current.controls);
    advance(7);
    act(() => {
      result.current.controls.finish();
    });
    advance(30);

    expect(result.current.state.elapsedSeconds).toBeCloseTo(7, 1);
  });

  it("goes back to zero on reset", () => {
    const { result } = renderHook(() => useTimerEngine(workout({})));

    startRunning(result.current.controls);
    advance(9);
    act(() => {
      result.current.controls.reset();
    });

    expect(result.current.state.status).toBe("idle");
    expect(result.current.state.elapsedSeconds).toBe(0);

    // And a reset clock stays put rather than resuming from its old start.
    advance(10);
    expect(result.current.state.elapsedSeconds).toBe(0);
  });

  it("starts from zero when the workout is run again", () => {
    // Redoing a workout is ordinary; carrying the previous attempt's time into
    // the new one would silently ruin the second score.
    const { result } = renderHook(() => useTimerEngine(workout({})));

    startRunning(result.current.controls);
    advance(12);
    act(() => {
      result.current.controls.reset();
    });

    startRunning(result.current.controls);
    expect(result.current.state.elapsedSeconds).toBe(0);

    advance(3);
    expect(result.current.state.elapsedSeconds).toBeCloseTo(3, 1);
  });
});

describe("stopping on its own", () => {
  it("ends an amrap when its window closes", () => {
    const { result } = renderHook(() =>
      useTimerEngine(workout({ mode: "amrap", time_cap_seconds: 20 })),
    );

    startRunning(result.current.controls);
    advance(19);
    expect(result.current.state.status).toBe("running");

    advance(2);
    expect(result.current.state.status).toBe("finished");
  });

  it("ends an interval workout when the plan runs out", () => {
    // Three one-minute legs, one round: the clock has somewhere to stop.
    const { result } = renderHook(() => useTimerEngine(emom));

    startRunning(result.current.controls);
    advance(179);
    expect(result.current.state.status).toBe("running");

    advance(2);
    expect(result.current.state.status).toBe("finished");
  });

  it("lets a capped for-time run past its cap rather than stopping", () => {
    // A cap is a target to report against, not a buzzer: an athlete who blows
    // through it still wants to see what they finished in.
    const { result } = renderHook(() =>
      useTimerEngine(workout({ mode: "for_time", time_cap_seconds: 10 })),
    );

    startRunning(result.current.controls);
    advance(15);

    expect(result.current.state.status).toBe("running");
    expect(result.current.state.overCap).toBe(true);
  });

  it("has no cap to exceed when none was set", () => {
    const { result } = renderHook(() => useTimerEngine(workout({ mode: "for_time" })));

    startRunning(result.current.controls);
    advance(600);

    expect(result.current.state.overCap).toBe(false);
    expect(result.current.state.capSeconds).toBeNull();
  });
});

describe("what the clock reads mid-workout", () => {
  it("names the movement for the leg it is in", () => {
    const { result } = renderHook(() => useTimerEngine(emom));

    startRunning(result.current.controls);
    advance(10);

    expect(result.current.state.phase).toBe("work");
    expect(result.current.state.currentMovement).toContain("Row");
    expect(result.current.state.legNumber).toBe(1);
    expect(result.current.state.totalLegs).toBe(3);
  });

  it("moves to the next leg when its minute is up", () => {
    const { result } = renderHook(() => useTimerEngine(emom));

    startRunning(result.current.controls);
    advance(70);

    expect(result.current.state.currentMovement).toContain("Wall Walk");
    expect(result.current.state.legNumber).toBe(2);
  });

  it("runs a rest leg as rest, with nothing to announce", () => {
    // The rest minute is a leg of the rotation, so the phase flips and there
    // is no movement to name — announcing one would send people through a
    // minute they are supposed to be recovering in.
    const { result } = renderHook(() => useTimerEngine(emom));

    startRunning(result.current.controls);
    advance(130);

    expect(result.current.state.phase).toBe("rest");
    expect(result.current.state.currentMovement).toBeNull();
  });

  it("counts the current leg down", () => {
    const { result } = renderHook(() => useTimerEngine(emom));

    startRunning(result.current.controls);
    advance(20);

    expect(result.current.state.remainingSeconds).toBe(40);
  });
});
