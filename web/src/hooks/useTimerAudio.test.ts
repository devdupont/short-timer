import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useTimerAudio } from "./useTimerAudio";
import type { TimerState } from "./useTimerEngine";
import type { WorkoutMode } from "../types";

/**
 * The timer's audio cues.
 *
 * The point of the palette is that an athlete reading the clock from across
 * the room can tell *what* happened without looking — a leg change has to
 * sound different from a round change, from rest, from the cap. So these
 * assert on the frequencies actually scheduled, which is the only thing that
 * distinguishes one cue from another.
 *
 * Cues are identified by their pitches: `tick` 800, `go` a single 1200, `cap`
 * three of them, `leg` two 900s, `rest` 500, `round` 700→1050, `finish` the
 * descending 1046/784/523.
 */

let freqs: number[] = [];

class FakeAudioContext {
  state: AudioContextState = "running";
  currentTime = 0;
  destination = {} as AudioNode;

  constructor() {
    // The hook builds its own context, so registering here is the only way to
    // get hold of the instance under test.
    contexts.push(this);
  }

  resume = vi.fn(async () => {
    // Genuinely async, as the real one is: a context does not come back
    // within the same tick that asked it to, which is exactly why the beep
    // that triggered the resume is the one that gets lost.
    await Promise.resolve();
    this.state = "running";
  });
  close = vi.fn(async () => {
    this.state = "closed";
  });

  createOscillator() {
    const osc = {
      type: "sine" as OscillatorType,
      frequency: { value: 0 },
      connect: (node: unknown) => node,
      start: () => {},
      stop: () => {
        freqs.push(osc.frequency.value);
      },
    };
    return osc;
  }

  createGain() {
    return {
      gain: { setValueAtTime: () => {}, linearRampToValueAtTime: () => {} },
      connect: (node: unknown) => node,
    };
  }
}

let contexts: FakeAudioContext[] = [];

/** Which cues were scheduled since the last `reset`. */
function cues(): string[] {
  const count = (freq: number) => freqs.filter((f) => f === freq).length;
  const played: string[] = [];
  if (count(800) > 0) played.push("tick");
  if (count(1200) >= 3) played.push("cap");
  else if (count(1200) === 1) played.push("go");
  if (count(900) >= 2) played.push("leg");
  if (count(500) > 0) played.push("rest");
  if (count(700) > 0 && count(1050) > 0) played.push("round");
  if (count(1046) > 0) played.push("finish");
  return played;
}

function reset(): void {
  freqs = [];
}

function state(over: Partial<TimerState> = {}): TimerState {
  return {
    status: "running",
    elapsedSeconds: 0,
    remainingSeconds: null,
    legElapsedSeconds: null,
    round: 1,
    totalRounds: null,
    phase: "work",
    overCap: false,
    currentMovement: null,
    legNumber: null,
    totalLegs: null,
    capSeconds: null,
    countdownRemaining: null,
    ...over,
  };
}

interface Props {
  state: TimerState;
  muted: boolean;
  mode: WorkoutMode;
}

/**
 * Render the hook, unlock the audio, and hand back a `step` that advances the
 * timer state. The first render only records a baseline — cues come from the
 * difference between two states, so nothing can play until the second.
 */
function harness(initial: TimerState, over: Partial<Props> = {}) {
  const view = renderHook(
    ({ state: s, muted, mode }: Props) => useTimerAudio(s, muted, mode),
    { initialProps: { state: initial, muted: false, mode: "emom" as WorkoutMode, ...over } },
  );
  act(() => {
    view.result.current.unlock();
  });
  reset();

  return {
    ...view,
    step(next: TimerState, props: Partial<Props> = {}) {
      reset();
      view.rerender({ state: next, muted: false, mode: "emom", ...over, ...props });
    },
  };
}

beforeEach(() => {
  reset();
  contexts = [];
  Object.defineProperty(window, "AudioContext", {
    configurable: true,
    writable: true,
    value: FakeAudioContext,
  });
});

afterEach(() => {
  delete (window as { AudioContext?: unknown }).AudioContext;
});

describe("the 3-2-1 warning pips", () => {
  it("pips on each of the last three seconds of a leg", () => {
    const h = harness(state({ remainingSeconds: 4 }));

    for (const remaining of [3, 2, 1]) {
      h.step(state({ remainingSeconds: remaining }));
      expect(cues()).toContain("tick");
    }
  });

  it("says nothing earlier than three seconds out", () => {
    const h = harness(state({ remainingSeconds: 6 }));

    h.step(state({ remainingSeconds: 5 }));

    expect(cues()).toEqual([]);
  });

  it("does not pip when the leg clock resets upward", () => {
    // Stepping to the next leg bumps the remaining seconds back up. Without
    // the decrease guard that would pip on every leg boundary.
    const h = harness(state({ remainingSeconds: 1 }));

    h.step(state({ remainingSeconds: 60 }));

    expect(cues()).not.toContain("tick");
  });

  it("pips through the lead-in as well", () => {
    // The same three pips before "go", so the start of the workout sounds the
    // same as every other boundary.
    const h = harness(state({ status: "countdown", countdownRemaining: 4 }));

    h.step(state({ status: "countdown", countdownRemaining: 2.4 }));

    expect(cues()).toContain("tick");
  });

  it("pips toward a time cap, where nothing counts down", () => {
    // for_time and amrap count up; the warning has to be derived from how far
    // the elapsed time is from the cap instead.
    const h = harness(state({ capSeconds: 60, elapsedSeconds: 56 }));

    h.step(state({ capSeconds: 60, elapsedSeconds: 57.5 }));

    expect(cues()).toContain("tick");
  });
});

describe("starting and stopping", () => {
  it("sounds the go tone when the lead-in hands off", () => {
    const h = harness(state({ status: "countdown", countdownRemaining: 0 }));

    h.step(state({ status: "running" }));

    expect(cues()).toContain("go");
  });

  it("stays silent when a paused clock resumes", () => {
    // Resuming isn't the start of anything, and a "go" there would send
    // people off at the wrong moment.
    const h = harness(state({ status: "paused" }));

    h.step(state({ status: "running" }));

    expect(cues()).toEqual([]);
  });

  it("sounds the cap tone when a workout run against a cap ends", () => {
    const h = harness(state({ capSeconds: 300, elapsedSeconds: 299 }));

    h.step(state({ status: "finished", capSeconds: 300, elapsedSeconds: 300 }));

    expect(cues()).toContain("cap");
  });

  it("sounds the finish flourish when there was no cap", () => {
    const h = harness(state({ remainingSeconds: 30 }));

    h.step(state({ status: "finished" }));

    expect(cues()).toContain("finish");
  });

  it("sounds the cap tone when a for-time run crosses its cap and keeps going", () => {
    // for_time doesn't auto-finish at the cap, so the tone has to come off the
    // flag flipping rather than off a status change.
    const h = harness(state({ capSeconds: 600, elapsedSeconds: 599, overCap: false }));

    h.step(state({ capSeconds: 600, elapsedSeconds: 601, overCap: true }));

    expect(cues()).toContain("cap");
  });
});

describe("boundaries inside a workout", () => {
  it("sounds the round tone when the round bumps", () => {
    const h = harness(state({ round: 1, totalRounds: 5, remainingSeconds: 1 }));

    h.step(state({ round: 2, totalRounds: 5, remainingSeconds: 60 }));

    expect(cues()).toContain("round");
  });

  it("prefers the round tone when a round and a leg change together", () => {
    // They always coincide at the top of a round; two tones at once would be
    // mush, and the round is the more significant event.
    const h = harness(state({ round: 1, totalRounds: 5, phase: "rest", remainingSeconds: 1 }));

    h.step(state({ round: 2, totalRounds: 5, phase: "work", remainingSeconds: 60 }));

    expect(cues()).toContain("round");
    expect(cues()).not.toContain("leg");
  });

  it("sounds the rest tone when rest begins", () => {
    const h = harness(state({ phase: "work", totalRounds: 5, remainingSeconds: 1 }));

    h.step(state({ phase: "rest", totalRounds: 5, remainingSeconds: 15 }));

    expect(cues()).toContain("rest");
  });

  it("sounds the leg tone when work picks up again after a rest", () => {
    const h = harness(state({ phase: "rest", totalRounds: 5, remainingSeconds: 1 }));

    h.step(state({ phase: "work", totalRounds: 5, remainingSeconds: 30 }));

    expect(cues()).toContain("leg");
  });

  it("sounds the leg tone when the rotation steps to the next movement", () => {
    // No phase or round change — the only evidence is the per-leg clock
    // jumping back up.
    const h = harness(state({ phase: "work", totalRounds: 5, remainingSeconds: 1 }));

    h.step(state({ phase: "work", totalRounds: 5, remainingSeconds: 60 }));

    expect(cues()).toContain("leg");
  });

  it("says nothing while a leg simply runs down", () => {
    const h = harness(state({ phase: "work", totalRounds: 5, remainingSeconds: 40 }));

    h.step(state({ phase: "work", totalRounds: 5, remainingSeconds: 39 }));

    expect(cues()).toEqual([]);
  });
});

describe("an EMOM whose minutes are its rounds", () => {
  it("chimes the round tone at each minute of a single-round EMOM", () => {
    // "Every minute, do the next thing, once through": each leg boundary *is*
    // a round to the athlete, so the leg tone would undersell it.
    const h = harness(state({ phase: "work", totalRounds: 1, remainingSeconds: 1 }), {
      mode: "emom",
    });

    h.step(state({ phase: "work", totalRounds: 1, remainingSeconds: 60 }));

    expect(cues()).toContain("round");
    expect(cues()).not.toContain("leg");
  });

  it("does the same for an unbounded EMOM, which feels identical", () => {
    const h = harness(state({ phase: "work", totalRounds: null, remainingSeconds: 1 }), {
      mode: "emom",
    });

    h.step(state({ phase: "work", totalRounds: null, remainingSeconds: 60 }));

    expect(cues()).toContain("round");
  });

  it("keeps the leg/round distinction when the EMOM has bounded rounds", () => {
    // Here movements cycle across a fixed number of rounds, so a leg change
    // and a round change are genuinely different events.
    const h = harness(state({ phase: "work", totalRounds: 3, remainingSeconds: 1 }), {
      mode: "emom",
    });

    h.step(state({ phase: "work", totalRounds: 3, remainingSeconds: 60 }));

    expect(cues()).toContain("leg");
    expect(cues()).not.toContain("round");
  });

  it("uses the leg tone for other interval modes regardless of round count", () => {
    const h = harness(state({ phase: "work", totalRounds: 1, remainingSeconds: 1 }), {
      mode: "interval",
    });

    h.step(state({ phase: "work", totalRounds: 1, remainingSeconds: 60 }));

    expect(cues()).toContain("leg");
  });
});

describe("staying quiet", () => {
  it("plays nothing at all when muted", () => {
    const h = harness(state({ remainingSeconds: 4 }), { muted: true });

    h.step(state({ remainingSeconds: 3 }), { muted: true });

    expect(cues()).toEqual([]);
  });

  it("plays nothing before the audio has been unlocked", () => {
    // Browsers refuse to start audio outside a user gesture, so there is no
    // context until Start is pressed.
    const view = renderHook(({ state: s, muted, mode }: Props) => useTimerAudio(s, muted, mode), {
      initialProps: {
        state: state({ remainingSeconds: 4 }),
        muted: false,
        mode: "emom" as WorkoutMode,
      },
    });
    reset();

    view.rerender({ state: state({ remainingSeconds: 3 }), muted: false, mode: "emom" });

    expect(cues()).toEqual([]);
  });

  it("says nothing on the very first state it sees", () => {
    // There's no previous state to compare against, so nothing has happened.
    harness(state({ status: "finished" }));

    expect(cues()).toEqual([]);
  });

  it("skips a beep while the context is parked", () => {
    // Resuming is async, so this beep is lost; the next one is what's heard.
    const h = harness(state({ remainingSeconds: 4 }));
    contexts[0].state = "suspended";

    h.step(state({ remainingSeconds: 3 }));

    expect(cues()).toEqual([]);
  });
});

describe("recovering a parked context", () => {
  it("asks a parked context to resume when a cue lands on it", () => {
    const h = harness(state({ remainingSeconds: 4 }));
    contexts[0].state = "suspended";
    contexts[0].resume.mockClear();

    h.step(state({ remainingSeconds: 3 }));

    expect(contexts[0].resume).toHaveBeenCalled();
  });

  it("resumes when the page comes back into view", () => {
    // WebKit parks the context when an iPhone locks, in a state the spec
    // doesn't name — so this checks "not running" rather than a named state,
    // and returning to view is the moment the interruption ends.
    harness(state({}));
    contexts[0].state = "interrupted" as AudioContextState;
    contexts[0].resume.mockClear();

    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });

    expect(contexts[0].resume).toHaveBeenCalled();
  });

  it("resumes on a tap, which is always allowed", () => {
    harness(state({}));
    contexts[0].state = "suspended";
    contexts[0].resume.mockClear();

    act(() => {
      window.dispatchEvent(new Event("pointerdown"));
    });

    expect(contexts[0].resume).toHaveBeenCalled();
  });

  it("does not try while the page is still hidden", () => {
    harness(state({}));
    contexts[0].state = "suspended";
    contexts[0].resume.mockClear();
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "hidden",
    });

    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });

    expect(contexts[0].resume).not.toHaveBeenCalled();

    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "visible",
    });
  });

  it("reuses the same context rather than building another", () => {
    // Safari caps how many a page may create; burning the cap retrying a
    // locked phone turns temporary silence into permanent silence.
    const h = harness(state({}));
    contexts[0].state = "suspended";

    act(() => {
      h.result.current.unlock();
    });

    expect(contexts).toHaveLength(1);
  });

  it("builds a fresh context when the old one was closed", () => {
    // A closed context is beyond reviving, and pressing Start is someone
    // asking for a working timer.
    const h = harness(state({}));
    contexts[0].state = "closed";

    act(() => {
      h.result.current.unlock();
    });

    expect(contexts).toHaveLength(2);
  });
});
