import type { Task } from '../types';

export interface KanbanColumnState {
    tasks: Task[];
    totalCount: number;
    loadedCount: number;
}

export type KanbanColumnsState = Record<string, KanbanColumnState>;

export interface KanbanColumnResponse {
    tasks: Task[];
    totalCount: number;
}

/**
 * Merge a poll refresh into existing column state without ever truncating
 * an array a "Show more" load already extended.
 *
 * The server only ever returns the first page per lane. Naively replacing
 * `tasks` with the response would drop everything a previous load-more
 * fetched — the bug where a refresh clobbers "Show more" results. Instead:
 * the fresh page (in server order) comes first, then any previously loaded
 * tasks not in the fresh page are retained — unless the poll shows them
 * having landed in a *different* lane, in which case they're dropped from
 * this one (they'll appear in their new lane's fresh page instead).
 */
export function mergeKanbanRefresh(
    prev: KanbanColumnsState,
    response: Record<string, KanbanColumnResponse>,
): KanbanColumnsState {
    const freshColumnByTaskId = new Map<string, string>();
    for (const [status, col] of Object.entries(response)) {
        for (const task of col.tasks) freshColumnByTaskId.set(task.id, status);
    }

    const next: KanbanColumnsState = {};
    for (const [status, col] of Object.entries(response)) {
        const prevTasks = prev[status]?.tasks ?? [];
        const freshIds = new Set(col.tasks.map(t => t.id));
        const merged = [...col.tasks];
        for (const task of prevTasks) {
            if (freshIds.has(task.id)) continue;
            const movedTo = freshColumnByTaskId.get(task.id);
            if (movedTo && movedTo !== status) continue;
            merged.push(task);
        }
        next[status] = { tasks: merged, totalCount: col.totalCount, loadedCount: merged.length };
    }
    return next;
}

/** Append a "Show more" page after the currently loaded tasks, deduped by id. */
export function appendLoadMore(
    prev: KanbanColumnsState,
    status: string,
    result: KanbanColumnResponse,
): KanbanColumnsState {
    const col = prev[status];
    if (!col) return prev;
    const existingIds = new Set(col.tasks.map(t => t.id));
    const merged = [...col.tasks, ...result.tasks.filter(t => !existingIds.has(t.id))];
    return { ...prev, [status]: { tasks: merged, totalCount: result.totalCount, loadedCount: merged.length } };
}

/** Trim a column back down to its default page size ("Show less"). */
export function collapseColumn(
    prev: KanbanColumnsState,
    status: string,
    pageSize: number,
): KanbanColumnsState {
    const col = prev[status];
    if (!col) return prev;
    const trimmed = col.tasks.slice(0, pageSize);
    return { ...prev, [status]: { tasks: trimmed, totalCount: col.totalCount, loadedCount: trimmed.length } };
}
