import { exec, execJson } from "./utils/exec.js";
import { logger } from "./utils/logger.js";
import type { MrMetadata, NoteEntry } from "./types.js";
import { HODOR_REVIEW_MARKER } from "./render.js";

export { HODOR_REVIEW_MARKER };

export interface DiffRefs {
  base_sha: string;
  head_sha: string;
  start_sha: string;
}

const DEFAULT_GITLAB_HOST = "gitlab.com";

/**
 * Match notes Hodor itself created. The body must begin with a hodor-owned HTML
 * comment (either `<!-- hodor-review -->` or a sibling like `<!-- hodor:sha:... -->`
 * that hodor prepends to summary comments). Anchoring at the start avoids deleting
 * human notes that quote the marker incidentally (e.g., a code block discussing hodor).
 */
const HODOR_NOTE_PREFIX_RE = /^\s*<!--\s*hodor[-:]/;

function isHodorNote(body: unknown, marker = HODOR_REVIEW_MARKER): boolean {
  if (typeof body !== "string") return false;
  // Default fast-path: body starts with the canonical marker (allowing leading whitespace).
  if (body.trimStart().startsWith(marker)) return true;
  // Accept hodor's own SHA prefix, e.g. `<!-- hodor:sha:abc -->\n<!-- hodor-review -->\n...`
  if (marker === HODOR_REVIEW_MARKER && HODOR_NOTE_PREFIX_RE.test(body)) {
    // Require the canonical marker to appear somewhere in the body so we don't
    // resolve unrelated `<!-- hodor:foo -->` notes that aren't review summaries.
    return body.includes(HODOR_REVIEW_MARKER);
  }
  return false;
}

/**
 * Parse concatenated JSON arrays from `glab api --paginate`.
 * glab outputs `[...][...][...]` — one array per page, no delimiter.
 * We track bracket depth (respecting strings/escapes) to find each
 * top-level array, parse them individually, and merge with flat().
 */
export function parseGlabPaginatedJson(raw: string): Array<Record<string, unknown>> {
  const trimmed = raw.trim();
  if (!trimmed) return [];

  const chunks: string[] = [];
  let depth = 0;
  let inString = false;
  let escaped = false;
  let start = -1;

  for (let i = 0; i < trimmed.length; i++) {
    const ch = trimmed[i];
    if (escaped) {
      escaped = false;
      continue;
    }
    if (ch === "\\" && inString) {
      escaped = true;
      continue;
    }
    if (ch === '"') {
      inString = !inString;
      continue;
    }
    if (inString) continue;

    if (ch === "[") {
      if (depth === 0) start = i;
      depth++;
    } else if (ch === "]") {
      depth--;
      if (depth === 0 && start >= 0) {
        chunks.push(trimmed.slice(start, i + 1));
        start = -1;
      }
    }
  }

  const results: Array<Record<string, unknown>> = [];
  for (const chunk of chunks) {
    try {
      const parsed = JSON.parse(chunk) as Array<Record<string, unknown>>;
      if (Array.isArray(parsed)) results.push(...parsed);
    } catch (err) {
      logger.warn(
        `Skipping malformed glab pagination chunk: ${err instanceof Error ? err.message : err}`,
      );
    }
  }
  return results;
}

export class GitLabAPIError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "GitLabAPIError";
  }
}

function normalizeBaseUrl(host?: string | null): string {
  const candidate =
    host ||
    process.env.GITLAB_HOST ||
    process.env.CI_SERVER_URL ||
    DEFAULT_GITLAB_HOST;
  const trimmed = candidate.trim() || DEFAULT_GITLAB_HOST;
  if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) {
    return trimmed.replace(/\/+$/, "");
  }
  return `https://${trimmed}`.replace(/\/+$/, "");
}

function encodedProjectPath(owner: string, repo: string): string {
  const projectPath = [owner.replace(/^\/+|\/+$/g, ""), repo.replace(/^\/+|\/+$/g, "")]
    .filter(Boolean)
    .join("/");
  return encodeURIComponent(projectPath);
}

function glabEnv(host?: string | null): NodeJS.ProcessEnv {
  const env = { ...process.env };
  // Ensure glab knows which host to talk to
  const baseUrl = normalizeBaseUrl(host);
  const hostname = baseUrl.replace(/^https?:\/\//, "");
  env.GITLAB_HOST = hostname;
  return env;
}

/**
 * Fetch merge request metadata using glab api.
 */
export async function fetchGitlabMrInfo(
  owner: string,
  repo: string,
  mrNumber: number | string,
  host?: string | null,
  options?: { includeComments?: boolean },
): Promise<MrMetadata> {
  const encoded = encodedProjectPath(owner, repo);
  const env = glabEnv(host);

  let mrData: Record<string, unknown>;
  try {
    mrData = await execJson<Record<string, unknown>>(
      "glab",
      ["api", `projects/${encoded}/merge_requests/${mrNumber}`],
      { env },
    );
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new GitLabAPIError(`Failed to fetch MR !${mrNumber}: ${msg}`);
  }

  const metadata: MrMetadata = {
    title: mrData.title as string | undefined,
    description: (mrData.description as string) ?? "",
    source_branch: mrData.source_branch as string | undefined,
    target_branch: mrData.target_branch as string | undefined,
    changes_count: mrData.changes_count as number | undefined,
    labels: mrData.labels as string[] | undefined,
    author: mrData.author as { username?: string; name?: string } | undefined,
    pipeline: mrData.pipeline as { status?: string; web_url?: string } | undefined,
    state: mrData.state as string | undefined,
  };

  if (options?.includeComments) {
    try {
      // glab --paginate concatenates JSON arrays across pages (e.g., `[...][...]`).
      // Parse each top-level array separately and merge, avoiding regex on raw JSON
      // which could corrupt string values containing `][`.
      const { stdout: rawNotes } = await exec(
        "glab",
        ["api", `projects/${encoded}/merge_requests/${mrNumber}/notes`, "--paginate"],
        { env },
      );
      const notes = parseGlabPaginatedJson(rawNotes);
      metadata.Notes = notes.map((n) => ({
        body: (n.body as string) ?? "",
        author: n.author as { username?: string; name?: string } | undefined,
        created_at: n.created_at as string | undefined,
        system: n.system as boolean | undefined,
      }));
    } catch (err) {
      logger.warn(`Failed to fetch MR notes: ${err instanceof Error ? err.message : err}`);
    }
  }

  return metadata;
}

/**
 * Post a comment on a GitLab merge request using glab api.
 */
export async function postGitlabMrComment(
  owner: string,
  repo: string,
  mrNumber: number | string,
  body: string,
  host?: string | null,
): Promise<void> {
  const encoded = encodedProjectPath(owner, repo);
  const env = glabEnv(host);

  try {
    await exec(
      "glab",
      [
        "api",
        `projects/${encoded}/merge_requests/${mrNumber}/notes`,
        "--method",
        "POST",
        "-H",
        "Content-Type: application/json",
        "--input",
        "-",
      ],
      { env, input: JSON.stringify({ body }) },
    );
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new GitLabAPIError(`Failed to post comment to MR !${mrNumber}: ${msg}`);
  }
}

/**
 * Summarize GitLab notes into a human-readable bullet list.
 */
export function summarizeGitlabNotes(
  notes: NoteEntry[] | undefined | null,
  maxEntries = 5,
): string {
  if (!notes || notes.length === 0) return "";

  const trivialPatterns = new Set([
    "lgtm",
    "+1",
    "-1",
    "👍",
    "👎",
    "thanks",
    "thank you",
    "looks good",
    "approved",
    "🚀",
    "✅",
    "❌",
  ]);

  const filtered: Array<{ username: string; body: string; createdAt: string }> = [];
  for (const note of notes) {
    const body = (note.body ?? "").trim();
    if (!body) continue;
    if (note.system) continue;
    if (body.length < 20) continue;

    const bodyLower = body.toLowerCase();
    let isTrivial = false;
    for (const pattern of trivialPatterns) {
      if (bodyLower.includes(pattern) && body.length < 50) {
        isTrivial = true;
        break;
      }
    }
    if (isTrivial) continue;

    const username =
      note.author?.username ?? note.author?.name ?? "unknown";
    filtered.push({ username, body, createdAt: note.created_at ?? "" });
  }

  // Sort oldest first
  filtered.sort((a, b) => a.createdAt.localeCompare(b.createdAt));

  // Take most recent
  const recent = filtered.slice(-maxEntries);

  const lines: string[] = [];
  for (const { username, body, createdAt } of recent) {
    let timestampStr = "";
    if (createdAt) {
      try {
        const dt = new Date(createdAt);
        timestampStr = dt.toISOString().replace("T", " ").slice(0, 16);
      } catch {
        timestampStr = createdAt.slice(0, 10);
      }
    }

    const header = timestampStr
      ? `- ${timestampStr} @${username}:`
      : `- @${username}:`;
    const indentedBody = body.split("\n").join("\n  ");
    lines.push(`${header}\n  ${indentedBody}`);
  }

  return lines.join("\n");
}

function isPositionErrorMessage(message: string): boolean {
  const lower = message.toLowerCase();
  const hasPositionHint =
    lower.includes("line_code") ||
    lower.includes("position") ||
    lower.includes("must be part of the diff") ||
    lower.includes("part of the diff");
  const hasStatusHint = lower.includes("400") || lower.includes("422");
  return hasPositionHint && hasStatusHint;
}

export async function getGitlabMrDiffRefs(
  owner: string,
  repo: string,
  mrNumber: number | string,
  host?: string | null,
): Promise<DiffRefs> {
  const encoded = encodedProjectPath(owner, repo);
  const env = glabEnv(host);

  let mrData: Record<string, unknown>;
  try {
    mrData = await execJson<Record<string, unknown>>(
      "glab",
      ["api", `projects/${encoded}/merge_requests/${mrNumber}`],
      { env },
    );
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new GitLabAPIError(`Failed to fetch diff refs for MR !${mrNumber}: ${msg}`);
  }

  const diffRefs = mrData.diff_refs as Record<string, unknown> | undefined;
  const base_sha = diffRefs?.base_sha;
  const head_sha = diffRefs?.head_sha;
  const start_sha = diffRefs?.start_sha;

  if (
    typeof base_sha !== "string" ||
    typeof head_sha !== "string" ||
    typeof start_sha !== "string" ||
    !base_sha ||
    !head_sha ||
    !start_sha
  ) {
    throw new GitLabAPIError(`MR !${mrNumber} has missing or incomplete diff_refs`);
  }

  return { base_sha, head_sha, start_sha };
}

export async function postGitlabInlineComment(
  owner: string,
  repo: string,
  mrNumber: number | string,
  body: string,
  filePath: string,
  line: number,
  diffRefs: DiffRefs,
  host?: string | null,
): Promise<Record<string, unknown> | null> {
  const encoded = encodedProjectPath(owner, repo);
  const env = glabEnv(host);
  const endpoint = `projects/${encoded}/merge_requests/${mrNumber}/discussions`;

  const payload: Record<string, unknown> = {
    body,
    position: {
      base_sha: diffRefs.base_sha,
      head_sha: diffRefs.head_sha,
      start_sha: diffRefs.start_sha,
      position_type: "text",
      old_path: filePath,
      new_path: filePath,
      new_line: line,
    },
  };

  try {
    return await execJson<Record<string, unknown>>(
      "glab",
      ["api", endpoint, "--method", "POST", "-H", "Content-Type: application/json", "--input", "-"],
      {
        env,
        input: JSON.stringify(payload),
      },
    );
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    if (isPositionErrorMessage(msg)) {
      logger.warn(`Skipping inline comment for ${filePath}:${line}: ${msg}`);
      return null;
    }
    throw new GitLabAPIError(`Failed to post inline comment to MR !${mrNumber}: ${msg}`);
  }
}

export interface DiffLinePosition {
  /** Added line (present only on the new side) — position uses new_line only. */
  added: boolean;
  /** Old-file line number for a context (unchanged) line — required alongside
   * new_line for GitLab to anchor a note on an unchanged line. */
  oldLine: number | null;
}

/**
 * Parse a unified-diff body into a map of new-file line number -> position
 * info. GitLab only accepts inline notes on lines that appear in the diff,
 * and an unchanged (context) line must carry BOTH old_line and new_line — a
 * new_line-only position on a context line is accepted when the draft is
 * created but makes bulk_publish fail with a 500. This map lets the caller
 * build the correct position (and skip lines not in the diff at all).
 */
export function parseDiffNewLineMap(diff: string): Map<number, DiffLinePosition> {
  const map = new Map<number, DiffLinePosition>();
  let oldLine = 0;
  let newLine = 0;
  let inHunk = false;
  for (const line of diff.split("\n")) {
    const hunk = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
    if (hunk) {
      oldLine = parseInt(hunk[1], 10);
      newLine = parseInt(hunk[2], 10);
      inHunk = true;
      continue;
    }
    if (!inHunk || line.startsWith("+++") || line.startsWith("---")) continue;
    if (line.startsWith("\\")) continue; // "\ No newline at end of file"
    if (line.startsWith("+")) {
      map.set(newLine, { added: true, oldLine: null });
      newLine++;
    } else if (line.startsWith("-")) {
      oldLine++;
    } else {
      // Context line (leading space, or an empty line inside a hunk).
      map.set(newLine, { added: false, oldLine });
      oldLine++;
      newLine++;
    }
  }
  return map;
}

/**
 * Fetch an MR's diffs and build a per-file map of new-line -> position info,
 * used to position inline notes correctly (see parseDiffNewLineMap). Keyed by
 * the file's new_path. Returns an empty map on failure (callers fall back to
 * the previous new_line-only behavior).
 */
export async function getGitlabMrDiffLineMap(
  owner: string,
  repo: string,
  mrNumber: number | string,
  host?: string | null,
): Promise<Map<string, Map<number, DiffLinePosition>>> {
  const encoded = encodedProjectPath(owner, repo);
  const env = glabEnv(host);
  const result = new Map<string, Map<number, DiffLinePosition>>();
  try {
    // /diffs is paginated; pull enough pages to cover large MRs.
    for (let page = 1; page <= 20; page++) {
      const changes = await execJson<Array<Record<string, unknown>>>(
        "glab",
        ["api", `projects/${encoded}/merge_requests/${mrNumber}/diffs?per_page=50&page=${page}`],
        { env },
      );
      if (!Array.isArray(changes) || changes.length === 0) break;
      for (const change of changes) {
        const newPath = change.new_path as string | undefined;
        const diff = change.diff as string | undefined;
        if (newPath && typeof diff === "string") {
          result.set(newPath, parseDiffNewLineMap(diff));
        }
      }
      if (changes.length < 50) break;
    }
  } catch (err) {
    logger.warn(`Failed to fetch MR diff line map: ${err instanceof Error ? err.message : err}`);
  }
  return result;
}

/**
 * The set of files an MR actually changes, per GitLab's own MR diff. This is
 * the authoritative source-vs-target file list — unlike a local `git diff`
 * against the merge-base, which on a CI merge-ref checkout also sweeps in
 * unrelated changes already merged into the target branch. Used to scope the
 * dependency-license check to the MR's real manifests. Returns null on failure
 * so callers can fall back to the unscoped behavior.
 */
export async function getGitlabMrChangedFiles(
  owner: string,
  repo: string,
  mrNumber: number | string,
  host?: string | null,
): Promise<string[] | null> {
  const encoded = encodedProjectPath(owner, repo);
  const env = glabEnv(host);
  const paths = new Set<string>();
  try {
    for (let page = 1; page <= 20; page++) {
      const changes = await execJson<Array<Record<string, unknown>>>(
        "glab",
        ["api", `projects/${encoded}/merge_requests/${mrNumber}/diffs?per_page=50&page=${page}`],
        { env },
      );
      if (!Array.isArray(changes) || changes.length === 0) break;
      for (const change of changes) {
        if (typeof change.new_path === "string") paths.add(change.new_path);
        if (typeof change.old_path === "string") paths.add(change.old_path);
      }
      if (changes.length < 50) break;
    }
    return [...paths];
  } catch (err) {
    logger.warn(`Failed to fetch MR changed files: ${err instanceof Error ? err.message : err}`);
    return null;
  }
}

export async function createGitlabDraftNote(
  owner: string,
  repo: string,
  mrNumber: number | string,
  body: string,
  host?: string | null,
  opts?: { filePath?: string; line?: number; oldLine?: number | null; diffRefs?: DiffRefs },
): Promise<Record<string, unknown>> {
  const encoded = encodedProjectPath(owner, repo);
  const env = glabEnv(host);
  const endpoint = `projects/${encoded}/merge_requests/${mrNumber}/draft_notes`;

  const payload: Record<string, unknown> = {
    note: body,
  };

  if (opts?.filePath && typeof opts.line === "number" && opts.diffRefs) {
    const position: Record<string, unknown> = {
      base_sha: opts.diffRefs.base_sha,
      head_sha: opts.diffRefs.head_sha,
      start_sha: opts.diffRefs.start_sha,
      position_type: "text",
      old_path: opts.filePath,
      new_path: opts.filePath,
      new_line: opts.line,
    };
    // A context (unchanged) line must carry old_line as well — without it,
    // GitLab accepts the draft but 500s the whole bulk_publish. Added lines
    // must NOT set old_line.
    if (typeof opts.oldLine === "number") position.old_line = opts.oldLine;
    payload.position = position;
  }

  try {
    return await execJson<Record<string, unknown>>(
      "glab",
      ["api", endpoint, "--method", "POST", "-H", "Content-Type: application/json", "--input", "-"],
      {
        env,
        input: JSON.stringify(payload),
      },
    );
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new GitLabAPIError(`Failed to create draft note for MR !${mrNumber}: ${msg}`);
  }
}

export async function bulkPublishGitlabDraftNotes(
  owner: string,
  repo: string,
  mrNumber: number | string,
  host?: string | null,
): Promise<void> {
  const encoded = encodedProjectPath(owner, repo);
  const env = glabEnv(host);

  try {
    await exec(
      "glab",
      [
        "api",
        `projects/${encoded}/merge_requests/${mrNumber}/draft_notes/bulk_publish`,
        "--method",
        "POST",
      ],
      { env },
    );
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new GitLabAPIError(`Failed to bulk publish draft notes for MR !${mrNumber}: ${msg}`);
  }
}

/**
 * Publish all draft notes, resiliently: try bulk_publish first, and if that
 * fails (GitLab 500s the entire batch if any single note has an unpublishable
 * position), fall back to publishing each draft note individually so one bad
 * note can't sink the valid ones. Returns how many published/failed.
 */
export async function publishGitlabDraftNotesResilient(
  owner: string,
  repo: string,
  mrNumber: number | string,
  host?: string | null,
): Promise<{ published: number; failed: number }> {
  try {
    await bulkPublishGitlabDraftNotes(owner, repo, mrNumber, host);
    return { published: -1, failed: 0 }; // -1: all published in one shot
  } catch (bulkErr) {
    logger.warn(
      `Bulk publish failed, retrying draft notes individually: ${bulkErr instanceof Error ? bulkErr.message : bulkErr}`,
    );
  }

  const encoded = encodedProjectPath(owner, repo);
  const env = glabEnv(host);
  let drafts: Array<Record<string, unknown>>;
  try {
    drafts = await execJson<Array<Record<string, unknown>>>(
      "glab",
      ["api", `projects/${encoded}/merge_requests/${mrNumber}/draft_notes?per_page=100`],
      { env },
    );
  } catch (err) {
    throw new GitLabAPIError(
      `Failed to list draft notes for MR !${mrNumber}: ${err instanceof Error ? err.message : err}`,
    );
  }

  let published = 0;
  let failed = 0;
  for (const draft of Array.isArray(drafts) ? drafts : []) {
    const id = draft.id;
    if (typeof id !== "number" && typeof id !== "string") continue;
    try {
      await exec(
        "glab",
        ["api", `projects/${encoded}/merge_requests/${mrNumber}/draft_notes/${id}/publish`, "--method", "PUT"],
        { env },
      );
      published++;
    } catch (err) {
      failed++;
      logger.warn(`Failed to publish draft note ${id}: ${err instanceof Error ? err.message : err}`);
    }
  }
  return { published, failed };
}

type GitlabCommitStatusState = "pending" | "running" | "success" | "failed" | "canceled";

export async function postGitlabCommitStatus(
  owner: string,
  repo: string,
  sha: string,
  state: GitlabCommitStatusState,
  host?: string | null,
  opts?: { name?: string; description?: string; targetUrl?: string },
): Promise<void> {
  const allowedStates = new Set<GitlabCommitStatusState>([
    "pending",
    "running",
    "success",
    "failed",
    "canceled",
  ]);
  if (!allowedStates.has(state)) {
    throw new GitLabAPIError(`Invalid GitLab commit status state: ${state}`);
  }

  const encoded = encodedProjectPath(owner, repo);
  const env = glabEnv(host);
  const endpoint = `projects/${encoded}/statuses/${sha}`;
  const payload: Record<string, unknown> = {
    state,
    name: opts?.name ?? "hodor",
  };

  if (opts?.description) {
    payload.description = opts.description;
  }
  if (opts?.targetUrl) {
    payload.target_url = opts.targetUrl;
  }

  try {
    await exec(
      "glab",
      ["api", endpoint, "--method", "POST", "-H", "Content-Type: application/json", "--input", "-"],
      {
        env,
        input: JSON.stringify(payload),
      },
    );
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new GitLabAPIError(`Failed to post commit status for ${sha}: ${msg}`);
  }
}

export async function cleanupHodorComments(
  owner: string,
  repo: string,
  mrNumber: number | string,
  host?: string | null,
  marker = HODOR_REVIEW_MARKER,
): Promise<number> {
  const encoded = encodedProjectPath(owner, repo);
  const env = glabEnv(host);

  let notes: Array<Record<string, unknown>>;
  try {
    const { stdout: rawNotes } = await exec(
      "glab",
      [
        "api",
        `projects/${encoded}/merge_requests/${mrNumber}/notes?per_page=100`,
        "--paginate",
      ],
      { env },
    );
    notes = parseGlabPaginatedJson(rawNotes);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new GitLabAPIError(`Failed to list notes for MR !${mrNumber}: ${msg}`);
  }

  const matchedNotes = notes.filter((note) => isHodorNote(note.body, marker));

  let deletedCount = 0;
  let failedCount = 0;
  for (const note of matchedNotes) {
    const noteId = note.id;
    if (typeof noteId !== "number") continue;

    try {
      await exec(
        "glab",
        [
          "api",
          `projects/${encoded}/merge_requests/${mrNumber}/notes/${noteId}`,
          "--method",
          "DELETE",
        ],
        { env },
      );
      deletedCount += 1;
      logger.debug(`Deleted GitLab MR note ${noteId}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      // Inline (discussion) notes can't be deleted via /notes/{id}; log and continue
      // so one stale note doesn't abort the whole cleanup.
      logger.warn(`Skipping note ${noteId} on MR !${mrNumber}: ${msg}`);
      failedCount += 1;
    }
  }

  if (failedCount > 0) {
    logger.warn(`Cleanup left ${failedCount} note(s) undeleted on MR !${mrNumber}`);
  }

  return deletedCount;
}

export async function listHodorDiscussions(
  owner: string,
  repo: string,
  mrNumber: number | string,
  host?: string | null,
  marker = HODOR_REVIEW_MARKER,
): Promise<
  Array<{
    discussionId: string;
    noteId: number;
    body: string;
    resolved: boolean;
    filePath?: string;
    line?: number;
  }>
> {
  const encoded = encodedProjectPath(owner, repo);
  const env = glabEnv(host);

  let discussions: Array<Record<string, unknown>>;
  try {
    const { stdout: rawDiscussions } = await exec(
      "glab",
      [
        "api",
        `projects/${encoded}/merge_requests/${mrNumber}/discussions?per_page=100`,
        "--paginate",
      ],
      { env },
    );
    discussions = parseGlabPaginatedJson(rawDiscussions);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new GitLabAPIError(`Failed to list discussions for MR !${mrNumber}: ${msg}`);
  }

  const results: Array<{
    discussionId: string;
    noteId: number;
    body: string;
    resolved: boolean;
    filePath?: string;
    line?: number;
  }> = [];

  for (const discussion of discussions) {
    const discussionId = discussion.id;
    if (typeof discussionId !== "string") {
      continue;
    }

    const notes = discussion.notes;
    if (!Array.isArray(notes)) {
      continue;
    }

    for (const note of notes) {
      if (!note || typeof note !== "object") {
        continue;
      }
      const noteObj = note as Record<string, unknown>;
      const noteId = noteObj.id;
      const body = noteObj.body;
      if (typeof noteId !== "number" || typeof body !== "string" || !isHodorNote(body, marker)) {
        continue;
      }

      const position =
        noteObj.position && typeof noteObj.position === "object"
          ? (noteObj.position as Record<string, unknown>)
          : undefined;

      const filePath = typeof position?.new_path === "string" ? position.new_path : undefined;
      const line = typeof position?.new_line === "number" ? position.new_line : undefined;

      // Skip non-resolvable threads. GitLab wraps the summary-comment note in a
      // discussion envelope with `resolvable: false`; PUT resolved=true on those
      // returns 403, independent of the caller's project role. Only diff/review
      // threads (resolvable: true) belong in the resolver's queue.
      if (noteObj.resolvable !== true) {
        continue;
      }

      results.push({
        discussionId,
        noteId,
        body,
        resolved: Boolean(noteObj.resolved),
        filePath,
        line,
      });
    }
  }

  return results;
}

/**
 * Fetch every note on an MR (from any author, resolved or not) for duplicate
 * detection against findings about to be posted — unlike listHodorDiscussions,
 * this is not filtered to Hodor's own comments, so it also catches a human
 * reviewer having already raised the same issue.
 */
export async function listAllMrNotes(
  owner: string,
  repo: string,
  mrNumber: number | string,
  host?: string | null,
): Promise<Array<{ filePath?: string; line?: number; body: string }>> {
  const encoded = encodedProjectPath(owner, repo);
  const env = glabEnv(host);

  let discussions: Array<Record<string, unknown>>;
  try {
    const { stdout: rawDiscussions } = await exec(
      "glab",
      [
        "api",
        `projects/${encoded}/merge_requests/${mrNumber}/discussions?per_page=100`,
        "--paginate",
      ],
      { env },
    );
    discussions = parseGlabPaginatedJson(rawDiscussions);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new GitLabAPIError(`Failed to list discussions for MR !${mrNumber}: ${msg}`);
  }

  const results: Array<{ filePath?: string; line?: number; body: string }> = [];

  for (const discussion of discussions) {
    const notes = discussion.notes;
    if (!Array.isArray(notes)) continue;

    for (const note of notes) {
      if (!note || typeof note !== "object") continue;
      const noteObj = note as Record<string, unknown>;
      const body = noteObj.body;
      if (typeof body !== "string") continue;

      const position =
        noteObj.position && typeof noteObj.position === "object"
          ? (noteObj.position as Record<string, unknown>)
          : undefined;
      const filePath = typeof position?.new_path === "string" ? position.new_path : undefined;
      const line = typeof position?.new_line === "number" ? position.new_line : undefined;

      results.push({ filePath, line, body });
    }
  }

  return results;
}

export async function resolveGitlabDiscussions(
  owner: string,
  repo: string,
  mrNumber: number | string,
  discussionIds: string[],
  host?: string | null,
): Promise<number> {
  const encoded = encodedProjectPath(owner, repo);
  const env = glabEnv(host);

  let resolvedCount = 0;

  for (const discussionId of discussionIds) {
    try {
      await exec(
        "glab",
        [
          "api",
          `projects/${encoded}/merge_requests/${mrNumber}/discussions/${discussionId}`,
          "--method",
          "PUT",
          "-H",
          "Content-Type: application/json",
          "--input",
          "-",
        ],
        {
          env,
          input: JSON.stringify({ resolved: true }),
        },
      );
      resolvedCount += 1;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      logger.warn(`Failed to resolve discussion ${discussionId} on MR !${mrNumber}: ${msg}`);
    }
  }

  return resolvedCount;
}
