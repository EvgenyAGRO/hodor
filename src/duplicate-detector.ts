// Similarity threshold for considering two titles as duplicates (0-100)
const SIMILARITY_THRESHOLD = 70;

// Line proximity threshold - findings within this many lines are considered same location
const LINE_PROXIMITY_THRESHOLD = 5;

export interface Finding {
  path?: string;
  line?: number;
  title?: string;
  body?: string;
}

export interface ExistingComment {
  path?: string;
  line?: number;
  body: string;
}

/** Strip markdown formatting, collapse whitespace, and lowercase for comparison. */
export function normalizeForComparison(text: string): string {
  if (!text) return "";

  let normalized = text;
  normalized = normalized.replace(/\*\*(.+?)\*\*/g, "$1");
  normalized = normalized.replace(/__(.+?)__/g, "$1");
  normalized = normalized.replace(/\*(.+?)\*/g, "$1");
  normalized = normalized.replace(/(?<!\w)_(.+?)_(?!\w)/g, "$1");
  normalized = normalized.replace(/`(.+?)`/g, "$1");
  normalized = normalized.replace(/\n/g, " ").replace(/\r/g, " ");
  normalized = normalized.replace(/\s+/g, " ");
  normalized = normalized.trim();
  normalized = normalized.toLowerCase();

  return normalized;
}

/** Extract the first line of a comment body as its title, stripped of bold markdown. */
export function extractTitle(body: string): string {
  if (!body) return "";

  const firstLine = body.split("\n")[0].trim();
  return firstLine.replace(/\*\*(.+?)\*\*/g, "$1").replace(/__(.+?)__/g, "$1").trim();
}

/** Find the longest contiguous matching run between a[alo:ahi] and b[blo:bhi]. */
function findLongestMatch(
  a: string,
  b: string,
  alo: number,
  ahi: number,
  blo: number,
  bhi: number,
): [number, number, number] {
  let besti = alo;
  let bestj = blo;
  let bestsize = 0;
  let j2len: Record<number, number> = {};

  for (let i = alo; i < ahi; i++) {
    const newj2len: Record<number, number> = {};
    for (let j = blo; j < bhi; j++) {
      if (a[i] !== b[j]) continue;
      const k = (j2len[j - 1] ?? 0) + 1;
      newj2len[j] = k;
      if (k > bestsize) {
        besti = i - k + 1;
        bestj = j - k + 1;
        bestsize = k;
      }
    }
    j2len = newj2len;
  }

  return [besti, bestj, bestsize];
}

/**
 * Ratcliff/Obershelp similarity ratio, equivalent to Python's
 * difflib.SequenceMatcher(None, a, b).ratio() for short (non-"junk") inputs
 * like comment titles.
 */
export function sequenceMatcherRatio(a: string, b: string): number {
  const total = a.length + b.length;
  if (total === 0) return 1.0;

  let matches = 0;
  const queue: Array<[number, number, number, number]> = [[0, a.length, 0, b.length]];
  while (queue.length > 0) {
    const [alo, ahi, blo, bhi] = queue.pop() as [number, number, number, number];
    const [i, j, k] = findLongestMatch(a, b, alo, ahi, blo, bhi);
    if (k === 0) continue;
    matches += k;
    if (alo < i && blo < j) queue.push([alo, i, blo, j]);
    if (i + k < ahi && j + k < bhi) queue.push([i + k, ahi, j + k, bhi]);
  }

  return (2.0 * matches) / total;
}

/** Fuzzy similarity score (0-100) between two texts, after normalization. */
export function similarityScore(text1: string, text2: string): number {
  if (!text1 && !text2) return 100;
  if (!text1 || !text2) return 0;

  const norm1 = normalizeForComparison(text1);
  const norm2 = normalizeForComparison(text2);
  return Math.floor(sequenceMatcherRatio(norm1, norm2) * 100);
}

/**
 * Check if a finding duplicates an existing comment: same file, nearby line
 * (within lineThreshold), and a similar title (or the title appears verbatim
 * in the existing comment body).
 */
export function isDuplicateFinding(
  newFinding: Finding,
  existing: ExistingComment[],
  similarityThreshold = SIMILARITY_THRESHOLD,
  lineThreshold = LINE_PROXIMITY_THRESHOLD,
): boolean {
  if (existing.length === 0) return false;

  const newPath = newFinding.path ?? "";
  const newLine = newFinding.line ?? 0;
  const newTitle = newFinding.title ?? "";

  for (const ex of existing) {
    const exBody = ex.body ?? "";
    const exTitle = extractTitle(exBody);

    if (ex.path && newPath !== ex.path) continue;

    if (ex.line != null && newLine) {
      if (Math.abs(newLine - ex.line) > lineThreshold) continue;
    }

    const score = similarityScore(newTitle, exTitle);
    if (score >= similarityThreshold) return true;

    const normNewTitle = normalizeForComparison(newTitle);
    const normExBody = normalizeForComparison(exBody);
    if (normNewTitle && normExBody.includes(normNewTitle)) return true;
  }

  return false;
}

/**
 * Remove findings that duplicate an existing comment or an earlier finding in
 * the same batch. Preserves the original order of the surviving findings.
 */
export function deduplicateFindings(
  findings: Finding[],
  existing: ExistingComment[],
  similarityThreshold = SIMILARITY_THRESHOLD,
  lineThreshold = LINE_PROXIMITY_THRESHOLD,
): Finding[] {
  const unique: Finding[] = [];
  const seen: ExistingComment[] = [...existing];

  for (const finding of findings) {
    if (isDuplicateFinding(finding, seen, similarityThreshold, lineThreshold)) continue;

    unique.push(finding);
    seen.push({
      path: finding.path,
      line: finding.line,
      body: `**${finding.title ?? ""}**\n\n${finding.body ?? ""}`,
    });
  }

  return unique;
}
