import { describe, it, expect } from 'vitest';
import type { Task } from '../types';
import { mergeKanbanRefresh, appendLoadMore, collapseColumn } from './kanbanColumns';
import type { KanbanColumnsState } from './kanbanColumns';

// ─── Helpers ───

function makeTask(overrides: Partial<Task> & { id: string; currentStatus: string }): Task {
    return {
        name: overrides.title || `Task ${overrides.id}`,
        title: `Task ${overrides.id}`,
        idShort: Number(overrides.id),
        shortLink: overrides.id,
        boardId: '1',
        boardName: 'Test Board',
        assignees: [],
        assigneeIds: [],
        createdAt: new Date(Date.now() - 3600_000).toISOString(),
        createdBy: 'test@test.com',
        mutations: [],
        comments: [],
        timeInStatuses: {},
        totalLifespanMs: 3600_000,
        workTimeMs: 0,
        executingTimeMs: 0,
        ...overrides,
    };
}

function columnState(tasks: Task[], totalCount: number): { tasks: Task[]; totalCount: number; loadedCount: number } {
    return { tasks, totalCount, loadedCount: tasks.length };
}

// ─── mergeKanbanRefresh — the refresh-overwrite regression ───

describe('mergeKanbanRefresh', () => {
    it('preserves load-more tasks beyond the poll page window instead of truncating them', () => {
        // User loaded 40 TESTING tasks via "Show more" (task-1..task-40).
        const loaded = Array.from({ length: 40 }, (_, i) =>
            makeTask({ id: `task-${i + 1}`, currentStatus: 'TESTING' }));
        const prev: KanbanColumnsState = {
            TESTING: columnState(loaded, 40),
        };

        // A silent poll refresh only ever returns the first page (20).
        const firstPage = loaded.slice(0, 20);
        const response = {
            TESTING: { tasks: firstPage, totalCount: 40 },
        };

        const next = mergeKanbanRefresh(prev, response);

        expect(next.TESTING.tasks).toHaveLength(40);
        expect(next.TESTING.tasks.map(t => t.id)).toEqual(loaded.map(t => t.id));
    });

    it('keeps the fresh page in server order and appends retained extras after it, unchanged', () => {
        const loaded = Array.from({ length: 25 }, (_, i) =>
            makeTask({ id: `task-${i + 1}`, currentStatus: 'TESTING' }));
        const prev: KanbanColumnsState = { TESTING: columnState(loaded, 25) };

        const firstPage = loaded.slice(0, 20);
        const response = { TESTING: { tasks: firstPage, totalCount: 25 } };

        const next = mergeKanbanRefresh(prev, response);

        // Fresh page first (server order), then the retained tail — no shuffling.
        expect(next.TESTING.tasks.map(t => t.id)).toEqual(loaded.map(t => t.id));
    });

    it('updates fields for tasks present in the fresh page (not stale-frozen)', () => {
        const task = makeTask({ id: 'task-1', currentStatus: 'TESTING', title: 'Old title' });
        const prev: KanbanColumnsState = { TESTING: columnState([task], 1) };

        const updated = { ...task, title: 'New title' };
        const response = { TESTING: { tasks: [updated], totalCount: 1 } };

        const next = mergeKanbanRefresh(prev, response);
        expect(next.TESTING.tasks[0].title).toBe('New title');
    });

    it('drops a task from its old lane when the poll shows it landed in a different lane', () => {
        const task = makeTask({ id: 'task-1', currentStatus: 'REVIEW' });
        const prev: KanbanColumnsState = { REVIEW: columnState([task], 1), TESTING: columnState([], 0) };

        const moved = { ...task, currentStatus: 'TESTING' };
        const response = {
            REVIEW: { tasks: [], totalCount: 0 },
            TESTING: { tasks: [moved], totalCount: 1 },
        };

        const next = mergeKanbanRefresh(prev, response);
        expect(next.REVIEW.tasks).toHaveLength(0);
        expect(next.TESTING.tasks.map(t => t.id)).toEqual(['task-1']);
    });

    it('keeps a beyond-page-window task in its lane when the poll does not mention it at all', () => {
        // task-21 is loaded (via load-more) but beyond first-page(20) in every
        // column of this poll response — it simply wasn't fetched, not moved.
        const loaded = Array.from({ length: 21 }, (_, i) =>
            makeTask({ id: `task-${i + 1}`, currentStatus: 'DONE' }));
        const prev: KanbanColumnsState = { DONE: columnState(loaded, 21) };

        const response = { DONE: { tasks: loaded.slice(0, 20), totalCount: 21 } };
        const next = mergeKanbanRefresh(prev, response);

        expect(next.DONE.tasks.map(t => t.id)).toContain('task-21');
        expect(next.DONE.tasks).toHaveLength(21);
    });

    it('adds brand-new tasks that appear in the fresh page', () => {
        const existing = makeTask({ id: 'task-1', currentStatus: 'TODO' });
        const prev: KanbanColumnsState = { TODO: columnState([existing], 1) };

        const brandNew = makeTask({ id: 'task-2', currentStatus: 'TODO' });
        const response = { TODO: { tasks: [brandNew, existing], totalCount: 2 } };

        const next = mergeKanbanRefresh(prev, response);
        expect(next.TODO.tasks.map(t => t.id)).toEqual(['task-2', 'task-1']);
    });

    it('reflects totalCount from the response even when nothing new is retained', () => {
        const prev: KanbanColumnsState = { TODO: columnState([], 0) };
        const response = { TODO: { tasks: [], totalCount: 5 } };
        const next = mergeKanbanRefresh(prev, response);
        expect(next.TODO.totalCount).toBe(5);
    });
});

// ─── appendLoadMore ───

describe('appendLoadMore', () => {
    it('appends the next page after the currently loaded tasks', () => {
        const page1 = Array.from({ length: 20 }, (_, i) => makeTask({ id: `t${i}`, currentStatus: 'TODO' }));
        const prev: KanbanColumnsState = { TODO: columnState(page1, 45) };

        const page2 = Array.from({ length: 20 }, (_, i) => makeTask({ id: `t${i + 20}`, currentStatus: 'TODO' }));
        const next = appendLoadMore(prev, 'TODO', { tasks: page2, totalCount: 45 });

        expect(next.TODO.tasks).toHaveLength(40);
        expect(next.TODO.tasks.map(t => t.id)).toEqual([...page1, ...page2].map(t => t.id));
    });

    it('does not duplicate a task id present in both the loaded set and the new page', () => {
        const page1 = [makeTask({ id: 't0', currentStatus: 'TODO' })];
        const prev: KanbanColumnsState = { TODO: columnState(page1, 2) };

        const overlap = [makeTask({ id: 't0', currentStatus: 'TODO' }), makeTask({ id: 't1', currentStatus: 'TODO' })];
        const next = appendLoadMore(prev, 'TODO', { tasks: overlap, totalCount: 2 });

        expect(next.TODO.tasks.map(t => t.id)).toEqual(['t0', 't1']);
    });

    it('is a no-op when the column is unknown', () => {
        const prev: KanbanColumnsState = { TODO: columnState([], 0) };
        const next = appendLoadMore(prev, 'MISSING', { tasks: [], totalCount: 0 });
        expect(next).toBe(prev);
    });
});

// ─── collapseColumn ───

describe('collapseColumn', () => {
    it('trims a column back down to the default page size', () => {
        const tasks = Array.from({ length: 40 }, (_, i) => makeTask({ id: `t${i}`, currentStatus: 'TODO' }));
        const prev: KanbanColumnsState = { TODO: columnState(tasks, 40) };

        const next = collapseColumn(prev, 'TODO', 20);
        expect(next.TODO.tasks).toHaveLength(20);
        expect(next.TODO.tasks.map(t => t.id)).toEqual(tasks.slice(0, 20).map(t => t.id));
        expect(next.TODO.totalCount).toBe(40);
    });
});
