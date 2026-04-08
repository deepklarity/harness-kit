/**
 * Parse comment body to separate human-readable summary from machine trace data.
 *
 * Agent output captured from tmux includes streaming protocol JSON that leaks
 * into the comment content. This parser extracts the clean summary and collects
 * trace metadata separately so the UI can show them differently.
 */
/** Lines matching these patterns are TUI noise from tmux capture. */
const TUI_NOISE_RE = /^(?:[*✦⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏●◐◓◑◒]\s*\S+[.…]{1,3}|Collapse|Recentactivity|\d+;[·.].*|ClaudeCodev[\d.]+|Tipsforgettingstarted|Welcomeback\S+)$/;

/**
 * Strip terminal noise that survives ANSI stripping: box-drawing characters,
 * control characters, spinner frames, TUI chrome, and duplicate lines from
 * tmux screen-buffer capture.
 */
function stripTerminalNoise(text: string): string {
    let cleaned = text;
    // Residual ANSI escape sequences (belt-and-suspenders)
    cleaned = cleaned.replace(/\x1B[@-_][0-?]*[ -/]*[@-~]/g, '');
    // Box-drawing and block-element Unicode ranges (U+2500–U+257F, U+2580–U+259F)
    cleaned = cleaned.replace(/[\u2500-\u257F\u2580-\u259F]/g, '');
    // Control characters (except \n and \t)
    cleaned = cleaned.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g, '');

    // Filter TUI noise lines and deduplicate consecutive identical lines
    const out: string[] = [];
    let prev: string | null = null;
    for (const line of cleaned.split('\n')) {
        const stripped = line.trim();
        if (stripped && TUI_NOISE_RE.test(stripped)) continue;
        if (stripped === prev) continue;
        out.push(line);
        prev = stripped;
    }

    cleaned = out.join('\n');
    // Collapse 3+ consecutive blank lines to 2
    cleaned = cleaned.replace(/(\n\s*){3,}/g, '\n\n');
    return cleaned;
}

export function parseCommentBody(raw: string): { summary: string; traceData: string | null } {
    const lines = stripTerminalNoise(raw).split('\n');
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
