import chalk from "chalk";
import { logger } from "./utils/logger.js";
import type { ReviewMetrics } from "./types.js";

function tok(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(value);
}

function formatDuration(seconds: number): string {
  if (seconds >= 60) {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return s > 0 ? `${m}m ${s}s` : `${m}m`;
  }
  return `${seconds}s`;
}

export function formatMetricsMarkdown(metrics: ReviewMetrics): string {
  const totalInput = metrics.inputTokens + metrics.cacheReadTokens;
  const parts = [`in \`${tok(totalInput)}\``];
  if (metrics.cacheReadTokens > 0) {
    parts.push(`cached \`${tok(metrics.cacheReadTokens)}\``);
  }
  parts.push(`out \`${tok(metrics.outputTokens)}\``);

  const lines = [
    `**Review Metrics** — ${metrics.turns} turns, ${metrics.toolCalls} tool calls, ${formatDuration(metrics.durationSeconds)}`,
    `- Tokens: ${parts.join(" | ")} (total \`${tok(metrics.totalTokens)}\`)`,
  ];
  if (metrics.cost > 0) {
    lines.push(`- Cost: \`$${metrics.cost.toFixed(4)}\``);
  }
  return lines.join("\n");
}

export function printMetrics(metrics: ReviewMetrics, stream: NodeJS.WritableStream = process.stderr): void {
  const dim = chalk.dim;
  const bold = chalk.bold;
  const cyan = chalk.cyan;

  const write = (line: string) => stream.write(line + "\n");

  write("");
  write(dim("─".repeat(50)));

  // Tokens — inputTokens may be only fresh tokens (SDK reports cache hits separately)
  const totalInput = metrics.inputTokens + metrics.cacheReadTokens;
  let tokenLine = `${dim("Tokens:")}  ${bold(tok(totalInput))} in`;
  if (metrics.cacheReadTokens > 0) {
    const hitPct = ((metrics.cacheReadTokens / totalInput) * 100).toFixed(0);
    tokenLine += dim(` (${tok(metrics.cacheReadTokens)} cached ${hitPct}% · ${tok(metrics.inputTokens)} fresh)`);
  }
  tokenLine += `  ${bold(tok(metrics.outputTokens))} out`;
  tokenLine += dim(`  (${tok(metrics.totalTokens)} total)`);
  write(tokenLine);

  // Agent work
  write(
    `${dim("Agent:")}   ${bold(String(metrics.turns))} turns  ${bold(String(metrics.toolCalls))} tool calls  ${cyan(formatDuration(metrics.durationSeconds))}`,
  );

  // Cost
  if (metrics.cost > 0) {
    write(`${dim("Cost:")}    ${bold("$" + metrics.cost.toFixed(4))}`);
  }

  write(dim("─".repeat(50)));
}

/**
 * Push review metrics to a Prometheus Pushgateway.
 * Failures are logged as warnings and never thrown.
 */
export async function pushMetrics(opts: {
  pushgatewayUrl: string;
  metrics: ReviewMetrics;
  labels?: Record<string, string>;
}): Promise<void> {
  const { pushgatewayUrl, metrics, labels = {} } = opts;

  // Build label string for all metrics
  const labelPairs = Object.entries(labels)
    .map(([k, v]) => `${k}="${v.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`)
    .join(",");
  const labelSuffix = labelPairs ? `{${labelPairs}}` : "";

  const totalInput = metrics.inputTokens + metrics.cacheReadTokens;
  const lines = [
    `# HELP hodor_review_input_tokens_total Total input tokens (fresh + cached)`,
    `# TYPE hodor_review_input_tokens_total gauge`,
    `hodor_review_input_tokens_total${labelSuffix} ${totalInput}`,
    `# HELP hodor_review_output_tokens_total Total output tokens`,
    `# TYPE hodor_review_output_tokens_total gauge`,
    `hodor_review_output_tokens_total${labelSuffix} ${metrics.outputTokens}`,
    `# HELP hodor_review_cache_read_tokens_total Tokens served from prompt cache`,
    `# TYPE hodor_review_cache_read_tokens_total gauge`,
    `hodor_review_cache_read_tokens_total${labelSuffix} ${metrics.cacheReadTokens}`,
    `# HELP hodor_review_cost_dollars Cost of the review in USD`,
    `# TYPE hodor_review_cost_dollars gauge`,
    `hodor_review_cost_dollars${labelSuffix} ${metrics.cost}`,
    `# HELP hodor_review_turns_total Number of agent turns`,
    `# TYPE hodor_review_turns_total gauge`,
    `hodor_review_turns_total${labelSuffix} ${metrics.turns}`,
    `# HELP hodor_review_tool_calls_total Number of tool calls`,
    `# TYPE hodor_review_tool_calls_total gauge`,
    `hodor_review_tool_calls_total${labelSuffix} ${metrics.toolCalls}`,
    `# HELP hodor_review_duration_seconds Review duration in seconds`,
    `# TYPE hodor_review_duration_seconds gauge`,
    `hodor_review_duration_seconds${labelSuffix} ${metrics.durationSeconds}`,
    "",
  ];
  const body = lines.join("\n");

  // POST to pushgateway: /metrics/job/<job>
  const baseUrl = pushgatewayUrl.replace(/\/+$/, "");
  const url = `${baseUrl}/metrics/job/hodor`;

  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "text/plain" },
      body,
      signal: AbortSignal.timeout(10_000),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      logger.warn(`Pushgateway returned ${res.status}: ${text.slice(0, 200)}`);
    } else {
      logger.info("Metrics pushed to Pushgateway");
    }
  } catch (err) {
    logger.warn(`Failed to push metrics to Pushgateway: ${err instanceof Error ? err.message : err}`);
  }
}
