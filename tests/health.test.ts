import { describe, it, expect, vi, beforeEach } from "vitest";

const execMock = vi.fn();

vi.mock("../src/utils/exec.js", () => ({
  exec: execMock,
}));

const ORIGINAL_ENV = { ...process.env };

describe("health checks", () => {
  beforeEach(() => {
    execMock.mockReset();
    process.env = { ...ORIGINAL_ENV };
    delete process.env.LLM_API_KEY;
    delete process.env.ANTHROPIC_API_KEY;
    delete process.env.OPENAI_API_KEY;
    delete process.env.GEMINI_API_KEY;
    delete process.env.DEEPSEEK_API_KEY;
    delete process.env.GITHUB_TOKEN;
    delete process.env.GITLAB_TOKEN;
    delete process.env.GITLAB_PRIVATE_TOKEN;
    delete process.env.CI_JOB_TOKEN;
  });

  it("checkLlmApiKey passes when a known key is set", async () => {
    const { checkLlmApiKey } = await import("../src/health.js");
    process.env.ANTHROPIC_API_KEY = "sk-test";
    const result = checkLlmApiKey();
    expect(result.passed).toBe(true);
    expect(result.message).toContain("ANTHROPIC_API_KEY");
  });

  it("checkLlmApiKey fails when no key is set", async () => {
    const { checkLlmApiKey } = await import("../src/health.js");
    const result = checkLlmApiKey();
    expect(result.passed).toBe(false);
    expect(result.required).toBe(true);
  });

  it("checkGitlabToken finds CI_JOB_TOKEN", async () => {
    const { checkGitlabToken } = await import("../src/health.js");
    process.env.CI_JOB_TOKEN = "abc";
    const result = checkGitlabToken();
    expect(result.passed).toBe(true);
    expect(result.required).toBe(false);
  });

  it("checkGitAvailable passes when exec resolves", async () => {
    execMock.mockResolvedValueOnce({ stdout: "git version 2.42.0\n", stderr: "" });
    const { checkGitAvailable } = await import("../src/health.js");
    const result = await checkGitAvailable();
    expect(result.passed).toBe(true);
    expect(result.message).toContain("git version 2.42.0");
  });

  it("checkGitAvailable fails when the binary is missing", async () => {
    const err = Object.assign(new Error("not found"), { code: "ENOENT" });
    execMock.mockRejectedValueOnce(err);
    const { checkGitAvailable } = await import("../src/health.js");
    const result = await checkGitAvailable();
    expect(result.passed).toBe(false);
    expect(result.message).toContain("not found in PATH");
  });

  it("runHealthChecks marks gitlab token as required when platform is gitlab", async () => {
    execMock.mockResolvedValue({ stdout: "ok\n", stderr: "" });
    process.env.ANTHROPIC_API_KEY = "sk-test";
    const { runHealthChecks, failedChecks } = await import("../src/health.js");

    const report = await runHealthChecks({ platform: "gitlab" });
    const gitlabTokenCheck = report.checks.find((c) => c.name === "GitLab Token");
    expect(gitlabTokenCheck?.required).toBe(true);
    expect(failedChecks(report)).toContainEqual(expect.objectContaining({ name: "GitLab Token" }));
  });

  it("runHealthChecks passes when required checks all pass", async () => {
    execMock.mockResolvedValue({ stdout: "ok\n", stderr: "" });
    process.env.ANTHROPIC_API_KEY = "sk-test";
    process.env.GITLAB_TOKEN = "token";
    const { runHealthChecks, allChecksPassed } = await import("../src/health.js");

    const report = await runHealthChecks({ platform: "gitlab" });
    expect(allChecksPassed(report)).toBe(true);
  });

  it("skipOptional omits non-required checks", async () => {
    execMock.mockResolvedValue({ stdout: "ok\n", stderr: "" });
    process.env.ANTHROPIC_API_KEY = "sk-test";
    const { runHealthChecks } = await import("../src/health.js");

    const report = await runHealthChecks({ skipOptional: true });
    expect(report.checks.find((c) => c.name === "Disk Space")).toBeUndefined();
    expect(report.checks.find((c) => c.name === "GitHub CLI (gh)")).toBeUndefined();
  });
});
