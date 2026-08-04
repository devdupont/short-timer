import { describe, expect, it } from "vitest";
import type { Workout, WorkoutSegment } from "./types";
import {
  buildPlan,
  buildTimeline,
  countsUp,
  locateBlock,
  movementLabel,
  planTotalDuration,
  segmentColors,
} from "./timerPlan";

/**
 * The clock and the visualizer read the same plan from this module, which is
 * what lets the timeline be trusted as a preview of what the timer will do.
 * These cover that shared reading: leg lengths, where rest falls, and where a
 * given elapsed second lands — the things a refactor here would break silently
 * in both places at once.
 */

function workout(over: Partial<Workout>): Workout {
  return {
    id: "w",
    name: "test",
    mode: "emom",
    segments: [],
    created_at: "",
    updated_at: "",
    ...over,
  };
}

function seg(over: Partial<WorkoutSegment>): WorkoutSegment {
  return { movements: [], ...over };
}

/** The workout this all started from: four working minutes, then one off. */
const emomWithRest = workout({
  mode: "emom",
  rounds: 3,
  work_seconds: 60,
  segments: [
    seg({ movements: [{ name: "Row", calories: 16 }] }),
    seg({ movements: [{ name: "GHD Sit-Up", reps: 16 }] }),
    seg({ movements: [{ name: "Wall Walk", reps: 3 }] }),
    seg({ movements: [{ name: "Renegade Row", reps: 16, load: "50/35 lb" }] }),
    seg({ is_rest: true }),
  ],
});

describe("buildTimeline — rotation", () => {
  it("runs a rest leg as rest, at full length and in place", () => {
    const { bars, totalSeconds } = buildTimeline(emomWithRest);

    expect(bars).toHaveLength(1);
    expect(bars[0].repeats).toBe(3);
    expect(bars[0].seconds).toBe(300);
    expect(totalSeconds).toBe(900);

    expect(bars[0].blocks.map((b) => b.kind)).toEqual([
      "work",
      "work",
      "work",
      "work",
      "rest",
    ]);
    // The rest minute keeps the length its neighbours have — it's a leg of the
    // rotation, not a gap between legs.
    expect(bars[0].blocks[4]).toMatchObject({ seconds: 60, start: 240, label: "Rest" });
  });

  it("gives each leg of a ladder its own length, with the rest that follows it", () => {
    const ladder = workout({
      mode: "interval",
      rounds: 1,
      segments: [300, 240, 180].map((s) => seg({ work_seconds: s, rest_seconds: 120 })),
    });

    const [bar] = buildTimeline(ladder).bars;
    expect(bar.blocks.map((b) => [b.kind, b.seconds])).toEqual([
      ["work", 300],
      ["rest", 120],
      ["work", 240],
      ["rest", 120],
      ["work", 180],
      ["rest", 120],
    ]);
  });

  it("rests once at the end of a round, not after every leg", () => {
    const chelsea = workout({
      mode: "emom",
      rounds: 30,
      work_seconds: 60,
      segments: [seg({ movements: [{ name: "Pull-up", reps: 5 }] })],
    });
    expect(planTotalDuration(buildPlan(chelsea))).toBe(1800);

    const rotation = workout({
      mode: "interval",
      rounds: 2,
      work_seconds: 30,
      rest_seconds: 30,
      segments: [seg({ movements: [{ name: "A" }] }), seg({ movements: [{ name: "B" }] })],
    });
    const plan = buildPlan(rotation);
    expect(plan.kind).toBe("rotation");
    if (plan.kind !== "rotation") return;
    expect(plan.legs.map((l) => l.rest)).toEqual([0, 30]);
    expect(plan.roundLength).toBe(90);
  });

  it("leaves an unbounded workout with no total", () => {
    const open = workout({ mode: "emom", work_seconds: 60, segments: [seg({})] });
    expect(buildTimeline(open).totalSeconds).toBeNull();
    expect(buildTimeline(open).bars[0].repeats).toBeNull();
  });
});

describe("buildTimeline — blocks", () => {
  const tabata = workout({
    mode: "tabata",
    work_seconds: 20,
    rest_seconds: 10,
    segments: ["Row", "Squat"].map((name) => seg({ rounds: 8, movements: [{ name }] })),
  });

  it("gives each self-contained block its own bar, offset by the ones before it", () => {
    const { bars, totalSeconds } = buildTimeline(tabata);

    expect(bars.map((b) => [b.label, b.seconds, b.repeats, b.start])).toEqual([
      ["Row", 30, 8, 0],
      ["Squat", 30, 8, 240],
    ]);
    expect(totalSeconds).toBe(480);
    // Every leg of a block plan rests, unlike a rotation's single round-end rest.
    expect(bars[0].blocks.map((b) => b.kind)).toEqual(["work", "rest"]);
  });
});

describe("locateBlock", () => {
  const timeline = buildTimeline(emomWithRest);
  const at = (seconds: number) => {
    const found = locateBlock(timeline, seconds);
    if (!found) return null;
    const block = timeline.bars[found.bar].blocks[found.block];
    return { repeat: found.repeat, kind: block.kind, label: block.label };
  };

  it("tracks the leg under the clock, including the rest minute", () => {
    expect(at(0)).toMatchObject({ repeat: 1, kind: "work" });
    expect(at(59)).toMatchObject({ repeat: 1, kind: "work" });
    expect(at(60)?.label).toContain("GHD Sit-Up");
    // Minute 5 of round 1: rest, all the way to the round boundary.
    expect(at(240)).toMatchObject({ repeat: 1, kind: "rest" });
    expect(at(299)).toMatchObject({ repeat: 1, kind: "rest" });
    // ...and over it, back to the first movement of round 2.
    expect(at(300)).toMatchObject({ repeat: 2, kind: "work" });
    expect(at(899)).toMatchObject({ repeat: 3, kind: "rest" });
  });

  it("clamps the repeat count to the rounds that exist", () => {
    expect(locateBlock(timeline, 10_000)?.repeat).toBe(3);
  });

  it("finds the right bar in a block plan", () => {
    const tabata = buildTimeline(
      workout({
        mode: "tabata",
        work_seconds: 20,
        rest_seconds: 10,
        segments: ["Row", "Squat"].map((name) => seg({ rounds: 8, movements: [{ name }] })),
      }),
    );
    expect(locateBlock(tabata, 250)).toMatchObject({ bar: 1, block: 0, repeat: 1 });
    expect(locateBlock(tabata, 265)).toMatchObject({ bar: 1, block: 1 });
  });
});

describe("segmentColors", () => {
  it("gives the same movement the same colour and rest none", () => {
    const colors = segmentColors(
      workout({
        segments: [
          seg({ movements: [{ name: "Row" }] }),
          seg({ movements: [{ name: "Squat" }] }),
          seg({ movements: [{ name: "Row" }] }),
          seg({ is_rest: true }),
        ],
      }),
    );
    expect(colors).toEqual([0, 1, 0, null]);
  });

  it("folds movements past the palette into one neutral rather than cycling", () => {
    const many = workout({
      segments: "abcdefgh".split("").map((n) => seg({ movements: [{ name: n }] })),
    });
    // Six slots, then null — a cycled hue would claim the seventh is the first.
    expect(segmentColors(many)).toEqual([0, 1, 2, 3, 4, 5, null, null]);
  });
});

describe("movementLabel", () => {
  it("keeps the calorie count, which is an erg's rep count", () => {
    expect(movementLabel(seg({ movements: [{ name: "Row", calories: 16 }] }))).toBe("16 cal Row");
  });

  it("names a rest leg even though it has no movement", () => {
    expect(movementLabel(seg({ is_rest: true }))).toBe("Rest");
    expect(movementLabel(seg({}))).toBeNull();
  });
});

describe("countsUp", () => {
  it("is set only for interval work asking for an up-counting clock", () => {
    expect(countsUp(workout({ mode: "emom", interval_clock: "count_up" }))).toBe(true);
    expect(countsUp(workout({ mode: "emom", interval_clock: "count_down" }))).toBe(false);
    expect(countsUp(workout({ mode: "emom" }))).toBe(false);
    // for_time already counts up on its own clock; this flag is about legs.
    expect(countsUp(workout({ mode: "for_time", interval_clock: "count_up" }))).toBe(false);
  });
});
