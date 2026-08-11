import { useEffect, useState } from "react";
import {
  ApiError,
  checkInvite,
  forgotPassword,
  login,
  passkeyLogin,
  passkeyLoginChallenge,
  register,
  resetPassword,
  verifyEmail,
} from "../api";
import { getCredential, passkeysSupported } from "../passkeys";
import { clearUrl, readLocation } from "../authLinks";
import type { Screen } from "../authLinks";
import type { InviteCheck } from "../types";

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : "Could not reach the server.";
}

export function AuthGate({ onSignedIn }: { onSignedIn: () => void }) {
  const initial = readLocation();
  const [screen, setScreen] = useState<Screen>(initial.screen);
  const [token] = useState<string | null>(initial.token);

  return (
    <div className="passcode-gate">
      <div className="form-card auth-card">
        <div className="auth-head">
          <h1>shortimer</h1>
        </div>
        {screen === "login" && (
          <LoginForm onSignedIn={onSignedIn} onForgot={() => setScreen("forgot")} />
        )}
        {screen === "register" && <RegisterForm token={token} onSignedIn={onSignedIn} />}
        {screen === "forgot" && <ForgotForm onBack={() => setScreen("login")} />}
        {screen === "reset" && <ResetForm token={token} onDone={() => setScreen("login")} />}
        {screen === "verify" && <VerifyScreen token={token} onSignedIn={onSignedIn} />}
      </div>
    </div>
  );
}

function LoginForm({
  onSignedIn,
  onForgot,
}: {
  onSignedIn: () => void;
  onForgot: () => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(email, password);
      onSignedIn();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function handlePasskey() {
    setSubmitting(true);
    setError(null);
    try {
      const challenge = await passkeyLoginChallenge();
      const credential = await getCredential(challenge.options);
      await passkeyLogin(challenge.challenge_handle, credential);
      onSignedIn();
    } catch (err) {
      // A cancelled prompt throws too, and telling someone their deliberate
      // cancellation was an error is just noise.
      if (err instanceof DOMException && err.name === "NotAllowedError") {
        setError(null);
      } else {
        setError(errorText(err));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <p className="section-sub">Sign in to your account.</p>
      <label className="field">
        <span className="field-label">Email</span>
        <input
          type="email"
          autoComplete="username"
          autoFocus
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
      </label>
      <label className="field">
        <span className="field-label">Password</span>
        <input
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </label>
      {error && <p className="error">{error}</p>}
      <button
        type="submit"
        className="primary-button"
        disabled={submitting || !email || !password}
      >
        {submitting ? "Signing in…" : "Sign in"}
      </button>
      {passkeysSupported() && (
        <button
          type="button"
          className="secondary-button"
          disabled={submitting}
          onClick={handlePasskey}
        >
          Sign in with a passkey
        </button>
      )}
      <button type="button" className="link-button" onClick={onForgot}>
        Forgot your password?
      </button>
    </form>
  );
}

function RegisterForm({
  token,
  onSignedIn,
}: {
  token: string | null;
  onSignedIn: () => void;
}) {
  const [check, setCheck] = useState<InviteCheck | null>(null);
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!token) {
      setCheck({ valid: false, email: null, reason: "This link is missing its invite code." });
      return;
    }
    let cancelled = false;
    checkInvite(token)
      .then((result) => {
        if (cancelled) return;
        setCheck(result);
        // An address-bound invite fills the field in; it can't be changed,
        // because only that address may redeem it.
        if (result.email) setEmail(result.email);
      })
      .catch(() => {
        if (!cancelled) {
          setCheck({ valid: false, email: null, reason: "Could not reach the server." });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    setSubmitting(true);
    setError(null);
    try {
      await register({ inviteToken: token, email, password, displayName });
      clearUrl();
      onSignedIn();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setSubmitting(false);
    }
  }

  if (check === null) return <p className="section-sub">Checking your invite…</p>;
  if (!check.valid) {
    return (
      <>
        <p className="error">{check.reason}</p>
        <a className="link-button" href="/">
          Go to sign in
        </a>
      </>
    );
  }

  return (
    <form onSubmit={handleSubmit}>
      <p className="section-sub">You've been invited. Set up your account.</p>
      <label className="field">
        <span className="field-label">Email</span>
        <input
          type="email"
          autoComplete="username"
          value={email}
          // Locked for a bound invite: another address would be refused anyway.
          disabled={Boolean(check.email)}
          onChange={(e) => setEmail(e.target.value)}
        />
      </label>
      <label className="field">
        <span className="field-label">Name</span>
        <input
          type="text"
          autoComplete="name"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
        />
      </label>
      <label className="field">
        <span className="field-label">Password</span>
        <input
          type="password"
          autoComplete="new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <span className="field-hint">At least 12 characters. A passphrase works well.</span>
      </label>
      {error && <p className="error">{error}</p>}
      <button
        type="submit"
        className="primary-button"
        disabled={submitting || !email || password.length < 12}
      >
        {submitting ? "Creating…" : "Create account"}
      </button>
    </form>
  );
}

function ForgotForm({ onBack }: { onBack: () => void }) {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await forgotPassword(email);
    } catch {
      // Deliberately ignored. The server answers identically whether or not
      // the address has an account, so surfacing a failure here would be the
      // one thing that tells an attacker which addresses are real.
    } finally {
      setSubmitting(false);
      setSent(true);
    }
  }

  if (sent) {
    return (
      <>
        <p className="section-sub">
          If that address has an account, a reset link is on its way. It expires in an hour.
        </p>
        <button type="button" className="link-button" onClick={onBack}>
          Back to sign in
        </button>
      </>
    );
  }

  return (
    <form onSubmit={handleSubmit}>
      <p className="section-sub">We'll email you a link to set a new password.</p>
      <label className="field">
        <span className="field-label">Email</span>
        <input
          type="email"
          autoComplete="username"
          autoFocus
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
      </label>
      <button type="submit" className="primary-button" disabled={submitting || !email}>
        {submitting ? "Sending…" : "Send reset link"}
      </button>
      <button type="button" className="link-button" onClick={onBack}>
        Back to sign in
      </button>
    </form>
  );
}

function ResetForm({ token, onDone }: { token: string | null; onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    setSubmitting(true);
    setError(null);
    try {
      await resetPassword(token, password);
      clearUrl();
      setDone(true);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setSubmitting(false);
    }
  }

  if (!token) return <p className="error">This link is missing its reset code.</p>;
  if (done) {
    return (
      <>
        <p className="section-sub">
          Your password is set, and every device that was signed in has been signed out.
        </p>
        <button type="button" className="primary-button" onClick={onDone}>
          Sign in
        </button>
      </>
    );
  }

  return (
    <form onSubmit={handleSubmit}>
      <p className="section-sub">Choose a new password.</p>
      <label className="field">
        <span className="field-label">New password</span>
        <input
          type="password"
          autoComplete="new-password"
          autoFocus
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <span className="field-hint">At least 12 characters.</span>
      </label>
      {error && <p className="error">{error}</p>}
      <button
        type="submit"
        className="primary-button"
        disabled={submitting || password.length < 12}
      >
        {submitting ? "Saving…" : "Set password"}
      </button>
    </form>
  );
}

function VerifyScreen({ token, onSignedIn }: { token: string | null; onSignedIn: () => void }) {
  const [state, setState] = useState<"working" | "done" | "failed">("working");
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!token) {
      setState("failed");
      setMessage("This link is missing its confirmation code.");
      return;
    }
    let cancelled = false;
    verifyEmail(token)
      .then(() => {
        if (cancelled) return;
        clearUrl();
        setState("done");
      })
      .catch((err) => {
        if (cancelled) return;
        setState("failed");
        setMessage(errorText(err));
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  if (state === "working") return <p className="section-sub">Confirming your address…</p>;
  if (state === "failed") {
    return (
      <>
        <p className="error">{message}</p>
        <a className="link-button" href="/">
          Go to sign in
        </a>
      </>
    );
  }
  return (
    <>
      <p className="section-sub">Your email address is confirmed.</p>
      <button type="button" className="primary-button" onClick={onSignedIn}>
        Continue
      </button>
    </>
  );
}
