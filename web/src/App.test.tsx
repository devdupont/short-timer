import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { Me } from "./types";

/**
 * The shell: whether you're signed in, which tabs exist, and what the nav
 * shows.
 *
 * Two things here have bitten before. The signed-in check has a third state
 * for "haven't asked yet", without which every reload flashes the sign-in
 * screen at someone who is already signed in. And an emailed link has to beat
 * an existing session, or a signed-in visitor clicking a confirmation link
 * lands on the home page with the token silently dropped.
 */

const api = vi.hoisted(() => ({
  getMe: vi.fn(),
  logout: vi.fn(),
  listWods: vi.fn(),
  listConcept2Wods: vi.fn(),
  listHybridWods: vi.fn(),
  listGymWods: vi.fn(),
  listWorkouts: vi.fn(),
  listWorkoutCategories: vi.fn(),
  listInvites: vi.fn(),
  verifyEmail: vi.fn(),
}));

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return { ...actual, ...api };
});

vi.mock("./passkeys", () => ({
  getCredential: vi.fn(),
  createCredential: vi.fn(),
  passkeysSupported: () => false,
}));

function me(over: Partial<Me> = {}): Me {
  return {
    id: "u1",
    email: "athlete@example.com",
    email_verified: true,
    role: "user",
    display_name: "Athlete",
    secrets_available: true,
    config: { gyms: [], feeds: [] },
    ...over,
  };
}

function at(url: string): void {
  window.history.pushState({}, "", url);
}

beforeEach(() => {
  vi.clearAllMocks();
  api.getMe.mockResolvedValue(me());
  api.logout.mockResolvedValue(undefined);
  api.listWods.mockResolvedValue([]);
  api.listConcept2Wods.mockResolvedValue([]);
  api.listHybridWods.mockResolvedValue([]);
  api.listGymWods.mockResolvedValue({ configured: true, wods: [] });
  api.listWorkouts.mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 });
  api.listWorkoutCategories.mockResolvedValue([]);
  api.listInvites.mockResolvedValue([]);
  at("/");
});

afterEach(() => {
  at("/");
});

describe("deciding whether you're signed in", () => {
  it("shows nothing while it asks, rather than flashing the sign-in screen", () => {
    // The reason the state has three values instead of two: a signed-in user
    // reloading used to be asked to sign in again for a moment every time.
    api.getMe.mockReturnValue(new Promise(() => {}));

    render(<App />);

    expect(screen.queryByText("Sign in to your account.")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Home" })).not.toBeInTheDocument();
  });

  it("shows the app when the session is good", async () => {
    render(<App />);

    expect(await screen.findByRole("button", { name: "Home" })).toBeInTheDocument();
  });

  it("shows the sign-in screen when it isn't", async () => {
    api.getMe.mockRejectedValue(new Error("401"));

    render(<App />);

    expect(await screen.findByText("Sign in to your account.")).toBeInTheDocument();
  });
});

describe("the nav", () => {
  it("offers the ordinary tabs", async () => {
    render(<App />);

    await screen.findByRole("button", { name: "Home" });
    for (const tab of ["Home", "Paste", "Build", "Library", "Settings"]) {
      expect(screen.getByRole("button", { name: tab })).toBeInTheDocument();
    }
  });

  it("hides Admin from an ordinary account", async () => {
    // The endpoints behind it 404 for everyone else, so a visible tab that
    // always failed would read as a bug rather than a closed door.
    render(<App />);

    await screen.findByRole("button", { name: "Home" });
    expect(screen.queryByRole("button", { name: "Admin" })).not.toBeInTheDocument();
  });

  it("shows Admin to an admin", async () => {
    api.getMe.mockResolvedValue(me({ role: "admin" }));

    render(<App />);

    expect(await screen.findByRole("button", { name: "Admin" })).toBeInTheDocument();
  });

  it("hides Admin from staff, who are not admins", async () => {
    api.getMe.mockResolvedValue(me({ role: "staff" }));

    render(<App />);

    await screen.findByRole("button", { name: "Home" });
    expect(screen.queryByRole("button", { name: "Admin" })).not.toBeInTheDocument();
  });

  it("offers no Timer tab until something is loaded", async () => {
    render(<App />);

    await screen.findByRole("button", { name: "Home" });
    expect(screen.queryByRole("button", { name: "Timer" })).not.toBeInTheDocument();
  });

  it("switches tabs", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Library" }));

    expect(await screen.findByRole("heading", { name: "Library" })).toBeInTheDocument();
  });
});

describe("signing out", () => {
  it("returns to the sign-in screen", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Sign out" }));

    expect(await screen.findByText("Sign in to your account.")).toBeInTheDocument();
    expect(api.logout).toHaveBeenCalled();
  });

  it("forgets the role, so Admin doesn't survive into the next session", async () => {
    api.getMe.mockResolvedValue(me({ role: "admin" }));
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("button", { name: "Admin" });

    await user.click(screen.getByRole("button", { name: "Sign out" }));
    await screen.findByText("Sign in to your account.");
    api.getMe.mockResolvedValue(me({ role: "user" }));

    // Signing back in as someone ordinary must not show the admin tab.
    expect(screen.queryByRole("button", { name: "Admin" })).not.toBeInTheDocument();
  });
});

describe("emailed links", () => {
  it("takes precedence over an existing session", async () => {
    // Arriving at one is deliberate; the alternative is dropping the token on
    // the floor, which is what used to happen.
    at("/verify?token=good");
    api.verifyEmail.mockResolvedValue(undefined);

    render(<App />);

    await waitFor(() => expect(api.verifyEmail).toHaveBeenCalledWith("good"));
    expect(await screen.findByText("Your email address is confirmed.")).toBeInTheDocument();
  });

  it("returns to the app once the link is finished with", async () => {
    at("/verify?token=good");
    api.verifyEmail.mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("button", { name: "Home" })).toBeInTheDocument();
  });

  it("leaves an ordinary visit alone", async () => {
    at("/");

    render(<App />);

    expect(await screen.findByRole("button", { name: "Home" })).toBeInTheDocument();
    expect(api.verifyEmail).not.toHaveBeenCalled();
  });
});
