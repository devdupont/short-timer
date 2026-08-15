import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Testing Library registers its own cleanup only when vitest runs with
// `globals: true`. This suite imports `describe`/`it`/`expect` explicitly
// instead, so the unmount has to be wired up here — without it, every render
// stacks up in the same document and queries start matching the previous
// test's markup.
afterEach(cleanup);
