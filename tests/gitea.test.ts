import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { MrMetadata } from "../src/types.js";

// Sample Gitea API responses
const SAMPLE_PR = {
  number: 42,
  title: "Fix login redirect",
  body: "Fixes the redirect loop on login page",
  state: "open",
  changed_files: 3,
  user: { login: "alice", full_name: "Alice Smith" },
  head: { ref: "fix/login-redirect", label: "alice:fix/login-redirect" },
  base: { ref: "main", label: "acme:main" },
  labels: [{ name: "bug" }, { name: "auth" }],
};

const SAMPLE_COMMENTS = [
  {
    body: "Looks like this needs a test for the edge case where session is expired",
    user: { login: "bob", full_name: "Bob Jones" },
    created_at: "2025-03-20T10:30:00Z",
  },
  {
    body: "Good catch, I added a test in the latest commit",
    user: { login: "alice", full_name: "Alice Smith" },
    created_at: "2025-03-20T11:00:00Z",
  },
];

describe("gitea module", () => {
  let originalEnv: NodeJS.ProcessEnv;
  let mockFetch: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    originalEnv = { ...process.env };
    process.env.GITEA_TOKEN = "test-token-123";
    mockFetch = vi.fn();
    vi.stubGlobal("fetch", mockFetch);
  });

  afterEach(() => {
    process.env = originalEnv;
    vi.restoreAllMocks();
  });

  describe("fetchGiteaPrInfo", () => {
    it("fetches and maps PR metadata correctly", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => SAMPLE_PR,
      });

      const { fetchGiteaPrInfo } = await import("../src/gitea.js");
      const result = await fetchGiteaPrInfo("acme", "widget", 42, "gitea.example.com");

      expect(result.title).toBe("Fix login redirect");
      expect(result.description).toBe("Fixes the redirect loop on login page");
      expect(result.source_branch).toBe("fix/login-redirect");
      expect(result.target_branch).toBe("main");
      expect(result.changes_count).toBe(3);
      expect(result.author?.username).toBe("alice");
      expect(result.author?.name).toBe("Alice Smith");
      expect(result.state).toBe("open");
      expect(result.labels).toEqual([{ name: "bug" }, { name: "auth" }]);

      // Verify correct URL was called
      expect(mockFetch).toHaveBeenCalledWith(
        "https://gitea.example.com/api/v1/repos/acme/widget/pulls/42",
        expect.objectContaining({
          method: "GET",
          headers: expect.objectContaining({
            Authorization: "token test-token-123",
          }),
        }),
      );
    });

    it("includes comments when requested", async () => {
      mockFetch
        .mockResolvedValueOnce({
          ok: true,
          json: async () => SAMPLE_PR,
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => SAMPLE_COMMENTS,
        });

      const { fetchGiteaPrInfo } = await import("../src/gitea.js");
      const result = await fetchGiteaPrInfo("acme", "widget", 42, "gitea.example.com", {
        includeComments: true,
      });

      expect(result.Notes).toHaveLength(2);
      expect(result.Notes![0].author?.username).toBe("bob");
      expect(result.Notes![0].body).toContain("edge case");
      expect(result.Notes![1].author?.username).toBe("alice");
    });
  });

  describe("fetchGiteaPrComments", () => {
    it("fetches and maps comments correctly", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => SAMPLE_COMMENTS,
      });

      const { fetchGiteaPrComments } = await import("../src/gitea.js");
      const notes = await fetchGiteaPrComments("acme", "widget", 42, "gitea.example.com");

      expect(notes).toHaveLength(2);
      expect(notes[0].body).toContain("edge case");
      expect(notes[0].author?.username).toBe("bob");
      expect(notes[0].created_at).toBe("2025-03-20T10:30:00Z");
      expect(notes[0].system).toBe(false);
    });

    it("returns all comments in single fetch (no pagination)", async () => {
      const manyComments = Array.from({ length: 100 }, (_, i) => ({
        body: `Comment ${i}`,
        user: { login: "user", full_name: "User" },
        created_at: "2025-03-20T10:00:00Z",
      }));

      mockFetch.mockResolvedValueOnce({ ok: true, json: async () => manyComments });

      const { fetchGiteaPrComments } = await import("../src/gitea.js");
      const notes = await fetchGiteaPrComments("acme", "widget", 42, "gitea.example.com");

      expect(notes).toHaveLength(100);
      expect(mockFetch).toHaveBeenCalledTimes(1);
    });

    it("handles empty comments", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => [],
      });

      const { fetchGiteaPrComments } = await import("../src/gitea.js");
      const notes = await fetchGiteaPrComments("acme", "widget", 42, "gitea.example.com");

      expect(notes).toHaveLength(0);
      expect(mockFetch).toHaveBeenCalledTimes(1);
    });
  });

  describe("postGiteaPrComment", () => {
    it("posts a comment with correct body", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ id: 1 }),
      });

      const { postGiteaPrComment } = await import("../src/gitea.js");
      await postGiteaPrComment("acme", "widget", 42, "Great PR!", "gitea.example.com");

      expect(mockFetch).toHaveBeenCalledWith(
        "https://gitea.example.com/api/v1/repos/acme/widget/issues/42/comments",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ body: "Great PR!" }),
        }),
      );
    });
  });

  describe("error handling", () => {
    it("throws GiteaAPIError on 401", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 401,
        text: async () => "Unauthorized",
      });

      const { fetchGiteaPrInfo, GiteaAPIError } = await import("../src/gitea.js");

      await expect(
        fetchGiteaPrInfo("acme", "widget", 42, "gitea.example.com"),
      ).rejects.toThrow(GiteaAPIError);
    });

    it("throws GiteaAPIError on 429 rate limit", async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 429,
        text: async () => "Too Many Requests",
      });

      const { fetchGiteaPrInfo, GiteaAPIError } = await import("../src/gitea.js");

      await expect(
        fetchGiteaPrInfo("acme", "widget", 42, "gitea.example.com"),
      ).rejects.toThrow(/Rate limited/);
    });

    it("throws when no token is set for write operations", async () => {
      delete process.env.GITEA_TOKEN;
      delete process.env.FORGEJO_TOKEN;

      vi.resetModules();
      const { postGiteaPrComment } = await import("../src/gitea.js");

      await expect(
        postGiteaPrComment("acme", "widget", 42, "test comment", "gitea.example.com"),
      ).rejects.toThrow(/GITEA_TOKEN/);
    });

    it("uses FORGEJO_TOKEN as fallback", async () => {
      delete process.env.GITEA_TOKEN;
      process.env.FORGEJO_TOKEN = "forgejo-token-456";

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => SAMPLE_PR,
      });

      vi.resetModules();
      const { fetchGiteaPrInfo } = await import("../src/gitea.js");
      await fetchGiteaPrInfo("acme", "widget", 42, "forgejo.example.com");

      expect(mockFetch).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({
          headers: expect.objectContaining({
            Authorization: "token forgejo-token-456",
          }),
        }),
      );
    });
  });
});
