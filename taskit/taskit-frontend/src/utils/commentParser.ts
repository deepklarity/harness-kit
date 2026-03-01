/**
 * Parse comment body to separate human-readable summary from machine trace data.
 *
 * Agent output captured from tmux includes streaming protocol JSON that leaks
 * into the comment content. This parser extracts the clean summary and collects
 * trace metadata separately so the UI can show them differently.
 */
export function parseCommentBody(raw: string): { summary: string; traceData: string | null } {
    const lines = raw.split('\n');
    const summaryLines: string[] = [];
    const traceLines: string[] = [];
    let inTrace = false;

    for (const line of lines) {
        const trimmed = line.trim();

        // Standalone JSON trace line (step_finish, step_start, etc.)
        if (trimmed.startsWith('{')) {
            try {
                const obj = JSON.parse(trimmed);
                if (typeof obj === 'object' && obj !== null && (
                    'type' in obj || 'sessionID' in obj || 'part' in obj
                )) {
                    traceLines.push(trimmed);
                    inTrace = true;
                    continue;
                }
            } catch {
                // Not valid JSON — treat as regular text
            }
        }

        if (inTrace) {
            // Once we've entered trace territory, remaining lines are trace
            traceLines.push(line);
            continue;
        }

        summaryLines.push(line);
    }

    // Clean inline JSON suffixes from the last summary line.
    // Patterns: ","time":{...}}} or ","usage":{...},"permission_denials":[]}
    let summary = summaryLines.join('\n');
    const inlineJsonMatch = summary.match(/","(?:time|usage|tokens|permission_denials)":\s*[\[{]/);
    if (inlineJsonMatch && inlineJsonMatch.index !== undefined) {
        const trailingJson = summary.slice(inlineJsonMatch.index);
        traceLines.unshift(trailingJson);
        summary = summary.slice(0, inlineJsonMatch.index);
    }

    // Strip leading \n literal (some harnesses prefix with literal backslash-n)
    summary = summary.replace(/^\\n/, '').trim();

    const traceData = traceLines.length > 0 ? traceLines.join('\n') : null;
    return { summary, traceData };
}
