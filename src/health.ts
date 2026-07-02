import { statfsSync } from "node:fs";
import { tmpdir } from "node:os";
import { exec } from "./utils/exec.js";
import type { Platform } from "./types.js";

export interface HealthCheckResult {
  name: string;
  passed: boolean;
  message: string;
  required: boolean;
}

export interface HealthReport {
  checks: HealthCheckResult[];
}

export function allChecksPassed(report: HealthReport): boolean {
  return report.checks.filter((c) => c.required).every((c) => c.passed);
}

export function failedChecks(report: HealthReport): HealthCheckResult[] {
  return report.checks.filter((c) => c.required && !c.passed);
}

export function warningChecks(report: HealthReport): HealthCheckResult[] {
  return report.checks.filter((c) => !c.required && !c.passed);
}

export function formatHealthReport(report: HealthReport): string {
  const lines = ["Health Check Report", "=".repeat(40)];
  for (const check of report.checks) {
    const status = check.passed ? "PASS" : check.required ? "FAIL" : "WARN";
    lines.push(`[${status}] ${check.name}: ${check.message}`);
  }
  lines.push("=".repeat(40));
  if (allChecksPassed(report)) {
    lines.push("All required checks passed!");
  } else {
    lines.push(`FAILED: ${failedChecks(report).length} required check(s) failed`);
  }
  return lines.join("\n");
}

async function checkCliVersion(
  name: string,
  cmd: string,
  args: string[],
  opts: { required?: boolean; installHint?: string } = {},
): Promise<HealthCheckResult> {
  const required = opts.required ?? true;
  try {
    const { stdout } = await exec(cmd, args);
    const versionLine = stdout.trim().split("\n")[0] || "unknown version";
    return { name, passed: true, message: `Available (${versionLine})`, required };
  } catch (err) {
    const notFound = (err as NodeJS.ErrnoException)?.code === "ENOENT";
    const message = notFound
      ? `${cmd} not found in PATH${opts.installHint ? `. ${opts.installHint}` : ""}`
      : `Error checking ${cmd}: ${err instanceof Error ? err.message : err}`;
    return { name, passed: false, message, required };
  }
}

export function checkGitAvailable(): Promise<HealthCheckResult> {
  return checkCliVersion("Git", "git", ["--version"]);
}

export function checkGhCliAvailable(): Promise<HealthCheckResult> {
  return checkCliVersion("GitHub CLI (gh)", "gh", ["--version"], {
    required: false,
    installHint: "Install from https://cli.github.com",
  });
}

export function checkGlabCliAvailable(): Promise<HealthCheckResult> {
  return checkCliVersion("GitLab CLI (glab)", "glab", ["--version"], {
    required: false,
    installHint: "Install from https://gitlab.com/gitlab-org/cli",
  });
}

export function checkLlmApiKey(): HealthCheckResult {
  const apiKeys = ["LLM_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY"];

  for (const keyName of apiKeys) {
    if (process.env[keyName]) {
      return { name: "LLM API Key", passed: true, message: `Found ${keyName}`, required: true };
    }
  }

  return {
    name: "LLM API Key",
    passed: false,
    message: `No LLM API key found. Set one of: ${apiKeys.join(", ")}`,
    required: true,
  };
}

export async function checkGithubToken(): Promise<HealthCheckResult> {
  if (process.env.GITHUB_TOKEN) {
    return { name: "GitHub Token", passed: true, message: "GITHUB_TOKEN is set", required: false };
  }

  try {
    await exec("gh", ["auth", "status"]);
    return { name: "GitHub Token", passed: true, message: "gh CLI is authenticated", required: false };
  } catch {
    return {
      name: "GitHub Token",
      passed: false,
      message: "GITHUB_TOKEN not set and gh CLI not authenticated",
      required: false,
    };
  }
}

export function checkGitlabToken(): HealthCheckResult {
  const gitlabTokens = ["GITLAB_TOKEN", "GITLAB_PRIVATE_TOKEN", "CI_JOB_TOKEN"];

  for (const tokenName of gitlabTokens) {
    if (process.env[tokenName]) {
      return { name: "GitLab Token", passed: true, message: `Found ${tokenName}`, required: false };
    }
  }

  return {
    name: "GitLab Token",
    passed: false,
    message: "No GitLab token found. Set GITLAB_TOKEN with api scope",
    required: false,
  };
}

export function checkDiskSpace(minGb = 1.0): HealthCheckResult {
  try {
    const dir = tmpdir();
    const stats = statfsSync(dir);
    const freeGb = (stats.bavail * stats.bsize) / 1024 ** 3;

    if (freeGb >= minGb) {
      return { name: "Disk Space", passed: true, message: `${freeGb.toFixed(1)}GB free in ${dir}`, required: false };
    }
    return {
      name: "Disk Space",
      passed: false,
      message: `Only ${freeGb.toFixed(1)}GB free (need ${minGb}GB)`,
      required: false,
    };
  } catch (err) {
    return {
      name: "Disk Space",
      passed: false,
      message: `Could not check disk space: ${err instanceof Error ? err.message : err}`,
      required: false,
    };
  }
}

export function checkNodeVersion(): HealthCheckResult {
  const version = process.versions.node;
  const major = Number(version.split(".")[0]);

  if (major >= 22) {
    return { name: "Node Version", passed: true, message: `Node ${version} (>=22 required)`, required: true };
  }
  return {
    name: "Node Version",
    passed: false,
    message: `Node ${version} is too old (>=22 required)`,
    required: true,
  };
}

export async function runHealthChecks(opts: { platform?: Platform; skipOptional?: boolean } = {}): Promise<HealthReport> {
  const { platform, skipOptional = false } = opts;
  const checks: HealthCheckResult[] = [];

  // Core checks (always run)
  checks.push(checkNodeVersion());
  checks.push(await checkGitAvailable());
  checks.push(checkLlmApiKey());

  if (!skipOptional) {
    checks.push(checkDiskSpace());
  }

  const ghCheck = await checkGhCliAvailable();
  const glabCheck = await checkGlabCliAvailable();
  const githubTokenCheck = await checkGithubToken();
  const gitlabTokenCheck = checkGitlabToken();

  // Mark platform-specific checks as required if the platform is known
  if (platform === "github" || platform === "gitea") {
    ghCheck.required = true;
    githubTokenCheck.required = true;
    checks.push(ghCheck, githubTokenCheck);
    if (!skipOptional) checks.push(glabCheck, gitlabTokenCheck);
  } else if (platform === "gitlab") {
    gitlabTokenCheck.required = true;
    checks.push(glabCheck, gitlabTokenCheck);
    if (!skipOptional) checks.push(ghCheck, githubTokenCheck);
  } else if (!skipOptional) {
    checks.push(ghCheck, githubTokenCheck, glabCheck, gitlabTokenCheck);
  }

  return { checks };
}
