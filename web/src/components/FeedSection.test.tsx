import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FeedCard, FeedSection } from "./FeedSection";
import { CROSSFIT_SPEC, GYM_SPEC } from "../feeds";
import type { FeedEntry } from "../feeds";

/**
 * One source's block on the home page.
 *
 * The card leans on the feed spec for what to show and whether the day is a
 * rest day — `feeds.test.ts` covers those rules, so these cover what the card
 * does with the answers: a rest day offers nothing to load, an entry already
 * in the library says so and loads without saving again.
 */

function entry(over: Partial<FeedEntry> = {}): FeedEntry {
  return {
    date: "2026-08-10",
    title: "Monday 260810",
    text: "5 rounds for time of:\n15 box jump-overs",
    url: "https://www.crossfit.com/260810",
    ...over,
  };
}

const onLoad = vi.fn();
const onEdit = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
});

describe("a single card", () => {
  function card(over: Partial<FeedEntry> = {}, loading = false) {
    return render(
      <FeedCard
        entry={entry(over)}
        spec={CROSSFIT_SPEC}
        featured
        loading={loading}
        onLoad={onLoad}
        onEdit={onEdit}
      />,
    );
  }

  it("shows the day, the title, and the trimmed workout", () => {
    card();

    expect(screen.getByText("Monday 260810")).toBeInTheDocument();
    expect(screen.getByText(/5 rounds for time of:/)).toBeInTheDocument();
  });

  it("writes the date out in words", () => {
    // The raw ISO date is parsed as local midnight rather than UTC, so the
    // weekday doesn't slip a day for anyone west of Greenwich.
    card({ date: "2026-08-10" });

    // Anchored so it can't match the title, which also begins "Monday".
    expect(screen.getByText(/^Monday,/)).toBeInTheDocument();
  });

  it("offers to load and save a day that isn't in the library", () => {
    card();

    expect(screen.getByRole("button", { name: "Load & save" })).toBeInTheDocument();
  });

  it("says when a day is already saved, and only loads it", () => {
    // Loading it again shouldn't mint a second copy in the library.
    card({ saved_workout_id: "w1" });

    expect(screen.getByText("In library")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Load into timer" })).toBeInTheDocument();
  });

  it("offers nothing to load on a rest day", () => {
    // There is no clock to run, so a Load button would only produce an empty
    // timer.
    card({ text: "Rest Day" });

    expect(screen.getByText("Rest day — nothing to load.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Load/ })).not.toBeInTheDocument();
  });

  it("hands the whole entry back when loaded", async () => {
    const user = userEvent.setup();
    card();

    await user.click(screen.getByRole("button", { name: "Load & save" }));

    expect(onLoad).toHaveBeenCalledWith(expect.objectContaining({ title: "Monday 260810" }));
  });

  it("hands it back for editing too", async () => {
    const user = userEvent.setup();
    card();

    await user.click(screen.getByRole("button", { name: "Edit first" }));

    expect(onEdit).toHaveBeenCalledWith(expect.objectContaining({ title: "Monday 260810" }));
  });

  it("blocks both actions while one is in flight", () => {
    card({}, true);

    expect(screen.getByRole("button", { name: "Loading…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Edit first" })).toBeDisabled();
  });

  it("links out to the source", () => {
    card();

    const link = screen.getByRole("link", { name: CROSSFIT_SPEC.linkLabel });
    expect(link).toHaveAttribute("href", "https://www.crossfit.com/260810");
    // Opens away from a running timer, never in place.
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("drops the link when the source has no page", () => {
    card({ url: "" });

    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("prefers a label the entry carries over the spec's", () => {
    // The gym feed sets this, because only the server knows which platform
    // the user actually connected.
    render(
      <FeedCard
        entry={entry({ link_label: "View on Wodify ↗" })}
        spec={GYM_SPEC}
        featured={false}
        loading={false}
        onLoad={onLoad}
        onEdit={onEdit}
      />,
    );

    expect(screen.getByRole("link", { name: "View on Wodify ↗" })).toBeInTheDocument();
  });
});

describe("a whole section", () => {
  function section(entries: FeedEntry[], emptyState?: React.ReactNode) {
    return render(
      <FeedSection
        spec={CROSSFIT_SPEC}
        entries={entries}
        loadingDate={null}
        emptyState={emptyState}
        onLoad={onLoad}
        onEdit={onEdit}
      />,
    );
  }

  it("names the source", () => {
    section([entry()]);

    expect(screen.getByRole("heading", { name: "CrossFit.com" })).toBeInTheDocument();
    expect(screen.getByText(CROSSFIT_SPEC.blurb)).toBeInTheDocument();
  });

  it("features today and tucks earlier days away", () => {
    // Today is what someone opened the app for; the rest are there if wanted.
    section([
      entry({ date: "2026-08-10", title: "Today" }),
      entry({ date: "2026-08-09", title: "Yesterday" }),
      entry({ date: "2026-08-08", title: "Before that" }),
    ]);

    expect(screen.getByText("Today")).toBeInTheDocument();
    expect(screen.getByText("Earlier this week (2)")).toBeInTheDocument();
  });

  it("shows no disclosure when today is all there is", () => {
    section([entry()]);

    expect(screen.queryByText(/Earlier this week/)).not.toBeInTheDocument();
  });

  it("shows the caller's empty state when there is nothing", () => {
    // Empty means something different per source, so the copy comes from the
    // caller rather than from here.
    section([], <p>No gym connected yet.</p>);

    expect(screen.getByText("No gym connected yet.")).toBeInTheDocument();
  });

  it("marks only the day being loaded as busy", async () => {
    render(
      <FeedSection
        spec={CROSSFIT_SPEC}
        entries={[entry({ date: "2026-08-10" }), entry({ date: "2026-08-09", title: "Older" })]}
        loadingDate="2026-08-10"
        onLoad={onLoad}
        onEdit={onEdit}
      />,
    );

    expect(screen.getByRole("button", { name: "Loading…" })).toBeInTheDocument();
    const earlier = screen.getByText("Older").closest("section") as HTMLElement;
    expect(within(earlier).getByRole("button", { name: "Load & save" })).toBeEnabled();
  });
});
