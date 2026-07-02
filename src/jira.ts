import { logger } from "./utils/logger.js";

// Matches Jira URLs like https://company.atlassian.net/browse/PROJECT-123
const JIRA_URL_PATTERN = /https?:\/\/([a-zA-Z0-9-]+\.atlassian\.net)\/browse\/([A-Za-z][A-Za-z0-9]+-\d+)/gi;

export interface JiraUrlMatch {
  host: string;
  key: string;
}

interface AdfNode {
  type?: string;
  text?: string;
  content?: AdfNode[];
}

export interface JiraIssue {
  key?: string;
  fields?: {
    summary?: string;
    issuetype?: { name?: string; subtask?: boolean };
    status?: { name?: string };
    priority?: { name?: string };
    description?: string | AdfNode;
    parent?: { key?: string };
  };
}

function getJiraAuthHeader(): Record<string, string> | null {
  const email = process.env.JIRA_EMAIL;
  const apiKey = process.env.JIRA_API_KEY;
  if (!email || !apiKey) return null;

  const encoded = Buffer.from(`${email}:${apiKey}`, "utf-8").toString("base64");
  return {
    Authorization: `Basic ${encoded}`,
    "Content-Type": "application/json",
  };
}

/** Extract Jira issue URLs from text, deduplicated and normalized to uppercase keys. */
export function extractJiraUrls(text: string | null | undefined): JiraUrlMatch[] {
  if (!text) return [];

  const seen = new Set<string>();
  const result: JiraUrlMatch[] = [];
  for (const match of text.matchAll(JIRA_URL_PATTERN)) {
    const host = match[1];
    const key = match[2].toUpperCase();
    const dedupeKey = `${host}:${key}`;
    if (!seen.has(dedupeKey)) {
      seen.add(dedupeKey);
      result.push({ host, key });
    }
  }
  return result;
}

/** Fetch Jira issue details via REST API. Returns null if credentials are missing or the fetch fails. */
export async function fetchJiraIssue(host: string, issueKey: string): Promise<JiraIssue | null> {
  const authHeader = getJiraAuthHeader();
  if (!authHeader) {
    logger.warn("Jira credentials not configured (JIRA_EMAIL, JIRA_API_KEY)");
    return null;
  }

  const url = `https://${host}/rest/api/3/issue/${issueKey}`;
  try {
    const response = await fetch(url, {
      headers: authHeader,
      signal: AbortSignal.timeout(10_000),
    });
    if (!response.ok) {
      logger.warn(`Failed to fetch Jira issue ${issueKey}: HTTP ${response.status}`);
      return null;
    }
    return (await response.json()) as JiraIssue;
  } catch (err) {
    logger.warn(`Failed to fetch Jira issue ${issueKey}: ${err instanceof Error ? err.message : err}`);
    return null;
  }
}

/** Fetch the parent issue if the given issue is a subtask. */
export async function getParentIssue(host: string, issue: JiraIssue): Promise<JiraIssue | null> {
  const fields = issue.fields ?? {};
  if (!fields.issuetype?.subtask) return null;

  const parentKey = fields.parent?.key;
  if (!parentKey) return null;

  logger.info(`Fetching parent issue: ${parentKey}`);
  return fetchJiraIssue(host, parentKey);
}

/** Extract plain text from Atlassian Document Format (simplified). */
export function extractTextFromAdf(adf: unknown): string {
  const parts: string[] = [];

  function recurse(node: unknown): void {
    if (Array.isArray(node)) {
      for (const item of node) recurse(item);
      return;
    }
    if (node && typeof node === "object") {
      const obj = node as AdfNode;
      if (obj.type === "text" && typeof obj.text === "string") {
        parts.push(obj.text);
      }
      if (Array.isArray(obj.content)) {
        for (const child of obj.content) recurse(child);
      }
    }
  }

  recurse(adf);
  return parts.join(" ").trim();
}

/** Format a single Jira issue for prompt context. */
export function summarizeJiraIssue(issue: JiraIssue, isParent = false): string {
  const fields = issue.fields ?? {};
  const key = issue.key ?? "Unknown";
  const summary = fields.summary ?? "No summary";
  const issueType = fields.issuetype?.name ?? "Issue";
  const status = fields.status?.name ?? "Unknown";
  const priority = fields.priority?.name ?? "";

  let description = "";
  const descField = fields.description;
  if (typeof descField === "string") {
    description = descField;
  } else if (descField && typeof descField === "object") {
    description = extractTextFromAdf(descField);
  }

  // Truncate description to a sane limit to prevent context explosion
  if (description.length > 5000) {
    description = description.slice(0, 4997) + "...";
  }

  const prefix = isParent ? "**Parent Issue**" : "**Linked Issue**";
  const lines = [`${prefix}: [${key}] ${summary}`, `- Type: ${issueType}`, `- Status: ${status}`];

  if (priority) lines.push(`- Priority: ${priority}`);
  if (description) lines.push(`- Description: ${description}`);

  return lines.join("\n");
}

/** Build the Jira context section for the review prompt from an MR/PR title and description. */
export async function buildJiraContext(
  mrTitle?: string | null,
  mrDescription?: string | null,
): Promise<string> {
  const combinedText = `${mrTitle ?? ""}\n${mrDescription ?? ""}`;
  const jiraUrls = extractJiraUrls(combinedText);
  if (jiraUrls.length === 0) return "";

  logger.info(`Found ${jiraUrls.length} Jira issue(s) in MR`);

  const sections: string[] = [];
  for (const { host, key } of jiraUrls) {
    const issue = await fetchJiraIssue(host, key);
    if (!issue) continue;

    sections.push(summarizeJiraIssue(issue));

    const parent = await getParentIssue(host, issue);
    if (parent) sections.push(summarizeJiraIssue(parent, true));
  }

  if (sections.length === 0) return "";
  return "## Jira Context\n\n" + sections.join("\n\n") + "\n";
}
