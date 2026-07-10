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

  it("resolves OR expressions as allowed when any alternative is allowed (licensee's choice)", () => {
    expect(mod.classifyLicense("(MIT OR Apache-2.0)")).toBe("allowed");
    // SPDX OR = the consumer picks one; a permissive option makes it allowed.
    expect(mod.classifyLicense("MIT OR GPL-3.0")).toBe("allowed");
    expect(mod.classifyLicense("(GPL-2.0-or-later OR MIT)")).toBe("allowed");
    // Only flagged when every alternative is restrictive.
    expect(mod.classifyLicense("GPL-3.0 OR AGPL-3.0")).toBe("flagged");
  });

  it("resolves AND expressions as flagged if any component is restrictive", () => {
    expect(mod.classifyLicense("Apache-2.0 AND MIT")).toBe("allowed");
    expect(mod.classifyLicense("Apache-2.0 AND GPL-3.0")).toBe("flagged");
  });

  it("falls back to keyword matching for free-text license names (Maven <name>, PyPI free text)", () => {
    expect(mod.classifyLicense("The Apache Software License, Version 2.0")).toBe("allowed");
    expect(mod.classifyLicense("Apache License, Version 2.0")).toBe("allowed");
    expect(mod.classifyLicense("GNU Lesser General Public License")).toBe("flagged");
    // Hibernate (default Spring Boot JPA provider) reports the pre-2.1 name.
    expect(mod.classifyLicense("GNU Library General Public License v2.1 or later")).toBe("flagged");
    expect(mod.classifyLicense("GNU General Public License v2.0")).toBe("flagged");
    expect(mod.classifyLicense("GNU Affero General Public License v3")).toBe("flagged");
    expect(mod.classifyLicense("Eclipse Public License - v 2.0")).toBe("allowed");
  });

  it("checks AGPL/LGPL keywords before the plain GPL keyword they'd otherwise also match", () => {
    // "lgpl" and "agpl" both contain "gpl" as a substring — order matters.
    // Both correctly resolve to "flagged" either way here, but exercises the
    // free-text keyword path (these aren't exact-match SPDX ids) rather than
    // silently passing because of the exact-match fast path.
    expect(mod.classifyLicense("GNU Lesser General Public License v3.0 or later")).toBe("flagged");
    expect(mod.classifyLicense("GNU Affero General Public License, version 3.0.1")).toBe("flagged");
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

describe("parsePyprojectToml", () => {
  it("parses PEP 621 [project].dependencies", () => {
    const content = `
[project]
name = "myapp"
dependencies = [
  "requests>=2.31.0",
  "flask==2.3.0",
]
`;
    const deps = mod.parsePyprojectToml(content);
    expect(deps.get("requests")).toBe(">=2.31.0");
    expect(deps.get("flask")).toBe("==2.3.0");
  });

  it("does not truncate the dependencies array at an extras bracket", () => {
    // A `]` inside "uvicorn[standard]" must not end the array capture early —
    // every dependency after it would otherwise be silently dropped.
    const content = `
[project]
dependencies = ["requests>=2.31.0", "uvicorn[standard]>=0.20", "flask==2.3.0"]
`;
    const deps = mod.parsePyprojectToml(content);
    expect(deps.get("requests")).toBe(">=2.31.0");
    expect(deps.get("uvicorn")).toBe(">=0.20");
    expect(deps.get("flask")).toBe("==2.3.0");
  });

  it("parses Poetry [tool.poetry.dependencies], both plain and inline-table forms", () => {
    const content = `
[tool.poetry.dependencies]
python = "^3.11"
numpy = "^1.26.0"
django = {version = "^4.2", extras = ["bcrypt"]}
`;
    const deps = mod.parsePyprojectToml(content);
    expect(deps.has("python")).toBe(false);
    expect(deps.get("numpy")).toBe("^1.26.0");
    expect(deps.get("django")).toBe("^4.2");
  });

  it("returns an empty map when neither section is present", () => {
    expect(mod.parsePyprojectToml("[build-system]\nrequires = []\n")).toEqual(new Map());
  });
});

describe("parseMavenDependencies", () => {
  it("extracts groupId:artifactId -> version from <dependencies>", () => {
    const xml = `
<project>
  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-web</artifactId>
      <version>3.2.0</version>
    </dependency>
  </dependencies>
</project>`;
    const deps = mod.parseMavenDependencies(xml);
    expect(deps.get("org.springframework.boot:spring-boot-starter-web")).toBe("3.2.0");
  });

  it("resolves ${property} versions against <properties>", () => {
    const xml = `
<project>
  <properties>
    <spring-boot.version>3.2.0</spring-boot.version>
  </properties>
  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-web</artifactId>
      <version>\${spring-boot.version}</version>
    </dependency>
  </dependencies>
</project>`;
    const deps = mod.parseMavenDependencies(xml);
    expect(deps.get("org.springframework.boot:spring-boot-starter-web")).toBe("3.2.0");
  });

  it("leaves unresolvable property versions empty rather than guessing", () => {
    const xml = `
<project>
  <dependencies>
    <dependency>
      <groupId>com.example</groupId>
      <artifactId>lib</artifactId>
      <version>\${parent.managed.version}</version>
    </dependency>
  </dependencies>
</project>`;
    const deps = mod.parseMavenDependencies(xml);
    expect(deps.get("com.example:lib")).toBe("");
  });

  it("ignores <dependencyManagement> entries (constraints, not actual dependencies)", () => {
    const xml = `
<project>
  <dependencyManagement>
    <dependencies>
      <dependency>
        <groupId>com.example</groupId>
        <artifactId>managed-only</artifactId>
        <version>1.0.0</version>
      </dependency>
    </dependencies>
  </dependencyManagement>
  <dependencies>
    <dependency>
      <groupId>com.example</groupId>
      <artifactId>real-dep</artifactId>
      <version>2.0.0</version>
    </dependency>
  </dependencies>
</project>`;
    const deps = mod.parseMavenDependencies(xml);
    expect(deps.has("com.example:managed-only")).toBe(false);
    expect(deps.get("com.example:real-dep")).toBe("2.0.0");
  });

  it("ignores <exclusions> so an excluded transitive dep isn't misread as a direct one", () => {
    const xml = `
<project>
  <dependencies>
    <dependency>
      <groupId>com.example</groupId>
      <artifactId>real-dep</artifactId>
      <version>2.0.0</version>
      <exclusions>
        <exclusion>
          <groupId>com.excluded</groupId>
          <artifactId>excluded-dep</artifactId>
        </exclusion>
      </exclusions>
    </dependency>
  </dependencies>
</project>`;
    const deps = mod.parseMavenDependencies(xml);
    expect(deps.get("com.example:real-dep")).toBe("2.0.0");
    expect(deps.has("com.excluded:excluded-dep")).toBe(false);
    expect(deps.size).toBe(1);
  });
});

describe("parseGoModDependencies", () => {
  it("parses a require(...) block, skipping indirect entries", () => {
    const content = `
module example.com/app

require (
	github.com/gin-gonic/gin v1.9.1
	github.com/stretchr/testify v1.8.4 // indirect
)
`;
    const deps = mod.parseGoModDependencies(content);
    expect(deps.get("github.com/gin-gonic/gin")).toBe("v1.9.1");
    expect(deps.has("github.com/stretchr/testify")).toBe(false);
  });

  it("parses single-line require statements", () => {
    const deps = mod.parseGoModDependencies("require github.com/pkg/errors v0.9.1\n");
    expect(deps.get("github.com/pkg/errors")).toBe("v0.9.1");
  });

  it("handles multiple require blocks in the same file", () => {
    const content = `
require (
	github.com/a/a v1.0.0
)

require (
	github.com/b/b v2.0.0 // indirect
)
`;
    const deps = mod.parseGoModDependencies(content);
    expect(deps.get("github.com/a/a")).toBe("v1.0.0");
    expect(deps.has("github.com/b/b")).toBe(false);
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
      if (args[0] === "merge-base") return { stdout: "base", stderr: "" };
      if (args[0] === "diff") return { stdout: "package.json\nsrc/index.ts\n", stderr: "" };
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

  it("discovers manifests in subdirectories (multi-module Maven / monorepo)", async () => {
    execMock.mockImplementation(async (_cmd: string, args: string[]) => {
      if (args[0] === "merge-base") return { stdout: "base", stderr: "" };
      if (args[0] === "diff") return { stdout: "services/api/pom.xml\n", stderr: "" };
      const ref = args[1];
      if (ref === "base:services/api/pom.xml") {
        return { stdout: "<project><dependencies></dependencies></project>", stderr: "" };
      }
      if (ref === "HEAD:services/api/pom.xml") {
        return {
          stdout:
            "<project><dependencies><dependency><groupId>com.example</groupId><artifactId>lib</artifactId><version>1.0.0</version></dependency></dependencies></project>",
          stderr: "",
        };
      }
      throw new Error("not found");
    });

    const changes = await mod.findManifestDependencyChanges("/workspace", "base");
    expect(changes).toEqual([
      { ecosystem: "maven", name: "com.example:lib", version: "1.0.0", manifestPath: "services/api/pom.xml" },
    ]);
  });

  it("honors restrictToPaths, ignoring manifests outside the MR's real changed files", async () => {
    // Simulates a CI merge-ref checkout where `git diff` sees a pom.xml from
    // another module (merged into the target by a different MR). Only the
    // manifest in the MR's authoritative file list should be checked.
    execMock.mockImplementation(async (_cmd: string, args: string[]) => {
      if (args[0] === "merge-base") return { stdout: "base", stderr: "" };
      if (args[0] === "diff") return { stdout: "mine/pom.xml\nother-module/pom.xml\n", stderr: "" };
      const ref = args[1];
      if (ref === "base:mine/pom.xml") return { stdout: "<project><dependencies></dependencies></project>", stderr: "" };
      if (ref === "HEAD:mine/pom.xml") {
        return {
          stdout:
            "<project><dependencies><dependency><groupId>com.example</groupId><artifactId>mine</artifactId><version>1.0.0</version></dependency></dependencies></project>",
          stderr: "",
        };
      }
      // other-module/pom.xml would also show a change, but it's not in the MR.
      if (ref === "base:other-module/pom.xml") return { stdout: "<project><dependencies></dependencies></project>", stderr: "" };
      if (ref === "HEAD:other-module/pom.xml") {
        return {
          stdout:
            "<project><dependencies><dependency><groupId>com.example</groupId><artifactId>other</artifactId><version>2.0.0</version></dependency></dependencies></project>",
          stderr: "",
        };
      }
      throw new Error("not found");
    });

    const changes = await mod.findManifestDependencyChanges(
      "/workspace",
      "base",
      "HEAD",
      false,
      new Set(["mine/pom.xml"]),
    );
    expect(changes).toEqual([
      { ecosystem: "maven", name: "com.example:mine", version: "1.0.0", manifestPath: "mine/pom.xml" },
    ]);
  });

  it("returns nothing (and does not flood) when the base ref is unreachable", async () => {
    // Shallow CI clone: merge-base and diff both fail. Must skip, not treat
    // the whole HEAD manifest as newly added.
    execMock.mockImplementation(async (_cmd: string, args: string[]) => {
      if (args[0] === "merge-base") throw new Error("no merge base");
      if (args[0] === "diff") throw new Error("bad object base");
      throw new Error("not found");
    });
    const changes = await mod.findManifestDependencyChanges("/workspace", "base");
    expect(changes).toEqual([]);
  });

  it("returns nothing when no manifest changed", async () => {
    execMock.mockImplementation(async (_cmd: string, args: string[]) => {
      if (args[0] === "merge-base") return { stdout: "base", stderr: "" };
      if (args[0] === "diff") return { stdout: "src/index.ts\nREADME.md\n", stderr: "" };
      throw new Error("not found");
    });
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

  it("classifies a Maven artifact by fetching its POM from Maven Central", async () => {
    mockFetch.mockResolvedValueOnce(
      new Response(
        "<project><licenses><license><name>The Apache Software License, Version 2.0</name></license></licenses></project>",
        { status: 200 },
      ),
    );
    const results = await mod.checkDependencyLicenses([
      { ecosystem: "maven", name: "com.example:lib", version: "1.0.0", manifestPath: "pom.xml" },
    ]);
    expect(mockFetch).toHaveBeenCalledWith(
      "https://repo1.maven.org/maven2/com/example/lib/1.0.0/lib-1.0.0.pom",
      expect.anything(),
    );
    expect(results[0].verdict).toBe("allowed");
  });

  it("falls back to the parent POM when a Maven artifact declares no <licenses>", async () => {
    mockFetch
      .mockResolvedValueOnce(
        new Response(
          "<project><parent><groupId>com.example</groupId><artifactId>parent-pom</artifactId><version>1.0.0</version></parent></project>",
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response("<project><licenses><license><name>MIT License</name></license></licenses></project>", {
          status: 200,
        }),
      );
    const results = await mod.checkDependencyLicenses([
      { ecosystem: "maven", name: "com.example:lib", version: "1.0.0", manifestPath: "pom.xml" },
    ]);
    expect(mockFetch).toHaveBeenCalledTimes(2);
    expect(results[0].license).toBe("MIT License");
    expect(results[0].verdict).toBe("allowed");
  });

  it("falls back to Maven Central latest release when the version is unresolved (Spring Boot BOM case)", async () => {
    // Version-less <dependency> entries (managed by spring-boot-starter-parent)
    // resolve their license via the latest published release.
    mockFetch.mockImplementation(async (url: string) => {
      if (url.endsWith("/maven-metadata.xml")) {
        return new Response("<metadata><versioning><release>3.2.0</release></versioning></metadata>", { status: 200 });
      }
      if (url.endsWith(".pom")) {
        return new Response(
          "<project><licenses><license><name>Apache License, Version 2.0</name></license></licenses></project>",
          { status: 200 },
        );
      }
      return new Response("", { status: 404 });
    });
    const results = await mod.checkDependencyLicenses([
      { ecosystem: "maven", name: "org.springframework.boot:spring-boot-starter-web", version: null, manifestPath: "pom.xml" },
    ]);
    expect(mockFetch).toHaveBeenCalledWith(
      "https://repo1.maven.org/maven2/org/springframework/boot/spring-boot-starter-web/maven-metadata.xml",
      expect.anything(),
    );
    expect(results[0].verdict).toBe("allowed");
  });

  it("marks a version-less Maven dep unknown when even the latest release can't be found", async () => {
    mockFetch.mockResolvedValue(new Response("", { status: 404 }));
    const results = await mod.checkDependencyLicenses([
      { ecosystem: "maven", name: "com.example:lib", version: null, manifestPath: "pom.xml" },
    ]);
    expect(results[0].verdict).toBe("unknown");
  });

  it("classifies a GitHub-hosted Go module via GitHub's license API", async () => {
    mockFetch.mockResolvedValueOnce(new Response(JSON.stringify({ license: { spdx_id: "MIT" } }), { status: 200 }));
    const results = await mod.checkDependencyLicenses([
      { ecosystem: "go", name: "github.com/gin-gonic/gin", version: "v1.9.1", manifestPath: "go.mod" },
    ]);
    expect(results[0].license).toBe("MIT");
    expect(results[0].verdict).toBe("allowed");
  });

  it("special-cases golang.org/x/* as BSD-3-Clause without an API call", async () => {
    const results = await mod.checkDependencyLicenses([
      { ecosystem: "go", name: "golang.org/x/net", version: "v0.17.0", manifestPath: "go.mod" },
    ]);
    expect(mockFetch).not.toHaveBeenCalled();
    expect(results[0].license).toBe("BSD-3-Clause");
    expect(results[0].verdict).toBe("allowed");
  });

  it("marks non-GitHub-hosted Go modules unknown rather than guessing", async () => {
    const results = await mod.checkDependencyLicenses([
      { ecosystem: "go", name: "gitlab.com/example/lib", version: "v1.0.0", manifestPath: "go.mod" },
    ]);
    expect(mockFetch).not.toHaveBeenCalled();
    expect(results[0].verdict).toBe("unknown");
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
    execMock.mockImplementation(async (_cmd: string, args: string[]) => {
      if (args[0] === "merge-base") return { stdout: "base", stderr: "" };
      if (args[0] === "diff") return { stdout: "", stderr: "" };
      throw new Error("not found");
    });
    const findings = await mod.buildLicenseFindings({ workspacePath: "/workspace", baseRef: "base" });
    expect(findings).toEqual([]);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("emits a P1 finding for a flagged license and skips allowed ones", async () => {
    execMock.mockImplementation(async (_cmd: string, args: string[]) => {
      if (args[0] === "merge-base") return { stdout: "base", stderr: "" };
      if (args[0] === "diff") return { stdout: "package.json\n", stderr: "" };
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

  it("suppresses findings when all registry lookups fail (assumes no network access)", async () => {
    execMock.mockImplementation(async (_cmd: string, args: string[]) => {
      if (args[0] === "merge-base") return { stdout: "base", stderr: "" };
      if (args[0] === "diff") return { stdout: "package.json\n", stderr: "" };
      const ref = args[1];
      if (ref === "base:package.json") return { stdout: JSON.stringify({ dependencies: {} }), stderr: "" };
      if (ref === "HEAD:package.json") {
        return {
          stdout: JSON.stringify({ dependencies: { a: "^1", b: "^1", c: "^1" } }),
          stderr: "",
        };
      }
      throw new Error("not found");
    });
    mockFetch.mockRejectedValue(new Error("ENETUNREACH"));

    const findings = await mod.buildLicenseFindings({ workspacePath: "/workspace", baseRef: "base" });
    expect(findings).toEqual([]);
  });

  it("suppresses unverified/unknown findings by default (only flags restrictive licenses)", async () => {
    execMock.mockImplementation(async (_cmd: string, args: string[]) => {
      if (args[0] === "merge-base") return { stdout: "base", stderr: "" };
      if (args[0] === "diff") return { stdout: "package.json\n", stderr: "" };
      const ref = args[1];
      if (ref === "base:package.json") return { stdout: JSON.stringify({ dependencies: {} }), stderr: "" };
      if (ref === "HEAD:package.json") return { stdout: JSON.stringify({ dependencies: { "mystery-pkg": "^1.0.0" } }), stderr: "" };
      throw new Error("not found");
    });
    // Registry returns no license -> verdict "unknown".
    mockFetch.mockResolvedValue(new Response(JSON.stringify({}), { status: 200 }));

    const findings = await mod.buildLicenseFindings({ workspacePath: "/workspace", baseRef: "base" });
    expect(findings).toEqual([]); // unknown suppressed by default
  });

  it("includes unverified findings when HODOR_LICENSE_REPORT_UNVERIFIED=true", async () => {
    execMock.mockImplementation(async (_cmd: string, args: string[]) => {
      if (args[0] === "merge-base") return { stdout: "base", stderr: "" };
      if (args[0] === "diff") return { stdout: "package.json\n", stderr: "" };
      const ref = args[1];
      if (ref === "base:package.json") return { stdout: JSON.stringify({ dependencies: {} }), stderr: "" };
      if (ref === "HEAD:package.json") return { stdout: JSON.stringify({ dependencies: { "mystery-pkg": "^1.0.0" } }), stderr: "" };
      throw new Error("not found");
    });
    mockFetch.mockResolvedValue(new Response(JSON.stringify({}), { status: 200 }));

    process.env.HODOR_LICENSE_REPORT_UNVERIFIED = "true";
    try {
      const findings = await mod.buildLicenseFindings({ workspacePath: "/workspace", baseRef: "base" });
      expect(findings).toHaveLength(1);
      expect(findings[0].priority).toBe(2);
      expect(findings[0].title).toContain("unverified");
    } finally {
      delete process.env.HODOR_LICENSE_REPORT_UNVERIFIED;
    }
  });

  it("skips internal dependencies matching HODOR_LICENSE_INTERNAL_GROUPS (no lookup, no finding)", async () => {
    execMock.mockImplementation(async (_cmd: string, args: string[]) => {
      if (args[0] === "merge-base") return { stdout: "base", stderr: "" };
      if (args[0] === "diff") return { stdout: "pom.xml\n", stderr: "" };
      const ref = args[1];
      if (ref === "base:pom.xml") return { stdout: "<project><dependencies></dependencies></project>", stderr: "" };
      if (ref === "HEAD:pom.xml") {
        return {
          stdout:
            "<project><dependencies><dependency><groupId>com.coronet</groupId><artifactId>iplookup-sdk</artifactId><version>1.0.0</version></dependency></dependencies></project>",
          stderr: "",
        };
      }
      throw new Error("not found");
    });

    process.env.HODOR_LICENSE_INTERNAL_GROUPS = "com.coronet";
    process.env.HODOR_LICENSE_REPORT_UNVERIFIED = "true"; // even with unverified on, internal is skipped
    try {
      const findings = await mod.buildLicenseFindings({ workspacePath: "/workspace", baseRef: "base" });
      expect(findings).toEqual([]);
      expect(mockFetch).not.toHaveBeenCalled(); // skipped before any lookup
    } finally {
      delete process.env.HODOR_LICENSE_INTERNAL_GROUPS;
      delete process.env.HODOR_LICENSE_REPORT_UNVERIFIED;
    }
  });
});

describe("filterChangesToAuthoritativeDiff", () => {
  const mavenChange = (name: string, version: string | null, manifestPath = "pom.xml") =>
    ({ ecosystem: "maven" as const, name, version, manifestPath });

  it("keeps a dependency whose artifactId appears on an added line", () => {
    const diff = [
      "diff --git a/pom.xml b/pom.xml",
      "--- a/pom.xml",
      "+++ b/pom.xml",
      "@@ -1,3 +1,6 @@",
      "         <dependency>",
      "+            <groupId>com.source</groupId>",
      "+            <artifactId>source-lib</artifactId>",
      "+            <version>1.0.0</version>",
      "         </dependency>",
    ].join("\n");
    const changes = [mavenChange("com.source:source-lib", "1.0.0")];
    expect(mod.filterChangesToAuthoritativeDiff(changes, diff)).toEqual(changes);
  });

  it("drops a target-only dependency that never appears on an added line", () => {
    // The MR only added `source-lib`; `target-lib` was added by the target
    // branch and merged in, so the stale-base scan surfaced it too.
    const diff = [
      "diff --git a/pom.xml b/pom.xml",
      "--- a/pom.xml",
      "+++ b/pom.xml",
      "@@ -1,3 +1,4 @@",
      "         <dependency>",
      "+            <artifactId>source-lib</artifactId>",
      "         </dependency>",
    ].join("\n");
    const changes = [
      mavenChange("com.source:source-lib", "1.0.0"),
      mavenChange("com.target:target-lib", "2.0.0"),
    ];
    const kept = mod.filterChangesToAuthoritativeDiff(changes, diff);
    expect(kept.map((c) => c.name)).toEqual(["com.source:source-lib"]);
  });

  it("keeps a version bump by matching the new version on an added line", () => {
    const diff = [
      "diff --git a/pom.xml b/pom.xml",
      "--- a/pom.xml",
      "+++ b/pom.xml",
      "@@ -10,3 +10,3 @@",
      "             <artifactId>bumped-lib</artifactId>",
      "-            <version>1.0.0</version>",
      "+            <version>2.0.0</version>",
    ].join("\n");
    const changes = [mavenChange("com.x:bumped-lib", "2.0.0")];
    expect(mod.filterChangesToAuthoritativeDiff(changes, diff)).toEqual(changes);
  });

  it("drops all changes for a manifest absent from the authoritative diff", () => {
    const diff = [
      "diff --git a/other/pom.xml b/other/pom.xml",
      "--- a/other/pom.xml",
      "+++ b/other/pom.xml",
      "@@ -1 +1,2 @@",
      "+            <artifactId>x</artifactId>",
    ].join("\n");
    const changes = [mavenChange("com.x:only-here", "1.0.0", "pom.xml")];
    expect(mod.filterChangesToAuthoritativeDiff(changes, diff)).toEqual([]);
  });
});

describe("addedLinesByFile", () => {
  it("collects added lines per new-file path and ignores headers/removals", () => {
    const diff = [
      "diff --git a/a.txt b/a.txt",
      "--- a/a.txt",
      "+++ b/a.txt",
      "@@ -1,2 +1,2 @@",
      "-old",
      "+new-a",
      " ctx",
      "diff --git a/b.txt b/b.txt",
      "--- a/b.txt",
      "+++ b/b.txt",
      "@@ -0,0 +1 @@",
      "+new-b",
    ].join("\n");
    const map = mod.addedLinesByFile(diff);
    expect(map.get("a.txt")).toEqual(["new-a"]);
    expect(map.get("b.txt")).toEqual(["new-b"]);
  });

  it("attributes nothing to a deleted file (+++ /dev/null)", () => {
    const diff = [
      "diff --git a/gone.txt b/gone.txt",
      "--- a/gone.txt",
      "+++ /dev/null",
      "@@ -1 +0,0 @@",
      "-bye",
    ].join("\n");
    const map = mod.addedLinesByFile(diff);
    expect(map.size).toBe(0);
  });
});
