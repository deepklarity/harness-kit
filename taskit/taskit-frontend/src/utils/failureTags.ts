/**
 * Build the failure-tag list shown on a failed task's sidebar banner.
 *
 * Two metadata fields describe the failure shape — `failure_class` (the
 * deterministic label from the failure-class tagger) and `last_failure_type`
 * (the raw type). They frequently carry the same value (e.g. both
 * "stale_execution"), which used to render as two identical badges
 * ("stale_execution stale_execution"). This collapses duplicates while
 * keeping distinct labels, in display order (class first, then raw type).
 */
export function dedupeFailureTags(
    failureClass: string | undefined,
    lastFailureType: string | undefined,
): string[] {
    const tags: string[] = [];
    const seen = new Set<string>();
    for (const tag of [failureClass, lastFailureType]) {
        if (tag && !seen.has(tag)) {
            seen.add(tag);
            tags.push(tag);
        }
    }
    return tags;
}
