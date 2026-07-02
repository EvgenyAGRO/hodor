import { describe, it, expect } from "vitest";
import {
  normalizeForComparison,
  extractTitle,
  similarityScore,
  isDuplicateFinding,
  deduplicateFindings,
} from "../src/duplicate-detector.js";

describe("normalizeForComparison", () => {
  it("strips whitespace", () => {
    expect(normalizeForComparison("  Fix the bug  ")).toBe(normalizeForComparison("Fix the bug"));
  });

  it("is case-insensitive", () => {
    expect(normalizeForComparison("Fix The Bug")).toBe(normalizeForComparison("fix the bug"));
  });

  it("removes markdown bold", () => {
    expect(normalizeForComparison("**[P1] Fix this**")).toBe(normalizeForComparison("[P1] Fix this"));
  });

  it("removes markdown italic", () => {
    expect(normalizeForComparison("*important* issue")).toBe(normalizeForComparison("important issue"));
  });

  it("collapses multiple spaces", () => {
    expect(normalizeForComparison("Fix   the    bug")).toBe(normalizeForComparison("Fix the bug"));
  });

  it("normalizes newlines to spaces", () => {
    expect(normalizeForComparison("Fix\nthe\nbug")).toBe(normalizeForComparison("Fix the bug"));
  });

  it("normalizes priority prefix casing", () => {
    expect(normalizeForComparison("[P1] Fix this")).toBe(normalizeForComparison("[p1] fix this"));
  });
});

describe("extractTitle", () => {
  it("extracts title from a bold header", () => {
    expect(extractTitle("**[P1] Fix null pointer**\n\nThis causes a crash when...")).toBe(
      "[P1] Fix null pointer",
    );
  });

  it("extracts title from plain text", () => {
    expect(extractTitle("[P2] Memory leak in handler\n\nThe connection is not closed...")).toBe(
      "[P2] Memory leak in handler",
    );
  });

  it("extracts title with no priority prefix", () => {
    expect(extractTitle("Missing error handling\n\nThe function does not...")).toBe(
      "Missing error handling",
    );
  });
});

describe("similarityScore", () => {
  it("scores identical texts as 100", () => {
    const text = "Fix the SQL injection bug";
    expect(similarityScore(text, text)).toBe(100);
  });

  it("scores completely different texts low", () => {
    expect(similarityScore("Fix the SQL injection bug", "Memory allocation failed")).toBeLessThan(50);
  });

  it("scores similar texts high", () => {
    expect(
      similarityScore("SQL injection vulnerability detected", "SQL injection risk detected"),
    ).toBeGreaterThan(70);
  });

  it("handles empty text", () => {
    expect(similarityScore("", "")).toBe(100);
    expect(similarityScore("text", "")).toBe(0);
    expect(similarityScore("", "text")).toBe(0);
  });
});

describe("isDuplicateFinding", () => {
  it("detects an exact duplicate", () => {
    const existing = [
      { path: "src/auth.py", line: 42, body: "**[P1] SQL injection risk**\n\nUse parameterized queries." },
    ];
    const newFinding = {
      path: "src/auth.py",
      line: 42,
      title: "[P1] SQL injection risk",
      body: "Use parameterized queries.",
    };
    expect(isDuplicateFinding(newFinding, existing)).toBe(true);
  });

  it("detects a case-variant duplicate", () => {
    const existing = [
      { path: "src/auth.py", line: 42, body: "**[P1] SQL Injection Risk**\n\nUse parameterized queries." },
    ];
    const newFinding = {
      path: "src/auth.py",
      line: 42,
      title: "[P1] sql injection risk",
      body: "use parameterized queries.",
    };
    expect(isDuplicateFinding(newFinding, existing)).toBe(true);
  });

  it("detects a whitespace-variant duplicate", () => {
    const existing = [{ path: "src/auth.py", line: 42, body: "**[P1] SQL  injection   risk**\n\nUse queries." }];
    const newFinding = { path: "src/auth.py", line: 42, title: "[P1] SQL injection risk", body: "Use queries." };
    expect(isDuplicateFinding(newFinding, existing)).toBe(true);
  });

  it("does not match a different file", () => {
    const existing = [{ path: "src/auth.py", line: 42, body: "**[P1] SQL injection risk**\n\nUse queries." }];
    const newFinding = {
      path: "src/database.py",
      line: 42,
      title: "[P1] SQL injection risk",
      body: "Use queries.",
    };
    expect(isDuplicateFinding(newFinding, existing)).toBe(false);
  });

  it("matches a nearby line within the proximity threshold", () => {
    const existing = [{ path: "src/auth.py", line: 42, body: "**[P1] SQL injection risk**\n\nUse queries." }];
    const newFinding = { path: "src/auth.py", line: 45, title: "[P1] SQL injection risk", body: "Use queries." };
    expect(isDuplicateFinding(newFinding, existing)).toBe(true);
  });

  it("does not match a far-away line", () => {
    const existing = [{ path: "src/auth.py", line: 42, body: "**[P1] SQL injection risk**\n\nUse queries." }];
    const newFinding = { path: "src/auth.py", line: 100, title: "[P1] SQL injection risk", body: "Use queries." };
    expect(isDuplicateFinding(newFinding, existing)).toBe(false);
  });

  it("matches a similar (fuzzy) title", () => {
    const existing = [
      { path: "src/auth.py", line: 42, body: "**[P1] SQL injection vulnerability**\n\nUse queries." },
    ];
    const newFinding = { path: "src/auth.py", line: 42, title: "[P1] SQL injection risk", body: "Use queries." };
    expect(isDuplicateFinding(newFinding, existing)).toBe(true);
  });

  it("does not match a completely different title", () => {
    const existing = [{ path: "src/auth.py", line: 42, body: "**[P1] SQL injection risk**\n\nUse queries." }];
    const newFinding = {
      path: "src/auth.py",
      line: 42,
      title: "[P2] Memory leak detected",
      body: "Close the connection.",
    };
    expect(isDuplicateFinding(newFinding, existing)).toBe(false);
  });

  it("returns false when there are no existing comments", () => {
    const newFinding = { path: "src/auth.py", line: 42, title: "[P1] SQL injection risk", body: "Use queries." };
    expect(isDuplicateFinding(newFinding, [])).toBe(false);
  });
});

describe("deduplicateFindings", () => {
  it("removes duplicates within the same batch", () => {
    const findings = [
      { path: "a.py", line: 10, title: "[P1] Bug A", body: "Fix it" },
      { path: "a.py", line: 10, title: "[P1] Bug A", body: "Fix it" },
      { path: "a.py", line: 11, title: "[P1] bug a", body: "fix it" },
      { path: "b.py", line: 20, title: "[P2] Bug B", body: "Another" },
    ];

    const unique = deduplicateFindings(findings, []);

    expect(unique).toHaveLength(2);
    expect(unique[0].title).toBe("[P1] Bug A");
    expect(unique[1].title).toBe("[P2] Bug B");
  });

  it("removes findings matching an existing comment", () => {
    const existing = [{ path: "a.py", line: 10, body: "**[P1] Existing bug**\n\nAlready reported." }];
    const findings = [
      { path: "a.py", line: 10, title: "[P1] Existing bug", body: "Already reported." },
      { path: "b.py", line: 20, title: "[P2] New bug", body: "Fresh finding." },
    ];

    const unique = deduplicateFindings(findings, existing);

    expect(unique).toHaveLength(1);
    expect(unique[0].title).toBe("[P2] New bug");
  });

  it("preserves original order", () => {
    const findings = [
      { path: "c.py", line: 30, title: "[P3] Bug C", body: "Third" },
      { path: "a.py", line: 10, title: "[P1] Bug A", body: "First" },
      { path: "b.py", line: 20, title: "[P2] Bug B", body: "Second" },
    ];

    const unique = deduplicateFindings(findings, []);

    expect(unique.map((f) => f.title)).toEqual(["[P3] Bug C", "[P1] Bug A", "[P2] Bug B"]);
  });
});
