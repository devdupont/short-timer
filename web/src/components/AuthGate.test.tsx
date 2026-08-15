import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthGate } from "./AuthGate";

/**
 * Every way into the app: signing in, redeeming an invite, and the two
 * emailed links.
 *
 * Which screen appears is read straight off the URL (`authLinks.test.ts`
 * covers that parsing); these cover what each screen then does, including the
 * two places where the *absence* of feedback is the feature — a cancelled
 * passkey prompt, and a password reset that must not reveal whether an
 * address has an account.
 */

const api = vi.hoisted(() => ({
  login: vi.fn(),
  register: vi.fn(),
  checkInvite: vi.fn(),
  forgotPassword: vi.fn(),
  resetPassword: vi.fn(),
  verifyEmail: vi.fn(),
  passkeyLogin: vi.fn(),
  passkeyLoginChallenge: vi.fn(),
}));

const passkeys = vi.hoisted(() => ({
  getCredential: vi.fn(),
  passkeysSupported: vi.fn(() => true),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, ...api };
});

vi.mock("../passkeys", () => passkeys);

const onSignedIn = vi.fn();

function at(url: string): void {
  window.history.pushState({}, "", url);
}

beforeEach(() => {
  vi.clearAllMocks();
  passkeys.passkeysSupported.mockReturnValue(true);
  api.checkInvite.mockResolvedValue({ valid: true, email: null, reason: null });
  at("/");
});

afterEach(() => {
  at("/");
});

describe("signing in", () => {
  it("signs in with the address and password given", async () => {
    const user = userEvent.setup();
    api.login.mockResolvedValue(undefined);
    render(<AuthGate onSignedIn={onSignedIn} />);

    await user.type(screen.getByLabelText("Email"), "athlete@example.com");
    await user.type(screen.getByLabelText("Password"), "a-long-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(api.login).toHaveBeenCalledWith("athlete@example.com", "a-long-password");
    await waitFor(() => expect(onSignedIn).toHaveBeenCalled());
  });

  it("will not submit an empty form", async () => {
    render(<AuthGate onSignedIn={onSignedIn} />);

    expect(screen.getByRole("button", { name: "Sign in" })).toBeDisabled();
  });

  it("shows why a sign-in was refused", async () => {
    const user = userEvent.setup();
    api.login.mockRejectedValue(new Error("nope"));
    render(<AuthGate onSignedIn={onSignedIn} />);

    await user.type(screen.getByLabelText("Email"), "athlete@example.com");
    await user.type(screen.getByLabelText("Password"), "wrong-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("Could not reach the server.")).toBeInTheDocument();
    expect(onSignedIn).not.toHaveBeenCalled();
  });
});

describe("signing in with a passkey", () => {
  it("is not offered on a browser that can't do it", () => {
    passkeys.passkeysSupported.mockReturnValue(false);

    render(<AuthGate onSignedIn={onSignedIn} />);

    expect(
      screen.queryByRole("button", { name: "Sign in with a passkey" }),
    ).not.toBeInTheDocument();
  });

  it("runs the ceremony and signs in", async () => {
    const user = userEvent.setup();
    api.passkeyLoginChallenge.mockResolvedValue({ options: { challenge: "c" }, challenge_handle: "h" });
    passkeys.getCredential.mockResolvedValue({ id: "cred" });
    api.passkeyLogin.mockResolvedValue(undefined);
    render(<AuthGate onSignedIn={onSignedIn} />);

    await user.click(screen.getByRole("button", { name: "Sign in with a passkey" }));

    await waitFor(() => expect(api.passkeyLogin).toHaveBeenCalledWith("h", { id: "cred" }));
    expect(onSignedIn).toHaveBeenCalled();
  });

  it("says nothing when the prompt is dismissed", async () => {
    // Dismissing is a deliberate act, not a failure — reporting it as an
    // error would call the user's own decision a problem.
    const user = userEvent.setup();
    api.passkeyLoginChallenge.mockResolvedValue({ options: {}, challenge_handle: "h" });
    passkeys.getCredential.mockRejectedValue(
      new DOMException("cancelled", "NotAllowedError"),
    );
    render(<AuthGate onSignedIn={onSignedIn} />);

    await user.click(screen.getByRole("button", { name: "Sign in with a passkey" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Sign in with a passkey" })).toBeEnabled(),
    );
    expect(screen.queryByText(/Could not/)).not.toBeInTheDocument();
    expect(onSignedIn).not.toHaveBeenCalled();
  });

  it("does report a genuine failure", async () => {
    const user = userEvent.setup();
    api.passkeyLoginChallenge.mockRejectedValue(new Error("server down"));
    render(<AuthGate onSignedIn={onSignedIn} />);

    await user.click(screen.getByRole("button", { name: "Sign in with a passkey" }));

    expect(await screen.findByText("Could not reach the server.")).toBeInTheDocument();
  });
});

describe("forgotting a password", () => {
  it("answers identically whether or not the address exists", async () => {
    // The one place where showing an error would leak which addresses have
    // accounts, so the failure path has to look exactly like the success one.
    const user = userEvent.setup();
    api.forgotPassword.mockRejectedValue(new Error("no such user"));
    render(<AuthGate onSignedIn={onSignedIn} />);

    await user.click(screen.getByRole("button", { name: "Forgot your password?" }));
    await user.type(screen.getByLabelText("Email"), "stranger@example.com");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));

    expect(await screen.findByText(/If that address has an account/)).toBeInTheDocument();
    expect(screen.queryByText(/Could not/)).not.toBeInTheDocument();
  });

  it("says the same thing when it worked", async () => {
    const user = userEvent.setup();
    api.forgotPassword.mockResolvedValue(undefined);
    render(<AuthGate onSignedIn={onSignedIn} />);

    await user.click(screen.getByRole("button", { name: "Forgot your password?" }));
    await user.type(screen.getByLabelText("Email"), "athlete@example.com");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));

    expect(await screen.findByText(/If that address has an account/)).toBeInTheDocument();
  });

  it("can be backed out of", async () => {
    const user = userEvent.setup();
    render(<AuthGate onSignedIn={onSignedIn} />);

    await user.click(screen.getByRole("button", { name: "Forgot your password?" }));
    await user.click(screen.getByRole("button", { name: "Back to sign in" }));

    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
  });
});

describe("redeeming an invite", () => {
  it("refuses a link with no code in it", async () => {
    at("/register");

    render(<AuthGate onSignedIn={onSignedIn} />);

    expect(await screen.findByText("This link is missing its invite code.")).toBeInTheDocument();
  });

  it("explains a link the server rejected", async () => {
    at("/register?token=spent");
    api.checkInvite.mockResolvedValue({
      valid: false,
      email: null,
      reason: "This invite link is not valid.",
    });

    render(<AuthGate onSignedIn={onSignedIn} />);

    expect(await screen.findByText("This invite link is not valid.")).toBeInTheDocument();
  });

  it("locks the address on an invite bound to one", async () => {
    // Only that address may redeem it, so an editable field would just invite
    // a rejection the person can't do anything about.
    at("/register?token=good");
    api.checkInvite.mockResolvedValue({
      valid: true,
      email: "invitee@example.com",
      reason: null,
    });

    render(<AuthGate onSignedIn={onSignedIn} />);

    const email = await screen.findByLabelText("Email");
    expect(email).toHaveValue("invitee@example.com");
    expect(email).toBeDisabled();
  });

  it("lets an open invite choose its own address", async () => {
    at("/register?token=good");

    render(<AuthGate onSignedIn={onSignedIn} />);

    expect(await screen.findByLabelText("Email")).toBeEnabled();
  });

  it("holds out for a password long enough to be accepted", async () => {
    const user = userEvent.setup();
    at("/register?token=good");
    render(<AuthGate onSignedIn={onSignedIn} />);

    await user.type(await screen.findByLabelText("Email"), "new@example.com");
    await user.type(screen.getByLabelText(/^Password/), "short");

    expect(screen.getByRole("button", { name: "Create account" })).toBeDisabled();
  });

  it("creates the account and takes the token out of the URL", async () => {
    // A spent token left in the address bar survives in history and in
    // anything copied out of it.
    const user = userEvent.setup();
    at("/register?token=good");
    api.register.mockResolvedValue(undefined);
    render(<AuthGate onSignedIn={onSignedIn} />);

    await user.type(await screen.findByLabelText("Email"), "new@example.com");
    await user.type(screen.getByLabelText("Name"), "New Athlete");
    await user.type(screen.getByLabelText(/^Password/), "a-long-enough-password");
    await user.click(screen.getByRole("button", { name: "Create account" }));

    await waitFor(() =>
      expect(api.register).toHaveBeenCalledWith({
        inviteToken: "good",
        email: "new@example.com",
        password: "a-long-enough-password",
        displayName: "New Athlete",
      }),
    );
    expect(onSignedIn).toHaveBeenCalled();
    expect(window.location.search).toBe("");
  });

  it("reports a refused registration", async () => {
    const user = userEvent.setup();
    at("/register?token=good");
    api.register.mockRejectedValue(new Error("taken"));
    render(<AuthGate onSignedIn={onSignedIn} />);

    await user.type(await screen.findByLabelText("Email"), "new@example.com");
    await user.type(screen.getByLabelText(/^Password/), "a-long-enough-password");
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByText("Could not reach the server.")).toBeInTheDocument();
  });
});

describe("resetting a password", () => {
  it("refuses a link with no code in it", () => {
    at("/reset");

    render(<AuthGate onSignedIn={onSignedIn} />);

    expect(screen.getByText("This link is missing its reset code.")).toBeInTheDocument();
  });

  it("sets the password and says what else that did", async () => {
    // A reset signs out every device, which is the point — someone resetting
    // may be locking an attacker out, and needs to know it worked.
    const user = userEvent.setup();
    at("/reset?token=good");
    api.resetPassword.mockResolvedValue(undefined);
    render(<AuthGate onSignedIn={onSignedIn} />);

    await user.type(screen.getByLabelText(/^New password/), "a-brand-new-password");
    await user.click(screen.getByRole("button", { name: "Set password" }));

    await waitFor(() =>
      expect(api.resetPassword).toHaveBeenCalledWith("good", "a-brand-new-password"),
    );
    expect(await screen.findByText(/every device that was signed in has been signed out/))
      .toBeInTheDocument();
    expect(window.location.search).toBe("");
  });

  it("reports a link the server refused", async () => {
    const user = userEvent.setup();
    at("/reset?token=expired");
    api.resetPassword.mockRejectedValue(new Error("expired"));
    render(<AuthGate onSignedIn={onSignedIn} />);

    await user.type(screen.getByLabelText(/^New password/), "a-brand-new-password");
    await user.click(screen.getByRole("button", { name: "Set password" }));

    expect(await screen.findByText("Could not reach the server.")).toBeInTheDocument();
  });
});

describe("confirming an address", () => {
  it("confirms it on arrival", async () => {
    at("/verify?token=good");
    api.verifyEmail.mockResolvedValue(undefined);

    render(<AuthGate onSignedIn={onSignedIn} />);

    await waitFor(() => expect(api.verifyEmail).toHaveBeenCalledWith("good"));
    expect(await screen.findByText("Your email address is confirmed.")).toBeInTheDocument();
  });

  it("refuses a link with no code in it", async () => {
    at("/verify");

    render(<AuthGate onSignedIn={onSignedIn} />);

    expect(
      await screen.findByText("This link is missing its confirmation code."),
    ).toBeInTheDocument();
  });

  it("explains a link that no longer works", async () => {
    at("/verify?token=expired");
    api.verifyEmail.mockRejectedValue(new Error("expired"));

    render(<AuthGate onSignedIn={onSignedIn} />);

    expect(await screen.findByText("Could not reach the server.")).toBeInTheDocument();
  });
});
