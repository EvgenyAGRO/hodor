import { exec } from "./utils/exec.js";
import { logger } from "./utils/logger.js";
import type { ReviewFinding } from "./types.js";

export type Ecosystem = "npm" | "pypi" | "maven" | "go";

export interface DependencyChange {
  ecosystem: Ecosystem;
  // For maven this is "groupId:artifactId"; for everything else it's the
  // package/module name as declared in the manifest.
  name: string;
  version: string | null;
  manifestPath: string;
}

export type LicenseVerdict = "allowed" | "flagged" | "unknown";

export interface LicenseCheckResult {
  change: DependencyChange;
  license: string | null;
  verdict: LicenseVerdict;
}

// Permissive licenses safe for unrestricted commercial use. SPDX identifiers
// plus a few free-text variants registries commonly return verbatim. This
// list (and FLAGGED_LICENSES below) is a reasonable general-purpose default —
// review and adjust it against your own legal/compliance policy.
const ALLOWED_LICENSES = new Set(
  [
    "MIT", "Apache-2.0", "Apache 2.0", "Apache License 2.0",
    "BSD-2-Clause", "BSD-3-Clause", "BSD-3-Clause-Clear", "BSD", "0BSD",
    "ISC", "Unlicense", "CC0-1.0", "Zlib", "WTFPL", "Python-2.0", "PSF-2.0",
    "PSF", "BlueOak-1.0.0", "Artistic-2.0", "MIT-0", "CC-BY-4.0",
    "EPL-1.0", "EPL-2.0", "MPL-2.0",
  ].map((l) => l.toLowerCase()),
);

// Copyleft/restrictive licenses that commonly conflict with unrestricted
// commercial/proprietary use — flagged outright rather than left "unknown".
const FLAGGED_LICENSES = new Set(
  [
    "GPL-1.0", "GPL-2.0", "GPL-3.0", "GPL-2.0-only", "GPL-3.0-only",
    "GPL-2.0-or-later", "GPL-3.0-or-later",
    "AGPL-1.0", "AGPL-3.0", "AGPL-3.0-only", "AGPL-3.0-or-later",
    "LGPL-2.0", "LGPL-2.1", "LGPL-3.0",
    "SSPL-1.0", "BUSL-1.1", "Commons-Clause",
    "CC-BY-NC-4.0", "CC-BY-NC-SA-4.0", "CC-BY-NC-ND-4.0",
    "EUPL-1.1", "EUPL-1.2", "OSL-3.0", "CPAL-1.0", "CPOL-1.02",
    "UNLICENSED", "PROPRIETARY",
  ].map((l) => l.toLowerCase()),
);

// PyPI's "license" field is free text and often empty; classifiers are the
// more reliable source. Maps common OSI classifier suffixes to SPDX ids.
const PYPI_CLASSIFIER_TO_LICENSE: Record<string, string> = {
  "mit license": "MIT",
  "apache software license": "Apache-2.0",
  "bsd license": "BSD-3-Clause",
  "isc license (iscl)": "ISC",
  "python software foundation license": "PSF-2.0",
  "gnu general public license v2 (gplv2)": "GPL-2.0",
  "gnu general public license v3 (gplv3)": "GPL-3.0",
  "gnu general public license (gpl)": "GPL-3.0",
  "gnu lesser general public license v2 (lgplv2)": "LGPL-2.0",
  "gnu lesser general public license v3 (lgplv3)": "LGPL-3.0",
  "gnu affero general public license v3": "AGPL-3.0",
  "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
};

/**
 * Keyword-based fallback classifier for free-text license names — Maven POMs
 * and some PyPI packages report a human-readable <name> rather than an SPDX
 * id (e.g. "The Apache Software License, Version 2.0", "GNU Lesser General
 * Public License"). Order matters: check the more specific copyleft variants
 * (AGPL/LGPL) before the plain "gpl" keyword they'd otherwise also match.
 */
function classifyByKeyword(normalized: string): LicenseVerdict {
  if (/\bagpl\b|affero general public/.test(normalized)) return "flagged";
  if (/\blgpl\b|lesser general public/.test(normalized)) return "flagged";
  if (/\bgpl\b|gnu general public/.test(normalized)) return "flagged";
  if (/sspl|server side public/.test(normalized)) return "flagged";
  if (/business source license|\bbusl\b/.test(normalized)) return "flagged";
  if (/commons clause/.test(normalized)) return "flagged";
  if (/creative commons.*non.?commercial|cc.by.nc/.test(normalized)) return "flagged";
  if (/european union public licen[cs]e|\beupl\b/.test(normalized)) return "flagged";

  if (/\bmit\b/.test(normalized)) return "allowed";
  if (/apache/.test(normalized)) return "allowed";
  if (/\bbsd\b/.test(normalized)) return "allowed";
  if (/\bisc\b/.test(normalized)) return "allowed";
  if (/eclipse public licen[cs]e|\bepl\b/.test(normalized)) return "allowed";
  if (/mozilla public licen[cs]e|\bmpl\b/.test(normalized)) return "allowed";
  if (/public domain|unlicense|\bcc0\b/.test(normalized)) return "allowed";
  if (/python software foundation|\bpsf\b/.test(normalized)) return "allowed";
  if (/\bzlib\b/.test(normalized)) return "allowed";

  return "unknown";
}

export function classifyLicense(license: string | null): LicenseVerdict {
  if (!license) return "unknown";
  const normalized = license.trim().toLowerCase();
  if (!normalized) return "unknown";
  if (ALLOWED_LICENSES.has(normalized)) return "allowed";
  if (FLAGGED_LICENSES.has(normalized)) return "flagged";

  // SPDX dual/OR expressions like "(MIT OR Apache-2.0)": allowed if every
  // alternative is allowed, flagged if any alternative is a known bad actor.
  const parts = normalized
    .replace(/[()]/g, "")
    .split(/\s+or\s+|\s+and\s+/)
    .map((p) => p.trim())
    .filter(Boolean);
  if (parts.length > 1) {
    if (parts.some((p) => FLAGGED_LICENSES.has(p))) return "flagged";
    if (parts.every((p) => ALLOWED_LICENSES.has(p))) return "allowed";
  }

  return classifyByKeyword(normalized);
}

/** Parse package.json content into a name -> version map of `dependencies`. */
export function parseNpmDependencies(content: string): Map<string, string> {
  const result = new Map<string, string>();
  try {
    const pkg = JSON.parse(content) as { dependencies?: Record<string, string> };
    for (const [name, version] of Object.entries(pkg.dependencies ?? {})) {
      result.set(name, version);
    }
  } catch {
    // Malformed/partial JSON (e.g. mid-conflict) — skip rather than guess.
  }
  return result;
}

/** Parse a requirements.txt-style file into a name -> version-spec map. */
export function parsePythonRequirements(content: string): Map<string, string> {
  const result = new Map<string, string>();
  for (const rawLine of content.split("\n")) {
    const line = rawLine.split("#")[0].trim();
    if (!line || line.startsWith("-")) continue;
    const match = line.match(/^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*([<>=!~].*)?$/);
    if (!match) continue;
    const name = match[1].toLowerCase();
    const versionSpec = (match[3] ?? "").trim();
    result.set(name, versionSpec);
  }
  return result;
}

function extractTomlSection(content: string, sectionHeader: string): string | null {
  const lines = content.split("\n");
  let capturing = false;
  const collected: string[] = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed === sectionHeader) {
      capturing = true;
      continue;
    }
    if (capturing && /^\[.+\]$/.test(trimmed)) break;
    if (capturing) collected.push(line);
  }
  return collected.length > 0 ? collected.join("\n") : null;
}

/**
 * Parse pyproject.toml dependencies — both PEP 621 (`[project].dependencies`,
 * a plain array of PEP 508 requirement strings) and legacy Poetry
 * (`[tool.poetry.dependencies]`, one `name = spec` entry per line, plus an
 * inline-table form for extras). This is a targeted subset parser, not a
 * general TOML parser — it only understands the shapes these two sections
 * commonly take.
 */
export function parsePyprojectToml(content: string): Map<string, string> {
  const result = new Map<string, string>();

  const projectSection = extractTomlSection(content, "[project]");
  const dependenciesArray = projectSection?.match(/dependencies\s*=\s*\[([\s\S]*?)\]/)?.[1];
  if (dependenciesArray) {
    const entries = dependenciesArray.match(/"([^"]+)"|'([^']+)'/g) ?? [];
    for (const raw of entries) {
      const spec = raw.slice(1, -1);
      const match = spec.match(/^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*([<>=!~].*)?$/);
      if (match) result.set(match[1].toLowerCase(), (match[3] ?? "").trim());
    }
  }

  const poetrySection = extractTomlSection(content, "[tool.poetry.dependencies]");
  if (poetrySection) {
    for (const rawLine of poetrySection.split("\n")) {
      const line = rawLine.trim();
      if (!line || line.startsWith("#")) continue;
      const kvMatch = line.match(/^([A-Za-z0-9][A-Za-z0-9._-]*)\s*=\s*(.+)$/);
      if (!kvMatch) continue;
      const name = kvMatch[1].toLowerCase();
      if (name === "python") continue; // interpreter constraint, not a dependency
      const rawValue = kvMatch[2].trim();
      const inlineTableVersion = rawValue.match(/version\s*=\s*"([^"]+)"/)?.[1];
      const version = inlineTableVersion ?? rawValue.replace(/^["']|["'],?$/g, "");
      result.set(name, version);
    }
  }

  return result;
}

function stripXmlComments(xml: string): string {
  return xml.replace(/<!--[\s\S]*?-->/g, "");
}

function extractMavenProperties(xml: string): Map<string, string> {
  const props = new Map<string, string>();
  const block = xml.match(/<properties>([\s\S]*?)<\/properties>/)?.[1];
  if (!block) return props;
  const tagRegex = /<([A-Za-z0-9_.-]+)>([^<]*)<\/\1>/g;
  let m;
  while ((m = tagRegex.exec(block))) {
    props.set(m[1], m[2].trim());
  }
  return props;
}

function resolveMavenVersion(raw: string | undefined, props: Map<string, string>): string | null {
  if (!raw) return null;
  const trimmed = raw.trim();
  const propMatch = trimmed.match(/^\$\{([^}]+)\}$/);
  if (propMatch) return props.get(propMatch[1]) ?? null;
  return trimmed;
}

/**
 * Parse a pom.xml's direct <dependencies> (not <dependencyManagement>, which
 * declares constraints rather than actual dependencies). Property-referenced
 * versions (`${spring-boot.version}`) are resolved against this same POM's
 * <properties>; versions inherited from a parent POM's <properties> can't be
 * resolved without fetching that parent, so they're left unresolved (null).
 */
export function parseMavenDependencies(xmlContent: string): Map<string, string> {
  const result = new Map<string, string>();
  const xml = stripXmlComments(xmlContent);
  const props = extractMavenProperties(xml);

  const withoutManagement = xml.replace(/<dependencyManagement>[\s\S]*?<\/dependencyManagement>/g, "");
  const depsBlock = withoutManagement.match(/<dependencies>([\s\S]*?)<\/dependencies>/)?.[1];
  if (!depsBlock) return result;

  const depRegex = /<dependency>([\s\S]*?)<\/dependency>/g;
  let match;
  while ((match = depRegex.exec(depsBlock))) {
    // Strip nested <exclusions> first so an exclusion's own groupId/artifactId
    // isn't misread as this <dependency>'s identity.
    const block = match[1].replace(/<exclusions>[\s\S]*?<\/exclusions>/g, "");
    const groupId = block.match(/<groupId>([^<]+)<\/groupId>/)?.[1]?.trim();
    const artifactId = block.match(/<artifactId>([^<]+)<\/artifactId>/)?.[1]?.trim();
    if (!groupId || !artifactId) continue;
    const rawVersion = block.match(/<version>([^<]+)<\/version>/)?.[1];
    const version = resolveMavenVersion(rawVersion, props);
    result.set(`${groupId}:${artifactId}`, version ?? "");
  }
  return result;
}

function addGoModLine(line: string, result: Map<string, string>): void {
  if (line.includes("// indirect")) return;
  const withoutComment = line.split("//")[0].trim();
  const match = withoutComment.match(/^(\S+)\s+(\S+)$/);
  if (!match) return;
  result.set(match[1], match[2]);
}

/** Parse go.mod's direct requirements (both `require (...)` blocks and single-line `require module version`), skipping `// indirect` entries. */
export function parseGoModDependencies(content: string): Map<string, string> {
  const result = new Map<string, string>();
  let inBlock = false;
  for (const rawLine of content.split("\n")) {
    const line = rawLine.trim();
    if (line.startsWith("require (")) {
      inBlock = true;
      continue;
    }
    if (inBlock) {
      if (line === ")") {
        inBlock = false;
        continue;
      }
      addGoModLine(line, result);
      continue;
    }
    if (line.startsWith("require ")) {
      addGoModLine(line.slice("require ".length), result);
    }
  }
  return result;
}

/** Entries present in `next` that are absent from `prev`, or whose version spec changed. */
export function diffDependencyMaps(
  prev: Map<string, string>,
  next: Map<string, string>,
  ecosystem: Ecosystem,
  manifestPath: string,
): DependencyChange[] {
  const changes: DependencyChange[] = [];
  for (const [name, version] of next) {
    if (prev.get(name) !== version) {
      changes.push({ ecosystem, name, version: version || null, manifestPath });
    }
  }
  return changes;
}

const MANIFEST_PARSERS: Record<string, { ecosystem: Ecosystem; parse: (content: string) => Map<string, string> }> = {
  "package.json": { ecosystem: "npm", parse: parseNpmDependencies },
  "requirements.txt": { ecosystem: "pypi", parse: parsePythonRequirements },
  "pyproject.toml": { ecosystem: "pypi", parse: parsePyprojectToml },
  "pom.xml": { ecosystem: "maven", parse: parseMavenDependencies },
  "go.mod": { ecosystem: "go", parse: parseGoModDependencies },
};

async function readFileAtRef(workspacePath: string, ref: string, path: string): Promise<string | null> {
  try {
    const { stdout } = await exec("git", ["show", `${ref}:${path}`], { cwd: workspacePath });
    return stdout;
  } catch {
    return null;
  }
}

/**
 * Find dependency additions/version changes in any tracked manifest between
 * baseRef and headRef. Only top-level manifests (repo root) are checked;
 * monorepo sub-package manifests are out of scope for now.
 */
export async function findManifestDependencyChanges(
  workspacePath: string,
  baseRef: string,
  headRef = "HEAD",
): Promise<DependencyChange[]> {
  const changes: DependencyChange[] = [];

  for (const [path, manifest] of Object.entries(MANIFEST_PARSERS)) {
    const [before, after] = await Promise.all([
      readFileAtRef(workspacePath, baseRef, path),
      readFileAtRef(workspacePath, headRef, path),
    ]);
    if (after === null || before === after) continue;

    const prevDeps = before ? manifest.parse(before) : new Map<string, string>();
    const nextDeps = manifest.parse(after);
    changes.push(...diffDependencyMaps(prevDeps, nextDeps, manifest.ecosystem, path));
  }

  return changes;
}

async function fetchNpmLicense(name: string): Promise<string | null> {
  // Version specs are ranges (^1.2.3); registry metadata is keyed by exact
  // version, so fall back to the package's "latest" dist-tag license, which
  // is a reasonable proxy — an exact resolved lockfile version isn't
  // available without a full install.
  const url = `https://registry.npmjs.org/${encodeURIComponent(name)}/latest`;
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(10_000) });
    if (!res.ok) return null;
    const data = (await res.json()) as { license?: string | { type?: string } };
    if (typeof data.license === "string") return data.license;
    if (data.license && typeof data.license === "object") return data.license.type ?? null;
    return null;
  } catch (err) {
    logger.warn(`Failed to fetch npm license for ${name}: ${err instanceof Error ? err.message : err}`);
    return null;
  }
}

async function fetchPypiLicense(name: string): Promise<string | null> {
  const url = `https://pypi.org/pypi/${encodeURIComponent(name)}/json`;
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(10_000) });
    if (!res.ok) return null;
    const data = (await res.json()) as {
      info?: {
        license?: string | null;
        license_expression?: string | null;
        classifiers?: string[];
      };
    };

    // PEP 639's license_expression is the modern, authoritative SPDX-format
    // field. Newer packages (setuptools/hatchling with pyproject.toml)
    // populate this instead of the legacy trove classifiers below.
    const expression = data.info?.license_expression?.trim();
    if (expression) return expression;

    const classifiers = data.info?.classifiers ?? [];
    for (const classifier of classifiers) {
      const parts = classifier.split("::").map((p) => p.trim());
      const leaf = parts[parts.length - 1]?.toLowerCase();
      if (leaf && PYPI_CLASSIFIER_TO_LICENSE[leaf]) {
        return PYPI_CLASSIFIER_TO_LICENSE[leaf];
      }
    }
    const freeText = data.info?.license?.trim();
    return freeText || null;
  } catch (err) {
    logger.warn(`Failed to fetch PyPI license for ${name}: ${err instanceof Error ? err.message : err}`);
    return null;
  }
}

function mavenGroupPath(groupId: string): string {
  return groupId.replace(/\./g, "/");
}

async function fetchMavenPom(groupId: string, artifactId: string, version: string): Promise<string | null> {
  const url = `https://repo1.maven.org/maven2/${mavenGroupPath(groupId)}/${artifactId}/${version}/${artifactId}-${version}.pom`;
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(10_000) });
    if (!res.ok) return null;
    return await res.text();
  } catch (err) {
    logger.warn(`Failed to fetch Maven POM for ${groupId}:${artifactId}:${version}: ${err instanceof Error ? err.message : err}`);
    return null;
  }
}

function extractMavenLicenseName(pomXml: string): string | null {
  const xml = stripXmlComments(pomXml);
  return xml.match(/<licenses>[\s\S]*?<license>[\s\S]*?<name>([^<]+)<\/name>/)?.[1]?.trim() ?? null;
}

function extractMavenParent(pomXml: string): { groupId: string; artifactId: string; version: string } | null {
  const block = stripXmlComments(pomXml).match(/<parent>([\s\S]*?)<\/parent>/)?.[1];
  if (!block) return null;
  const groupId = block.match(/<groupId>([^<]+)<\/groupId>/)?.[1]?.trim();
  const artifactId = block.match(/<artifactId>([^<]+)<\/artifactId>/)?.[1]?.trim();
  const version = block.match(/<version>([^<]+)<\/version>/)?.[1]?.trim();
  return groupId && artifactId && version ? { groupId, artifactId, version } : null;
}

/**
 * Fetch a Maven artifact's license from its POM on Maven Central, falling
 * back one level to its <parent> POM if the artifact doesn't declare
 * <licenses> directly (common — many artifacts inherit it from a parent).
 */
async function fetchMavenLicense(groupIdArtifactId: string, version: string | null): Promise<string | null> {
  if (!version) return null; // unresolved property version (inherited from an unfetched parent's <properties>)
  const [groupId, artifactId] = groupIdArtifactId.split(":");
  if (!groupId || !artifactId) return null;

  const pom = await fetchMavenPom(groupId, artifactId, version);
  if (!pom) return null;

  const direct = extractMavenLicenseName(pom);
  if (direct) return direct;

  const parent = extractMavenParent(pom);
  if (!parent) return null;
  const parentPom = await fetchMavenPom(parent.groupId, parent.artifactId, parent.version);
  return parentPom ? extractMavenLicenseName(parentPom) : null;
}

/**
 * Fetch a Go module's license via GitHub's license-detection API (only for
 * github.com-hosted modules — the vast majority of the Go OSS ecosystem, but
 * not exhaustive; gitlab.com/bitbucket.org/custom-domain modules fall back
 * to "unknown"). golang.org/x/* is special-cased since it's extremely common
 * and consistently BSD-3-Clause, but isn't GitHub-hosted.
 */
async function fetchGoLicense(modulePath: string): Promise<string | null> {
  if (modulePath.startsWith("golang.org/x/")) return "BSD-3-Clause";

  const githubMatch = modulePath.match(/^github\.com\/([^/]+)\/([^/]+)/);
  if (!githubMatch) return null;
  const [, owner, repo] = githubMatch;

  const headers: Record<string, string> = { Accept: "application/vnd.github+json" };
  if (process.env.GITHUB_TOKEN) headers.Authorization = `Bearer ${process.env.GITHUB_TOKEN}`;

  try {
    const res = await fetch(`https://api.github.com/repos/${owner}/${repo}/license`, {
      headers,
      signal: AbortSignal.timeout(10_000),
    });
    if (!res.ok) return null;
    const data = (await res.json()) as { license?: { spdx_id?: string } };
    const spdxId = data.license?.spdx_id;
    return spdxId && spdxId !== "NOASSERTION" ? spdxId : null;
  } catch (err) {
    logger.warn(`Failed to fetch Go module license for ${modulePath}: ${err instanceof Error ? err.message : err}`);
    return null;
  }
}

export async function checkDependencyLicenses(
  changes: DependencyChange[],
): Promise<LicenseCheckResult[]> {
  return Promise.all(
    changes.map(async (change) => {
      let license: string | null;
      switch (change.ecosystem) {
        case "npm":
          license = await fetchNpmLicense(change.name);
          break;
        case "pypi":
          license = await fetchPypiLicense(change.name);
          break;
        case "maven":
          license = await fetchMavenLicense(change.name, change.version);
          break;
        case "go":
          license = await fetchGoLicense(change.name);
          break;
      }
      return { change, license, verdict: classifyLicense(license) };
    }),
  );
}

function findDependencyLine(fileContent: string, name: string, ecosystem: Ecosystem): number {
  const lines = fileContent.split("\n");
  let needle: string;
  if (ecosystem === "npm") needle = `"${name}"`;
  else if (ecosystem === "maven") needle = `<artifactId>${name.split(":")[1] ?? name}</artifactId>`;
  else needle = name.toLowerCase();

  for (let i = 0; i < lines.length; i++) {
    const line = ecosystem === "npm" || ecosystem === "maven" ? lines[i] : lines[i].toLowerCase();
    if (line.includes(needle)) return i + 1;
  }
  return 1;
}

/**
 * Run the full manifest-diff -> registry-lookup -> classification pipeline
 * and return findings in the same shape the review agent emits, ready to
 * merge into ReviewOutput.findings. Best-effort throughout: a registry
 * lookup failure for one package doesn't block the others or the review.
 */
export async function buildLicenseFindings(opts: {
  workspacePath: string;
  baseRef: string;
  headRef?: string;
}): Promise<ReviewFinding[]> {
  const { workspacePath, baseRef, headRef = "HEAD" } = opts;

  const changes = await findManifestDependencyChanges(workspacePath, baseRef, headRef);
  if (changes.length === 0) return [];

  logger.info(`Checking license(s) for ${changes.length} added/changed dependenc${changes.length === 1 ? "y" : "ies"}`);
  const results = await checkDependencyLicenses(changes);

  const findings: ReviewFinding[] = [];
  for (const result of results) {
    if (result.verdict === "allowed") continue;

    const { change, license, verdict } = result;
    const manifestContent = await readFileAtRef(workspacePath, headRef, change.manifestPath);
    const line = manifestContent ? findDependencyLine(manifestContent, change.name, change.ecosystem) : 1;
    const licenseLabel = license ?? "unknown/unspecified";

    const title =
      verdict === "flagged"
        ? `[P1] Dependency "${change.name}" uses a restrictive license (${licenseLabel})`
        : `[P2] Dependency "${change.name}" has an unverified license`;

    const body =
      verdict === "flagged"
        ? `The ${change.ecosystem} package \`${change.name}\`${change.version ? ` (${change.version})` : ""} is licensed under **${licenseLabel}**, which is commonly incompatible with unrestricted commercial/proprietary use (copyleft or non-commercial terms). Verify this is acceptable for this project before merging, or replace the dependency.`
        : `Could not determine a clear license for the ${change.ecosystem} package \`${change.name}\`${change.version ? ` (${change.version})` : ""}${license ? ` (registry reported: "${license}")` : " (no license metadata found in the registry)"}. Manually verify its license terms before relying on it.`;

    findings.push({
      title,
      body,
      priority: verdict === "flagged" ? 1 : 2,
      code_location: {
        absolute_file_path: `${workspacePath}/${change.manifestPath}`,
        line_range: { start: line, end: line },
      },
    });
  }

  return findings;
}
