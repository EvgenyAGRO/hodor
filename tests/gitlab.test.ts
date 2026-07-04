import { describe, it, expect } from "vitest";
import { parseGlabPaginatedJson, summarizeGitlabNotes, parseDiffNewLineMap } from "../src/gitlab.js";

describe("parseDiffNewLineMap", () => {
  it("marks added lines as added (new_line only) and context lines with old_line", () => {
    // Added a line between two context lines. Old file: A,B; new file: A,X,B.
    const diff = ["@@ -1,2 +1,3 @@", " A", "+X", " B"].join("\n");
    const map = parseDiffNewLineMap(diff);
    expect(map.get(1)).toEqual({ added: false, oldLine: 1 }); // context "A"
    expect(map.get(2)).toEqual({ added: true, oldLine: null }); // added "X"
    expect(map.get(3)).toEqual({ added: false, oldLine: 2 }); // context "B" (old line 2)
  });

  it("advances old_line past removed lines so context old_line stays correct", () => {
    // Old: A,B,C ; removed B ; new: A,C
    const diff = ["@@ -1,3 +1,2 @@", " A", "-B", " C"].join("\n");
    const map = parseDiffNewLineMap(diff);
    expect(map.get(1)).toEqual({ added: false, oldLine: 1 }); // "A"
    expect(map.get(2)).toEqual({ added: false, oldLine: 3 }); // "C" is old line 3 (B was 2)
    expect(map.has(3)).toBe(false); // nothing on new line 3
  });

  it("handles multiple hunks and does not map lines outside any hunk", () => {
    const diff = [
      "@@ -10,2 +10,3 @@",
      " ctx10",
      "+new11",
      " ctx12",
      "@@ -50,1 +51,2 @@",
      "+new51",
      " ctx52",
    ].join("\n");
    const map = parseDiffNewLineMap(diff);
    expect(map.get(10)).toEqual({ added: false, oldLine: 10 });
    expect(map.get(11)).toEqual({ added: true, oldLine: null });
    expect(map.get(12)).toEqual({ added: false, oldLine: 11 });
    expect(map.get(51)).toEqual({ added: true, oldLine: null });
    expect(map.get(52)).toEqual({ added: false, oldLine: 50 });
    expect(map.has(20)).toBe(false); // between hunks, not in the diff
  });

  it("ignores file headers and the no-newline marker", () => {
    const diff = ["--- a/f", "+++ b/f", "@@ -1 +1 @@", "-old", "+new", "\\ No newline at end of file"].join("\n");
    const map = parseDiffNewLineMap(diff);
    expect(map.get(1)).toEqual({ added: true, oldLine: null });
    expect(map.size).toBe(1);
  });
});

describe("parseGlabPaginatedJson", () => {
  it("parses a single page", () => {
    const raw = '[{"id":1,"body":"hello"},{"id":2,"body":"world"}]';
    const result = parseGlabPaginatedJson(raw);
    expect(result).toEqual([
      { id: 1, body: "hello" },
      { id: 2, body: "world" },
    ]);
  });

  it("merges multiple pages", () => {
    const raw = '[{"id":1}][{"id":2}][{"id":3}]';
    const result = parseGlabPaginatedJson(raw);
    expect(result).toEqual([{ id: 1 }, { id: 2 }, { id: 3 }]);
  });

  it("preserves note bodies containing ][", () => {
    const raw = '[{"body":"array ][ boundary in text"}][{"body":"next page"}]';
    const result = parseGlabPaginatedJson(raw);
    expect(result).toHaveLength(2);
    expect(result[0].body).toBe("array ][ boundary in text");
    expect(result[1].body).toBe("next page");
  });

  it("preserves note bodies containing ] [", () => {
    const raw = '[{"body":"spaced ] [ boundary"}][{"body":"ok"}]';
    const result = parseGlabPaginatedJson(raw);
    expect(result[0].body).toBe("spaced ] [ boundary");
  });

  it("preserves escaped quotes in strings", () => {
    const raw = '[{"body":"he said \\"hello\\" and ]["}][{"id":2}]';
    const result = parseGlabPaginatedJson(raw);
    expect(result).toHaveLength(2);
    expect(result[0].body).toBe('he said "hello" and ][');
  });

  it("handles empty page before non-empty page", () => {
    const raw = '[][{"id":1}]';
    const result = parseGlabPaginatedJson(raw);
    expect(result).toEqual([{ id: 1 }]);
  });

  it("handles non-empty page before empty page", () => {
    const raw = '[{"id":1}][]';
    const result = parseGlabPaginatedJson(raw);
    expect(result).toEqual([{ id: 1 }]);
  });

  it("handles all empty pages", () => {
    const raw = "[][]";
    const result = parseGlabPaginatedJson(raw);
    expect(result).toEqual([]);
  });

  it("handles single empty array", () => {
    const result = parseGlabPaginatedJson("[]");
    expect(result).toEqual([]);
  });

  it("handles empty string", () => {
    const result = parseGlabPaginatedJson("");
    expect(result).toEqual([]);
  });

  it("handles whitespace between pages", () => {
    const raw = '[{"id":1}]\n[{"id":2}]\n[{"id":3}]';
    const result = parseGlabPaginatedJson(raw);
    expect(result).toEqual([{ id: 1 }, { id: 2 }, { id: 3 }]);
  });

  it("handles nested arrays in values", () => {
    const raw = '[{"tags":["a","b"],"id":1}][{"tags":[],"id":2}]';
    const result = parseGlabPaginatedJson(raw);
    expect(result).toHaveLength(2);
    expect(result[0]).toEqual({ tags: ["a", "b"], id: 1 });
    expect(result[1]).toEqual({ tags: [], id: 2 });
  });

  it("handles real-world glab note with HTML and markdown", () => {
    const note = {
      id: 71780,
      body: 'added 1 commit\n\n<ul><li>25a479e4 - chore: remove unused deploy/ folder</li></ul>\n\n[Compare with previous version](/acme/alerts/-/merge_requests/78/diffs?diff_id=68132)',
      system: true,
      author: { username: "karan" },
    };
    const raw = `[${JSON.stringify(note)}]`;
    const result = parseGlabPaginatedJson(raw);
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe(71780);
    expect(result[0].body).toContain("[Compare with previous version]");
  });
});

describe("summarizeGitlabNotes", () => {
  it("filters out system notes", () => {
    const notes = [
      { body: "This is a real review comment with substance", author: { username: "alice" }, system: false },
      { body: "added 1 commit", author: { username: "bot" }, system: true },
    ];
    const result = summarizeGitlabNotes(notes);
    expect(result).toContain("@alice");
    expect(result).not.toContain("@bot");
  });

  it("filters out trivial short comments", () => {
    const notes = [
      { body: "This is a substantive review comment", author: { username: "alice" }, system: false },
      { body: "lgtm", author: { username: "bob" }, system: false },
      { body: "+1", author: { username: "charlie" }, system: false },
    ];
    const result = summarizeGitlabNotes(notes);
    expect(result).toContain("@alice");
    expect(result).not.toContain("@bob");
    expect(result).not.toContain("@charlie");
  });

  it("returns empty string for no notes", () => {
    expect(summarizeGitlabNotes(null)).toBe("");
    expect(summarizeGitlabNotes(undefined)).toBe("");
    expect(summarizeGitlabNotes([])).toBe("");
  });

  it("limits to maxEntries most recent", () => {
    const notes = Array.from({ length: 10 }, (_, i) => ({
      body: `Substantive comment number ${i + 1} with enough length`,
      author: { username: `user${i}` },
      created_at: `2026-03-${String(i + 1).padStart(2, "0")}T10:00:00Z`,
      system: false,
    }));
    const result = summarizeGitlabNotes(notes, 3);
    // Should only contain the 3 most recent (user7, user8, user9)
    expect(result).toContain("@user7");
    expect(result).toContain("@user8");
    expect(result).toContain("@user9");
    expect(result).not.toContain("@user0");
  });
});
