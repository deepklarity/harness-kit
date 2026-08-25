// Active-time math for tasks, shared by the data layer (HarnessTimeService)
// and the live timer UI so both stay consistent.
//
// Definitions:
//   executing time = time in EXECUTING, minus any question-pause
//   work time = time in IN_PROGRESS + EXECUTING (pause-adjusted) + REVIEW
// Both are cumulative across status re-entries because the caller passes a
// timeInStatuses map that already sums every span of each status.

type Meta = Record<string, unknown> | null | undefined;

/** Total time spent paused waiting on a question (accumulated + any in-flight pause). */
export function questionPauseMs(metadata?: Meta): number {
    if (!metadata) return 0;
    let pauseMs = (metadata.executing_paused_ms as number) || 0;
    const pausedAt = metadata.question_paused_at as string | undefined;
    if (pausedAt) {
        pauseMs += Date.now() - new Date(pausedAt).getTime();
    }
    return Math.max(0, pauseMs);
}

/** Subtract question-pause time (accumulated + any in-flight pause) from raw EXECUTING ms. */
export function subtractQuestionPause(rawMs: number, metadata?: Meta): number {
    if (rawMs <= 0) return Math.max(0, rawMs);
    return Math.max(0, rawMs - questionPauseMs(metadata));
}

export interface ActiveTimes {
    /** IN_PROGRESS + EXECUTING (pause-adjusted) + REVIEW — all active phases. */
    workTimeMs: number;
    /** EXECUTING only, pause-adjusted. */
    executingTimeMs: number;
}

export function computeActiveTimes(
    timeInStatuses: Record<string, number>,
    metadata?: Meta,
): ActiveTimes {
    const executingTimeMs = subtractQuestionPause(timeInStatuses['EXECUTING'] || 0, metadata);
    const workTimeMs = (timeInStatuses['IN_PROGRESS'] || 0)
        + executingTimeMs
        + (timeInStatuses['REVIEW'] || 0);
    return { workTimeMs, executingTimeMs };
}
