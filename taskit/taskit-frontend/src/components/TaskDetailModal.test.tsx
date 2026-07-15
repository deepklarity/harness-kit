import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ServiceContext } from '@/contexts/ServiceContext';
import { AuthContext } from '@/contexts/AuthContext';
import type { AuthContextValue } from '@/contexts/AuthContext';
import type { HarnessTimeService } from '../services/harness/HarnessTimeService';
import { TaskDetailModal } from './TaskDetailModal';
import type { Task, TaskComment, TaskMutation } from '../types';

function makeTask(overrides: Partial<Task> = {}): Task {
    return {
        id: '356',
        name: 'Task page readability',
        title: 'Task page readability',
        idShort: 356,
        shortLink: '356',
        boardId: 'b1',
        boardName: 'Wave 6',
        currentStatus: 'FAILED',
        assignees: ['agent'],
        assigneeIds: ['a1'],
        createdAt: new Date('2025-01-01').toISOString(),
        createdBy: 'odin@example.com',
        mutations: [],
        comments: [],
        timeInStatuses: {},
        totalLifespanMs: 0,
        workTimeMs: 0,
        executingTimeMs: 2003000,
        metadata: {},
        ...overrides,
    };
}

function makeComment(n: number): TaskComment {
    return {
        id: `c${n}`,
        taskId: '356',
        authorEmail: 'agent+sonnet@odin.agent',
        authorLabel: 'agent',
        content: `Comment number ${n}`,
        attachments: [],
        commentType: 'status_update',
        createdAt: new Date(2025, 0, 1, 0, n).toISOString(),
    };
}

function makeMutation(overrides: Partial<TaskMutation> = {}): TaskMutation {
    return {
        id: 'm1',
        type: 'status_change',
        date: new Date('2025-01-02').toISOString(),
        timestamp: Date.now(),
        actor: 'celery',
        actorId: 'celery',
        description: 'status changed from EXECUTING to FAILED',
        fieldName: 'status',
        ...overrides,
    };
}

function mockService(): Partial<HarnessTimeService> {
    return {
        fetchReflections: vi.fn().mockResolvedValue([]),
        fetchTaskIdeOptions: vi.fn().mockResolvedValue(null),
        getLabels: vi.fn().mockResolvedValue([]),
    };
}

const mockAuth: AuthContextValue = {
    user: { id: 'u1', email: 'alice@example.com', displayName: 'Alice' },
    loading: false,
    authEnabled: false,
    login: vi.fn(),
    logout: vi.fn(),
    changePassword: vi.fn(),
    getIdToken: vi.fn().mockResolvedValue(null),
};

function renderModal(task: Task, service: Partial<HarnessTimeService> = mockService()) {
    return render(
        <MemoryRouter>
            <ServiceContext.Provider value={service as HarnessTimeService}>
                <AuthContext.Provider value={mockAuth}>
                    <TaskDetailModal
                        task={task}
                        onClose={vi.fn()}
                        allMembers={[]}
                        onUpdateAssignees={vi.fn()}
                        onUpdateTask={vi.fn()}
                        availableStatuses={['TODO', 'IN_PROGRESS', 'REVIEW', 'TESTING', 'DONE', 'FAILED']}
                        onRefresh={vi.fn()}
                    />
                </AuthContext.Provider>
            </ServiceContext.Provider>
        </MemoryRouter>,
    );
}

describe('TaskDetailModal — sidebar honesty', () => {
    beforeEach(() => {
        // The session-meta effect polls fetch on a timer; stub it so no real
        // network call escapes and no interval leaks across tests.
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, json: async () => ({}) }));
    });
    afterEach(() => {
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
    });

    it('hides the Budget line when no time budget is set', () => {
        renderModal(makeTask({ devEta: undefined, executingTimeMs: 2003000 }));
        // No budget set → the bare "Budget: —" line must not render.
        expect(screen.queryByText('Budget:')).not.toBeInTheDocument();
        // But the used time still shows, now labelled so the operator knows
        // what it counts.
        expect(screen.getByText('Time used:')).toBeInTheDocument();
    });

    it('shows Budget + Used when a budget is set', () => {
        renderModal(makeTask({ devEta: 2, executingTimeMs: 2003000 }));
        expect(screen.getByText('Budget:')).toBeInTheDocument();
        expect(screen.getByText('Used:')).toBeInTheDocument();
    });

    it('hides the WHY row when there is no assignment reason', () => {
        renderModal(makeTask({ metadata: {} }));
        expect(screen.queryByTestId('assignment-reason-row')).not.toBeInTheDocument();
    });

    it('renders the failure tag once when class and type match', () => {
        renderModal(
            makeTask({
                currentStatus: 'FAILED',
                metadata: {
                    last_failure_reason: 'agent exited non-zero',
                    failure_class: 'stale_execution',
                    last_failure_type: 'stale_execution',
                },
            }),
        );
        // Both metadata fields carry the same value — the tag must appear once,
        // not "stale_execution stale_execution".
        expect(screen.getAllByText('stale_execution')).toHaveLength(1);
    });
});

describe('TaskDetailModal — one history', () => {
    beforeEach(() => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, json: async () => ({}) }));
    });
    afterEach(() => {
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
    });

    it('collapses the Activity timeline by default (comments is the read surface)', () => {
        const task = makeTask({
            currentStatus: 'DONE',
            mutations: [makeMutation({ description: 'UNIQUE_MUTATION_DESC_123' })],
        });
        renderModal(task);

        const toggle = screen.getByTestId('activity-toggle');
        expect(toggle.getAttribute('aria-expanded')).toBe('false');
        // Collapsed → the mutation tick is not in the document yet.
        expect(screen.queryByText('UNIQUE_MUTATION_DESC_123')).not.toBeInTheDocument();

        // Expanding reveals the timeline.
        fireEvent.click(toggle);
        expect(screen.getByText('UNIQUE_MUTATION_DESC_123')).toBeInTheDocument();
    });
});

describe('TaskDetailModal — fixture snapshot (70+ comments)', () => {
    beforeEach(() => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, json: async () => ({}) }));
    });
    afterEach(() => {
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
    });

    it('renders with 70 comments: truncates to the last 10 and offers the rest', async () => {
        const comments = Array.from({ length: 70 }, (_, i) => makeComment(i + 1));
        const task = makeTask({ currentStatus: 'REVIEW', comments });
        renderModal(task);

        // Header reports the full event count.
        await waitFor(() => {
            expect(screen.getByText(/Comments \(70\)/)).toBeInTheDocument();
        });

        // Only the last 10 comment items render by default.
        const items = document.querySelectorAll('[id^="comment-item-c"]');
        expect(items).toHaveLength(10);

        // The remaining 60 are offered behind one button.
        expect(screen.getByText(/Show 60 older comments/)).toBeInTheDocument();
    });
});
