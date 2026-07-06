#!/usr/bin/env node

// Thin entry point. Its ONLY job is to install synchronous crash handlers
// BEFORE any heavy module is evaluated, then hand off to the real CLI via a
// dynamic import.
//
// Why this exists: Bun buffers stdout/stderr to a pipe (as in CI) and does NOT
// flush that buffer when the process exits abnormally. A failure thrown while
// the module graph is still being *imported* — before the CLI action installs
// its own synchronous console (see cli-main.ts) — therefore vanished, leaving a
// completely empty CI log (the failure that took days to diagnose because there
// was nothing to look at). ESM hoists all static imports, so handlers declared
// at the top of a module that also statically imports the heavy code would be
// installed too late. Loading the real CLI through `import()` keeps the heavy
// module graph out of this file's static imports, so these handlers are already
// armed when it (and everything it pulls in) is evaluated. Any throw or
// rejection is then written synchronously to a real fd and can't be buffered
// away.

import { writeSync } from "node:fs";

/** Write to stderr, falling back to stdout, never throwing. */
function writeFatal(s: string): void {
  try {
    writeSync(2, s);
  } catch {
    try {
      writeSync(1, s);
    } catch {
      /* nothing else we can do */
    }
  }
}

function report(kind: string, err: unknown): void {
  const detail =
    err instanceof Error ? (err.stack ?? `${err.name}: ${err.message}`) : String(err);
  writeFatal(`\n[hodor:fatal] ${kind}\n${detail}\n`);
}

process.on("uncaughtException", (err) => {
  report("uncaughtException", err);
  process.exit(1);
});
process.on("unhandledRejection", (reason) => {
  report("unhandledRejection", reason);
  process.exit(1);
});

// Load and run the real CLI. A rejection here means the module graph failed to
// evaluate (an import-time error) — exactly the case that used to produce an
// empty log.
import("./cli-main.js").catch((err) => {
  report("startup", err);
  process.exit(1);
});
