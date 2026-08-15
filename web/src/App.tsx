import { useCallback, useEffect, useState } from "react";
import "./App.css";
import { Admin } from "./components/Admin";
import { AuthGate } from "./components/AuthGate";
import { hasPendingAuthLink } from "./authLinks";
import { Home } from "./components/Home";
import { Settings } from "./components/Settings";
import { TimerView } from "./components/TimerView";
import { WorkoutBuilder } from "./components/WorkoutBuilder";
import { WorkoutImport } from "./components/WorkoutImport";
import { WorkoutLibrary } from "./components/WorkoutLibrary";
import type { EditTarget } from "./components/WorkoutBuilder";
import { getMe, logout } from "./api";
import type { Role, Workout } from "./types";

type Tab = "home" | "import" | "build" | "library" | "timer" | "settings" | "admin";

function App() {
  // `null` means "we haven't asked yet". Without that third state the app
  // flashes the sign-in screen on every reload, which it used to do: the
  // session cookie was never checked at startup, so a signed-in user was
  // asked to sign in again each time they opened the page.
  const [signedIn, setSignedIn] = useState<boolean | null>(null);
  // Only the role is kept, not the whole `Me`: the admin tab is the one thing
  // out here that depends on it, and Settings fetches its own copy anyway.
  const [role, setRole] = useState<Role | null>(null);
  const [tab, setTab] = useState<Tab>("home");
  const [activeWorkout, setActiveWorkout] = useState<Workout | null>(null);
  const [libraryRefreshKey, setLibraryRefreshKey] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<EditTarget | null>(null);
  // Read once, at mount: the gate clears the token from the URL as it works,
  // so re-reading `location` later would say there's nothing pending.
  const [authLink, setAuthLink] = useState(hasPendingAuthLink);

  // Shared by the startup check and the sign-in callback, so a fresh sign-in
  // picks up the role too — keying an effect on `signedIn` instead would
  // re-fetch on the transition it just caused.
  const loadMe = useCallback(async () => {
    try {
      const me = await getMe();
      setRole(me.role);
      setSignedIn(true);
    } catch {
      setRole(null);
      setSignedIn(false);
    }
  }, []);

  useEffect(() => {
    void loadMe();
  }, [loadMe]);

  if (signedIn === null) {
    return <div className="passcode-gate" />;
  }
  // An emailed link wins over an existing session: arriving at one is a
  // deliberate act, and the alternative is dropping it on the floor.
  if (!signedIn || authLink) {
    return (
      <AuthGate
        onSignedIn={() => {
          setAuthLink(false);
          void loadMe();
        }}
      />
    );
  }

  function handleSaved(workout: Workout) {
    setLibraryRefreshKey((k) => k + 1);
    setActiveWorkout(workout);
    setEditTarget(null);
    setTab("timer");
  }

  /** Open a workout in the builder so it can be tweaked before timing it. */
  function handleEdit(target: EditTarget) {
    setEditTarget(target);
    setTab("build");
  }

  // Load into the timer without touching the library (used for unsaved previews).
  function handleLoadOnly(workout: Workout) {
    setActiveWorkout(workout);
    setTab("timer");
  }

  // Selecting anything from the nav also closes the mobile menu.
  function selectTab(next: Tab) {
    setTab(next);
    setMenuOpen(false);
  }

  const tabs: { id: Tab; label: string }[] = [
    { id: "home", label: "Home" },
    { id: "import", label: "Paste" },
    { id: "build", label: "Build" },
    { id: "library", label: "Library" },
    ...(activeWorkout ? [{ id: "timer" as Tab, label: "Timer" }] : []),
    { id: "settings", label: "Settings" },
    // The endpoints behind this 404 for everyone else, so a visible tab that
    // always failed would read as a bug rather than as a closed door.
    ...(role === "admin" ? [{ id: "admin" as Tab, label: "Admin" }] : []),
  ];

  return (
    <div className="app">
      <header className="app-header">
        <h1>shortimer</h1>

        <button
          className={`hamburger ${menuOpen ? "open" : ""}`}
          aria-label="Menu"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((o) => !o)}
        >
          <span />
          <span />
          <span />
        </button>

        {menuOpen && <div className="nav-backdrop" onClick={() => setMenuOpen(false)} />}

        <div className={`nav-group ${menuOpen ? "open" : ""}`}>
          <nav className="tabs">
            {tabs.map(({ id, label }) => (
              <button
                key={id}
                className={tab === id ? "active" : ""}
                onClick={() => selectTab(id)}
              >
                {label}
              </button>
            ))}
          </nav>
          <button
            className="logout-button"
            onClick={() => {
              logout();
              setMenuOpen(false);
              setRole(null);
              setTab("home");
              setSignedIn(false);
            }}
          >
            Sign out
          </button>
        </div>
      </header>

      <main>
        {tab === "home" && (
          <Home
            onLoad={handleSaved}
            onEdit={handleEdit}
            onOpenSettings={() => setTab("settings")}
          />
        )}
        {tab === "import" && <WorkoutImport onSaved={handleSaved} onLoad={handleLoadOnly} />}
        {tab === "build" && (
          // Remount when the edit target changes so the form re-seeds from it.
          <WorkoutBuilder
            key={editTarget?.workout.id ?? "new"}
            onSaved={handleSaved}
            editTarget={editTarget}
            onCancelEdit={() => setEditTarget(null)}
          />
        )}
        {tab === "library" && (
          <WorkoutLibrary
            refreshKey={libraryRefreshKey}
            onSelect={(workout) => {
              setActiveWorkout(workout);
              setTab("timer");
            }}
            onEdit={(workout) => handleEdit({ workout, saved: true })}
          />
        )}
        {tab === "timer" && activeWorkout && <TimerView workout={activeWorkout} />}
        {tab === "settings" && <Settings />}
        {tab === "admin" && role === "admin" && <Admin />}
      </main>
    </div>
  );
}

export default App;
