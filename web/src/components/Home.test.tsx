import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Home } from "./Home";
import type { GymConnection, Me, Workout } from "../types";

/**
 * The home page: which feeds are fetched, and what an empty one says.
 *
 * The gym feed is the interesting part. The API reports a single `configured`
 * flag, which collapses several different situations the user would act on
 * differently — no gym connected, one saved but switched off, one switched on
 * but incomplete. Telling them apart is done here, from the user's own config,
 * so each one gets copy that says what to actually do.
 */

const api = vi.hoisted(() => ({
  getMe: vi.fn(),
  getWorkout: vi.fn(),
  listWods: vi.fn(),
  listConcept2Wods: vi.fn(),
  listHybridWods: vi.fn(),
  listGymWods: vi.fn(),
  loadWorkoutFromText: vi.fn(),
  parseWorkout: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, ...api };
});

const WORKOUT: Workout = {
  id: "w1",
  name: "Monday 260810",
  mode: "for_time",
  segments: [],
  created_at: "",
  updated_at: "",
};

const ENTRY = {
  date: "2026-08-10",
  title: "Monday 260810",
  text: "5 rounds for time of:\n15 box jump-overs",
  url: "https://www.crossfit.com/260810",
};

function gym(over: Partial<GymConnection> = {}): GymConnection {
  return {
    provider: "wodify_member",
    credential: { is_set: true },
    enabled: true,
    ...over,
  };
}

function me(over: Partial<Me["config"]> = {}): Me {
  return {
    id: "u1",
    email: "athlete@example.com",
    email_verified: true,
    role: "user",
    display_name: "Athlete",
    secrets_available: true,
    config: { gyms: [], feeds: [{ kind: "crossfit", enabled: true }], ...over },
  };
}

const onLoad = vi.fn();
const onEdit = vi.fn();
const onOpenSettings = vi.fn();

function view() {
  return render(<Home onLoad={onLoad} onEdit={onEdit} onOpenSettings={onOpenSettings} />);
}

beforeEach(() => {
  vi.clearAllMocks();
  api.getMe.mockResolvedValue(me());
  api.listWods.mockResolvedValue([ENTRY]);
  api.listConcept2Wods.mockResolvedValue([]);
  api.listHybridWods.mockResolvedValue([]);
  api.listGymWods.mockResolvedValue({ configured: true, wods: [] });
  api.loadWorkoutFromText.mockResolvedValue(WORKOUT);
  api.parseWorkout.mockResolvedValue(WORKOUT);
  api.getWorkout.mockResolvedValue(WORKOUT);
});

describe("getting started", () => {
  it("says so while the profile is still loading", () => {
    api.getMe.mockReturnValue(new Promise(() => {}));

    view();

    expect(screen.getByText("Loading your workouts…")).toBeInTheDocument();
  });

  it("reports a profile it couldn't load", async () => {
    api.getMe.mockRejectedValue(new Error("down"));

    view();

    expect(await screen.findByText("Could not load your profile.")).toBeInTheDocument();
  });

  it("offers to pick feeds when none are on", async () => {
    const user = userEvent.setup();
    api.getMe.mockResolvedValue(me({ feeds: [] }));
    view();

    await user.click(await screen.findByRole("button", { name: "Choose your feeds" }));

    expect(onOpenSettings).toHaveBeenCalled();
  });
});

describe("which feeds get fetched", () => {
  it("fetches only the ones switched on", async () => {
    // A disabled feed costing a request is the thing the registry exists to
    // avoid.
    api.getMe.mockResolvedValue(
      me({
        feeds: [
          { kind: "crossfit", enabled: true },
          { kind: "concept2", enabled: false },
          { kind: "hybrid", enabled: false },
        ],
      }),
    );

    view();

    await waitFor(() => expect(api.listWods).toHaveBeenCalled());
    expect(api.listConcept2Wods).not.toHaveBeenCalled();
    expect(api.listHybridWods).not.toHaveBeenCalled();
  });

  it("renders them in the order the user chose", async () => {
    api.getMe.mockResolvedValue(
      me({
        feeds: [
          { kind: "concept2", enabled: true },
          { kind: "crossfit", enabled: true },
        ],
      }),
    );

    view();

    const headings = await screen.findAllByRole("heading", { level: 2 });
    expect(headings.map((h) => h.textContent)).toEqual(["Concept2", "CrossFit.com"]);
  });

  it("lets one broken feed fail without taking the others down", async () => {
    api.getMe.mockResolvedValue(
      me({
        feeds: [
          { kind: "crossfit", enabled: true },
          { kind: "concept2", enabled: true },
        ],
      }),
    );
    api.listConcept2Wods.mockRejectedValue(new Error("down"));

    view();

    expect(await screen.findByText("Could not reach this feed right now.")).toBeInTheDocument();
    expect(screen.getByText("Monday 260810")).toBeInTheDocument();
  });

  it("says a working feed is simply quiet today", async () => {
    api.listWods.mockResolvedValue([]);

    view();

    expect(
      await screen.findByText("No workouts available from crossfit.com right now."),
    ).toBeInTheDocument();
  });
});

describe("why the gym feed is empty", () => {
  async function gymHome(gyms: GymConnection[], configured = true) {
    api.getMe.mockResolvedValue(me({ gyms, feeds: [{ kind: "gym", enabled: true }] }));
    api.listGymWods.mockResolvedValue({ configured, wods: [] });
    view();
    return screen.findByRole("button", { name: "Open Settings" });
  }

  it("asks for a gym when none is connected", async () => {
    await gymHome([]);

    expect(screen.getByText(/No gym connected yet/)).toBeInTheDocument();
  });

  it("treats a gym with no credential as not connected", async () => {
    await gymHome([gym({ credential: { is_set: false } })]);

    expect(screen.getByText(/No gym connected yet/)).toBeInTheDocument();
  });

  it("points at the switch when the connection is saved but off", async () => {
    // Nothing is wrong with the credential; the user just turned it off.
    await gymHome([gym({ enabled: false })]);

    expect(screen.getByText(/saved but switched off/)).toBeInTheDocument();
  });

  it("says which way to go when the connection is incomplete", async () => {
    // Connected and on, but the server can't use it — a missing location or
    // program, which only Settings can show.
    await gymHome([gym()], false);

    expect(screen.getByText(/incomplete/)).toBeInTheDocument();
  });

  it("falls back to plain quiet when everything is fine", async () => {
    await gymHome([gym()], true);

    expect(
      screen.getByText("Your gym hasn't published anything for the last few days."),
    ).toBeInTheDocument();
  });

  it("opens settings from the empty state", async () => {
    const user = userEvent.setup();
    const button = await gymHome([]);

    await user.click(button);

    expect(onOpenSettings).toHaveBeenCalled();
  });
});

describe("acting on a day", () => {
  it("loads and saves the day's workout", async () => {
    const user = userEvent.setup();
    view();

    await user.click(await screen.findByRole("button", { name: "Load & save" }));

    await waitFor(() =>
      expect(api.loadWorkoutFromText).toHaveBeenCalledWith(ENTRY.text, ENTRY.title),
    );
    expect(onLoad).toHaveBeenCalledWith(WORKOUT);
  });

  it("reports a day that wouldn't load", async () => {
    api.loadWorkoutFromText.mockRejectedValue(new Error("down"));
    const user = userEvent.setup();
    view();

    await user.click(await screen.findByRole("button", { name: "Load & save" }));

    expect(await screen.findByText("Could not load that workout.")).toBeInTheDocument();
  });

  it("parses a day for editing without saving it", async () => {
    // Editing shouldn't write anything to the library until the coach says so.
    const user = userEvent.setup();
    view();

    await user.click(await screen.findByRole("button", { name: "Edit first" }));

    await waitFor(() =>
      expect(onEdit).toHaveBeenCalledWith({ workout: WORKOUT, saved: false }),
    );
  });

  it("edits the saved record in place when there already is one", async () => {
    // Otherwise editing a saved day would fork a second copy of it.
    api.listWods.mockResolvedValue([{ ...ENTRY, saved_workout_id: "w1" }]);
    const user = userEvent.setup();
    view();

    await user.click(await screen.findByRole("button", { name: "Edit first" }));

    await waitFor(() => expect(api.getWorkout).toHaveBeenCalledWith("w1"));
    expect(onEdit).toHaveBeenCalledWith({ workout: WORKOUT, saved: true });
    expect(api.parseWorkout).not.toHaveBeenCalled();
  });

  it("reports a day that wouldn't open for editing", async () => {
    api.parseWorkout.mockRejectedValue(new Error("down"));
    const user = userEvent.setup();
    view();

    await user.click(await screen.findByRole("button", { name: "Edit first" }));

    expect(await screen.findByText("Could not open that workout.")).toBeInTheDocument();
  });
});
