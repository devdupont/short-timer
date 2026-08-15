import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useWakeLock } from "./useWakeLock";

/**
 * Keeping the screen awake while the clock is being watched.
 *
 * Everything here is about *not* holding a lock that no longer does anything.
 * The browser drops the lock whenever the page is hidden, so a hook that
 * assumed it still held one would never re-ask, and the screen would start
 * sleeping mid-workout with nothing to indicate why.
 */

type Listener = () => void;

interface FakeSentinel {
  release: ReturnType<typeof vi.fn>;
  addEventListener: (type: string, fn: Listener) => void;
  drop: () => void;
}

function sentinel(): FakeSentinel {
  const listeners: Listener[] = [];
  return {
    release: vi.fn(async () => {}),
    addEventListener: (_type: string, fn: Listener) => listeners.push(fn),
    drop: () => listeners.forEach((fn) => fn()),
  };
}

let request: ReturnType<typeof vi.fn>;

/** Pretend the page is visible or hidden, as the browser reports it. */
function setVisibility(state: "visible" | "hidden"): void {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => state,
  });
}

beforeEach(() => {
  request = vi.fn(async () => sentinel());
  Object.defineProperty(navigator, "wakeLock", {
    configurable: true,
    writable: true,
    value: { request },
  });
  setVisibility("visible");
});

afterEach(() => {
  delete (navigator as { wakeLock?: unknown }).wakeLock;
  setVisibility("visible");
});

/** Let the in-flight `request` promise settle. */
async function settle(): Promise<void> {
  await vi.waitFor(() => {});
}

describe("asking for the lock", () => {
  it("asks once the clock is running", async () => {
    renderHook(() => useWakeLock(true));
    await settle();

    expect(request).toHaveBeenCalledWith("screen");
  });

  it("does not ask while the clock is idle", async () => {
    renderHook(() => useWakeLock(false));
    await settle();

    expect(request).not.toHaveBeenCalled();
  });

  it("does nothing on a browser without the API", async () => {
    // Refusal isn't an error worth surfacing — the clock is unaffected and
    // the screen simply dims on its own schedule, as it always did.
    delete (navigator as { wakeLock?: unknown }).wakeLock;

    expect(() => renderHook(() => useWakeLock(true))).not.toThrow();
  });

  it("does not ask while the page is hidden, because it would be refused", async () => {
    setVisibility("hidden");

    renderHook(() => useWakeLock(true));
    await settle();

    expect(request).not.toHaveBeenCalled();
  });

  it("carries on when the browser refuses", async () => {
    // Low battery and battery-saver modes both refuse.
    request.mockRejectedValue(new Error("denied"));

    const { unmount } = renderHook(() => useWakeLock(true));
    await settle();

    expect(() => unmount()).not.toThrow();
  });
});

describe("giving it back", () => {
  it("releases the lock when the clock stops", async () => {
    const held = sentinel();
    request.mockResolvedValue(held);

    const { rerender } = renderHook(({ active }) => useWakeLock(active), {
      initialProps: { active: true },
    });
    await settle();

    rerender({ active: false });

    expect(held.release).toHaveBeenCalled();
  });

  it("releases the lock when the view goes away", async () => {
    const held = sentinel();
    request.mockResolvedValue(held);

    const { unmount } = renderHook(() => useWakeLock(true));
    await settle();

    unmount();

    expect(held.release).toHaveBeenCalled();
  });

  it("releases a lock granted after the hook had already gone", async () => {
    // The gap between asking and being granted is real: unmounting mid-flight
    // would otherwise leak a lock nothing holds a reference to any more.
    const held = sentinel();
    let grant: (value: FakeSentinel) => void = () => {};
    request.mockReturnValue(
      new Promise<FakeSentinel>((resolve) => {
        grant = resolve;
      }),
    );

    const { unmount } = renderHook(() => useWakeLock(true));
    unmount();
    grant(held);
    await settle();

    expect(held.release).toHaveBeenCalled();
  });
});

describe("when the browser takes it back on its own", () => {
  it("asks again after the page becomes visible", async () => {
    // The lock is dropped whenever the page is hidden, so returning from a
    // locked phone or another tab has to re-acquire or the screen sleeps.
    const held = sentinel();
    request.mockResolvedValue(held);

    renderHook(() => useWakeLock(true));
    await settle();
    expect(request).toHaveBeenCalledTimes(1);

    held.drop();
    document.dispatchEvent(new Event("visibilitychange"));
    await settle();

    expect(request).toHaveBeenCalledTimes(2);
  });

  it("does not stack a second lock while one is still held", async () => {
    const held = sentinel();
    request.mockResolvedValue(held);

    renderHook(() => useWakeLock(true));
    await settle();

    document.dispatchEvent(new Event("visibilitychange"));
    await settle();

    expect(request).toHaveBeenCalledTimes(1);
  });

  it("stops listening once the view is gone", async () => {
    const { unmount } = renderHook(() => useWakeLock(true));
    await settle();
    unmount();
    request.mockClear();

    document.dispatchEvent(new Event("visibilitychange"));
    await settle();

    expect(request).not.toHaveBeenCalled();
  });
});
