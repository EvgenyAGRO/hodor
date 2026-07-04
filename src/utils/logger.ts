import { writeSync } from "node:fs";
import chalk from "chalk";

export type LogLevel = "debug" | "info" | "warn" | "error";

/**
 * Write to stderr synchronously (fd 2). Bun (and Node, to a pipe/file) buffers
 * `process.stderr.write` and does NOT flush that buffer on `process.exit()`,
 * so a fast failure loses all its output — which is exactly what made a CI
 * failure show up as an empty log. A synchronous fd write can't be buffered
 * away, so every line is durably captured even if the process exits or is
 * killed immediately after.
 */
export function writeStderrSync(s: string): void {
  try {
    writeSync(2, s);
  } catch {
    // Fall back to the stream if the fd write ever fails (e.g. EPIPE).
    process.stderr.write(s);
  }
}

/** Synchronous stdout (fd 1) counterpart to writeStderrSync. */
export function writeStdoutSync(s: string): void {
  try {
    writeSync(1, s);
  } catch {
    process.stdout.write(s);
  }
}

let currentLevel: LogLevel = "warn";

const LEVELS: Record<LogLevel, number> = {
  debug: 0,
  info: 1,
  warn: 2,
  error: 3,
};

export function setLogLevel(level: LogLevel): void {
  currentLevel = level;
}

function shouldLog(level: LogLevel): boolean {
  return LEVELS[level] >= LEVELS[currentLevel];
}

function timestamp(): string {
  return new Date().toISOString();
}

export const logger = {
  debug(msg: string): void {
    if (shouldLog("debug")) {
      writeStderrSync(`${chalk.gray(timestamp())} ${chalk.gray("DEBUG")} ${msg}\n`);
    }
  },
  info(msg: string): void {
    if (shouldLog("info")) {
      writeStderrSync(`${chalk.gray(timestamp())} ${chalk.blue("INFO")}  ${msg}\n`);
    }
  },
  warn(msg: string): void {
    if (shouldLog("warn")) {
      writeStderrSync(`${chalk.gray(timestamp())} ${chalk.yellow("WARN")}  ${msg}\n`);
    }
  },
  error(msg: string): void {
    if (shouldLog("error")) {
      writeStderrSync(`${chalk.gray(timestamp())} ${chalk.red("ERROR")} ${msg}\n`);
    }
  },
};
