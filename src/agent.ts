import { existsSync } from "node:fs";
import { join } from "node:path";
import { Value } from "@sinclair/typebox/value";
import type { AgentSession, ToolDefinition } from "@earendil-works/pi-coding-agent";
import type { Api, Model } from "@earendil-works/pi-ai";
import { logger } from "./utils/logger.js";
import { exec } from "./utils/exec.js";
import {
  fetchGithubPrInfo,
  normalizeGithubMetadata,
} from "./github.js";
import {
  fetchGitlabMrInfo,
  postGitlabMrComment,
  getGitlabMrDiffRefs,
  getGitlabMrDiffLineMap,
  getGitlabMrUnifiedDiff,
  createGitlabDraftNote,
  publishGitlabDraftNotesResilient,
  postGitlabCommitStatus,
  listHodorDiscussions,
  listAllMrNotes,
  resolveGitlabDiscussions,
  HODOR_REVIEW_MARKER,
} from "./gitlab.js";
import {
  fetchGiteaPrInfo,
  postGiteaPrComment,
} from "./gitea.js";
import type { DiffRefs } from "./gitlab.js";
import { setupWorkspace, cleanupWorkspace } from "./workspace.js";
import { buildPrReviewPrompt } from "./prompt.js";
import { buildJiraContext } from "./jira.js";
import { deduplicateFindings } from "./duplicate-detector.js";
import type { Finding, ExistingComment } from "./duplicate-detector.js";
import { buildLicenseFindings } from "./license-checker.js";
import {
  getDefaultReasoningEffortForModel,
  mapReasoningEffort,
  parseModelString,
} from "./model.js";
import { formatMetricsMarkdown, printMetrics } from "./metrics.js";
import { SUBMIT_REVIEW_SCHEMA, validateReviewOutput } from "./review.js";
import { resolveReviewLocations } from "./resolve-location.js";
import { REVIEW_SYSTEM_PROMPT } from "./system-prompt.js";
import { renderMarkdown, renderSummaryMarkdown } from "./render.js";
import { relativizeWorkspacePath } from "./utils/path.js";
import type {
  Platform,
  ParsedPrUrl,
  ReviewMetrics,
  PostCommentResult,
  MrMetadata,
  ReviewOutput,
} from "./types.js";

export interface AgentProgressEvent {
  type: "tool_start" | "tool_end" | "thinking" | "turn_start" | "turn_end" | "agent_start" | "agent_end" | "text_delta" | "thinking_delta" | "tool_result";
  toolName?: string;
  toolArgs?: string;
  isError?: boolean;
  turnIndex?: number;
  delta?: string;
  result?: string;
}

export function detectPlatform(prUrl: string): Platform {
  const url = new URL(prUrl);
  const hostname = url.hostname;
  if (prUrl.includes("/-/merge_requests/") || hostname.includes("gitlab")) {
    return "gitlab";
  }
  // Gitea/Forgejo: /pulls/ (plural) — must check before GitHub since /pulls/ contains /pull/
  if (prUrl.includes("/pulls/") || hostname.includes("gitea") || hostname.includes("forgejo") || hostname.includes("codeberg")) {
    return "gitea";
  }
  if (prUrl.includes("/pull/") || hostname.includes("github")) {
    return "github";
  }
  throw new Error(
    `Cannot detect platform for URL: ${prUrl}. Expected a GitHub (/pull/), GitLab (/-/merge_requests/), or Gitea/Forgejo (/pulls/) URL.`,
  );
}

export function parsePrUrl(prUrl: string): ParsedPrUrl {
  const url = new URL(prUrl);
  const pathParts = url.pathname.split("/").filter(Boolean);
  const host = url.host;

  // GitHub format: /owner/repo/pull/123
  if (pathParts.length >= 4 && pathParts[2] === "pull") {
    const prNumber = parseInt(pathParts[3], 10);
    if (!Number.isSafeInteger(prNumber) || prNumber <= 0) {
      throw new Error(`Invalid PR number in URL: ${prUrl}. Expected a positive integer after /pull/.`);
    }
    return {
      owner: pathParts[0],
      repo: pathParts[1],
      prNumber,
      host,
    };
  }

  // Gitea/Forgejo format: /owner/repo/pulls/123
  if (pathParts.length >= 4 && pathParts[2] === "pulls") {
    const prNumber = parseInt(pathParts[3], 10);
    if (!Number.isSafeInteger(prNumber) || prNumber <= 0) {
      throw new Error(`Invalid PR number in URL: ${prUrl}. Expected a positive integer after /pulls/.`);
    }
    return {
      owner: pathParts[0],
      repo: pathParts[1],
      prNumber,
      host,
    };
  }

  // GitLab format: /group/subgroup/repo/-/merge_requests/123
  const mrIndex = pathParts.indexOf("merge_requests");
  if (mrIndex >= 0) {
    if (mrIndex < 2 || mrIndex + 1 >= pathParts.length) {
      throw new Error(
        `Invalid GitLab MR URL format: ${prUrl}. Expected .../-/merge_requests/<number>`,
      );
    }
    if (pathParts[mrIndex - 1] !== "-") {
      throw new Error(
        `Invalid GitLab MR URL format: ${prUrl}. Missing '/-/' segment before merge_requests.`,
      );
    }

    const repo = pathParts[mrIndex - 2];
    const ownerParts = pathParts.slice(0, mrIndex - 2);
    const owner =
      ownerParts.length > 0 ? ownerParts.join("/") : pathParts[0];
    const prNumber = parseInt(pathParts[mrIndex + 1], 10);
    if (!Number.isSafeInteger(prNumber) || prNumber <= 0) {
      throw new Error(`Invalid MR number in URL: ${prUrl}. Expected a positive integer after /merge_requests/.`);
    }
    return { owner, repo, prNumber, host };
  }

  throw new Error(
    `Invalid PR/MR URL format: ${prUrl}. Expected GitHub (/pull/), GitLab (/-/merge_requests/), or Gitea/Forgejo (/pulls/) URL.`,
  );
}

function formatLocationRelative(
  loc: { absolute_file_path: string },
  workspacePath?: string | null,
): string {
  return relativizeWorkspacePath(loc.absolute_file_path, workspacePath ?? undefined);
}

/**
 * Post a pass/fail commit status to a GitLab MR head SHA based on review priorities.
 * Findings with priority <= 1 (P0/P1) are treated as blocking.
 */
export async function postGitlabReviewCommitStatus(
  parsed: ParsedPrUrl,
  review: ReviewOutput,
  diffRefs: DiffRefs,
): Promise<void> {
  const blocking = review.findings.filter((f) => f.priority <= 1).length;
  const state = blocking > 0 ? "failed" : "success";
  const description =
    blocking > 0
      ? `${blocking} blocking issue(s) found`
      : review.findings.length > 0
        ? `${review.findings.length} non-blocking issue(s)`
        : "No issues found";

  await postGitlabCommitStatus(
    parsed.owner,
    parsed.repo,
    diffRefs.head_sha,
    state,
    parsed.host,
    { description },
  );
}

export async function postReviewComment(opts: {
  prUrl: string;
  reviewText: string;
  model?: string | null;
  metricsFooter?: string | null;
  headSha?: string | null;
}): Promise<PostCommentResult> {
  const { prUrl, reviewText, model, metricsFooter, headSha } = opts;
  const platform = detectPlatform(prUrl);
  logger.info(`Posting comment to ${platform} PR/MR: ${prUrl}`);

  let parsed: ParsedPrUrl;
  try {
    parsed = parsePrUrl(prUrl);
  } catch (err) {
    return { success: false, error: String(err) };
  }

  let body = reviewText;
  if (headSha) {
    body = `<!-- hodor:sha:${headSha} -->\n${body}`;
  }
  if (model) {
    body = `${body}\n\n---\n\nReview generated by Hodor (model: \`${model}\`)`;
  }
  if (metricsFooter) {
    body = `${body}\n\n${metricsFooter}`;
  }

  try {
    if (platform === "github") {
      await exec("gh", [
        "pr",
        "review",
        String(parsed.prNumber),
        "--repo",
        `${parsed.owner}/${parsed.repo}`,
        "--comment",
        "--body",
        body,
      ]);
      logger.info(`Successfully posted review to GitHub PR #${parsed.prNumber}`);
      return { success: true, platform: "github", prNumber: parsed.prNumber };
    } else if (platform === "gitea") {
      await postGiteaPrComment(
        parsed.owner,
        parsed.repo,
        parsed.prNumber,
        body,
        parsed.host,
      );
      logger.info(`Successfully posted review to Gitea PR #${parsed.prNumber}`);
      return { success: true, platform: "gitea", prNumber: parsed.prNumber };
    } else {
      await postGitlabMrComment(
        parsed.owner,
        parsed.repo,
        parsed.prNumber,
        body,
        parsed.host,
      );
      logger.info(
        `Successfully posted review to GitLab MR !${parsed.prNumber}`,
      );
      return {
        success: true,
        platform: "gitlab",
        mrNumber: parsed.prNumber,
      };
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    logger.error(`Failed to post comment: ${msg}`);
    return { success: false, error: msg };
  }
}

const HODOR_REVIEW_SHA_RE = /^\s*<!--\s*hodor:sha:([a-f0-9]{40})\s*-->/i;

export function getHodorReviewShaCandidates(notes: MrMetadata["Notes"] | undefined | null): string[] {
  if (!notes || notes.length === 0) return [];

  const candidates: Array<{ sha: string; createdAtMs: number | null; index: number }> = [];
  for (const [index, note] of notes.entries()) {
    const match = note.body?.match(HODOR_REVIEW_SHA_RE);
    if (!match) continue;

    const createdAtMs = Date.parse(note.created_at ?? "");
    candidates.push({
      sha: match[1],
      createdAtMs: Number.isFinite(createdAtMs) ? createdAtMs : null,
      index,
    });
  }

  candidates.sort((a, b) => {
    if (a.createdAtMs != null && b.createdAtMs != null && a.createdAtMs !== b.createdAtMs) {
      return b.createdAtMs - a.createdAtMs;
    }
    if (a.createdAtMs != null && b.createdAtMs == null) return -1;
    if (a.createdAtMs == null && b.createdAtMs != null) return 1;
    return a.index - b.index;
  });

  const seen = new Set<string>();
  const shas: string[] = [];
  for (const { sha } of candidates) {
    if (seen.has(sha)) continue;
    seen.add(sha);
    shas.push(sha);
  }
  return shas;
}

async function findLatestValidReviewSha(
  notes: MrMetadata["Notes"] | undefined | null,
  workspacePath: string,
): Promise<string | null> {
  const candidates = getHodorReviewShaCandidates(notes);
  if (candidates.length === 0) return null;

  logger.info(`Found ${candidates.length} previous Hodor review marker(s)`);
  for (const sha of candidates) {
    try {
      const { stdout: objType } = await exec("git", ["cat-file", "-t", sha], { cwd: workspacePath });
      if (objType.trim() !== "commit") throw new Error("not a commit");
      await exec("git", ["merge-base", "--is-ancestor", sha, "HEAD"], { cwd: workspacePath });
      return sha;
    } catch {
      logger.info(`Skipping previous review SHA ${sha.slice(0, 8)}; not a valid ancestor of HEAD`);
    }
  }

  return null;
}

export async function postReviewStructured(opts: {
  prUrl: string;
  review: ReviewOutput;
  model?: string | null;
  metricsFooter?: string | null;
  reviewStyle?: "summary" | "inline" | "hybrid";
  commitStatus?: boolean;
  codeQualityPath?: string | null;
  headSha?: string | null;
  workspacePath?: string | null;
}): Promise<PostCommentResult> {
  const {
    prUrl,
    review,
    model,
    metricsFooter,
    reviewStyle,
    commitStatus,
    codeQualityPath,
    headSha,
    workspacePath,
  } = opts;

  const platform = detectPlatform(prUrl);
  if (platform === "github") {
    return postReviewComment({
      prUrl,
      reviewText: renderMarkdown(review),
      model,
      metricsFooter,
      headSha,
    });
  }

  if (reviewStyle === "summary") {
    return postReviewComment({
      prUrl,
      reviewText: renderMarkdown(review),
      model,
      metricsFooter,
      headSha,
    });
  }

  const parsed = parsePrUrl(prUrl);

  try {
    const discussions = await listHodorDiscussions(
      parsed.owner,
      parsed.repo,
      parsed.prNumber,
      parsed.host,
    );
    const unresolvedIds = [...new Set(
      discussions.filter((d) => !d.resolved).map((d) => d.discussionId),
    )];
    if (unresolvedIds.length > 0) {
      const resolved = await resolveGitlabDiscussions(
        parsed.owner,
        parsed.repo,
        parsed.prNumber,
        unresolvedIds,
        parsed.host,
      );
      if (resolved > 0) logger.info(`Resolved ${resolved} old Hodor discussion(s)`);
    }
  } catch (err) {
    logger.warn(`Failed to resolve old discussions: ${err instanceof Error ? err.message : err}`);
  }

  let diffRefs: DiffRefs | null = null;
  try {
    diffRefs = await getGitlabMrDiffRefs(
      parsed.owner,
      parsed.repo,
      parsed.prNumber,
      parsed.host,
    );
  } catch (err) {
    logger.warn(`Failed to get diff_refs, falling back to summary mode: ${err instanceof Error ? err.message : err}`);
  }

  if (!diffRefs) {
    return postReviewComment({
      prUrl,
      reviewText: renderMarkdown(review),
      model,
      metricsFooter,
      headSha,
    });
  }

  // Skip findings that duplicate an existing MR comment (from a human or a
  // prior Hodor run) or another finding already accepted in this same batch.
  let dedupedReview: ReviewOutput = review;
  try {
    const existingNotes = await listAllMrNotes(parsed.owner, parsed.repo, parsed.prNumber, parsed.host);
    const existingComments: ExistingComment[] = existingNotes.map((n) => ({
      path: n.filePath,
      line: n.line,
      body: n.body,
    }));
    const candidates: Finding[] = review.findings.map((f) => ({
      path: formatLocationRelative(f.code_location, workspacePath),
      line: f.code_location.line_range.start,
      title: f.title,
      body: f.body,
    }));
    const deduped = deduplicateFindings(candidates, existingComments);
    if (deduped.length < candidates.length) {
      // deduplicateFindings preserves object identity for survivors, so we can
      // filter the original findings by matching the parallel `candidates` entry.
      const survivedCandidates = new Set(deduped);
      const survivingFindings = review.findings.filter((_, i) => survivedCandidates.has(candidates[i]));
      logger.info(`Skipped ${candidates.length - deduped.length} duplicate finding(s) already present on the MR`);
      dedupedReview = { ...review, findings: survivingFindings };
    }
  } catch (err) {
    logger.warn(`Failed to check for duplicate comments (continuing without dedup): ${err instanceof Error ? err.message : err}`);
  }

  let inlineCount = 0;
  let failedCount = 0;
  let skippedNotInDiff = 0;
  let summaryPosted = false;
  let draftsPublished = false;
  let statusPosted = false;
  const postingErrors: string[] = [];

  // Build a per-file map of which new-file lines are in the diff and whether
  // each is an added or context line. GitLab only anchors inline notes on
  // lines within the diff, and a context (unchanged) line needs old_line as
  // well as new_line — otherwise the draft is created but bulk_publish 500s.
  const diffLineMap = await getGitlabMrDiffLineMap(parsed.owner, parsed.repo, parsed.prNumber, parsed.host);

  for (const finding of dedupedReview.findings) {
    const relPath = formatLocationRelative(finding.code_location, workspacePath);
    const priorityTag = `[P${finding.priority}]`;
    const title = /^\[P[0-3]\]/.test(finding.title)
      ? finding.title
      : `${priorityTag} ${finding.title}`;
    let body = `${HODOR_REVIEW_MARKER}\n**${title}**\n\n${finding.body}`;

    if (finding.suggestion) {
      // GitLab suggestion blocks anchor to the comment's line and extend `+N` lines below.
      // Single-line: `suggestion:-0+0` (replace one line). Range: `suggestion:-0+N`
      // where N is the number of additional lines beyond the anchor.
      const { start, end } = finding.code_location.line_range;
      const span = Math.max(0, end - start);
      body += `\n\n\`\`\`suggestion:-0+${span}\n${finding.suggestion}\n\`\`\``;
    }

    // Resolve the note's position from the diff. The model often anchors a
    // finding a line or two above the changed hunk (e.g. at a method/field
    // declaration just before the edit). GitLab can only attach an inline note
    // to a line that's actually in the diff, so if the start line isn't, scan
    // the rest of the finding's range for the first line that IS — anchoring
    // there instead of dropping the whole finding to summary-only. Only when
    // no line in the range is in the diff do we skip (and let the summary
    // carry it) rather than create an unpublishable draft that 500s the batch.
    const { start: rangeStart, end: rangeEnd } = finding.code_location.line_range;
    const fileLineMap = diffLineMap.get(relPath);
    let anchorLine = rangeStart;
    let linePos = fileLineMap?.get(rangeStart);
    if (fileLineMap && !linePos) {
      for (let ln = rangeStart + 1; ln <= rangeEnd; ln++) {
        const p = fileLineMap.get(ln);
        if (p) {
          anchorLine = ln;
          linePos = p;
          break;
        }
      }
    }
    if (diffLineMap.size > 0 && !linePos) {
      logger.info(`Skipping inline note for "${finding.title}" — ${relPath}:${rangeStart}-${rangeEnd} is not in the MR diff`);
      skippedNotInDiff++;
      continue;
    }
    // Added line -> new_line only; context line -> both old_line and new_line.
    const oldLine = linePos && !linePos.added ? linePos.oldLine : undefined;

    try {
      await createGitlabDraftNote(
        parsed.owner,
        parsed.repo,
        parsed.prNumber,
        body,
        parsed.host,
        {
          filePath: relPath,
          line: anchorLine,
          oldLine,
          diffRefs,
        },
      );
      inlineCount++;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      logger.warn(`Failed to create inline note for "${finding.title}": ${msg}`);
      postingErrors.push(`inline note: ${msg}`);
      failedCount++;
    }
  }

  logger.info(
    `Created ${inlineCount} inline draft note(s)` +
      `${failedCount > 0 ? ` (${failedCount} failed)` : ""}` +
      `${skippedNotInDiff > 0 ? ` (${skippedNotInDiff} not in diff → summary only)` : ""}`,
  );

  if (reviewStyle === "hybrid" || reviewStyle === undefined) {
    let summaryBody = renderSummaryMarkdown(dedupedReview);
    if (headSha) summaryBody = `<!-- hodor:sha:${headSha} -->\n${summaryBody}`;
    if (model) summaryBody += `\n---\n\nReview generated by Hodor (model: \`${model}\`)`;
    if (metricsFooter) summaryBody += `\n\n${metricsFooter}`;
    try {
      await postGitlabMrComment(
        parsed.owner,
        parsed.repo,
        parsed.prNumber,
        summaryBody,
        parsed.host,
      );
      summaryPosted = true;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      logger.warn(`Failed to post summary comment: ${msg}`);
      postingErrors.push(`summary comment: ${msg}`);
    }
  }

  if (inlineCount > 0) {
    try {
      const { published, failed } = await publishGitlabDraftNotesResilient(
        parsed.owner,
        parsed.repo,
        parsed.prNumber,
        parsed.host,
      );
      if (failed > 0) {
        logger.warn(`Published draft notes with ${failed} failure(s)`);
        postingErrors.push(`${failed} inline note(s) failed to publish`);
      } else {
        logger.info("Published all draft notes");
      }
      // Consider it published if at least one note went out (published === -1
      // means the bulk call published them all in one shot).
      draftsPublished = published !== 0;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      logger.warn(`Failed to publish draft notes: ${msg}`);
      postingErrors.push(`draft publish: ${msg}`);
    }
  }

  if (commitStatus && diffRefs) {
    try {
      await postGitlabReviewCommitStatus(parsed, dedupedReview, diffRefs);
      logger.info("Posted commit status");
      statusPosted = true;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      logger.warn(`Failed to post commit status: ${msg}`);
      postingErrors.push(`commit status: ${msg}`);
    }
  }

  if (codeQualityPath) {
    try {
      const { formatCodeQualityReport } = await import("./codequality.js");
      const report = formatCodeQualityReport(dedupedReview, workspacePath ?? undefined);
      const { writeFileSync } = await import("node:fs");
      writeFileSync(codeQualityPath, report, "utf-8");
      logger.info(`Wrote code quality report to ${codeQualityPath}`);
    } catch (err) {
      logger.warn(`Failed to write code quality report: ${err instanceof Error ? err.message : err}`);
    }
  }

  const visibleResult = summaryPosted || (inlineCount > 0 && draftsPublished) || statusPosted;
  const expectedInlineComments = reviewStyle === "inline" && dedupedReview.findings.length > 0;
  if ((postingErrors.length > 0 && !visibleResult) || (expectedInlineComments && inlineCount === 0)) {
    return {
      success: false,
      platform: "gitlab",
      mrNumber: parsed.prNumber,
      error: postingErrors[0] ?? "No GitLab inline comments were created",
    };
  }

  return {
    success: true,
    platform: "gitlab",
    mrNumber: parsed.prNumber,
  };
}

// Paths that waste context without contributing reviewable logic.
const DIFF_SKIP_PATTERNS: RegExp[] = [
  /(?:^|\/)testdata\//,                                         // test fixture directories
  /(?:^|\/)(?:package-lock\.json|yarn\.lock|pnpm-lock\.yaml|go\.sum|Cargo\.lock|poetry\.lock|Gemfile\.lock|composer\.lock)$/,
  /\.mdx?$/,                                                    // markdown docs
];

export function filterEmbeddedDiff(rawDiff: string): { filtered: string; skippedFiles: string[] } {
  const skippedFiles: string[] = [];
  // Each file section starts with "diff --git a/". Split while preserving the delimiter.
  const sections = rawDiff.split(/(?=^diff --git )/m);
  const kept: string[] = [];
  for (const section of sections) {
    const match = section.match(/^diff --git a\/(.*?) b\//);
    if (!match) {
      kept.push(section);
      continue;
    }
    const filePath = match[1];
    if (DIFF_SKIP_PATTERNS.some((re) => re.test(filePath))) {
      skippedFiles.push(filePath);
    } else {
      kept.push(section);
    }
  }
  return { filtered: kept.join(""), skippedFiles };
}

/**
 * Build the `git` argv for the diff we embed in the prompt.
 *
 * `restrictPaths` scopes the diff to an explicit pathspec (via `--`). We pass
 * the MR's authoritative changed-file list here so a stale CI diff base can't
 * pull in unrelated, already-merged changes — see the call site for the full
 * rationale. An empty/omitted list leaves the diff unscoped (the old behaviour).
 */
export function buildEmbeddedDiffArgs(opts: {
  previousReviewSha: string | null;
  diffBaseSha: string | null;
  localMode: boolean;
  targetBranch: string;
  restrictPaths?: string[] | null;
}): string[] {
  const { previousReviewSha, diffBaseSha, localMode, targetBranch, restrictPaths } = opts;
  const args = previousReviewSha
    ? ["--no-pager", "diff", `${previousReviewSha}...HEAD`]
    : diffBaseSha
      ? ["--no-pager", "diff", diffBaseSha, "HEAD"]
      : localMode
        ? ["--no-pager", "diff", targetBranch] // includes uncommitted changes
        : ["--no-pager", "diff", `origin/${targetBranch}...HEAD`];
  if (restrictPaths && restrictPaths.length > 0) {
    args.push("--", ...restrictPaths);
  }
  return args;
}

const SUBMIT_REVIEW_RECOVERY_ATTEMPTS = 2;

// Hard ceiling on agent turns per review. A healthy review submits in well under
// this; the cap is a backstop against runaway exploration (e.g. an oversized
// diff sending the agent read/grep-ing the whole repo), not the primary cost
// lever — scoping the diff to the MR's changed files is. Override with
// HODOR_MAX_TURNS. When the cap trips we abort the run and let the submit_review
// recovery flow capture whatever the agent already has, granted a few extra turns.
const DEFAULT_MAX_AGENT_TURNS = 25;
const RECOVERY_TURN_GRACE = 3;

/** Resolve the agent turn cap from HODOR_MAX_TURNS, falling back to the default. */
export function resolveMaxAgentTurns(raw: string | undefined): number {
  if (raw === undefined || raw.trim() === "") return DEFAULT_MAX_AGENT_TURNS;
  const parsed = Number(raw);
  if (!Number.isFinite(parsed) || parsed < 1) return DEFAULT_MAX_AGENT_TURNS;
  return Math.floor(parsed);
}

/** Raised when the agent exhausts all in-session submit_review recovery attempts with no content. */
export class StuckPatternError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "StuckPatternError";
  }
}

/** Raised when the agent is stuck repeating the same tool error. */
export class ToolErrorLoopError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ToolErrorLoopError";
  }
}

export interface ToolOutcome {
  isError: boolean;
  message: string;
}

// Threshold for detecting a tool error loop: consecutive identical tool errors.
const TOOL_ERROR_LOOP_THRESHOLD = 3;

/** Scan tool outcomes from the end for a run of consecutive identical errors. */
export function detectToolErrorLoop(
  outcomes: ToolOutcome[],
): { isLoop: boolean; count: number; message?: string } {
  let count = 0;
  let lastMessage: string | undefined;

  for (let i = outcomes.length - 1; i >= 0; i--) {
    const outcome = outcomes[i];
    if (!outcome.isError) break;
    if (lastMessage === undefined) {
      lastMessage = outcome.message;
      count = 1;
    } else if (outcome.message === lastMessage) {
      count++;
    } else {
      break;
    }
  }

  const isLoop = count >= TOOL_ERROR_LOOP_THRESHOLD;
  return { isLoop, count, message: isLoop ? lastMessage : undefined };
}

function throwIfToolErrorLoop(outcomes: ToolOutcome[]): void {
  const result = detectToolErrorLoop(outcomes);
  if (result.isLoop) {
    throw new ToolErrorLoopError(
      `Agent stuck in tool error loop (${result.count} consecutive identical errors): ${result.message}`,
    );
  }
}

export function buildSubmitReviewRecoveryPrompt(attempt: number, maxAttempts: number): string {
  const finalAttempt =
    attempt >= maxAttempts
      ? "\nThis is the final automatic recovery attempt; do not end the turn without calling `submit_review`."
      : "";

  return [
    "Your previous assistant turn ended without a valid `submit_review` tool call, so Hodor cannot capture the review.",
    "Continue from the existing review context. Use only read-only tools and only the changed files/diff already identified.",
    "If more evidence is needed, inspect the relevant diff or file context now.",
    "When analysis is complete, call `submit_review` exactly once. Do not write the review as normal text.",
    "If there are no findings, call `submit_review` with `\"findings\": []` and `\"overall_correctness\": \"patch is correct\"`.",
    finalAttempt,
  ].filter(Boolean).join("\n");
}

export function parseReviewFromAssistantText(text: string): ReviewOutput | null {
  const candidates = getJsonCandidates(text);
  for (const candidate of candidates) {
    try {
      const parsed = JSON.parse(candidate) as unknown;
      if (!Value.Check(SUBMIT_REVIEW_SCHEMA, parsed)) {
        continue;
      }
      return validateReviewOutput(parsed as ReviewOutput);
    } catch {
      // Keep scanning; assistant text often contains prose around the payload.
    }
  }
  return null;
}

function getJsonCandidates(text: string): string[] {
  const candidates: string[] = [];
  const seen = new Set<string>();

  const addCandidate = (value: string): void => {
    const trimmed = value.trim();
    if (!trimmed || seen.has(trimmed)) return;
    seen.add(trimmed);
    candidates.push(trimmed);
  };

  addCandidate(text);

  const fencedJson = /```(?:json)?\s*([\s\S]*?)```/gi;
  for (const match of text.matchAll(fencedJson)) {
    addCandidate(match[1] ?? "");
  }

  const firstBrace = text.indexOf("{");
  const lastBrace = text.lastIndexOf("}");
  if (firstBrace >= 0 && lastBrace > firstBrace) {
    addCandidate(text.slice(firstBrace, lastBrace + 1));
  }

  return candidates;
}

function summarizeLastAssistantMessage(session: AgentSession): string {
  const messages = session.messages as unknown as Array<Record<string, unknown>>;
  const lastAssistant = [...messages].reverse().find((msg) => msg.role === "assistant");
  if (!lastAssistant) {
    return "no assistant message";
  }

  const stopReason =
    typeof lastAssistant.stopReason === "string" ? lastAssistant.stopReason : "unknown";
  const errorMessage =
    typeof lastAssistant.errorMessage === "string" ? `, error=${JSON.stringify(truncateForLog(lastAssistant.errorMessage, 300))}` : "";
  const content = Array.isArray(lastAssistant.content)
    ? lastAssistant.content
      .map((item) => {
        const block = item as Record<string, unknown>;
        const type = typeof block.type === "string" ? block.type : "unknown";
        if (type === "toolCall" && typeof block.name === "string") {
          return `toolCall:${block.name}`;
        }
        return type;
      })
      .join(",")
    : "unknown";
  const rawText = session.getLastAssistantText()?.trim();
  const textSummary = rawText
    ? `, text=${JSON.stringify(truncateForLog(rawText.replace(/\s+/g, " "), 500))}`
    : "";

  return `stopReason=${stopReason}, content=[${content || "none"}]${errorMessage}${textSummary}`;
}

function truncateForLog(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength - 1)}…`;
}

export async function reviewPr(opts: {
  prUrl?: string;
  model?: string;
  reasoningEffort?: string;
  customPrompt?: string | null;
  promptFile?: string | null;
  cleanup?: boolean;
  workspaceDir?: string | null;
  includeMetricsFooter?: boolean;
  onEvent?: (event: AgentProgressEvent) => void;
  bedrockTags?: Record<string, string> | null;
  localMode?: boolean;
  diffAgainst?: string;
  full?: boolean;
  targetBranchOverride?: string;
  maxRetriesWhenStuck?: number;
  skipLicenseCheck?: boolean;
}): Promise<{ review: ReviewOutput; metricsFooter: string | null; headSha: string | null; metrics: ReviewMetrics; workspacePath: string }> {
  const {
    prUrl,
    model = "anthropic/claude-sonnet-4-5-20250929",
    reasoningEffort,
    customPrompt,
    promptFile,
    cleanup = true,
    workspaceDir,
    includeMetricsFooter = false,
    onEvent,
    bedrockTags,
    localMode = false,
    diffAgainst,
    full = false,
    targetBranchOverride,
    maxRetriesWhenStuck = 1,
    skipLicenseCheck = false,
  } = opts;

  logger.info(`Starting PR review for: ${localMode ? "local diff" : prUrl}`);

  let owner = "", repo = "", host = "";
  let prNumber = 0;
  let platform: Platform = "github";

  if (!localMode && prUrl) {
    const urlParsed = parsePrUrl(prUrl);
    owner = urlParsed.owner;
    repo = urlParsed.repo;
    prNumber = urlParsed.prNumber;
    host = urlParsed.host;
    platform = detectPlatform(prUrl);
    logger.info(`Platform: ${platform}, Repo: ${owner}/${repo}, PR: ${prNumber}, Host: ${host}`);
  }

  // --- Preflight: validate model + credentials before any expensive I/O ---
  const parsed = parseModelString(model);

  // Snapshot env vars we may mutate, restore in finally block.
  const envSnapshot: Record<string, string | undefined> = {
    AWS_REGION: process.env.AWS_REGION,
  };

  // Import pi SDK
  const {
    AuthStorage,
    createAgentSession,
    DefaultResourceLoader,
    ModelRegistry,
    SessionManager,
    SettingsManager,
    getAgentDir,
  } = await import("@earendil-works/pi-coding-agent");

  // In-memory auth storage avoids loading ~/.pi/auth.json — env vars only.
  const authStorage = AuthStorage.inMemory();
  if (process.env.LLM_API_KEY) {
    authStorage.setRuntimeApiKey(parsed.provider, process.env.LLM_API_KEY);
  }
  const modelRegistry = ModelRegistry.inMemory(authStorage);

  // Resolve model — use registry for known models, construct manually for custom ARNs
  let piModel: Model<Api>;
  if (parsed.modelId.startsWith("arn:")) {
    // Custom bedrock ARN (inference profile, cross-region, etc.)
    // Extract region from ARN: arn:aws:bedrock:<region>:<account>:...
    const arnParts = parsed.modelId.split(":");
    const region = arnParts.length >= 4 ? arnParts[3] : "us-east-1";
    // Set AWS_REGION so the BedrockRuntimeClient uses the correct endpoint
    if (!process.env.AWS_REGION && !process.env.AWS_DEFAULT_REGION) {
      process.env.AWS_REGION = region;
    }
    piModel = {
      id: parsed.modelId,
      name: parsed.modelId,
      api: "bedrock-converse-stream",
      provider: "amazon-bedrock",
      baseUrl: `https://bedrock-runtime.${region}.amazonaws.com`,
      reasoning: false,
      input: ["text"] as ("text" | "image")[],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: 200000,
      maxTokens: 16384,
    } as Model<Api>;
    logger.info(`Custom bedrock ARN model — region: ${region}`);
  } else {
    const registryModel = modelRegistry.find(parsed.provider, parsed.modelId);
    if (registryModel) {
      piModel = registryModel;
    } else if (parsed.provider === "openrouter") {
      piModel = {
        id: parsed.modelId,
        name: parsed.modelId,
        api: "openai-completions",
        provider: "openrouter",
        baseUrl: "https://openrouter.ai/api/v1",
        reasoning: true,
        input: ["text", "image"] as ("text" | "image")[],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 256000,
        maxTokens: 65536,
      } as Model<Api>;
      logger.warn(`Using best-effort unregistered OpenRouter model — ${parsed.modelId}`);
    } else {
      throw new Error(
        `Unsupported model "${model}". Provider "${parsed.provider}" is recognized by pi-ai, but model "${parsed.modelId}" was not found in the installed registry.`,
      );
    }
  }
  const thinkingLevel =
    mapReasoningEffort(reasoningEffort) ?? getDefaultReasoningEffortForModel(piModel);
  if (!reasoningEffort && thinkingLevel) {
    logger.info(`Default reasoning effort for ${piModel.name}: ${thinkingLevel}`);
  }

  // Note: For bedrock, don't preflight-check AWS credentials because the SDK
  // resolves them from many sources (env vars, IMDS, ECS task role, IRSA,
  // ~/.aws/credentials, etc.) and we can't reliably detect all of them.
  if (parsed.provider !== "amazon-bedrock") {
    const resolvedKey = await modelRegistry.getApiKeyForProvider(parsed.provider);
    if (!resolvedKey) {
      throw new Error(
        `No API key found for provider "${parsed.provider}". Set the provider-specific environment variable, configure pi auth, or set LLM_API_KEY.`,
      );
    }
  }
  logger.info("Preflight OK — model and credentials validated");

  // --- End preflight ---

  // Setup workspace
  let workspacePath: string;
  let targetBranch: string;
  let diffBaseSha: string | null = null;
  let isTemporary = false;
  // Path to the authoritative MR diff written to disk when it's too large to
  // embed inline (see below). Cleaned up in the finally block.
  let authoritativeDiffPath: string | null = null;

  if (localMode) {
    // Resolve to git repo root so paths from git diff match tool expectations
    const cwd = workspaceDir ?? process.cwd();
    try {
      const { stdout: toplevel } = await exec("git", ["rev-parse", "--show-toplevel"], { cwd });
      workspacePath = toplevel.trim();
    } catch {
      workspacePath = cwd; // fallback if not in a git repo
    }
    targetBranch = diffAgainst ?? "origin/main";
    logger.info(`Local mode: workspace=${workspacePath}, diffAgainst=${targetBranch}`);
  } else {
    const wsResult = await setupWorkspace({
      platform,
      owner,
      repo,
      prNumber: String(prNumber),
      host,
      workingDir: workspaceDir ?? undefined,
      reuse: workspaceDir != null,
    });
    workspacePath = wsResult.workspace;
    targetBranch = wsResult.targetBranch;
    diffBaseSha = wsResult.diffBaseSha;
    isTemporary = wsResult.isTemporary;
  }

  // --full with an explicit target overrides the detected base. Drop the CI
  // merge-base SHA so the diff uses origin/<target>...HEAD against the given ref.
  // CI clones don't fetch arbitrary branches, so fetch-and-verify the ref first
  // and fail loudly rather than silently reviewing against a missing base.
  if (!localMode && full && targetBranchOverride) {
    logger.info(`Full review: overriding target branch to '${targetBranchOverride}'`);
    try {
      await exec("git", ["fetch", "--quiet", "origin", targetBranchOverride], { cwd: workspacePath });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      throw new Error(`Failed to fetch --target-branch '${targetBranchOverride}' from origin for --full review: ${msg}`);
    }
    try {
      await exec("git", ["rev-parse", "--verify", "--quiet", `origin/${targetBranchOverride}`], { cwd: workspacePath });
    } catch {
      throw new Error(`--target-branch 'origin/${targetBranchOverride}' not found after fetch; cannot run --full review against it.`);
    }
    targetBranch = targetBranchOverride;
    diffBaseSha = null;
  }

  let activeSession: AgentSession | undefined;

  try {
    let mrMetadata: MrMetadata | null = null;
    if (!localMode && platform === "gitlab") {
      try {
        mrMetadata = await fetchGitlabMrInfo(owner, repo, prNumber, host, {
          includeComments: true,
        });
      } catch (err) {
        logger.warn(`Failed to fetch GitLab metadata: ${err}`);
      }
    } else if (!localMode && platform === "github") {
      try {
        const githubRaw = await fetchGithubPrInfo(owner, repo, prNumber);
        mrMetadata = normalizeGithubMetadata(githubRaw);
      } catch (err) {
        logger.warn(`Failed to fetch GitHub metadata: ${err}`);
      }
    } else if (!localMode && platform === "gitea") {
      try {
        mrMetadata = await fetchGiteaPrInfo(owner, repo, prNumber, host, {
          includeComments: true,
        });
      } catch (err) {
        logger.warn(`Failed to fetch Gitea metadata: ${err}`);
      }
    }

    // Detect previous Hodor review SHA for incremental mode. GitLab returns MR
    // notes newest-first by default, so select by timestamp instead of taking
    // the last match from API order. --full skips this entirely so the review
    // always covers the whole source-vs-target diff.
    const previousReviewSha = full
      ? null
      : await findLatestValidReviewSha(mrMetadata?.Notes, workspacePath);
    if (full) {
      logger.info("Full review mode: ignoring previous hodor reviews, diffing entire source-vs-target range");
    } else if (previousReviewSha) {
      logger.info(`Incremental mode: previous review at ${previousReviewSha.slice(0, 8)}`);
    }

    // Get HEAD SHA for embedding in posted comments (skip in local mode — no posting)
    let headSha: string | null = null;
    if (!localMode) {
      const { stdout: headShaRaw } = await exec("git", ["rev-parse", "HEAD"], { cwd: workspacePath });
      headSha = headShaRaw.trim();
    }

    // Base ref for the PR/MR diff, reused for dependency-license checking below.
    const licenseCheckBaseRef =
      previousReviewSha ?? diffBaseSha ?? (localMode ? targetBranch : `origin/${targetBranch}`);

    // Prefer GitLab's authoritative source-vs-target diff over a local
    // `git diff`. The CI diff base (CI_MERGE_REQUEST_DIFF_BASE_SHA) goes stale
    // when the source branch merges the target back in: a raw `git diff <base>
    // HEAD` then also surfaces every other already-merged change — whole files
    // AND individual hunks inside a file the MR also touched — so the agent
    // reviews (and bills for) code this MR never introduced. GitLab computes the
    // true diff; we embed it. `mrChangedFiles` (the authoritative file list) is
    // reused for the license check below. See getGitlabMrUnifiedDiff.
    let mrChangedFiles: string[] | null = null;
    let gitlabAuthoritativeDiff: string | null = null;
    let gitlabMrDiffText: string | null = null;
    let tooLargeFiles: string[] = [];
    if (!localMode && platform === "gitlab") {
      try {
        const mrDiff = await getGitlabMrUnifiedDiff(owner, repo, prNumber, host);
        if (mrDiff) {
          mrChangedFiles = mrDiff.files;
          gitlabMrDiffText = mrDiff.diff;
          tooLargeFiles = mrDiff.tooLargeFiles;
          if (tooLargeFiles.length > 0) {
            logger.warn(`GitLab omitted content for ${tooLargeFiles.length} too-large file(s); the agent is told to inspect them directly`);
          }
          // Incremental reviews already diff `previousReviewSha...HEAD` (a
          // correct three-dot range that excludes upstream changes); only full
          // reviews need the API diff to dodge the stale two-dot base.
          if (!previousReviewSha) gitlabAuthoritativeDiff = mrDiff.diff;
        }
      } catch (err) {
        logger.warn(`Failed to fetch authoritative GitLab MR diff: ${err instanceof Error ? err.message : err}`);
      }
    }
    const diffRestrictPaths =
      mrChangedFiles && mrChangedFiles.length > 0 ? mrChangedFiles : null;

    // Pre-fetch diff for embedding in prompt (avoids per-file tool calls)
    const MAX_EMBED_BYTES = 200 * 1024; // 200KB
    let embeddedDiff: string | null = null;
    try {
      let rawDiff: string;
      if (gitlabAuthoritativeDiff !== null) {
        rawDiff = gitlabAuthoritativeDiff;
        logger.info(
          `Using GitLab authoritative MR diff (${Buffer.byteLength(rawDiff, "utf-8")} bytes across ${mrChangedFiles?.length ?? 0} file(s))`,
        );
      } else {
        // GitHub/Gitea/local/incremental: a local git diff is correct here
        // (three-dot range or working tree). Scope to the MR's files when known.
        const diffArgs = buildEmbeddedDiffArgs({
          previousReviewSha,
          diffBaseSha,
          localMode,
          targetBranch,
          restrictPaths: diffRestrictPaths,
        });
        if (diffRestrictPaths) {
          logger.info(`Scoping embedded diff to ${diffRestrictPaths.length} MR-changed file(s)`);
        }
        rawDiff = (await exec("git", diffArgs, { cwd: workspacePath })).stdout;
      }
      const { filtered: filteredDiff, skippedFiles } = filterEmbeddedDiff(rawDiff);
      if (skippedFiles.length > 0) {
        logger.info(`Filtered ${skippedFiles.length} file(s) from embedded diff: ${skippedFiles.join(", ")}`);
      }
      const filteredBytes = Buffer.byteLength(filteredDiff, "utf-8");
      if (filteredBytes <= MAX_EMBED_BYTES) {
        embeddedDiff = filteredDiff;
        logger.info(`Embedding diff in prompt (${filteredBytes} bytes, raw: ${Buffer.byteLength(rawDiff, "utf-8")} bytes)`);
      } else if (gitlabAuthoritativeDiff !== null) {
        // Too large to inline, but we hold the authoritative content — persist it
        // so command mode points the agent at this file instead of running an
        // unscoped, stale local `git diff` (which would reintroduce the leak).
        authoritativeDiffPath = join(workspacePath, ".hodor-mr-diff.diff");
        const { writeFileSync } = await import("node:fs");
        writeFileSync(authoritativeDiffPath, filteredDiff, "utf-8");
        logger.info(`Diff too large to embed (${filteredBytes} bytes); wrote authoritative diff to ${authoritativeDiffPath} for command mode`);
      } else {
        logger.info(`Diff too large to embed (${filteredBytes} bytes filtered, ${Buffer.byteLength(rawDiff, "utf-8")} bytes raw), using command mode`);
      }
    } catch (err) {
      logger.warn(`Failed to prepare diff, falling back to command mode: ${err}`);
    }

    // Fetch Jira context (best-effort) if the MR/PR title or description links a Jira issue
    let jiraContext = "";
    try {
      jiraContext = await buildJiraContext(mrMetadata?.title, mrMetadata?.description);
    } catch (err) {
      logger.warn(`Failed to build Jira context: ${err}`);
    }

    // Build prompt (always uses JSON template; rendered to markdown post-hoc)
    const prompt = buildPrReviewPrompt({
      prUrl: prUrl ?? `local diff (against ${targetBranch})`,
      platform,
      targetBranch,
      diffBaseSha,
      mrMetadata,
      customInstructions: customPrompt,
      customPromptFile: promptFile,
      embeddedDiff,
      authoritativeDiffPath,
      // The local git diff is unreliable when we sourced GitLab's authoritative
      // diff (stale CI base); don't let the prompt advertise git-diff commands.
      suppressGitCommands: gitlabAuthoritativeDiff !== null,
      tooLargeFiles,
      previousReviewSha,
      localMode,
      jiraContext,
    });

    const startTime = Date.now();
    const settingsManager = SettingsManager.inMemory({
      compaction: { enabled: true },
    });
    const skillPaths = [join(workspacePath, ".agents", "skills")]
      .filter((p) => existsSync(p));
    const resourceLoader = new DefaultResourceLoader({
      cwd: workspacePath,
      agentDir: getAgentDir(),
      settingsManager,
      systemPrompt: REVIEW_SYSTEM_PROMPT,
      appendSystemPrompt: [],
      noExtensions: true,
      noSkills: true,
      noPromptTemplates: true,
      noThemes: true,
      additionalSkillPaths: skillPaths,
      agentsFilesOverride: () => ({ agentsFiles: [] }),
    });
    await resourceLoader.reload();
    const { skills, diagnostics: skillDiagnostics } = resourceLoader.getSkills();
    if (skills.length > 0) {
      logger.info(`Discovered ${skills.length} repository skill(s)`);
      for (const skill of skills) {
        logger.info(`Found skill: ${skill.name} (${skill.filePath})`);
      }
    }
    for (const diagnostic of skillDiagnostics) {
      const path = diagnostic.path ? ` (${diagnostic.path})` : "";
      logger.warn(`Skill diagnostic: ${diagnostic.message}${path}`);
    }

    /** Extract human-readable summary from tool args */
    function formatToolArgs(_toolName: string, args: unknown): string {
      if (typeof args === "string") return args.slice(0, 200);
      const obj = args as Record<string, unknown> | undefined;
      if (!obj) return "";
      // bash tool: show the command, strip workspace prefix
      if (obj.command) {
        return String(obj.command)
          .replace(new RegExp(`cd ${workspacePath.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} && `), "")
          .slice(0, 200);
      }
      // grep/find: show pattern + path
      if (obj.pattern) {
        const path = obj.path ? ` in ${obj.path}` : "";
        return `${obj.pattern}${path}`;
      }
      // read/ls: show the path
      if (obj.path || obj.file_path) return String(obj.path ?? obj.file_path);
      return JSON.stringify(obj).slice(0, 200);
    }

    /** Extract text content from tool result */
    function formatToolResult(result: unknown): string {
      if (typeof result === "string") return result;
      const obj = result as Record<string, unknown> | undefined;
      if (!obj) return "";
      // pi-sdk wraps results as {content: [{type: "text", text: "..."}]}
      const content = obj.content as Array<{ type?: string; text?: string }> | undefined;
      if (Array.isArray(content)) {
        return content
          .filter((c) => c.type === "text" && c.text)
          .map((c) => c.text)
          .join("\n");
      }
      return JSON.stringify(result)?.slice(0, 500) ?? "";
    }
    for (let attempt = 0; attempt <= maxRetriesWhenStuck; attempt++) {
      const isLastAttempt = attempt === maxRetriesWhenStuck;
      if (attempt > 0) {
        logger.warn(`Retrying from scratch with a fresh agent session (attempt ${attempt}/${maxRetriesWhenStuck})...`);
        activeSession?.dispose();
        activeSession = undefined;
      }

      try {
        let submittedReview: ReviewOutput | null = null;
        let submitReviewCalls = 0;
        const toolOutcomes: ToolOutcome[] = [];
        const submitReviewTool: ToolDefinition = {
          name: "submit_review",
          label: "Submit Review",
          description: "Submit the final structured review after the analysis is complete.",
          promptSnippet: "Submit the final structured review (call exactly once when done)",
          parameters: SUBMIT_REVIEW_SCHEMA,
          execute: async (_toolCallId, params, _signal, _onUpdate, _ctx) => {
            submitReviewCalls++;
            if (submittedReview) {
              logger.warn("Agent called submit_review more than once; ignoring duplicate submission");
              return {
                content: [{
                  type: "text",
                  text: "Review already submitted. Do not call submit_review again.",
                }],
                details: { ignoredDuplicate: true },
              };
            }

            try {
              submittedReview = validateReviewOutput(params as ReviewOutput);
            } catch (err) {
              logger.warn(`Invalid submit_review payload: ${err instanceof Error ? err.message : err}`);
              throw err;
            }
            logger.info(
              `Received structured review via submit_review (${submittedReview.findings.length} finding(s))`,
            );
            return {
              content: [{
                type: "text",
                text: "Review received. Do not output the review as normal text.",
              }],
              details: {},
              terminate: true,
            };
          },
        };

        const { session } = await createAgentSession({
          cwd: workspacePath,
          model: piModel,
          thinkingLevel,
          // pi v0.74 filters customTools through the same allowlist as built-ins
          // (see _refreshToolRegistry in @earendil-works/pi-coding-agent's
          // agent-session.ts). The submit_review custom tool must be named here
          // or the LLM never sees it and the agent loop exits without calling it.
          tools: ["read", "bash", "grep", "find", "ls", "submit_review"],
          customTools: [submitReviewTool],
          authStorage,
          modelRegistry,
          sessionManager: SessionManager.inMemory(),
          settingsManager,
          resourceLoader,
        });
        activeSession = session;

        // Pin sampling temperature to 0 (env-overridable) so reviews are
        // deterministic and consistent run-to-run. Without this the provider's
        // default sampling (~1.0 for many models) makes each run explore
        // differently and surface a different subset of findings. Skipped for
        // models that don't support a temperature param (some reasoning models
        // reject it). Also injects Bedrock cost tags when applicable — one hook.
        const tempEnv = process.env.HODOR_TEMPERATURE;
        const temperature = tempEnv !== undefined && tempEnv.trim() !== "" ? Number(tempEnv) : 0;
        const modelSupportsTemp =
          (piModel as { supportsTemperature?: boolean }).supportsTemperature !== false &&
          Number.isFinite(temperature);
        const needsBedrockTags = Boolean(bedrockTags) && parsed.provider === "amazon-bedrock";
        if (modelSupportsTemp || needsBedrockTags) {
          type AgentWithStream = { agent?: { streamFn?: (...args: unknown[]) => unknown } };
          const agent = (session as unknown as AgentWithStream).agent;
          if (agent && typeof agent.streamFn === "function") {
            const originalStreamFn = agent.streamFn.bind(agent);
            agent.streamFn = (...args: unknown[]) => {
              const options = { ...((args[2] ?? {}) as Record<string, unknown>) };
              if (modelSupportsTemp && options.temperature === undefined) options.temperature = temperature;
              if (needsBedrockTags) options.requestMetadata = bedrockTags;
              return originalStreamFn(args[0], args[1], options);
            };
            if (modelSupportsTemp) logger.info(`Sampling temperature pinned to ${temperature} for deterministic review`);
            if (needsBedrockTags) logger.info(`Bedrock cost allocation tags: ${JSON.stringify(bedrockTags)}`);
          }
        }

        // Subscribe to agent events for progress + metrics tracking
        let turnCount = 0;
        let toolCallCount = 0;

        // Turn-cap state. `turnCap` is bumped with a small grace during recovery
        // so the abort below can't starve the submit_review recovery prompt.
        // `abortArmed` guards against firing abort() repeatedly for one run; it is
        // re-armed before each recovery prompt.
        const maxAgentTurns = resolveMaxAgentTurns(process.env.HODOR_MAX_TURNS);
        let turnCap = maxAgentTurns;
        let turnLimitReached = false;
        let abortArmed = true;

        session.subscribe((event) => {
          switch (event.type) {
            case "agent_start":
              onEvent?.({ type: "agent_start" });
              break;
            case "agent_end":
              onEvent?.({ type: "agent_end" });
              break;
            case "turn_start":
              turnCount++;
              if (abortArmed && turnCount > turnCap) {
                abortArmed = false;
                turnLimitReached = true;
                logger.warn(
                  `Agent reached turn cap (${turnCap} turns); aborting exploration to force review submission. ` +
                    `Increase HODOR_MAX_TURNS if legitimate reviews need more.`,
                );
                // Fire-and-forget: abort() resolves the in-flight prompt() so the
                // recovery flow can request a submit_review from existing context.
                void session.abort().catch((err) => {
                  logger.warn(`Turn-cap abort failed: ${err instanceof Error ? err.message : err}`);
                });
              }
              onEvent?.({ type: "turn_start", turnIndex: turnCount });
              break;
            case "turn_end":
              onEvent?.({ type: "turn_end", turnIndex: turnCount });
              break;
            case "tool_execution_start":
              toolCallCount++;
              onEvent?.({
                type: "tool_start",
                toolName: event.toolName,
                toolArgs: formatToolArgs(event.toolName, event.args),
              });
              break;
            case "tool_execution_end": {
              const resultText = formatToolResult(event.result);
              onEvent?.({
                type: "tool_end",
                toolName: event.toolName,
                isError: event.isError,
                result: resultText,
              });
              toolOutcomes.push({ isError: !!event.isError, message: event.isError ? resultText : "" });
              break;
            }
            case "message_start":
              onEvent?.({ type: "thinking" });
              break;
            case "message_update": {
              const msgEvent = (event as Record<string, unknown>).assistantMessageEvent as
                { type: string; delta?: string } | undefined;
              if (!msgEvent?.delta) break;
              if (msgEvent.type === "text_delta") {
                onEvent?.({ type: "text_delta", delta: msgEvent.delta });
              } else if (msgEvent.type === "thinking_delta") {
                onEvent?.({ type: "thinking_delta", delta: msgEvent.delta });
              }
              break;
            }
          }
        });

        const throwIfAgentErrored = (): void => {
          // pi-agent-core stores failed/aborted assistant turns in state.errorMessage.
          // A turn-cap abort deliberately lands here too, so skip the check once the
          // cap has tripped — that abort is expected, not an LLM failure.
          if (turnLimitReached) return;
          const agentError = session.state.errorMessage;
          if (agentError) {
            throw new Error(`LLM request failed: ${agentError}`);
          }
        };

        const recoverReviewFromAssistantText = (source: string): boolean => {
          const rawText = session.getLastAssistantText() ?? "";
          if (!rawText.trim()) return false;

          const parsedReview = parseReviewFromAssistantText(rawText);
          if (!parsedReview) return false;

          submittedReview = parsedReview;
          logger.warn(
            `Recovered structured review from assistant text after ${source}; model did not call submit_review`,
          );
          return true;
        };

        logger.info("Sending prompt to agent...");
        await session.prompt(prompt);
        throwIfAgentErrored();
        throwIfToolErrorLoop(toolOutcomes);

        if (!submittedReview) {
          recoverReviewFromAssistantText("initial agent run");
        }

        for (
          let recoveryAttempt = 1;
          !submittedReview && recoveryAttempt <= SUBMIT_REVIEW_RECOVERY_ATTEMPTS;
          recoveryAttempt++
        ) {
          // If we aborted on the turn cap, grant the recovery prompt a small,
          // bounded budget (and re-arm the abort) so it can actually submit
          // instead of being cut off on its first turn.
          if (turnLimitReached) {
            turnCap = turnCount + RECOVERY_TURN_GRACE;
            abortArmed = true;
          }
          logger.warn(
            `Agent ended without a valid submit_review (${summarizeLastAssistantMessage(session)}); ` +
            `requesting recovery ${recoveryAttempt}/${SUBMIT_REVIEW_RECOVERY_ATTEMPTS}`,
          );
          await session.prompt(buildSubmitReviewRecoveryPrompt(recoveryAttempt, SUBMIT_REVIEW_RECOVERY_ATTEMPTS));
          throwIfAgentErrored();
          throwIfToolErrorLoop(toolOutcomes);
          recoverReviewFromAssistantText(`recovery attempt ${recoveryAttempt}`);
        }

        if (!submittedReview) {
          const diagnostic = summarizeLastAssistantMessage(session);
          if (submitReviewCalls > 0) {
            throw new StuckPatternError(
              `Agent called submit_review but did not provide a valid review payload after ` +
              `${SUBMIT_REVIEW_RECOVERY_ATTEMPTS} recovery attempt(s): ${diagnostic}`,
            );
          }
          throw new StuckPatternError(
            `Agent did not call submit_review after ${SUBMIT_REVIEW_RECOVERY_ATTEMPTS} recovery attempt(s): ${diagnostic}`,
          );
        }

        const rawReview = submittedReview as ReviewOutput;
        if (submitReviewCalls > 1) {
          logger.warn(`Agent called submit_review ${submitReviewCalls} times; using the first valid submission`);
        }

        // Resolve each finding's line_range from its quoted snippet against the
        // checked-out file, correcting model line-number errors before posting.
        const { review, stats: locationStats } = resolveReviewLocations(rawReview, {
          workspacePath,
          diffText: embeddedDiff,
        });
        if (locationStats.corrected > 0 || locationStats.unmatched > 0) {
          logger.info(
            `Location resolution: ${locationStats.corrected} corrected, ${locationStats.confirmed} confirmed, ` +
              `${locationStats.unmatched} unmatched, ${locationStats.noSnippet} without snippet`,
          );
        }

        if (!skipLicenseCheck) {
          try {
            // Scope the license check to the MR's real changed files (GitLab's
            // authoritative source-vs-target list). Without this, on a CI
            // merge-ref checkout the local git diff also sees pom.xml/etc.
            // changes from other MRs already merged into the target branch,
            // producing license findings for files this MR never touched.
            // Reuses the list already fetched above for diff scoping.
            const restrictToPaths: string[] | undefined = diffRestrictPaths ?? undefined;
            const licenseFindings = await buildLicenseFindings({
              workspacePath,
              baseRef: licenseCheckBaseRef,
              useWorkingTree: localMode,
              restrictToPaths,
              // GitLab's authoritative diff (when available) grounds the check so
              // a manifest the target also changed can't leak target-only deps.
              authoritativeDiff: gitlabMrDiffText,
            });
            if (licenseFindings.length > 0) {
              logger.info(`License check: ${licenseFindings.length} finding(s)`);
              review.findings.push(...licenseFindings);
              if (licenseFindings.some((f) => f.priority <= 1)) {
                review.overall_correctness = "patch is incorrect";
              }
            }
          } catch (err) {
            logger.warn(`License check failed (continuing without it): ${err instanceof Error ? err.message : err}`);
          }
        }

        logger.info(
          `Captured ${review.findings.length} finding(s), verdict: ${review.overall_correctness}`,
        );

        const durationSeconds = (Date.now() - startTime) / 1000;
        logger.info(`Review complete (${review.findings.length} finding(s))`);

        // Aggregate usage from all assistant messages
        interface MsgUsage {
          input: number;
          output: number;
          cacheRead: number;
          cacheWrite: number;
          totalTokens: number;
          cost: { total: number };
        }
        interface AssistantMsg {
          role: string;
          usage?: MsgUsage;
        }

        const allMessages = session.messages as AssistantMsg[];

        let inputTokens = 0;
        let outputTokens = 0;
        let cacheReadTokens = 0;
        let cacheWriteTokens = 0;
        let totalTokens = 0;
        let cost = 0;

        for (const msg of allMessages) {
          if (msg.role === "assistant" && msg.usage) {
            inputTokens += msg.usage.input ?? 0;
            outputTokens += msg.usage.output ?? 0;
            cacheReadTokens += msg.usage.cacheRead ?? 0;
            cacheWriteTokens += msg.usage.cacheWrite ?? 0;
            totalTokens += msg.usage.totalTokens ?? 0;
            cost += msg.usage.cost?.total ?? 0;
          }
        }

        const metrics: ReviewMetrics = {
          inputTokens,
          outputTokens,
          cacheReadTokens,
          cacheWriteTokens,
          totalTokens,
          cost,
          turns: turnCount,
          toolCalls: toolCallCount,
          durationSeconds: Math.round(durationSeconds),
        };
        printMetrics(metrics);

        let metricsFooter: string | null = null;
        if (includeMetricsFooter) {
          metricsFooter = formatMetricsMarkdown(metrics);
        }

        return { review, metricsFooter, headSha, metrics, workspacePath };
      } catch (err) {
        if ((err instanceof StuckPatternError || err instanceof ToolErrorLoopError) && !isLastAttempt) {
          logger.warn(`${err.message}`);
          continue;
        }
        throw err;
      }
    }

    // Unreachable: the loop above always returns on success or throws on the
    // final attempt, but TS can't prove that from a dynamic loop bound.
    throw new Error("Review failed: exhausted all retry attempts");

  } finally {
    activeSession?.dispose();

    // Remove the on-disk authoritative diff (best-effort). A temporary workspace
    // is deleted wholesale below, but a reused/CI checkout would otherwise keep
    // the stray file around.
    if (authoritativeDiffPath) {
      try {
        const { rmSync } = await import("node:fs");
        rmSync(authoritativeDiffPath, { force: true });
      } catch (err) {
        logger.warn(`Failed to remove temp diff file ${authoritativeDiffPath}: ${err instanceof Error ? err.message : err}`);
      }
    }

    // Restore mutated env vars
    for (const [key, val] of Object.entries(envSnapshot)) {
      if (val === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = val;
      }
    }

    if (cleanup && isTemporary) {
      logger.info("Cleaning up workspace...");
      await cleanupWorkspace(workspacePath);
    }
  }
}
