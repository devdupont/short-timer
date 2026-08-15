import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Settings } from "./Settings";
import type { GymConnection, GymProviderInfo, Me } from "../types";

/**
 * Feed preferences and gym connections.
 *
 * The rule that shapes the credential fields: keys are write-only. The server
 * says whether one is stored and shows its last few characters, but never
 * sends the value back — so the box starts empty and an empty box has to mean
 * "keep the stored key", never "clear it". Saving a blank field and wiping a
 * working connection is the failure being guarded against.
 */

const api = vi.hoisted(() => ({
  getMe: vi.fn(),
  listGymProviders: vi.fn(),
  getGymHealth: vi.fn(),
  updateConfig: vi.fn(),
  listSessions: vi.fn(),
  listPasskeys: vi.fn(),
  listApiTokens: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, ...api };
});

vi.mock("../passkeys", () => ({
  createCredential: vi.fn(),
  passkeysSupported: () => false,
}));

const WODIFY: GymProviderInfo = {
  provider: "wodify_member",
  platform: "WODIFY",
  label: "My gym's Wodify whiteboard",
  blurb: "For gym members.",
  credential_label: "Whiteboard key",
  credential_hint: "From your gym's public whiteboard link.",
  location: { label: "Location", placeholder: "Main", required: false },
  program: { label: "Program", placeholder: "CrossFit", required: false },
  help_text: "Ask your gym to enable the public whiteboard.",
} as GymProviderInfo;

function gym(over: Partial<GymConnection> = {}): GymConnection {
  return {
    provider: "wodify_member",
    credential: { is_set: true, masked: "…1234" },
    location: "Main",
    program: "CrossFit",
    enabled: true,
    ...over,
  };
}

function me(over: Partial<Me> = {}): Me {
  return {
    id: "u1",
    email: "athlete@example.com",
    email_verified: true,
    role: "user",
    display_name: "Athlete",
    secrets_available: true,
    config: {
      gyms: [],
      feeds: [
        { kind: "crossfit", enabled: true },
        { kind: "concept2", enabled: false },
      ],
    },
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  api.getMe.mockResolvedValue(me());
  api.listGymProviders.mockResolvedValue([WODIFY]);
  api.getGymHealth.mockResolvedValue([]);
  api.updateConfig.mockImplementation(async () => me());
  api.listSessions.mockResolvedValue([]);
  api.listPasskeys.mockResolvedValue([]);
  api.listApiTokens.mockResolvedValue([]);
});

/** The Wodify card, so queries don't collide with the account panel below.
 *  Async because everything on this screen arrives after a fetch. */
async function card(): Promise<HTMLElement> {
  const heading = await screen.findByRole("heading", { name: WODIFY.label });
  return heading.closest("section") as HTMLElement;
}

describe("loading", () => {
  it("says so while it waits", () => {
    api.getMe.mockReturnValue(new Promise(() => {}));

    render(<Settings />);

    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("reports a failure instead of an empty form", async () => {
    api.getMe.mockRejectedValue(new Error("down"));

    render(<Settings />);

    expect(await screen.findByText("Could not reach the server.")).toBeInTheDocument();
  });

  it("still renders when the health check fails", async () => {
    // Health only enriches a status line; losing it shouldn't stop someone
    // editing their settings.
    api.getGymHealth.mockRejectedValue(new Error("down"));

    render(<Settings />);

    expect(await screen.findByRole("heading", { name: "Home page feeds" })).toBeInTheDocument();
  });

  it("warns when the server can't store keys at all", async () => {
    api.getMe.mockResolvedValue(me({ secrets_available: false }));

    render(<Settings />);

    expect(await screen.findByText(/no encryption keys configured/)).toBeInTheDocument();
    expect(within(await card()).getByLabelText(/Whiteboard key/)).toBeDisabled();
  });
});

describe("home page feeds", () => {
  it("lists them with their current state", async () => {
    render(<Settings />);

    const crossfit = await screen.findByLabelText("CrossFit.com");
    expect(crossfit).toBeChecked();
    expect(screen.getByLabelText("Concept2")).not.toBeChecked();
  });

  it("turns one on", async () => {
    const user = userEvent.setup();
    render(<Settings />);

    await user.click(await screen.findByLabelText("Concept2"));

    await waitFor(() =>
      expect(api.updateConfig).toHaveBeenCalledWith({
        feeds: [
          { kind: "crossfit", enabled: true },
          { kind: "concept2", enabled: true },
        ],
      }),
    );
    expect(await screen.findByText("Concept2 shown on your home page.")).toBeInTheDocument();
  });

  it("says hiding a feed doesn't disconnect anything", async () => {
    const user = userEvent.setup();
    render(<Settings />);

    await user.click(await screen.findByLabelText("CrossFit.com"));

    expect(await screen.findByText("CrossFit.com hidden.")).toBeInTheDocument();
  });

  it("reorders them", async () => {
    const user = userEvent.setup();
    render(<Settings />);

    await user.click(await screen.findByLabelText("Move Concept2 up"));

    await waitFor(() =>
      expect(api.updateConfig).toHaveBeenCalledWith({
        feeds: [
          { kind: "concept2", enabled: false },
          { kind: "crossfit", enabled: true },
        ],
      }),
    );
  });

  it("cannot move the first one up or the last one down", async () => {
    render(<Settings />);

    expect(await screen.findByLabelText("Move CrossFit.com up")).toBeDisabled();
    expect(screen.getByLabelText("Move Concept2 down")).toBeDisabled();
  });
});

describe("what a connection's status says", () => {
  it("says nothing is connected when no key is stored", async () => {
    render(<Settings />);

    expect(await screen.findByText("Not connected.")).toBeInTheDocument();
  });

  it("distinguishes a saved but switched-off connection", async () => {
    api.getMe.mockResolvedValue(me({ config: { ...me().config, gyms: [gym({ enabled: false })] } }));

    render(<Settings />);

    expect(await screen.findByText(/switched off — nothing is fetched/)).toBeInTheDocument();
  });

  it("flags a connection that is on but has never fetched", async () => {
    // Fetchers swallow their errors so one bad day can't empty a feed, which
    // makes "your key is wrong" and "your gym is quiet" look identical. Never
    // having fetched at all is the one that's actionable.
    api.getMe.mockResolvedValue(me({ config: { ...me().config, gyms: [gym()] } }));
    api.getGymHealth.mockResolvedValue([
      { provider: "wodify_member", last_fetched_at: null, cached_days: 0 },
    ]);

    render(<Settings />);

    expect(await screen.findByText(/has never fetched successfully/)).toBeInTheDocument();
  });

  it("says a working connection is feeding the home page", async () => {
    api.getMe.mockResolvedValue(me({ config: { ...me().config, gyms: [gym()] } }));
    api.getGymHealth.mockResolvedValue([
      {
        provider: "wodify_member",
        last_fetched_at: new Date().toISOString(),
        cached_days: 1,
      },
    ]);

    render(<Settings />);

    expect(await screen.findByText(/Feeding your home page/)).toBeInTheDocument();
    expect(screen.getByText(/1 day cached/)).toBeInTheDocument();
  });
});

describe("saving a gym key", () => {
  it("shows the masked key rather than the value", async () => {
    // The value never comes back from the server, by design.
    api.getMe.mockResolvedValue(me({ config: { ...me().config, gyms: [gym()] } }));

    render(<Settings />);

    expect(await screen.findByText("Saved …1234")).toBeInTheDocument();
    expect(within(await card()).getByLabelText(/Whiteboard key/)).toHaveValue("");
  });

  it("omits a blank key so the stored one survives", async () => {
    // Someone editing only the location must not wipe their working key.
    api.getMe.mockResolvedValue(me({ config: { ...me().config, gyms: [gym()] } }));
    const user = userEvent.setup();
    render(<Settings />);

    await user.click(within(await card()).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(api.updateConfig).toHaveBeenCalled());
    const sent = api.updateConfig.mock.calls[0][0];
    expect(sent.gyms.wodify_member).not.toHaveProperty("credential");
  });

  it("sends a key that was actually typed", async () => {
    const user = userEvent.setup();
    render(<Settings />);

    await user.type(within(await card()).getByLabelText(/Whiteboard key/), "new-key");
    await user.click(within(await card()).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(api.updateConfig).toHaveBeenCalledWith({
        gyms: {
          wodify_member: {
            credential: "new-key",
            location: "",
            program: "",
            enabled: false,
          },
        },
      }),
    );
  });

  it("clears a stored key on request, explicitly", async () => {
    // The only way to remove one, precisely because blank means "keep".
    api.getMe.mockResolvedValue(me({ config: { ...me().config, gyms: [gym()] } }));
    const user = userEvent.setup();
    render(<Settings />);

    await user.click(await screen.findByRole("button", { name: "Remove saved key" }));

    await waitFor(() =>
      expect(api.updateConfig).toHaveBeenCalledWith({
        gyms: { wodify_member: { credential: "" } },
      }),
    );
  });

  it("offers no removal when there is nothing stored", async () => {
    render(<Settings />);

    await screen.findByText("Not connected.");
    expect(screen.queryByRole("button", { name: "Remove saved key" })).not.toBeInTheDocument();
  });

  it("reports a refused save", async () => {
    api.updateConfig.mockRejectedValue(new Error("nope"));
    const user = userEvent.setup();
    render(<Settings />);

    await user.click(within(await card()).getByRole("button", { name: "Save" }));

    expect(await screen.findAllByText("Could not reach the server.")).not.toHaveLength(0);
  });
});
