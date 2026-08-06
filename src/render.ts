/**
 * Render structured review output into clean markdown for PR/MR comments.
 */

import type { ReviewFinding, ReviewOutput } from "./types.js";
import { relativizeWorkspacePath } from "./utils/path.js";

export const HODOR_REVIEW_MARKER = "<!-- hodor-review -->";

/**
 * Render a ReviewOutput into clean markdown for posting as a PR/MR comment.
 */
export function renderMarkdown(review: ReviewOutput): string {
  const lines: string[] = [HODOR_REVIEW_MARKER];

  // Group findings by priority
  const critical: ReviewFinding[] = []; // P0, P1
  const important: ReviewFinding[] = []; // P2
  const minor: ReviewFinding[] = []; // P3

  for (const f of review.findings) {
    const p = f.priority;
    if (p <= 1) critical.push(f);
    else if (p === 2) important.push(f);
    else minor.push(f);
  }

  lines.push("### Issues Found");
  lines.push("");

  if (review.findings.length === 0) {
    lines.push("No issues found.");
    lines.push("");
  }

  if (critical.length > 0) {
    lines.push("**Critical (P0/P1)**");
    for (const f of critical) {
      lines.push(formatFinding(f));
    }
    lines.push("");
  }

  if (important.length > 0) {
    lines.push("**Important (P2)**");
    for (const f of important) {
      lines.push(formatFinding(f));
    }
    lines.push("");
  }

  if (minor.length > 0) {
    lines.push("**Minor (P3)**");
    for (const f of minor) {
      lines.push(formatFinding(f));
    }
    lines.push("");
  }

  // Summary
  lines.push("### Summary");
  lines.push(
    `Total issues: ${critical.length} critical, ${important.length} important, ${minor.length} minor.`,
  );
  lines.push("");

  // Overall verdict
  lines.push("### Overall Verdict");
  lines.push(`**Status**: ${verdictLabel(review)}`);
  lines.push("");
  if (review.overall_explanation) {
    lines.push(`**Explanation**: ${review.overall_explanation}`);
  }

  return lines.join("\n").trimEnd() + "\n";
}

export function renderSummaryMarkdown(review: ReviewOutput): string {
  const lines: string[] = [HODOR_REVIEW_MARKER];

  const counts = { critical: 0, important: 0, minor: 0 };
  for (const f of review.findings) {
    if (f.priority <= 1) counts.critical++;
    else if (f.priority === 2) counts.important++;
    else counts.minor++;
  }

  lines.push("");
  lines.push("| Category | Count |");
  lines.push("| --- | ---: |");
  lines.push(`| Critical (P0/P1) | ${counts.critical} |`);
  lines.push(`| Important (P2) | ${counts.important} |`);
  lines.push(`| Minor (P3) | ${counts.minor} |`);

  lines.push("");
  lines.push(`**Overall verdict**: ${verdictLabel(review)}`);
  lines.push("");
  lines.push(`**Explanation**: ${review.overall_explanation}`);

  if (review.findings.length > 0) {
    lines.push("");
    lines.push("| Finding | Location | Priority |");
    lines.push("| --- | --- | --- |");
    for (const f of review.findings) {
      const loc = formatLocation(f.code_location);
      const safeTitle = f.title.replace(/\|/g, "\\|");
      lines.push(`| ${safeTitle} | \`${loc}\` | P${f.priority} |`);
    }
  }

  return lines.join("\n").trimEnd() + "\n";
}

/**
 * Human-readable verdict.
 *
 * `overall_correctness` is a bare boolean and the review template forces it to
 * "patch is incorrect" whenever findings is non-empty, so a single P3 nit used
 * to render as "Patch has blocking issues". Grade by severity instead, using the
 * same P0/P1 threshold postGitlabReviewCommitStatus() gates merges on, so the
 * comment and the commit status can never disagree.
 */
function verdictLabel(review: ReviewOutput): string {
  const blocking = review.findings.filter((f) => f.priority <= 1).length;
  if (blocking > 0) return "Patch has blocking issues";
  if (review.findings.length > 0) return "Patch has non-blocking issues";
  // Graded purely from findings, deliberately ignoring overall_correctness.
  // validateReviewOutput() forces 0 findings => "patch is correct", so the two
  // only diverge after postReviewStructured() dedupes already-posted findings
  // away while keeping the original correctness. Honoring the stale boolean
  // there would print "Patch has blocking issues" above a 0/0/0 table and
  // contradict the commit status, which grades from findings too.
  return "Patch is correct";
}

function formatFinding(f: ReviewFinding): string {
  const loc = ` (\`${formatLocation(f.code_location)}\`)`;
  const title = `- **${f.title}**${loc}`;
  const body = `  - ${f.body}`;
  return `${title}\n${body}`;
}

function formatLocation(loc: {
  absolute_file_path: string;
  line_range: { start: number; end: number };
}): string {
  // Shared with inline comments and the CodeClimate artifact. The local copy
  // this replaced hard-coded /builds/<group>/<project>/, which silently mangled
  // nested GitLab subgroups: /builds/coronet/bugatti/backend/java/Products/x.java
  // rendered as `backend/java/Products/x.java` instead of `Products/x.java`.
  // relativizeWorkspacePath() honors CI_PROJECT_DIR first and gets this right.
  const filePath = relativizeWorkspacePath(loc.absolute_file_path);

  const { start, end } = loc.line_range;
  return start === end ? `${filePath}:${start}` : `${filePath}:${start}-${end}`;
}
