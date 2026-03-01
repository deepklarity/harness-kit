/**
 * Parse structured failure details from a task summary string.
 *
 * Extracts typed fields (Failure type, Reason, Origin, Debug) from
 * harness-generated failure summaries, separating them from the
 * human-readable display text.
 */
export function parseFailureDetails(summary: string): {
    displaySummary: string;
    failureType: string | null;
    failureReason: string | null;
    failureOrigin: string | null;
    failureDebug: string | null;
} {
    if (!summary) {
        return { displaySummary: '', failureType: null, failureReason: null, failureOrigin: null, failureDebug: null };
    }

    const lines = summary.split('\n');
    let failureType: string | null = null;
    let failureReason: string | null = null;
    let failureOrigin: string | null = null;
    let inDebugBlock = false;
    const debugLines: string[] = [];
    const remaining: string[] = [];

    for (const line of lines) {
        const trimmed = line.trim();

        if (inDebugBlock) {
            if (
                trimmed.startsWith('Failure type:')
                || trimmed.startsWith('Reason:')
                || trimmed.startsWith('Origin:')
            ) {
                inDebugBlock = false;
            } else {
                debugLines.push(line);
                continue;
            }
        }

        if (trimmed.startsWith('Failure type:')) {
            failureType = trimmed.slice('Failure type:'.length).trim() || null;
            continue;
        }
        if (trimmed.startsWith('Reason:')) {
            failureReason = trimmed.slice('Reason:'.length).trim() || null;
            continue;
        }
        if (trimmed.startsWith('Origin:')) {
            failureOrigin = trimmed.slice('Origin:'.length).trim() || null;
            continue;
        }
        if (trimmed.startsWith('Debug:')) {
            inDebugBlock = true;
            debugLines.push(line.slice(line.indexOf('Debug:') + 'Debug:'.length).trimStart());
            continue;
        }
        remaining.push(line);
    }

    const failureDebug = debugLines.join('\n').trim() || null;
    return {
        displaySummary: remaining.join('\n').trim(),
        failureType,
        failureReason,
        failureOrigin,
        failureDebug,
    };
}
