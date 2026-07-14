import { describe, it, expect } from 'vitest';
import { isMachineComment, isInlineReply, partitionComments } from './commentStream';
import type { TaskComment, CommentType } from '../types';

function makeComment(overrides: Partial<TaskComment> = {}): TaskComment {
    return {
        id: 'c1',
        taskId: 't1',
        authorEmail: 'agent+sonnet@odin.agent',
        authorLabel: 'agent',
        content: 'Completed in 12s\nDid the thing.',
        attachments: [],
        commentType: 'status_update' as CommentType,
        createdAt: new Date().toISOString(),
        ...overrides,
    };
}

describe('commentStream — machine vs event classification', () => {
    it('flags execution-trace attachments as machine noise', () => {
        const c = makeComment({ attachments: ['trace:execution_jsonl'] });
        expect(isMachineComment(c)).toBe(true);
    });

    it('flags debug-prefixed attachments as machine noise', () => {
        const c = makeComment({ attachments: ['debug:input_prompt'] });
        expect(isMachineComment(c)).toBe(true);
    });

    it('treats an agent status summary (no trace/debug) as a human-relevant event', () => {
        const c = makeComment({ attachments: [] });
        expect(isMachineComment(c)).toBe(false);
    });

    it('treats structured proof/question attachments as events, not machine', () => {
        const c = makeComment({ commentType: 'proof', attachments: [{ type: 'proof' }] });
        expect(isMachineComment(c)).toBe(false);
    });

    it('identifies inline replies', () => {
        expect(isInlineReply(makeComment({ commentType: 'reply' }))).toBe(true);
        expect(isInlineReply(makeComment({ commentType: 'question' }))).toBe(false);
    });
});

describe('commentStream — partitionComments', () => {
    it('routes trace/debug to machine and everything else to events, preserving order', () => {
        const comments = [
            makeComment({ id: 'e1', commentType: 'status_update', attachments: [] }),
            makeComment({ id: 'm1', attachments: ['trace:execution_jsonl'] }),
            makeComment({ id: 'q1', commentType: 'question', attachments: [{ type: 'question', status: 'pending' }] }),
            makeComment({ id: 'm2', attachments: ['debug:context'] }),
            makeComment({ id: 'p1', commentType: 'proof', attachments: [{ type: 'proof' }] }),
        ];
        const { events, machine } = partitionComments(comments);
        expect(events.map(c => c.id)).toEqual(['e1', 'q1', 'p1']);
        expect(machine.map(c => c.id)).toEqual(['m1', 'm2']);
    });

    it('excludes inline replies from both buckets', () => {
        const comments = [
            makeComment({ id: 'q1', commentType: 'question' }),
            makeComment({ id: 'r1', commentType: 'reply' }),
        ];
        const { events, machine } = partitionComments(comments);
        expect(events.map(c => c.id)).toEqual(['q1']);
        expect(machine).toHaveLength(0);
    });

    it('returns empty buckets for an empty list', () => {
        expect(partitionComments([])).toEqual({ events: [], machine: [] });
    });
});
