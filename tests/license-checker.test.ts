import { describe, it, expect, vi, beforeEach, beforeAll } from "vitest";

const execMock = vi.fn();
vi.mock("../src/utils/exec.js", () => ({
  exec: execMock,
}));

let mod: typeof import("../src/license-checker.js");
beforeAll(async () => {
  mod = await import("../src/license-checker.js");
});

describe("classifyLicense", () => {
  it("allows well-known permissive licenses", () => {
    expect(mod.classifyLicense("MIT")).toBe("allowed");
    expect(mod.classifyLicense("Apache-2.0")).toBe("allowed");
    expect(mod.classifyLicense("BSD-3-Clause")).toBe("allowed");
    expect(mod.classifyLicense("isc")).toBe("allowed");
  });

  it("flags known copyleft/restrictive licenses", () => {
    expect(mod.classifyLicense("GPL-3.0")).toBe("flagged");
    expect(mod.classifyLicense("AGPL-3.0")).toBe("flagged");
    expect(mod.classifyLicense("SSPL-1.0")).toBe("flagged");
    expect(mod.classifyLicense("UNLICENSED")).toBe("flagged");
  });

  it("treats missing/empty license as unknown", () => {
    expect(mod.classifyLicense(null)).toBe("unknown");
    expect(mod.classifyLicense("")).toBe("unknown");
    expect(mod.classifyLicense("   ")).toBe("unknown");
  });

  it("treats unrecognized license strings as unknown", () => {
    expect(mod.classifyLicense("SomeCustomEULA-1.0")).toBe("unknown");
  });

  it("resolves OR expressions as allowed only if every alternative is allowed", () => {
    expect(mod.classifyLicense("(MIT OR Apache-2.0)")).toBe("allowed");
    expect(mod.classifyLicense("MIT OR GPL-3.0")).toBe("flagged");
  });
});

describe("parseNpmDependencies", () => {
  it("extracts the dependencies map", () => {
    const content = JSON.stringify({
      name: "pkg",
      dependencies: { chalk: "^5.0.0", commander: "^14.0.0" },
      devDependencies: { vitest: "^4.0.0" },
    });
    const deps = mod.parseNpmDependencies(content);
    expect(deps.get("chalk")).toBe("^5.0.0");
    expect(deps.get("commander")).toBe("^14.0.0");
    expect(deps.has("vitest")).toBe(false);
  });

  it("returns an empty map for malformed JSON", () => {
    expect(mod.parseNpmDependencies("{ not json")).toEqual(new Map());
  });

  it("returns an empty map when dependencies is absent", () => {
    expect(mod.parseNpmDependencies(JSON.stringify({ name: "pkg" }))).toEqual(new Map());
  });
});

describe("parsePythonRequirements", () => {
  it("parses pinned and ranged requirements", () => {
    const content = [
      "requests==2.31.0",
      "flask>=2.0,<3.0",
      "# a comment",
      "",
      "-r base.txt",
      "numpy",
    ].join("\n");
    const deps = mod.parsePythonRequirements(content);
    expect(deps.get("requests")).toBe("==2.31.0");
    expect(deps.get("flask")).toBe(">=2.0,<3.0");
    expect(deps.get("numpy")).toBe("");
    expect(deps.has("-r")).toBe(false);
  });

  it("strips extras from the package name", () => {
    const deps = mod.parsePythonRequirements("requests[security]==2.31.0");
    expect(deps.get("requests")).toBe("==2.31.0");
  });
});

describe("diffDependencyMaps", () => {
  it("returns entries new to next", () => {
    const prev = new Map([["a", "1.0.0"]]);
    const next = new Map([
      ["a", "1.0.0"],
      ["b", "2.0.0"],
    ]);
    const changes = mod.diffDependencyMaps(prev, next, "npm", "package.json");
    expect(changes).toEqual([{ ecosystem: "npm", name: "b", version: "2.0.0", manifestPath: "package.json" }]);
  });

  it("returns entries whose version changed", () => {
    const prev = new Map([["a", "1.0.0"]]);
    const next = new Map([["a", "2.0.0"]]);
    const changes = mod.diffDependencyMaps(prev, next, "npm", "package.json");
    expect(changes).toHaveLength(1);
    expect(changes[0].version).toBe("2.0.0");
  });

  it("returns nothing when versions are unchanged", () => {
    const prev = new Map([["a", "1.0.0"]]);
    const next = new Map([["a", "1.0.0"]]);
    expect(mod.diffDependencyMaps(prev, next, "npm", "package.json")).toEqual([]);
  });
});

describe("findManifestDependencyChanges", () => {
  beforeEach(() => {
    execMock.mockReset();
  });

  it("detects a newly added npm dependency", async () => {
    execMock.mockImplementation(async (_cmd: string, args: string[]) => {
      const ref = args[1];
      if (ref === "base:package.json") {
        return { stdout: JSON.stringify({ dependencies: { chalk: "^5.0.0" } }), stderr: "" };
      }
      if (ref === "HEAD:package.json") {
        return {
          stdout: JSON.stringify({ dependencies: { chalk: "^5.0.0", "left-pad": "^1.3.0" } }),
          stderr: "",
        };
      }
      throw new Error("not found");
    });

    const changes = await mod.findManifestDependencyChanges("/workspace", "base");
    expect(changes).toEqual([
      { ecosystem: "npm", name: "left-pad", version: "^1.3.0", manifestPath: "package.json" },
    ]);
  });

  it("returns nothing when no manifest changed", async () => {
    execMock.mockRejectedValue(new Error("not found"));
    const changes = await mod.findManifestDependencyChanges("/workspace", "base");
    expect(changes).toEqual([]);
  });
});

describe("checkDependencyLicenses", () => {
  const mockFetch = vi.fn();
  beforeEach(() => {
    mockFetch.mockReset();
    vi.stubGlobal("fetch", mockFetch);
  });

  it("classifies an npm package via the registry license field", async () => {
    mockFetch.mockResolvedValueOnce(new Response(JSON.stringify({ license: "MIT" }), { status: 200 }));
    const results = await mod.checkDependencyLicenses([
      { ecosystem: "npm", name: "chalk", version: "^5.0.0", manifestPath: "package.json" },
    ]);
    expect(results[0].verdict).toBe("allowed");
    expect(results[0].license).toBe("MIT");
  });

  it("classifies a PyPI package via classifiers when license field is empty", async () => {
    mockFetch.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          info: {
            license: "",
            classifiers: ["Programming Language :: Python :: 3", "License :: OSI Approved :: GNU Affero General Public License v3"],
          },
        }),
        { status: 200 },
      ),
    );
    const results = await mod.checkDependencyLicenses([
      { ecosystem: "pypi", name: "some-agpl-pkg", version: "==1.0.0", manifestPath: "requirements.txt" },
    ]);
    expect(results[0].verdict).toBe("flagged");
    expect(results[0].license).toBe("AGPL-3.0");
  });

  it("prefers PyPI's license_expression field over classifiers (modern packaging metadata)", async () => {
    // Packages built with modern setuptools/hatchling + pyproject.toml (e.g.
    // pylint) populate license_expression and leave classifiers/license empty.
    mockFetch.mockResolvedValueOnce(
      new Response(
        JSON.stringify({ info: { license: null, license_expression: "GPL-2.0-or-later", classifiers: [] } }),
        { status: 200 },
      ),
    );
    const results = await mod.checkDependencyLicenses([
      { ecosystem: "pypi", name: "pylint", version: "==3.0.0", manifestPath: "requirements.txt" },
    ]);
    expect(results[0].license).toBe("GPL-2.0-or-later");
    expect(results[0].verdict).toBe("flagged");
  });

  it("marks a package unknown when the registry lookup fails", async () => {
    mockFetch.mockRejectedValueOnce(new Error("network error"));
    const results = await mod.checkDependencyLicenses([
      { ecosystem: "npm", name: "some-pkg", version: "^1.0.0", manifestPath: "package.json" },
    ]);
    expect(results[0].verdict).toBe("unknown");
    expect(results[0].license).toBeNull();
  });
});

describe("buildLicenseFindings", () => {
  const mockFetch = vi.fn();
  beforeEach(() => {
    execMock.mockReset();
    mockFetch.mockReset();
    vi.stubGlobal("fetch", mockFetch);
  });

  it("returns no findings when nothing changed", async () => {
    execMock.mockRejectedValue(new Error("not found"));
    const findings = await mod.buildLicenseFindings({ workspacePath: "/workspace", baseRef: "base" });
    expect(findings).toEqual([]);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("emits a P1 finding for a flagged license and skips allowed ones", async () => {
    execMock.mockImplementation(async (_cmd: string, args: string[]) => {
      const ref = args[1];
      if (ref === "base:package.json") {
        return { stdout: JSON.stringify({ dependencies: {} }), stderr: "" };
      }
      if (ref === "HEAD:package.json") {
        return {
          stdout: JSON.stringify({
            dependencies: { chalk: "^5.0.0", "gpl-pkg": "^1.0.0" },
          }),
          stderr: "",
        };
      }
      throw new Error("not found");
    });
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes("chalk")) {
        return new Response(JSON.stringify({ license: "MIT" }), { status: 200 });
      }
      return new Response(JSON.stringify({ license: "GPL-3.0" }), { status: 200 });
    });

    const findings = await mod.buildLicenseFindings({ workspacePath: "/workspace", baseRef: "base" });

    expect(findings).toHaveLength(1);
    expect(findings[0].priority).toBe(1);
    expect(findings[0].title).toContain("gpl-pkg");
    expect(findings[0].code_location.absolute_file_path).toBe("/workspace/package.json");
  });
});
