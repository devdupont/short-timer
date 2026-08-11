import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Account } from "./Account";
import type { ApiToken, Me, Passkey, SessionView } from "../types";

/**
 * The account panel: password, devices, passkeys, and API tokens.
 *
 * The rule this whole panel is built around is that nothing here can read a
 * credential back. A minted API token is shown once because only its hash is
 * kept; changing a password asks for the current one even though you're
 * already signed in, because a session is long-lived and a borrowed laptop
 * shouldn't be enough to lock the owner out.
 */

const api = vi.hoisted(() => ({
  listSessions: vi.fn(),
  changePassword: vi.fn(),
  endOtherSessions: vi.fn(),
  resendVerification: vi.fn(),
  listPasskeys: vi.fn(),
  deletePasskey: vi.fn(),
  passkeyRegisterChallenge: vi.fn(),
  registerPasskey: vi.fn(),
  listApiTokens: vi.fn(),
  createApiToken: vi.fn(),
  revokeApiToken: vi.fn(),
}));

const passkeysLib = vi.hoisted(() => ({
  createCredential: vi.fn(),
  passkeysSupported: vi.fn(() => true),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, ...api };
});

vi.mock("../passkeys", () => passkeysLib);

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

function session(over: Partial<SessionView> = {}): SessionView {
  return {
    created_at: "2026-08-01T00:00:00Z",
    last_seen_at: "2026-08-10T00:00:00Z",
    user_agent: "Firefox",
    ...over,
  } as SessionView;
}

function passkey(over: Partial<Passkey> = {}): Passkey {
  return {
    id: "p1",
    user_id: "u1",
    nickname: "My phone",
    sign_count: 0,
    aaguid: "",
    backed_up: true,
    last_used_at: null,
    created_at: "",
    ...over,
  } as Passkey;
}

function token(over: Partial<ApiToken> = {}): ApiToken {
  return {
    id: "t1",
    name: "MCP server",
    prefix: "abc123",
    scopes: ["library:read"],
    last_used_at: null,
    created_at: "",
    ...over,
  } as ApiToken;
}

function view(profile: Me = me()) {
  return render(<Account me={profile} />);
}

/** The token form's own password box — the change-password form has one too. */
function tokenPassword(): HTMLElement {
  const form = screen.getByRole("button", { name: "Create token" }).closest("form") as HTMLElement;
  return within(form).getByLabelText(/^Current password/);
}

beforeEach(() => {
  vi.clearAllMocks();
  passkeysLib.passkeysSupported.mockReturnValue(true);
  api.listSessions.mockResolvedValue([]);
  api.listPasskeys.mockResolvedValue([]);
  api.listApiTokens.mockResolvedValue([]);
  api.changePassword.mockResolvedValue(undefined);
  api.endOtherSessions.mockResolvedValue(undefined);
  api.resendVerification.mockResolvedValue(undefined);
  api.deletePasskey.mockResolvedValue(undefined);
  api.revokeApiToken.mockResolvedValue(undefined);
  api.createApiToken.mockResolvedValue({ token: "sk-minted-once" });
  api.passkeyRegisterChallenge.mockResolvedValue({ options: {}, challenge_handle: "h" });
  passkeysLib.createCredential.mockResolvedValue({ id: "cred" });
  api.registerPasskey.mockResolvedValue(undefined);
});

describe("who you are", () => {
  it("names the account", () => {
    view();

    expect(screen.getByText("athlete@example.com")).toBeInTheDocument();
  });

  it("mentions a privileged role, but not the ordinary one", () => {
    view(me({ role: "admin" }));

    expect(screen.getByText(/· admin/)).toBeInTheDocument();
  });

  it("offers to resend confirmation while the address is unconfirmed", async () => {
    const user = userEvent.setup();
    view(me({ email_verified: false }));

    await user.click(screen.getByRole("button", { name: "Resend the confirmation email" }));

    expect(api.resendVerification).toHaveBeenCalled();
    expect(await screen.findByText("Confirmation email sent.")).toBeInTheDocument();
  });

  it("says nothing about confirmation once it's done", () => {
    view();

    expect(screen.queryByRole("button", { name: /Resend/ })).not.toBeInTheDocument();
  });
});

describe("changing a password", () => {
  it("insists on the current one as well as the new one", async () => {
    // Being signed in isn't proof enough — sessions outlive the laptop they
    // were opened on.
    const user = userEvent.setup();
    view();

    await user.type(screen.getByLabelText(/^New password/), "a-long-new-password");

    expect(screen.getByRole("button", { name: "Change password" })).toBeDisabled();
  });

  it("holds out for a long enough new password", async () => {
    const user = userEvent.setup();
    view();

    await user.type(screen.getByLabelText("Current password"), "old-password");
    await user.type(screen.getByLabelText(/^New password/), "short");

    expect(screen.getByRole("button", { name: "Change password" })).toBeDisabled();
  });

  it("changes it and says what else that did", async () => {
    const user = userEvent.setup();
    view();

    await user.type(screen.getByLabelText("Current password"), "old-password");
    await user.type(screen.getByLabelText(/^New password/), "a-long-new-password");
    await user.click(screen.getByRole("button", { name: "Change password" }));

    await waitFor(() =>
      expect(api.changePassword).toHaveBeenCalledWith("old-password", "a-long-new-password"),
    );
    expect(
      await screen.findByText("Password changed. Every other device has been signed out."),
    ).toBeInTheDocument();
  });

  it("empties both boxes afterwards", async () => {
    const user = userEvent.setup();
    view();

    await user.type(screen.getByLabelText("Current password"), "old-password");
    await user.type(screen.getByLabelText(/^New password/), "a-long-new-password");
    await user.click(screen.getByRole("button", { name: "Change password" }));

    await waitFor(() => expect(screen.getByLabelText("Current password")).toHaveValue(""));
  });

  it("reports a refused change", async () => {
    api.changePassword.mockRejectedValue(new Error("wrong"));
    const user = userEvent.setup();
    view();

    await user.type(screen.getByLabelText("Current password"), "wrong-password");
    await user.type(screen.getByLabelText(/^New password/), "a-long-new-password");
    await user.click(screen.getByRole("button", { name: "Change password" }));

    expect(await screen.findByText("Could not reach the server.")).toBeInTheDocument();
  });
});

describe("signed-in devices", () => {
  it("lists them", async () => {
    api.listSessions.mockResolvedValue([session({ user_agent: "Firefox on Mac" })]);

    view();

    expect(await screen.findByText("Firefox on Mac")).toBeInTheDocument();
  });

  it("names a device that didn't identify itself", async () => {
    api.listSessions.mockResolvedValue([session({ user_agent: null })]);

    view();

    expect(await screen.findByText("Unknown device")).toBeInTheDocument();
  });

  it("only offers to sign out elsewhere when there is an elsewhere", async () => {
    api.listSessions.mockResolvedValue([session()]);

    view();

    await screen.findByText("Firefox");
    expect(
      screen.queryByRole("button", { name: "Sign out everywhere else" }),
    ).not.toBeInTheDocument();
  });

  it("signs the others out", async () => {
    api.listSessions.mockResolvedValue([session(), session({ user_agent: "Safari" })]);
    const user = userEvent.setup();
    view();

    await user.click(await screen.findByRole("button", { name: "Sign out everywhere else" }));

    expect(api.endOtherSessions).toHaveBeenCalled();
    expect(await screen.findByText("Signed out everywhere else.")).toBeInTheDocument();
  });
});

describe("passkeys", () => {
  it("says so on a browser that can't do them", () => {
    passkeysLib.passkeysSupported.mockReturnValue(false);

    view();

    expect(screen.getByText("This browser doesn't support passkeys.")).toBeInTheDocument();
  });

  it("marks which ones survive losing the device", async () => {
    api.listPasskeys.mockResolvedValue([
      passkey({ id: "p1", nickname: "Phone", backed_up: true }),
      passkey({ id: "p2", nickname: "Key", backed_up: false }),
    ]);

    view();

    const phone = (await screen.findByText("Phone")).closest("li") as HTMLElement;
    const key = screen.getByText("Key").closest("li") as HTMLElement;
    expect(within(phone).getByText(/Synced/)).toBeInTheDocument();
    expect(within(key).getByText(/This device only/)).toBeInTheDocument();
  });

  it("nudges when the only passkey would die with its device", async () => {
    // One device-bound passkey is a lockout waiting to happen.
    api.listPasskeys.mockResolvedValue([passkey({ backed_up: false })]);

    view();

    expect(await screen.findByText(/Adding a second one/)).toBeInTheDocument();
  });

  it("does not nudge when the only passkey is synced", async () => {
    api.listPasskeys.mockResolvedValue([passkey({ backed_up: true })]);

    view();

    await screen.findByText("My phone");
    expect(screen.queryByText(/Adding a second one/)).not.toBeInTheDocument();
  });

  it("registers a new one", async () => {
    const user = userEvent.setup();
    view();

    await user.type(screen.getByPlaceholderText("My phone"), "Work laptop");
    await user.click(screen.getByRole("button", { name: "Add a passkey" }));

    await waitFor(() =>
      expect(api.registerPasskey).toHaveBeenCalledWith({
        challengeHandle: "h",
        credential: { id: "cred" },
        nickname: "Work laptop",
      }),
    );
    expect(await screen.findByText("Passkey added.")).toBeInTheDocument();
  });

  it("says nothing when the device prompt is dismissed", async () => {
    // Dismissing is a choice, not a failure.
    passkeysLib.createCredential.mockRejectedValue(
      new DOMException("cancelled", "NotAllowedError"),
    );
    const user = userEvent.setup();
    view();

    await user.type(screen.getByPlaceholderText("My phone"), "Work laptop");
    await user.click(screen.getByRole("button", { name: "Add a passkey" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Add a passkey" })).toBeEnabled(),
    );
    expect(screen.queryByText(/Could not/)).not.toBeInTheDocument();
  });

  it("does report a genuine registration failure", async () => {
    api.passkeyRegisterChallenge.mockRejectedValue(new Error("down"));
    const user = userEvent.setup();
    view();

    await user.type(screen.getByPlaceholderText("My phone"), "Work laptop");
    await user.click(screen.getByRole("button", { name: "Add a passkey" }));

    expect(await screen.findByText("Could not reach the server.")).toBeInTheDocument();
  });

  it("removes one", async () => {
    api.listPasskeys.mockResolvedValue([passkey({ id: "p9" })]);
    const user = userEvent.setup();
    view();

    await user.click(await screen.findByRole("button", { name: "Remove" }));

    expect(api.deletePasskey).toHaveBeenCalledWith("p9");
  });
});

describe("api tokens", () => {
  it("shows a minted token once, and keeps it until dismissed", async () => {
    // The server keeps only a hash, so this is the one and only time the
    // value exists anywhere the user can see it.
    const user = userEvent.setup();
    view();

    await user.type(screen.getByPlaceholderText("MCP server"), "My token");
    await user.type(tokenPassword(), "my-password");
    await user.click(screen.getByRole("button", { name: "Create token" }));

    expect(await screen.findByText("sk-minted-once")).toBeInTheDocument();
    expect(screen.getByText(/It won't be shown again/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.queryByText("sk-minted-once")).not.toBeInTheDocument();
  });

  it("asks for the password again, because a token outlives the session", async () => {
    const user = userEvent.setup();
    view();

    await user.type(screen.getByPlaceholderText("MCP server"), "My token");

    expect(screen.getByRole("button", { name: "Create token" })).toBeDisabled();
  });

  it("mints a read-only token by default", async () => {
    const user = userEvent.setup();
    view();

    await user.type(screen.getByPlaceholderText("MCP server"), "My token");
    await user.type(tokenPassword(), "my-password");
    await user.click(screen.getByRole("button", { name: "Create token" }));

    await waitFor(() =>
      expect(api.createApiToken).toHaveBeenCalledWith({
        name: "My token",
        scopes: ["library:read"],
        currentPassword: "my-password",
      }),
    );
  });

  it("adds write scope only when asked", async () => {
    const user = userEvent.setup();
    view();

    await user.type(screen.getByPlaceholderText("MCP server"), "My token");
    await user.click(screen.getByRole("checkbox"));
    await user.type(tokenPassword(), "my-password");
    await user.click(screen.getByRole("button", { name: "Create token" }));

    await waitFor(() =>
      expect(api.createApiToken).toHaveBeenCalledWith(
        expect.objectContaining({ scopes: ["library:read", "library:write"] }),
      ),
    );
  });

  it("lists existing tokens by prefix only", async () => {
    api.listApiTokens.mockResolvedValue([token({ name: "MCP", prefix: "abc123" })]);

    view();

    expect(await screen.findByText("abc123…")).toBeInTheDocument();
  });

  it("revokes one", async () => {
    api.listApiTokens.mockResolvedValue([token({ id: "t9" })]);
    const user = userEvent.setup();
    view();

    await user.click(await screen.findByRole("button", { name: "Revoke" }));

    expect(api.revokeApiToken).toHaveBeenCalledWith("t9");
  });

  it("reports a refused mint", async () => {
    api.createApiToken.mockRejectedValue(new Error("wrong password"));
    const user = userEvent.setup();
    view();

    await user.type(screen.getByPlaceholderText("MCP server"), "My token");
    await user.type(tokenPassword(), "wrong");
    await user.click(screen.getByRole("button", { name: "Create token" }));

    expect(await screen.findByText("Could not reach the server.")).toBeInTheDocument();
  });
});
