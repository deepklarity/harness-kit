/**
 * Detect and clean repeated paragraphs in LLM output.
 *
 * LLMs sometimes "stutter" — repeating the same paragraph 2-3x in a single
 * response. This is especially common in verdict summaries where the model
 * restates its conclusion. We split on double-newlines, deduplicate by
 * normalized content, and keep only the first occurrence.
 */
export function deduplicateSummary(text: string): string {
    if (!text) return text;

    const paragraphs = text.split(/\n{2,}/);
    const seen = new Set<string>();
    const unique: string[] = [];

    for (const p of paragraphs) {
        // Normalize: collapse whitespace, trim, lowercase for comparison
        const normalized = p.trim().replace(/\s+/g, ' ').toLowerCase();
        if (!normalized) continue;
        if (seen.has(normalized)) continue;
        seen.add(normalized);
        unique.push(p);
    }

    return unique.join('\n\n');
}

/**
 * Check if token_usage has any meaningful data.
 * Returns false for `{}`, `null`, `undefined`, or `{total_tokens: 0}`.
 */
export function hasTokenUsage(usage: Record<string, unknown> | null | undefined): boolean {
    if (!usage) return false;
    return Object.values(usage).some(v => typeof v === 'number' && v > 0);
}
