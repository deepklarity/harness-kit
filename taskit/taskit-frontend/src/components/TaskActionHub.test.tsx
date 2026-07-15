import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ServiceContext } from '@/contexts/ServiceContext';
import type { HarnessTimeService } from '../services/harness/HarnessTimeService';
import { TaskActionHub } from './TaskActionHub';
import type { Task, TaskComment, CommentType } from '../types';

function makeTask(overrides: Partial<Task> = {}): Task {
    return {
        id: '225',
        name: 'A task',
        idShort: 225,
        shortLink: '225',
        boardId: 'b1',
        boardName: 'Wave 6',
        currentStatus: 'IN_PROGRESS',
        assignees: ['Agent'],
        assigneeIds: ['a1'],
        createdAt: new Date().toISOString(),
        createdBy: 'user@example.com',
        mutations: [],
        comments: [],
        timeInStatuses: {},
        totalLifespanMs: 0,
        workTimeMs: 0,
        executingTimeMs: 0,
        ...overrides,
    };
}

function makeQuestion(overrides: Partial<TaskComment> = {}): TaskComment {
    return {
        id: 'q1',
        taskId: '225',
        authorEmail: 'agent+sonnet@odin.agent',
        authorLabel: 'agent',
        content: 'Merge conflict in foo.py — keep ours or theirs?',
        attachments: [{ type: 'question', status: 'pending' }],
        commentType: 'question' as CommentType,
        createdAt: new Date().toISOString(),
        ...overrides,
    };
}

function renderHub(task: Task, service: Partial<HarnessTimeService>, onUpdateTask = vi.fn(), onRefresh = vi.fn()) {
    return render(
        <ServiceContext.Provider value={service as HarnessTimeService}>
            <TaskActionHub task={task} onUpdateTask={onUpdateTask} onRefresh={onRefresh} />
        </ServiceContext.Provider>,
    );
}

describe('TaskActionHub — region 2 (waiting on you)', () => {
    it('renders nothing when no action is required', () => {
        const { container } = renderHub(makeTask({ currentStatus: 'IN_PROGRESS' }), {});
        expect(container).toBeEmptyDOMElement();
    });

    it('surfaces a parked merge question and resumes via reply-to-question', async () => {
        const replyToQuestion = vi.fn().mockResolvedValue(undefined);
        const onRefresh = vi.fn();
        const task = makeTask({
            currentStatus: 'REVIEW',
            needsHuman: true,
            needsHumanReason: 'Merge needs human',
            comments: [makeQuestion()],
        });
        renderHub(task, { replyToQuestion }, vi.fn(), onRefresh);

        expect(screen.getByTestId('parked-callout')).toBeInTheDocument();
        expect(screen.getByText(/Merge needs human/)).toBeInTheDocument();
        // The actual blocking question text is surfaced, not buried in the stream
        expect(screen.getByText(/keep ours or theirs/)).toBeInTheDocument();

        fireEvent.change(screen.getByTestId('parked-reply-input'), { target: { value: 'keep ours' } });
        fireEvent.click(screen.getByTestId('parked-reply-send'));

        await waitFor(() => expect(replyToQuestion).toHaveBeenCalledWith('225', 'q1', 'keep ours'));
        await waitFor(() => expect(onRefresh).toHaveBeenCalledWith('225'));
    });

    it('resumes a parked merge with no pending question via a plain comment POST', async () => {
        const addComment = vi.fn().mockResolvedValue(undefined);
        const replyToQuestion = vi.fn();
        const task = makeTask({
            currentStatus: 'REVIEW',
            needsHuman: true,
            needsHumanReason: 'Merge needs human',
            comments: [],
        });
        renderHub(task, { addComment, replyToQuestion });

        fireEvent.change(screen.getByTestId('parked-reply-input'), { target: { value: 'rebase onto main' } });
        fireEvent.click(screen.getByTestId('parked-reply-send'));

        await waitFor(() => expect(addComment).toHaveBeenCalledWith('225', 'rebase onto main', undefined));
        expect(replyToQuestion).not.toHaveBeenCalled();
    });

    it('does not send an empty reply', () => {
        const addComment = vi.fn();
        const task = makeTask({ currentStatus: 'REVIEW', needsHuman: true, needsHumanReason: 'Merge needs human' });
        renderHub(task, { addComment });
        expect(screen.getByTestId('parked-reply-send')).toBeDisabled();
    });

    it('offers flip-to-DONE on the TESTING shelf', async () => {
        const onUpdateTask = vi.fn().mockResolvedValue(undefined);
        const task = makeTask({ currentStatus: 'TESTING' });
        renderHub(task, {}, onUpdateTask);

        const btn = screen.getByTestId('mark-done-btn');
        expect(btn).toBeInTheDocument();
        fireEvent.click(btn);
        await waitFor(() => expect(onUpdateTask).toHaveBeenCalledWith('225', { status: 'DONE' }));
    });

    it('requeues a FAILED task by re-dispatching to IN_PROGRESS', async () => {
        const onUpdateTask = vi.fn().mockResolvedValue(undefined);
        const task = makeTask({ currentStatus: 'FAILED', assignees: ['Agent'], assigneeIds: ['a1'] });
        renderHub(task, {}, onUpdateTask);

        const btn = screen.getByTestId('requeue-btn');
        expect(btn).toBeEnabled();
        fireEvent.click(btn);
        await waitFor(() => expect(onUpdateTask).toHaveBeenCalledWith('225', { status: 'IN_PROGRESS' }));
    });

    it('disables requeue when the FAILED task has no assignee', () => {
        const task = makeTask({ currentStatus: 'FAILED', assignees: [], assigneeIds: [] });
        renderHub(task, {});
        expect(screen.getByTestId('requeue-btn')).toBeDisabled();
        expect(screen.getByText(/Assign an agent to requeue/i)).toBeInTheDocument();
    });

    it('shows the per-class suggested action for a review-cap failure', () => {
        // Task #359 — the 3-strike cap used to show "Requeue to re-dispatch
        // the same agent", which was the banner lie. Now the server sends
        // the honest next step (read the reviewer's note) and the hub
        // renders it verbatim.
        const task = makeTask({
            currentStatus: 'FAILED',
            assignees: ['Agent'],
            assigneeIds: ['a1'],
            failureSuggestedAction:
                'The reviewer rejected this work three times in a row. Read the latest reviewer\u2019s note and decide whether to change direction or send back with new guidance.',
        });
        renderHub(task, {});

        const nextStep = screen.getByTestId('failed-next-step');
        expect(nextStep.textContent).toMatch(/reviewer rejected this work three times/i);
        // The old lie is gone.
        expect(nextStep.textContent).not.toMatch(/requeue.*re-dispatch/i);
    });

    it('falls back to the requeue wording when the server did not send a suggested action', () => {
        // Older tasks pre-#359 have no failureSuggestedAction; the hub
        // must still render *something* honest. Requeue with the old
        // wording is the safe default for that cohort.
        const task = makeTask({
            currentStatus: 'FAILED',
            assignees: ['Agent'],
            assigneeIds: ['a1'],
        });
        renderHub(task, {});
        expect(screen.getByTestId('failed-next-step').textContent)
            .toMatch(/requeue.*re-dispatch/i);
    });
});
