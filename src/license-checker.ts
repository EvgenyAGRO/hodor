import { exec } from "./utils/exec.js";
import { logger } from "./utils/logger.js";
import type { ReviewFinding } from "./types.js";

export type Ecosystem = "npm" | "pypi";

export interface DependencyChange {
  ecosystem: Ecosystem;
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
// plus a few free-text variants registries commonly return verbatim.
const ALLOWED_LICENSES = new Set(
  [
    "MIT", "Apache-2.0", "Apache 2.0", "Apache License 2.0",
    "BSD-2-Clause", "BSD-3-Clause", "BSD-3-Clause-Clear", "BSD", "0BSD",
    "ISC", "Unlicense", "CC0-1.0", "Zlib", "WTFPL", "Python-2.0", "PSF-2.0",
    "PSF", "BlueOak-1.0.0", "Artistic-2.0", "MIT-0", "CC-BY-4.0",
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
  return "unknown";
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

const MANIFESTS: Array<{ path: string; ecosystem: Ecosystem }> = [
  { path: "package.json", ecosystem: "npm" },
  { path: "requirements.txt", ecosystem: "pypi" },
];

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

  for (const manifest of MANIFESTS) {
    const [before, after] = await Promise.all([
      readFileAtRef(workspacePath, baseRef, manifest.path),
      readFileAtRef(workspacePath, headRef, manifest.path),
    ]);
    if (after === null || before === after) continue;

    const parse = manifest.ecosystem === "npm" ? parseNpmDependencies : parsePythonRequirements;
    const prevDeps = before ? parse(before) : new Map<string, string>();
    const nextDeps = parse(after);
    changes.push(...diffDependencyMaps(prevDeps, nextDeps, manifest.ecosystem, manifest.path));
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

export async function checkDependencyLicenses(
  changes: DependencyChange[],
): Promise<LicenseCheckResult[]> {
  return Promise.all(
    changes.map(async (change) => {
      const license =
        change.ecosystem === "npm"
          ? await fetchNpmLicense(change.name)
          : await fetchPypiLicense(change.name);
      return { change, license, verdict: classifyLicense(license) };
    }),
  );
}

function findDependencyLine(fileContent: string, name: string, ecosystem: Ecosystem): number {
  const lines = fileContent.split("\n");
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
