import { useState } from "react";
import { ApiError, login } from "../api";

export function PasscodeGate({ onUnlocked }: { onUnlocked: () => void }) {
  const [passcode, setPasscode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(passcode);
      onUnlocked();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the server.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="passcode-gate">
      <form onSubmit={handleSubmit} className="passcode-form">
        <h1>short-timer</h1>
        <p>Enter the passcode to continue.</p>
        <input
          type="password"
          autoFocus
          value={passcode}
          onChange={(e) => setPasscode(e.target.value)}
          placeholder="Passcode"
        />
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={submitting || !passcode}>
          {submitting ? "Checking…" : "Unlock"}
        </button>
      </form>
    </div>
  );
}
