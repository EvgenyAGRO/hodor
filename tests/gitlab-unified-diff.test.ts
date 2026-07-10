import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  execJson: vi.fn(),
}));

vi.mock("../src/utils/exec.js", () => ({
  exec: vi.fn(async () => ({ stdout: "", stderr: "" })),
  execJson: mocks.execJson,
}));

// Import after the mock is registered.
const { getGitlabMrUnifiedDiff } = await import("../src/gitlab.js");

/** Return `pages` in order across successive execJson calls, then []. */
function respondWithPages(pages: Array<Array<Record<string, unknown>>>): void {
  let call = 0;
  mocks.execJson.mockImplementation(async () => pages[call++] ?? []);
}

describe("getGitlabMrUnifiedDiff", () => {
  afterEach(() => {
    mocks.execJson.mockReset();
  });

  it("reconstructs a git-format section for a modified file", async () => {
    respondWithPages([
      [
        {
          old_path: "src/app.ts",
          new_path: "src/app.ts",
          new_file: false,
          renamed_file: false,
          deleted_file: false,
          a_mode: "100644",
          b_mode: "100644",
          diff: "@@ -1,2 +1,2 @@\n-const x = 1;\n+const x = 2;\n ok\n",
        },
      ],
    ]);

    const result = await getGitlabMrUnifiedDiff("owner", "repo", 7, "gitlab.com");
    expect(result).not.toBeNull();
    expect(result?.files).toEqual(["src/app.ts"]);
    expect(result?.hasTooLargeFiles).toBe(false);
    expect(result?.diff).toBe(
      [
        "diff --git a/src/app.ts b/src/app.ts",
        "--- a/src/app.ts",
        "+++ b/src/app.ts",
        "@@ -1,2 +1,2 @@",
        "-const x = 1;",
        "+const x = 2;",
        " ok",
        "",
      ].join("\n"),
    );
  });

  it("emits /dev/null headers for new and deleted files", async () => {
    respondWithPages([
      [
        {
          old_path: "new.ts",
          new_path: "new.ts",
          new_file: true,
          renamed_file: false,
          deleted_file: false,
          b_mode: "100644",
          diff: "@@ -0,0 +1 @@\n+hello\n",
        },
        {
          old_path: "gone.ts",
          new_path: "gone.ts",
          new_file: false,
          renamed_file: false,
          deleted_file: true,
          a_mode: "100644",
          diff: "@@ -1 +0,0 @@\n-bye\n",
        },
      ],
    ]);

    const result = await getGitlabMrUnifiedDiff("owner", "repo", 7);
    expect(result?.diff).toContain("diff --git a/new.ts b/new.ts\nnew file mode 100644\n--- /dev/null\n+++ b/new.ts\n");
    expect(result?.diff).toContain("diff --git a/gone.ts b/gone.ts\ndeleted file mode 100644\n--- a/gone.ts\n+++ /dev/null\n");
  });

  it("captures rename metadata and the union of old/new paths", async () => {
    respondWithPages([
      [
        {
          old_path: "old/name.ts",
          new_path: "new/name.ts",
          new_file: false,
          renamed_file: true,
          deleted_file: false,
          diff: "@@ -1 +1 @@\n-a\n+b\n",
        },
      ],
    ]);

    const result = await getGitlabMrUnifiedDiff("owner", "repo", 7);
    expect(result?.files.sort()).toEqual(["new/name.ts", "old/name.ts"]);
    expect(result?.diff).toContain("diff --git a/old/name.ts b/new/name.ts");
    expect(result?.diff).toContain("rename from old/name.ts");
    expect(result?.diff).toContain("rename to new/name.ts");
    expect(result?.diff).toContain("--- a/old/name.ts");
    expect(result?.diff).toContain("+++ b/new/name.ts");
  });

  it("flags too-large files (content omitted by GitLab)", async () => {
    respondWithPages([
      [
        {
          old_path: "big.bin",
          new_path: "big.bin",
          new_file: false,
          renamed_file: false,
          deleted_file: false,
          too_large: true,
          diff: "",
        },
      ],
    ]);

    const result = await getGitlabMrUnifiedDiff("owner", "repo", 7);
    expect(result?.hasTooLargeFiles).toBe(true);
    // Header still present even with no hunk body.
    expect(result?.diff).toContain("diff --git a/big.bin b/big.bin");
  });

  it("paginates until a short page and aggregates all files", async () => {
    const page1 = Array.from({ length: 50 }, (_, i) => ({
      old_path: `f${i}.ts`,
      new_path: `f${i}.ts`,
      new_file: false,
      renamed_file: false,
      deleted_file: false,
      diff: "@@ -1 +1 @@\n-a\n+b\n",
    }));
    const page2 = [
      {
        old_path: "f50.ts",
        new_path: "f50.ts",
        new_file: false,
        renamed_file: false,
        deleted_file: false,
        diff: "@@ -1 +1 @@\n-a\n+b\n",
      },
    ];
    respondWithPages([page1, page2]);

    const result = await getGitlabMrUnifiedDiff("owner", "repo", 7);
    expect(result?.files).toHaveLength(51);
    expect(mocks.execJson).toHaveBeenCalledTimes(2);
  });

  it("returns null when the API call throws", async () => {
    mocks.execJson.mockRejectedValue(new Error("boom"));
    const result = await getGitlabMrUnifiedDiff("owner", "repo", 7);
    expect(result).toBeNull();
  });
});
