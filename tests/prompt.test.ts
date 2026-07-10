import { describe, it, expect } from "vitest";
import {
  buildMrSections,
  buildPrReviewPrompt,
  normalizeLabelNames,
} from "../src/prompt.js";

describe("buildMrSections", () => {
  it("handles string labels", () => {
    const metadata = {
      title: "Add string labels support",
      labels: ["bug", "gitlab"],
    };

    const { contextSection } = buildMrSections(metadata);
    expect(contextSection).toContain("- Labels: bug, gitlab");
  });

  it("prefers label_details when available", () => {
    const metadata = {
      title: "Prefer detailed labels",
      labels: ["fallback"],
      label_details: [{ name: "frontend" }, { name: "regression" }],
    };

    const { contextSection } = buildMrSections(metadata);
    expect(contextSection).toContain("- Labels: frontend, regression");
  });

  it("returns empty strings when no metadata", () => {
    const { contextSection, notesSection, reminderSection } =
      buildMrSections(null);
    expect(contextSection).toBe("");
    expect(notesSection).toBe("");
    expect(reminderSection).toBe("");
  });

  it("includes author and branches", () => {
    const metadata = {
      title: "Test PR",
      author: { username: "testuser" },
      source_branch: "feature",
      target_branch: "main",
    };

    const { contextSection } = buildMrSections(metadata);
    expect(contextSection).toContain("- Author: @testuser");
    expect(contextSection).toContain("- Branches: feature → main");
  });
});

describe("normalizeLabelNames", () => {
  it("handles string labels", () => {
    expect(normalizeLabelNames(["bug", "feature"])).toEqual([
      "bug",
      "feature",
    ]);
  });

  it("handles dict labels", () => {
    expect(
      normalizeLabelNames([{ name: "bug" }, { name: "feature" }]),
    ).toEqual(["bug", "feature"]);
  });

  it("returns empty for null/undefined", () => {
    expect(normalizeLabelNames(null)).toEqual([]);
    expect(normalizeLabelNames(undefined)).toEqual([]);
  });
});

describe("buildPrReviewPrompt", () => {
  it("uses the tool submission contract by default", () => {
    const prompt = buildPrReviewPrompt({
      prUrl: "https://github.com/acme/hodor/pull/42",
      platform: "github",
      targetBranch: "main",
    });

    expect(prompt).toContain("submit_review");
    expect(prompt).toContain("Do not print the review as normal assistant text.");
    expect(prompt).not.toContain("Output ONLY the raw JSON object");
  });

  it("includes cross-layer contract tracing guidance", () => {
    const prompt = buildPrReviewPrompt({
      prUrl: "https://github.com/acme/hodor/pull/42",
      platform: "github",
      targetBranch: "main",
    });

    expect(prompt).toContain("Contract Trace Checklist");
    expect(prompt).toContain("public `user_id` string vs internal integer primary key");
  });

  it("includes conditional review lenses for focused specialist checks", () => {
    const prompt = buildPrReviewPrompt({
      prUrl: "https://github.com/acme/hodor/pull/42",
      platform: "github",
      targetBranch: "main",
    });

    expect(prompt).toContain("Conditional Review Lenses");
    expect(prompt).toContain("Silent failure / error handling lens");
    expect(prompt).toContain("Critical test gap lens");
    expect(prompt).toContain("Comment/documentation accuracy lens");
    expect(prompt).toContain("Type/API invariant lens");
    expect(prompt).toContain("Simplification lens");
  });

  it("advertises git-diff commands in plain command mode", () => {
    const prompt = buildPrReviewPrompt({
      prUrl: "https://github.com/acme/hodor/pull/42",
      platform: "github",
      targetBranch: "main",
    });

    expect(prompt).toContain("git --no-pager diff origin/main...HEAD");
  });

  it("embeds the diff inline and suppresses git-diff commands when authoritative", () => {
    const staleCmd = "git --no-pager diff 1e8a628d1e8a628d1e8a628d1e8a628d1e8a628d HEAD";
    const prompt = buildPrReviewPrompt({
      prUrl: "https://gitlab.com/acme/hodor/-/merge_requests/42",
      platform: "gitlab",
      targetBranch: "develop",
      diffBaseSha: "1e8a628d1e8a628d1e8a628d1e8a628d1e8a628d",
      embeddedDiff: "diff --git a/a.ts b/a.ts\n--- a/a.ts\n+++ b/a.ts\n@@ -1 +1 @@\n-a\n+b\n",
      suppressGitCommands: true,
    });

    expect(prompt).toContain("Full Diff (Pre-fetched)");
    expect(prompt).toContain("Do NOT run `git diff`");
    // The stale two-dot base command must appear nowhere in the prompt.
    expect(prompt).not.toContain(staleCmd);
  });

  it("points the agent at the saved diff file (not git diff) when too large to embed", () => {
    const diffPath = "/builds/acme/hodor/.hodor-mr-diff.diff";
    const staleCmd = "git --no-pager diff 1e8a628d1e8a628d1e8a628d1e8a628d1e8a628d HEAD";
    const prompt = buildPrReviewPrompt({
      prUrl: "https://gitlab.com/acme/hodor/-/merge_requests/42",
      platform: "gitlab",
      targetBranch: "develop",
      diffBaseSha: "1e8a628d1e8a628d1e8a628d1e8a628d1e8a628d",
      embeddedDiff: null,
      authoritativeDiffPath: diffPath,
      suppressGitCommands: true,
    });

    expect(prompt).toContain("Full Diff (Saved to File)");
    expect(prompt).toContain(diffPath);
    expect(prompt).toContain("Do NOT run `git diff`");
    expect(prompt).not.toContain(staleCmd);
  });

  it("treats an empty embedded diff as 'no reviewable changes', not command mode", () => {
    const staleCmd = "git --no-pager diff 1e8a628d1e8a628d1e8a628d1e8a628d1e8a628d HEAD";
    const prompt = buildPrReviewPrompt({
      prUrl: "https://gitlab.com/acme/hodor/-/merge_requests/42",
      platform: "gitlab",
      targetBranch: "develop",
      diffBaseSha: "1e8a628d1e8a628d1e8a628d1e8a628d1e8a628d",
      embeddedDiff: "",
      suppressGitCommands: true,
    });

    expect(prompt).toContain("No reviewable code changes");
    // Must NOT fall through to the stale command mode.
    expect(prompt).not.toContain(staleCmd);
  });

  it("lists too-large omitted files and tells the agent to read them", () => {
    const prompt = buildPrReviewPrompt({
      prUrl: "https://gitlab.com/acme/hodor/-/merge_requests/42",
      platform: "gitlab",
      targetBranch: "develop",
      embeddedDiff: "diff --git a/a.ts b/a.ts\n--- a/a.ts\n+++ b/a.ts\n@@ -1 +1 @@\n-a\n+b\n",
      suppressGitCommands: true,
      tooLargeFiles: ["big/Generated.java"],
    });

    expect(prompt).toContain("Files Omitted From the Diff (Too Large)");
    expect(prompt).toContain("big/Generated.java");
    expect(prompt).toContain("Inspect each one with `read`");
  });

  it("points to too-large files even when the rest of the diff is empty", () => {
    const staleCmd = "git --no-pager diff 1e8a628d1e8a628d1e8a628d1e8a628d1e8a628d HEAD";
    const prompt = buildPrReviewPrompt({
      prUrl: "https://gitlab.com/acme/hodor/-/merge_requests/42",
      platform: "gitlab",
      targetBranch: "develop",
      diffBaseSha: "1e8a628d1e8a628d1e8a628d1e8a628d1e8a628d",
      embeddedDiff: "",
      suppressGitCommands: true,
      tooLargeFiles: ["big/Generated.java"],
    });

    expect(prompt).toContain("big/Generated.java");
    expect(prompt).not.toContain("No reviewable code changes");
    expect(prompt).not.toContain(staleCmd);
  });
});
