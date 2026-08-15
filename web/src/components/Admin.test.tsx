import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Admin } from "./Admin";
import type { Invite, InviteCreated } from "../types";

/**
 * The admin screen, driven the way an admin drives it.
 *
 * `invites.test.ts` covers the classification rules on their own. This covers
 * the wiring around them: that the list is fetched, that Revoke is offered
 * only where it would work, that a minted link is shown once, and that a
 * failure says so instead of silently doing nothing.
 *
 * The api module is mocked rather than `fetch`, because what's being tested is
 * the screen's behaviour, not the request shapes — `api.test.ts` owns those.
 */

const { listInvites, createInvite, revokeInvite } = vi.hoisted(() => ({
  listInvites: vi.fn(),
  createInvite: vi.fn(),
  revokeInvite: vi.fn(),
}));

vi.mock("../api", async () => {
  // ApiError is a real class the component type-checks against, so keep it.
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, listInvites, createInvite, revokeInvite };
});

function invite(over: Partial<Invite> = {}): Invite {
  return {
    id: "i1",
    email: "athlete@example.com",
    role: "user",
    created_by: "admin",
    created_at: "2026-08-10T00:00:00Z",
    expires_at: "2099-01-01T00:00:00Z",
    redeemed_at: null,
    redeemed_by: null,
    ...over,
  };
}

function created(over: Partial<InviteCreated> = {}): InviteCreated {
  return {
    invite: invite({ id: "new" }),
    token: "tok",
    link: "http://localhost:5173/register?token=tok",
    emailed: false,
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  listInvites.mockResolvedValue([]);
  createInvite.mockResolvedValue(created());
  revokeInvite.mockResolvedValue(undefined);
});

/** The list item for a given invite label. */
function rowFor(text: string | RegExp): HTMLElement {
  return screen.getByText(text).closest("li") as HTMLElement;
}

describe("listing invites", () => {
  it("shows who each invite is for and where it stands", async () => {
    listInvites.mockResolvedValue([invite({ email: "athlete@example.com", role: "staff" })]);

    render(<Admin />);

    expect(await screen.findByText(/athlete@example\.com · staff/)).toBeInTheDocument();
    expect(screen.getByText(/^Pending, expires/)).toBeInTheDocument();
  });

  it("calls an open invite what it is", async () => {
    // A null address isn't missing data — it's an invite anyone holding the
    // link may redeem, and saying so is the difference between the two.
    listInvites.mockResolvedValue([invite({ email: null })]);

    render(<Admin />);

    expect(await screen.findByText(/Anyone with the link · user/)).toBeInTheDocument();
  });

  it("offers Revoke on an invite that is still live", async () => {
    listInvites.mockResolvedValue([invite()]);

    render(<Admin />);
    await screen.findByText(/^Pending, expires/);
    const row = rowFor(/athlete@example\.com/);

    expect(within(row).getByRole("button", { name: "Revoke" })).toBeInTheDocument();
  });

  it("does not offer Revoke on one that was redeemed", async () => {
    // The server refuses to delete a redeemed invite — it's the record of how
    // an account came to exist — so a button here would only ever fail.
    listInvites.mockResolvedValue([invite({ redeemed_at: "2026-08-10T12:00:00Z" })]);

    render(<Admin />);
    await screen.findByText(/^Redeemed/);
    const row = rowFor(/athlete@example\.com/);

    expect(within(row).queryByRole("button", { name: "Revoke" })).not.toBeInTheDocument();
  });

  it("shows nothing rather than breaking when the list cannot be loaded", async () => {
    listInvites.mockRejectedValue(new Error("network"));

    render(<Admin />);

    expect(await screen.findByRole("button", { name: "Create invite" })).toBeInTheDocument();
  });
});

describe("minting an invite", () => {
  it("sends the address and role that were chosen", async () => {
    const user = userEvent.setup();
    render(<Admin />);

    await user.type(screen.getByRole("textbox"), "new@example.com");
    await user.selectOptions(screen.getByRole("combobox"), "staff");
    await user.click(screen.getByRole("button", { name: "Create invite" }));

    expect(createInvite).toHaveBeenCalledWith("new@example.com", "staff");
  });

  it("sends a null address for an open invite", async () => {
    // The API spells "anyone may redeem this" as a null address, not "".
    const user = userEvent.setup();
    render(<Admin />);

    await user.click(screen.getByRole("button", { name: "Create invite" }));

    expect(createInvite).toHaveBeenCalledWith(null, "user");
  });

  it("ignores an address that is only whitespace", async () => {
    const user = userEvent.setup();
    render(<Admin />);

    await user.type(screen.getByRole("textbox"), "   ");
    await user.click(screen.getByRole("button", { name: "Create invite" }));

    expect(createInvite).toHaveBeenCalledWith(null, "user");
  });

  it("shows the link, because the response is the only place it appears", async () => {
    const user = userEvent.setup();
    render(<Admin />);

    await user.click(screen.getByRole("button", { name: "Create invite" }));

    expect(
      await screen.findByText("http://localhost:5173/register?token=tok"),
    ).toBeInTheDocument();
    expect(screen.getByText(/Send this link/)).toBeInTheDocument();
  });

  it("says when it was emailed instead", async () => {
    createInvite.mockResolvedValue(created({ emailed: true }));
    const user = userEvent.setup();
    render(<Admin />);

    await user.click(screen.getByRole("button", { name: "Create invite" }));

    expect(await screen.findByText(/Emailed to the address/)).toBeInTheDocument();
    // The link is still shown, in case the mail never lands.
    expect(screen.getByText("http://localhost:5173/register?token=tok")).toBeInTheDocument();
  });

  it("keeps the link on screen until it is dismissed", async () => {
    const user = userEvent.setup();
    render(<Admin />);

    await user.click(screen.getByRole("button", { name: "Create invite" }));
    await screen.findByText("http://localhost:5173/register?token=tok");
    await user.click(screen.getByRole("button", { name: "Done" }));

    expect(
      screen.queryByText("http://localhost:5173/register?token=tok"),
    ).not.toBeInTheDocument();
  });

  it("clears the form and reloads the list afterwards", async () => {
    const user = userEvent.setup();
    render(<Admin />);

    await user.type(screen.getByRole("textbox"), "new@example.com");
    await user.click(screen.getByRole("button", { name: "Create invite" }));

    await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue(""));
    // Once on mount, once after minting — otherwise the new invite is missing
    // from the list until the admin reloads the page.
    expect(listInvites).toHaveBeenCalledTimes(2);
  });

  it("reports a refusal rather than appearing to do nothing", async () => {
    createInvite.mockRejectedValue(new Error("nope"));
    const user = userEvent.setup();
    render(<Admin />);

    await user.click(screen.getByRole("button", { name: "Create invite" }));

    expect(await screen.findByText("Could not reach the server.")).toBeInTheDocument();
  });
});

describe("revoking", () => {
  it("revokes the invite that was clicked", async () => {
    listInvites.mockResolvedValue([invite({ id: "abc" })]);
    const user = userEvent.setup();
    render(<Admin />);

    await user.click(await screen.findByRole("button", { name: "Revoke" }));

    expect(revokeInvite).toHaveBeenCalledWith("abc");
  });

  it("refreshes the list so the revoked invite disappears", async () => {
    listInvites.mockResolvedValueOnce([invite({ id: "abc" })]).mockResolvedValueOnce([]);
    const user = userEvent.setup();
    render(<Admin />);

    await user.click(await screen.findByRole("button", { name: "Revoke" }));

    await waitFor(() =>
      expect(screen.queryByText(/athlete@example\.com/)).not.toBeInTheDocument(),
    );
  });

  it("reports a failed revoke", async () => {
    listInvites.mockResolvedValue([invite()]);
    revokeInvite.mockRejectedValue(new Error("nope"));
    const user = userEvent.setup();
    render(<Admin />);

    await user.click(await screen.findByRole("button", { name: "Revoke" }));

    expect(await screen.findByText("Could not reach the server.")).toBeInTheDocument();
  });
});
