import { useEffect, useRef } from "react";

/**
 * Keep the screen on while the clock is the thing being watched.
 *
 * A gym timer is looked at, not touched: a phone propped against a barbell
 * sees no input for the length of an AMRAP and locks itself mid-workout. The
 * Screen Wake Lock API is how a page says "what's on screen is time-sensitive"
 * — the browser's supported channel for it, rather than a trick like looping a
 * silent video.
 *
 * Two things about the lock shape this hook. It's only granted to a *visible*
 * page, and it's dropped automatically the moment the page is hidden — so
 * coming back from a locked screen or another tab means asking again, which is
 * what the visibilitychange listener is for. And it can be refused outright
 * (unsupported browser, low battery); that's not an error worth surfacing,
 * since every phone still behaves exactly as it did before — the clock runs,
 * the screen just dims on its own schedule.
 */
export function useWakeLock(active: boolean) {
  const sentinelRef = useRef<WakeLockSentinel | null>(null);

  useEffect(() => {
    if (!active || !("wakeLock" in navigator)) return;

    // Guards the gap between asking for a lock and being granted one: the
    // clock can stop, or the view unmount, while the request is in flight.
    let cancelled = false;

    async function acquire() {
      if (cancelled || sentinelRef.current || document.visibilityState !== "visible") return;
      try {
        const sentinel = await navigator.wakeLock.request("screen");
        if (cancelled) {
          await sentinel.release();
          return;
        }
        sentinelRef.current = sentinel;
        // The browser can drop the lock on its own (page hidden, battery
        // saver). Forget it when that happens so the next chance re-asks
        // rather than holding a sentinel that no longer does anything.
        sentinel.addEventListener("release", () => {
          if (sentinelRef.current === sentinel) sentinelRef.current = null;
        });
      } catch {
        /* Refused — nothing to recover from, the clock is unaffected. */
      }
    }

    void acquire();
    document.addEventListener("visibilitychange", acquire);

    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", acquire);
      const sentinel = sentinelRef.current;
      sentinelRef.current = null;
      void sentinel?.release().catch(() => {});
    };
  }, [active]);
}
