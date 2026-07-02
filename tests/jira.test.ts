import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  extractJiraUrls,
  extractTextFromAdf,
  summarizeJiraIssue,
  buildJiraContext,
} from "../src/jira.js";

describe("extractJiraUrls", () => {
  it("matches a single URL", () => {
    const text = "Fixes https://corotech.atlassian.net/browse/EDR-1966";
    expect(extractJiraUrls(text)).toEqual([{ host: "corotech.atlassian.net", key: "EDR-1966" }]);
  });

  it("matches multiple URLs", () => {
    const text = `
      Main issue: https://company.atlassian.net/browse/PROJ-123
      Related: https://company.atlassian.net/browse/PROJ-456
    `;
    expect(extractJiraUrls(text)).toEqual([
      { host: "company.atlassian.net", key: "PROJ-123" },
      { host: "company.atlassian.net", key: "PROJ-456" },
    ]);
  });

  it("dedupes repeated URLs", () => {
    const text = `
      https://company.atlassian.net/browse/PROJ-123
      https://company.atlassian.net/browse/PROJ-123
    `;
    expect(extractJiraUrls(text)).toEqual([{ host: "company.atlassian.net", key: "PROJ-123" }]);
  });

  it("returns empty array when there are no URLs", () => {
    expect(extractJiraUrls("This MR has no Jira links")).toEqual([]);
  });

  it("returns empty array for empty/null/undefined text", () => {
    expect(extractJiraUrls("")).toEqual([]);
    expect(extractJiraUrls(null)).toEqual([]);
    expect(extractJiraUrls(undefined)).toEqual([]);
  });
});

describe("extractTextFromAdf", () => {
  it("extracts text from a simple paragraph", () => {
    const adf = {
      type: "doc",
      content: [{ type: "paragraph", content: [{ type: "text", text: "Hello world" }] }],
    };
    expect(extractTextFromAdf(adf)).toBe("Hello world");
  });

  it("extracts text across multiple paragraphs", () => {
    const adf = {
      type: "doc",
      content: [
        { type: "paragraph", content: [{ type: "text", text: "First" }] },
        { type: "paragraph", content: [{ type: "text", text: "Second" }] },
      ],
    };
    expect(extractTextFromAdf(adf)).toBe("First Second");
  });
});

describe("summarizeJiraIssue", () => {
  it("formats a basic issue", () => {
    const issue = {
      key: "EDR-1966",
      fields: {
        summary: "Add login validation",
        issuetype: { name: "Task" },
        status: { name: "In Progress" },
        priority: { name: "High" },
        description: "Implement email validation",
      },
    };
    const result = summarizeJiraIssue(issue);
    expect(result).toContain("EDR-1966");
    expect(result).toContain("Add login validation");
    expect(result).toContain("Task");
    expect(result).toContain("In Progress");
    expect(result).toContain("High");
  });

  it("marks parent issues", () => {
    const issue = {
      key: "EDR-1000",
      fields: { summary: "Parent story", issuetype: { name: "Story" }, status: { name: "Open" } },
    };
    expect(summarizeJiraIssue(issue, true)).toContain("Parent Issue");
  });
});

describe("buildJiraContext", () => {
  const mockFetch = vi.fn();

  beforeEach(() => {
    mockFetch.mockReset();
    vi.stubGlobal("fetch", mockFetch);
    process.env.JIRA_EMAIL = "bot@company.com";
    process.env.JIRA_API_KEY = "token";
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.JIRA_EMAIL;
    delete process.env.JIRA_API_KEY;
  });

  it("returns empty string when there are no Jira URLs", async () => {
    const result = await buildJiraContext("Simple title", "No jira links here");
    expect(result).toBe("");
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("builds context for a linked issue", async () => {
    mockFetch.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          key: "EDR-1966",
          fields: { summary: "Fix bug", issuetype: { name: "Bug", subtask: false }, status: { name: "Open" } },
        }),
        { status: 200 },
      ),
    );

    const result = await buildJiraContext("Fix https://corotech.atlassian.net/browse/EDR-1966", "Description");

    expect(result).toContain("## Jira Context");
    expect(result).toContain("EDR-1966");
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });

  it("includes the parent issue for a subtask", async () => {
    mockFetch
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            key: "EDR-1967",
            fields: {
              summary: "Subtask",
              issuetype: { name: "Sub-task", subtask: true },
              status: { name: "Open" },
              parent: { key: "EDR-1966" },
            },
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            key: "EDR-1966",
            fields: { summary: "Parent story", issuetype: { name: "Story" }, status: { name: "In Progress" } },
          }),
          { status: 200 },
        ),
      );

    const result = await buildJiraContext("https://corotech.atlassian.net/browse/EDR-1967", "");

    expect(result).toContain("EDR-1967");
    expect(result).toContain("EDR-1966");
    expect(result).toContain("Parent Issue");
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });

  it("returns empty string when credentials are missing", async () => {
    delete process.env.JIRA_EMAIL;
    delete process.env.JIRA_API_KEY;

    const result = await buildJiraContext("https://corotech.atlassian.net/browse/EDR-1966", "");
    expect(result).toBe("");
    expect(mockFetch).not.toHaveBeenCalled();
  });
});
