# short-timer web

React + Vite + TypeScript frontend for short-timer. See the [root
README](../README.md) for the full setup (backend, MongoDB, env vars).

```bash
npm install
cp .env.example .env   # VITE_API_BASE_URL, defaults to http://localhost:8000
npm run dev            # http://localhost:5173
npm run build          # type-check + production bundle
npm run lint           # oxlint
npm test               # vitest — unit tests for the timer plan
```

## Running on a phone

The clock is meant to be watched, not touched, which puts it at odds with two
things a phone does on its own. It takes a screen wake lock while a workout is
counting (and in TV mode) so the display doesn't lock mid-AMRAP — see
`hooks/useWakeLock.ts`. And it treats coming back into view, or any tap, as a
chance to resume its `AudioContext`: iOS parks one when the screen locks, in a
WebKit-only "interrupted" state, and a parked context plays nothing for the
rest of the session — see `hooks/useTimerAudio.ts`.
