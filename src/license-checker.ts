import { readFile } from "node:fs/promises";
import { join } from "node:path";
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
  // "Library General Public License" is the original (pre-2.1) name for the
  // LGPL — Hibernate and other common Java libs still report it that way.
  if (/\blgpl\b|lesser general public|library general public/.test(normalized)) return "flagged";
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

function classifySingle(normalizedPart: string): LicenseVerdict {
  if (ALLOWED_LICENSES.has(normalizedPart)) return "allowed";
  if (FLAGGED_LICENSES.has(normalizedPart)) return "flagged";
  return classifyByKeyword(normalizedPart);
}

export function classifyLicense(license: string | null): LicenseVerdict {
  if (!license) return "unknown";
  const normalized = license.trim().toLowerCase();
  if (!normalized) return "unknown";
  if (ALLOWED_LICENSES.has(normalized)) return "allowed";
  if (FLAGGED_LICENSES.has(normalized)) return "flagged";

  // SPDX composite expressions. OR means the licensee chooses, so one allowed
  // alternative is enough — dual-licensed "(GPL-2.0 OR MIT)" is fine, use it
  // under MIT. A flagged part still wins over unknowns ("GPL-3.0 OR SomeEULA")
  // since free-text names like "LGPL v3.0 or later" also land here. AND means
  // every license applies simultaneously, so any flagged part taints the whole.
  const stripped = normalized.replace(/[()]/g, " ");
  const hasOr = /\s+or\s+/.test(stripped);
  const hasAnd = /\s+and\s+/.test(stripped);
  if (hasOr && !hasAnd) {
    const verdicts = stripped
      .split(/\s+or\s+/)
      .map((p) => p.trim())
      .filter(Boolean)
      .map(classifySingle);
    if (verdicts.includes("allowed")) return "allowed";
    if (verdicts.includes("flagged")) return "flagged";
    return "unknown";
  }
  if (hasAnd) {
    const verdicts = stripped
      .split(/\s+(?:and|or)\s+/)
      .map((p) => p.trim())
      .filter(Boolean)
      .map(classifySingle);
    if (verdicts.includes("flagged")) return "flagged";
    if (verdicts.length > 1 && verdicts.every((v) => v === "allowed")) return "allowed";
    return "unknown";
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
/**
 * Extract the body of the `[` ... `]` array starting at/after `fromIndex` by
 * depth-counting brackets — a non-greedy regex would stop at the first `]`
 * inside an extras spec like "uvicorn[standard]>=0.20" and silently drop
 * every entry after it.
 */
function extractArrayBody(text: string, fromIndex: number): string | null {
  const open = text.indexOf("[", fromIndex);
  if (open === -1) return null;
  let depth = 0;
  for (let i = open; i < text.length; i++) {
    if (text[i] === "[") depth++;
    else if (text[i] === "]") {
      depth--;
      if (depth === 0) return text.slice(open + 1, i);
    }
  }
  return null;
}

export function parsePyprojectToml(content: string): Map<string, string> {
  const result = new Map<string, string>();

  const projectSection = extractTomlSection(content, "[project]");
  const depsKey = projectSection?.match(/(?:^|\n)\s*dependencies\s*=/);
  const dependenciesArray =
    projectSection && depsKey?.index !== undefined
      ? extractArrayBody(projectSection, depsKey.index + depsKey[0].length)
      : null;
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

  // Drop sections whose <dependencies> aren't the project's own direct deps:
  // dependencyManagement declares constraints, <build> holds plugin deps, and
  // <profiles>/<reporting> are conditional or tooling-only.
  const cleaned = xml
    .replace(/<dependencyManagement>[\s\S]*?<\/dependencyManagement>/g, "")
    .replace(/<build>[\s\S]*?<\/build>/g, "")
    .replace(/<profiles>[\s\S]*?<\/profiles>/g, "")
    .replace(/<reporting>[\s\S]*?<\/reporting>/g, "");

  const blockRegex = /<dependencies>([\s\S]*?)<\/dependencies>/g;
  let blockMatch;
  while ((blockMatch = blockRegex.exec(cleaned))) {
    const depRegex = /<dependency>([\s\S]*?)<\/dependency>/g;
    let match;
    while ((match = depRegex.exec(blockMatch[1]))) {
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
 * Read the "after" side of the comparison: the working tree in local mode
 * (so uncommitted manifest edits are seen, matching how the main review
 * diffs), or the committed headRef in CI mode.
 */
async function readHeadSide(
  workspacePath: string,
  headRef: string,
  path: string,
  useWorkingTree: boolean,
): Promise<string | null> {
  if (useWorkingTree) {
    try {
      return await readFile(join(workspacePath, path), "utf-8");
    } catch {
      return null;
    }
  }
  return readFileAtRef(workspacePath, headRef, path);
}

/**
 * Find dependency additions/version changes in any tracked manifest between
 * baseRef and headRef, anywhere in the tree — multi-module Maven projects and
 * monorepos keep manifests in subdirectories, not just the repo root. Changed
 * manifests are discovered from the git diff, so only files actually touched
 * by this change range are parsed.
 */
export async function findManifestDependencyChanges(
  workspacePath: string,
  baseRef: string,
  headRef = "HEAD",
  useWorkingTree = false,
  restrictToPaths?: Set<string>,
): Promise<DependencyChange[]> {
  // Resolve the merge base so a target branch that advanced after this branch
  // forked doesn't leak its own dependency changes into the comparison.
  let base = baseRef;
  try {
    const { stdout } = await exec("git", ["merge-base", baseRef, headRef], { cwd: workspacePath });
    base = stdout.trim() || baseRef;
  } catch {
    // Shallow clone or unrelated refs — compare against baseRef directly.
  }

  // Discover changed manifests from the diff itself. If the base is not a
  // reachable commit (shallow CI clone), this fails and we skip the check
  // entirely — treating an unreadable base as "empty" would make every
  // existing dependency look newly added and flood the MR with findings.
  let changedFiles: string[];
  try {
    const diffArgs = useWorkingTree
      ? ["diff", "--name-only", base]
      : ["diff", "--name-only", base, headRef];
    const { stdout } = await exec("git", diffArgs, { cwd: workspacePath });
    changedFiles = stdout.split("\n").map((l) => l.trim()).filter(Boolean);
  } catch (err) {
    logger.warn(`License check: failed to list changed files: ${err instanceof Error ? err.message : err}`);
    return [];
  }

  const changes: DependencyChange[] = [];
  for (const path of changedFiles) {
    // On a CI merge-ref checkout, `git diff base HEAD` also includes files
    // already merged into the target branch by other MRs. When the caller
    // supplies the MR's authoritative changed-file list, honor it so we only
    // flag dependencies this MR actually touched.
    if (restrictToPaths && !restrictToPaths.has(path)) continue;
    const basename = path.split("/").pop() ?? path;
    const manifest = MANIFEST_PARSERS[basename];
    if (!manifest) continue;

    const [before, after] = await Promise.all([
      readFileAtRef(workspacePath, base, path),
      readHeadSide(workspacePath, headRef, path, useWorkingTree),
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

/**
 * Resolve the latest released version of an artifact from Maven Central's
 * maven-metadata.xml. Used when the pom.xml omits <version> — the norm in
 * Spring Boot projects where versions are managed by the parent BOM.
 */
async function fetchMavenLatestVersion(groupId: string, artifactId: string): Promise<string | null> {
  const url = `https://repo1.maven.org/maven2/${mavenGroupPath(groupId)}/${artifactId}/maven-metadata.xml`;
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(10_000) });
    if (!res.ok) return null;
    const xml = await res.text();
    return (
      xml.match(/<release>([^<]+)<\/release>/)?.[1]?.trim() ??
      xml.match(/<latest>([^<]+)<\/latest>/)?.[1]?.trim() ??
      null
    );
  } catch (err) {
    logger.warn(`Failed to fetch Maven metadata for ${groupId}:${artifactId}: ${err instanceof Error ? err.message : err}`);
    return null;
  }
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
 * A missing/unresolved version (parent-BOM-managed, the Spring Boot norm)
 * falls back to the latest Central release — a license proxy consistent
 * with the npm "latest" approach above.
 */
async function fetchMavenLicense(groupIdArtifactId: string, version: string | null): Promise<string | null> {
  const [groupId, artifactId] = groupIdArtifactId.split(":");
  if (!groupId || !artifactId) return null;

  const resolved = version ?? (await fetchMavenLatestVersion(groupId, artifactId));
  if (!resolved) return null;

  const pom = await fetchMavenPom(groupId, artifactId, resolved);
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

// Cap concurrent registry lookups: a large manifest change would otherwise
// fire hundreds of simultaneous requests and trip rate limits (GitHub's
// unauthenticated API allows only 60 req/hr for Go module lookups).
const LOOKUP_CONCURRENCY = 6;

export async function checkDependencyLicenses(
  changes: DependencyChange[],
): Promise<LicenseCheckResult[]> {
  // Dedupe lookups — the same dependency can be added to several manifests in
  // one MR (monorepos, multi-module Maven builds).
  const licenseByKey = new Map<string, Promise<string | null>>();
  const lookup = (change: DependencyChange): Promise<string | null> => {
    const key = `${change.ecosystem}:${change.name}:${change.version ?? ""}`;
    let pending = licenseByKey.get(key);
    if (!pending) {
      switch (change.ecosystem) {
        case "npm":
          pending = fetchNpmLicense(change.name);
          break;
        case "pypi":
          pending = fetchPypiLicense(change.name);
          break;
        case "maven":
          pending = fetchMavenLicense(change.name, change.version);
          break;
        case "go":
          pending = fetchGoLicense(change.name);
          break;
      }
      licenseByKey.set(key, pending);
    }
    return pending;
  };

  const results: LicenseCheckResult[] = [];
  for (let i = 0; i < changes.length; i += LOOKUP_CONCURRENCY) {
    const batch = changes.slice(i, i + LOOKUP_CONCURRENCY);
    results.push(
      ...(await Promise.all(
        batch.map(async (change) => {
          const license = await lookup(change);
          return { change, license, verdict: classifyLicense(license) };
        }),
      )),
    );
  }
  return results;
}

function findDependencyLine(fileContent: string, name: string, ecosystem: Ecosystem): number {
  const lines = fileContent.split("\n");

  if (ecosystem === "maven") {
    const [groupId, artifactId] = name.split(":");
    const needle = `<artifactId>${artifactId ?? name}</artifactId>`;
    const candidates: number[] = [];
    for (let i = 0; i < lines.length; i++) {
      if (lines[i].includes(needle)) candidates.push(i);
    }
    // The same artifactId can also appear under <build><plugins> or
    // <dependencyManagement> — prefer the occurrence adjacent to the
    // matching <groupId>.
    for (const i of candidates) {
      const context = lines.slice(Math.max(0, i - 3), i + 4).join("\n");
      if (groupId && context.includes(`<groupId>${groupId}</groupId>`)) return i + 1;
    }
    return candidates.length > 0 ? candidates[0] + 1 : 1;
  }

  const needle = ecosystem === "npm" ? `"${name}"` : name.toLowerCase();
  for (let i = 0; i < lines.length; i++) {
    const line = ecosystem === "npm" ? lines[i] : lines[i].toLowerCase();
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
/** Added lines (`+`, excluding the `+++` header), keyed by new-file path. */
export function addedLinesByFile(diffText: string): Map<string, string[]> {
  const byFile = new Map<string, string[]>();
  let current: string | null = null;
  for (const line of diffText.split("\n")) {
    if (line.startsWith("+++ ")) {
      let p = line.slice(4).trim();
      if (p.startsWith("b/")) p = p.slice(2);
      current = p === "/dev/null" ? null : p;
      if (current && !byFile.has(current)) byFile.set(current, []);
      continue;
    }
    if (line.startsWith("diff --git ") || line.startsWith("--- ")) continue;
    if (current && line.startsWith("+")) {
      byFile.get(current)?.push(line.slice(1));
    }
  }
  return byFile;
}

/** Tokens that identify a dependency in its manifest's added lines. */
function dependencyMatchTokens(change: DependencyChange): string[] {
  const tokens: string[] = [];
  if (change.ecosystem === "maven") {
    const artifactId = change.name.split(":").pop();
    if (artifactId) tokens.push(artifactId);
  } else {
    tokens.push(change.name);
  }
  if (change.version) tokens.push(change.version);
  return tokens.filter(Boolean);
}

/**
 * Keep only dependency changes this MR actually introduced, per the authoritative
 * source-vs-target diff. A `git diff` against the stale CI base compares whole
 * manifests, so a dependency that the *target* branch added to a manifest the MR
 * also edits looks newly added here. We drop any change whose identifying token
 * (maven artifactId, else package name, or the version) never appears on an
 * added line of that manifest in the authoritative diff. Permissive by design —
 * it only removes clearly target-only deps, never a dep the MR truly touched.
 */
export function filterChangesToAuthoritativeDiff(
  changes: DependencyChange[],
  diffText: string,
): DependencyChange[] {
  const added = addedLinesByFile(diffText);
  return changes.filter((change) => {
    const lines = added.get(change.manifestPath);
    if (!lines || lines.length === 0) return false;
    const tokens = dependencyMatchTokens(change);
    return tokens.some((token) => lines.some((line) => line.includes(token)));
  });
}

export async function buildLicenseFindings(opts: {
  workspacePath: string;
  baseRef: string;
  headRef?: string;
  useWorkingTree?: boolean;
  restrictToPaths?: string[];
  /** Authoritative source-vs-target diff (GitLab). When present, dependency
   *  changes are filtered to what this MR actually added — see
   *  filterChangesToAuthoritativeDiff. */
  authoritativeDiff?: string | null;
}): Promise<ReviewFinding[]> {
  const { workspacePath, baseRef, headRef = "HEAD", useWorkingTree = false, restrictToPaths, authoritativeDiff } = opts;

  let changes = await findManifestDependencyChanges(
    workspacePath,
    baseRef,
    headRef,
    useWorkingTree,
    restrictToPaths ? new Set(restrictToPaths) : undefined,
  );
  if (changes.length === 0) return [];

  // Ground the comparison in GitLab's true diff so a manifest the target branch
  // also changed doesn't leak target-only dependencies into this MR's findings.
  if (authoritativeDiff) {
    const before = changes.length;
    changes = filterChangesToAuthoritativeDiff(changes, authoritativeDiff);
    const dropped = before - changes.length;
    if (dropped > 0) logger.info(`License check: dropped ${dropped} dependency change(s) not in the MR's authoritative diff`);
    if (changes.length === 0) return [];
  }

  // Skip first-party/internal dependencies — they aren't published to public
  // registries, so a license lookup always fails and yields pure "unverified"
  // noise (e.g. com.coronet:*). Configurable via HODOR_LICENSE_INTERNAL_GROUPS
  // (comma-separated name/groupId prefixes).
  const internalPrefixes = (process.env.HODOR_LICENSE_INTERNAL_GROUPS ?? "")
    .split(",")
    .map((p) => p.trim().toLowerCase())
    .filter(Boolean);
  if (internalPrefixes.length > 0) {
    const before = changes.length;
    changes = changes.filter((c) => !internalPrefixes.some((p) => c.name.toLowerCase().startsWith(p)));
    const skipped = before - changes.length;
    if (skipped > 0) logger.info(`License check: skipped ${skipped} internal dependenc${skipped === 1 ? "y" : "ies"}`);
    if (changes.length === 0) return [];
  }

  logger.info(`Checking license(s) for ${changes.length} added/changed dependenc${changes.length === 1 ? "y" : "ies"}`);
  const results = await checkDependencyLicenses(changes);

  // "unknown" means we couldn't determine a license — an internal artifact, a
  // dep on a non-Central/private registry (e.g. JitPack com.github.*), or one
  // with no declared metadata. These are low-signal and were the dominant
  // source of review noise, so they're suppressed by default; only genuinely
  // restrictive (flagged) licenses are reported. Opt back in with
  // HODOR_LICENSE_REPORT_UNVERIFIED=true.
  const reportUnverified = process.env.HODOR_LICENSE_REPORT_UNVERIFIED === "true";

  // Only relevant when unverified reporting is on: if every lookup came back
  // empty, the registries are likely unreachable (air-gapped/proxied CI) — skip
  // rather than flooding the MR with per-dep "unverified license" findings.
  if (reportUnverified && results.length >= 3 && results.every((r) => r.license === null)) {
    logger.warn("License check: all registry lookups returned nothing — assuming no network access, skipping");
    return [];
  }

  const findings: ReviewFinding[] = [];
  for (const result of results) {
    if (result.verdict === "allowed") continue;
    if (result.verdict === "unknown" && !reportUnverified) continue;

    const { change, license, verdict } = result;
    const manifestContent = await readHeadSide(workspacePath, headRef, change.manifestPath, useWorkingTree);
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
