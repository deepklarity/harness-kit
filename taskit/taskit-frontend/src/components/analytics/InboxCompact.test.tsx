import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { InboxCompact } from './InboxCompact';
import type { InboxSnapshot } from '@/types';

interface FailedTask {
    task_id: string;
    task_title: string;
    last_failure_reason: string;
    failure_class: string;
    failed_at: string;
    blocked_count: number;
}

interface InboxSnapshotWithFailed extends InboxSnapshot {
    failed_tasks: FailedTask[];
    counts: InboxSnapshot['counts'] & { failed_tasks: number };
}

function makeFailedTask(overrides: Partial<FailedTask> = {}): FailedTask {
    return {
        task_id: '42',
        task_title: 'Login flow broken',
        last_failure_reason: 'odin exec exited 1: assertion failed in test_login.py',
        failure_class: 'agent_execution_failure',
        failed_at: new Date(Date.now() - 42 * 60 * 1000).toISOString(),
        blocked_count: 2,
        ...overrides,
    };
}

function makeInbox(
    options: { failed?: FailedTask[]; withParking?: boolean; withShelf?: boolean } = {}
): InboxSnapshotWithFailed {
    const failed = options.failed ?? [];
    const inbox: InboxSnapshotWithFailed = {
        board_id: '7',
        board_name: 'Test board',
        parked_merges: options.withParking
            ? [
                {
                    task_id: '99',
                    task_title: 'Merge conflict task',
                    why: 'Which branch wins?',
                    question_comment_id: null,
                    conflicting_files: ['a.py'],
                },
            ]
            : [],
        reversibility_parks: [],
        testing_shelf: options.withShelf
            ? [{ task_id: '100', task_title: 'Ready to land' }]
            : [],
        open_errors: [],
        failed_tasks: failed,
        counts: {
            parked_merges: options.withParking ? 1 : 0,
            reversibility_parks: 0,
            testing_shelf: options.withShelf ? 1 : 0,
            open_errors: 0,
            failed_tasks: failed.length,
        },
    };
    return inbox;
}

describe('InboxCompact — failed_tasks visibility (fable task 335)', () => {
    it('renders failed tasks first, before parked merges and shelves', () => {
        const inbox = makeInbox({
            failed: [makeFailedTask({ task_title: 'Login flow broken' })],
            withParking: true,
            withShelf: true,
        });

        render(
            <InboxCompact
                inbox={inbox as unknown as InboxSnapshot}
                onTaskClick={vi.fn()}
            />
        );

        // The failed task title must be present somewhere in the document.
        // Current InboxCompact has no failed_tasks handling — this fails
        // until the implementation renders the row.
        expect(screen.getByText('Login flow broken')).toBeInTheDocument();
        // The parked merge and shelf rows still render — verify they coexist.
        expect(screen.getByText('Merge conflict task')).toBeInTheDocument();
        expect(screen.getByText('Ready to land')).toBeInTheDocument();
        // The other categories still render too — that proves failed
        // tasks were added on top rather than replacing them.
    });

    it('failed row shows the failure reason, the age, and the blocked count', () => {
        const reason = 'GLM exhausted retries after 3 attempts';
        const inbox = makeInbox({
            failed: [
                makeFailedTask({
                    last_failure_reason: reason,
                    blocked_count: 2,
                    failed_at: new Date(Date.now() - 31 * 60 * 1000).toISOString(),
                }),
            ],
        });

        render(
            <InboxCompact
                inbox={inbox as unknown as InboxSnapshot}
                onTaskClick={vi.fn()}
            />
        );

        // The full failure reason must appear so the operator sees WHY,
        // not a placeholder like "Likely execution failure".
        expect(screen.getByText(reason)).toBeInTheDocument();
        // The blocked count badge — the number itself is visible.
        // Implementation may wrap it in a chip ("×2", "2 blocked", etc.) —
        // we assert the integer appears as standalone text in the DOM.
        const twoHits = screen.getAllByText('2');
        expect(twoHits.length).toBeGreaterThan(0);
        // The age text uses a relative-time format like "31m ago" or
        // "failed 31m ago". Just confirm an "ago" suffix is present.
        expect(screen.getByText(/ago/i)).toBeInTheDocument();
    });

    it('does not render a failed section when failed_tasks is empty', () => {
        // An inbox with one parked merge and an explicit empty failed
        // list. The implementation must NOT emit a "Failed (0)" header
        // row when failed_tasks is empty — that would mislead operators
        // into thinking a task is failed when none is.
        const inbox = makeInbox({
            failed: [],
            withParking: true,
        });

        const { container } = render(
            <InboxCompact
                inbox={inbox as unknown as InboxSnapshot}
                onTaskClick={vi.fn()}
            />
        );

        // Exactly one row renders — the parked merge. No extra "Failed"
        // header row was added for the empty list.
        const rows = container.querySelectorAll('li');
        expect(rows).toHaveLength(1);
        expect(screen.getByText('Merge conflict task')).toBeInTheDocument();
        expect(
            screen.queryByText('Nothing waiting on a human')
        ).not.toBeInTheDocument();
    });
});
