import type { TaskComment } from '../types';

/**
 * Comment-stream segregation for the task detail modal (region 6).
 *
 * The raw stream mixes human-relevant lifecycle events (status summaries,
 * questions, replies, proof, reflections, human comments) with raw machine
 * output (execution-trace JSONL and debug-input dumps). The modal shows the
 * events by default and tucks the machine noise behind a toggle.
 *
 * A comment is "machine noise" when it carries a raw-trace attachment
 * (`trace:execution_jsonl`) or any `debug:`-prefixed attachment string. Every
 * other top-level comment — including agent status summaries, which carry the
 * human-readable "Completed in …" line — is a human-relevant event.
 *
 * Replies (`commentType === 'reply'`) are rendered inline beneath their
 * question, so they belong to neither top-level bucket.
 */

const MACHINE_CONTENT_PREFIXES = [
    'Effective input',        // debug dump of the composed agent prompt
    'timestamp=',             // raw harness log lines
    '{"type":',               // raw stream-JSONL
];

export function isMachineComment(comment: TaskComment): boolean {
    // CommentType doesn't list 'debug', but the backend may emit it; keep the
    // defensive check (cast, not removal) so such comments still bucket as noise.
    if (comment.commentType === ('debug' as TaskComment['commentType'])) return true;
    const content = (comment.content ?? '').trimStart();
    if (MACHINE_CONTENT_PREFIXES.some(p => content.startsWith(p))) return true;
    const attachments = comment.attachments;
    if (!Array.isArray(attachments)) return false;
    return attachments.some(
        a => typeof a === 'string' && (a === 'trace:execution_jsonl' || a.startsWith('debug:')),
    );
}

export function isInlineReply(comment: TaskComment): boolean {
    return comment.commentType === 'reply';
}

export interface PartitionedComments {
    /** Human-relevant lifecycle events, in original order. */
    events: TaskComment[];
    /** Raw agent/trace output, shown only when the machine toggle is on. */
    machine: TaskComment[];
}

/**
 * Split a comment list into human-relevant events and machine noise.
 * Inline replies are excluded from both buckets (they render under questions).
 */
export function partitionComments(comments: TaskComment[]): PartitionedComments {
    const events: TaskComment[] = [];
    const machine: TaskComment[] = [];
    for (const comment of comments) {
        if (isInlineReply(comment)) continue;
        if (isMachineComment(comment)) {
            machine.push(comment);
        } else {
            events.push(comment);
        }
    }
    return { events, machine };
}
