import { describe, expect, it } from "vitest";
import { CONCEPT2_SPEC, CROSSFIT_SPEC, FEED_SPECS, GYM_SPEC, HYBRID_SPEC } from "./feeds";

/**
 * What each source's card shows, and what it hides.
 *
 * These rules only exist because the raw feeds are messy in source-specific
 * ways — crossfit.com embeds markdown and appends coaching notes, Concept2
 * repeats its headline in the body, and a gym publishes whatever it typed.
 * Every helper here is module-private, so the tests go through the exported
 * specs, which is also what the card component uses.
 */

/** Long enough to trip the 500-character cap, in whole words. */
const LONG = "alpha bravo charlie delta echo foxtrot ".repeat(20);

describe("crossfit.com", () => {
  it("unwraps markdown links, keeping the words and dropping the target", () => {
    const body = CROSSFIT_SPEC.body("[Thrusters](https://www.crossfit.com/thruster) 95 lb");

    expect(body).toBe("Thrusters 95 lb");
  });

  it("drops bold markers", () => {
    expect(CROSSFIT_SPEC.body("**Fran**")).toBe("Fran");
  });

  it("normalises windows line endings", () => {
    expect(CROSSFIT_SPEC.body("21-15-9\r\nThrusters")).toBe("21-15-9\nThrusters");
  });

  it("cuts the coaching notes that follow the workout", () => {
    // The full text still reaches the parser; this is only what the card shows.
    const body = CROSSFIT_SPEC.body("21-15-9 reps for time\n\nPost time to comments.\nCompare to 210302.");

    expect(body).toBe("21-15-9 reps for time");
  });

  it.each([
    "Stimulus and Strategy",
    "Intermediate Option",
    "Beginner Option",
    "Scaling",
    "post your rounds to comments",
  ])("cuts at %s too, whatever its casing", (marker) => {
    const body = CROSSFIT_SPEC.body(`Cindy\n20 min AMRAP\n\n${marker}\nlots more text`);

    expect(body).toBe("Cindy\n20 min AMRAP");
    expect(body).not.toContain("lots more text");
  });

  it("keeps the whole workout when there is nothing to cut", () => {
    expect(CROSSFIT_SPEC.body("Run 5k")).toBe("Run 5k");
  });

  it("collapses a rest day to two words", () => {
    // Rest days are followed by an athlete bio with no marker to cut on, so
    // without this the card would be a wall of unrelated prose.
    const body = CROSSFIT_SPEC.body("Rest Day\n\nMeet Jane, who found CrossFit in 2009 and…");

    expect(body).toBe("Rest Day");
  });

  it("recognises a rest day through its markdown", () => {
    expect(CROSSFIT_SPEC.isRestDay("**Rest Day**")).toBe(true);
    expect(CROSSFIT_SPEC.isRestDay("rest day")).toBe(true);
    expect(CROSSFIT_SPEC.isRestDay("Fran\n21-15-9")).toBe(false);
  });

  it("does not mistake a workout that merely mentions rest", () => {
    // "Rest" appears constantly in interval programming; only a day that
    // *starts* by declaring itself a rest day is one.
    expect(CROSSFIT_SPEC.isRestDay("EMOM 10\nMinute 5: Rest")).toBe(false);
  });
});

describe("concept2", () => {
  it("drops the headline, which the card already shows as the title", () => {
    const body = CONCEPT2_SPEC.body("2/3/2/3/2/3/2 minutes\nWith 1 minute rest between intervals.");

    expect(body).toBe("With 1 minute rest between intervals.");
  });

  it("keeps the headline when that is all there is", () => {
    // Showing the headline twice beats showing an empty card.
    expect(CONCEPT2_SPEC.body("30 minutes free rate")).toBe("30 minutes free rate");
  });

  it("keeps the headline when the description is blank", () => {
    expect(CONCEPT2_SPEC.body("30 minutes free rate\n\n   ")).toBe("30 minutes free rate");
  });

  it("never reports a rest day, because there isn't one", () => {
    // Concept2 programs an interval workout every single day.
    expect(CONCEPT2_SPEC.isRestDay("Rest Day")).toBe(false);
  });
});

describe("hybrid calisthenics", () => {
  it("shows the stored lines as they are", () => {
    expect(HYBRID_SPEC.body("Pushups\n3 sets")).toBe("Pushups\n3 sets");
  });

  it.each(["A Day of Rest", "a day of rest", "Rest Day"])("recognises %s", (text) => {
    expect(HYBRID_SPEC.isRestDay(text)).toBe(true);
  });

  it("does not call a working day a rest day", () => {
    expect(HYBRID_SPEC.isRestDay("Pushups\n3 sets")).toBe(false);
  });
});

describe("your gym", () => {
  it("shows what the gym wrote, with no prose stripped", () => {
    // A gym platform returns the workout as the gym typed it; guessing at
    // structure here would mangle it.
    const text = "Strength: Back Squat 5x5\n\nMetcon: 12 min AMRAP";

    expect(GYM_SPEC.body(text)).toBe(text);
  });

  it("never reports a rest day", () => {
    // A gym not running a class simply publishes nothing that day.
    expect(GYM_SPEC.isRestDay("Rest Day")).toBe(false);
  });
});

describe("the length cap", () => {
  it("leaves anything within the cap alone", () => {
    const text = "a".repeat(500);

    expect(GYM_SPEC.body(text)).toBe(text);
  });

  it("truncates past the cap and marks it", () => {
    const body = GYM_SPEC.body(LONG);

    expect(body.length).toBeLessThanOrEqual(501);
    expect(body.endsWith("…")).toBe(true);
  });

  it("cuts on a word boundary rather than mid-word", () => {
    const body = GYM_SPEC.body(LONG);
    const kept = body.slice(0, -1); // everything before the ellipsis

    // The 500th character lands inside a word, so the partial word is dropped:
    // what's kept is a prefix of the source that stops right at a space.
    expect(LONG.startsWith(kept)).toBe(true);
    expect(LONG[kept.length]).toMatch(/\s/);
    expect(kept).not.toMatch(/\s$/);
  });

  it("applies to every source, not just the gym", () => {
    for (const spec of [CROSSFIT_SPEC, CONCEPT2_SPEC, HYBRID_SPEC, GYM_SPEC]) {
      expect(spec.body(LONG).endsWith("…")).toBe(true);
    }
  });
});

describe("the spec registry", () => {
  it("files every spec under its own kind", () => {
    // A copy-paste in this record would render one feed's cards under another
    // feed's rules, which reads as a parsing bug rather than a wiring one.
    for (const [kind, spec] of Object.entries(FEED_SPECS)) {
      expect(spec.kind).toBe(kind);
    }
  });

  it("gives every feed something to say when it is empty", () => {
    for (const spec of Object.values(FEED_SPECS)) {
      expect(spec.emptyMessage).toBeTruthy();
      expect(spec.heading).toBeTruthy();
      expect(spec.linkLabel).toBeTruthy();
    }
  });
});
