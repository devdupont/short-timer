/**
 * Reading the emailed links off the address bar.
 *
 * The links (`/register?token=`, `/reset?token=`, `/verify?token=`) are read
 * straight from `location` rather than routed, because this app has no router
 * — one reader is smaller than adding one, and these are the only addresses
 * that exist besides `/`.
 *
 * This lives apart from `AuthGate` because `App` needs it too, and a component
 * file that also exports plain functions loses fast refresh.
 */

/** Which screen the visitor lands on. */
export type Screen = "login" | "register" | "forgot" | "reset" | "verify";

export function readLocation(): { screen: Screen; token: string | null } {
  const params = new URLSearchParams(window.location.search);
  const token = params.get("token");
  const path = window.location.pathname.replace(/\/+$/, "");

  if (path.endsWith("/register")) return { screen: "register", token };
  if (path.endsWith("/reset")) return { screen: "reset", token };
  if (path.endsWith("/verify")) return { screen: "verify", token };
  return { screen: "login", token: null };
}

/** Drop the token from the address bar once it's been used or read. */
export function clearUrl() {
  window.history.replaceState({}, "", "/");
}

/**
 * Whether this URL is an emailed link waiting to be finished.
 *
 * `App` needs this because the gate only ever mounted for signed-out visitors,
 * so someone with a live session who clicked their confirmation link landed on
 * the app and the token was silently dropped — leaving an address that could
 * never be confirmed, since every new link behaved the same way.
 */
export function hasPendingAuthLink(): boolean {
  const { screen } = readLocation();
  return screen === "register" || screen === "reset" || screen === "verify";
}
