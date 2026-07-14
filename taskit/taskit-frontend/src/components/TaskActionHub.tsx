import { useMemo, useState } from 'react';
import type { Task, TaskComment } from '../types';
import { useService } from '../contexts/ServiceContext';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { HelpCircle, Send, CheckCircle2, RotateCcw, Loader2 } from 'lucide-react';

/**
 * Region 2 — "Waiting on you". The single place a human answers the one
 * question the modal exists to answer: is anything blocked on ME, and what do
 * I do about it. Rendered at the top of the right column so it is unmissable.
 *
 * Three mutually-relevant states, in priority order:
 *  1. Parked awaiting a human (`task.needsHuman`) — a merge conflict or a
 *     pending agent question. ANY human comment resumes a parked merge
 *     (backend `resume_merge_on_human_reply` signal); a pending question is
 *     answered via the reply endpoint so its record is marked answered.
 *  2. TESTING shelf — merged and as-good-as-done; the flip to DONE is the
 *     human's call.
 *  3. FAILED — requeue by re-dispatching to IN_PROGRESS (the DAG executor
 *     only picks up IN_PROGRESS; TODO is a dead end). Needs an assignee.
 *
 * Renders nothing when none apply — it is an action hub, not a metric row.
 */

function findPendingQuestion(comments: TaskComment[]): TaskComment | null {
    // Newest pending question wins so the reply targets the live block.
    for (let i = comments.length - 1; i >= 0; i--) {
        const c = comments[i];
        if (c.commentType !== 'question') continue;
        const meta = Array.isArray(c.attachments)
            ? (c.attachments as Array<Record<string, unknown>>).find(a => a?.type === 'question')
            : null;
        const status = (meta?.status as string) || 'pending';
        if (status === 'pending') return c;
    }
    return null;
}

interface TaskActionHubProps {
    task: Task;
    onUpdateTask: (taskId: string, updates: Record<string, unknown>) => Promise<void> | void;
    onRefresh?: (taskId: string) => void;
    /** Author email stamped on the resume comment/reply. Optional. */
    authorEmail?: string;
}

export function TaskActionHub({ task, onUpdateTask, onRefresh, authorEmail }: TaskActionHubProps) {
    const service = useService();
    const [replyText, setReplyText] = useState('');
    const [sending, setSending] = useState(false);
    const [statusBusy, setStatusBusy] = useState(false);

    const pendingQuestion = useMemo(() => findPendingQuestion(task.comments), [task.comments]);
    const isParked = !!task.needsHuman;
    const isTesting = task.currentStatus === 'TESTING';
    const isFailed = task.currentStatus === 'FAILED';
    const hasAssignee = (task.assigneeIds?.length ?? 0) > 0;

    if (!isParked && !isTesting && !isFailed) return null;

    const handleSendReply = async () => {
        const text = replyText.trim();
        if (!text || sending) return;
        setSending(true);
        try {
            if (pendingQuestion) {
                // Reply endpoint marks the question answered AND resumes a
                // parked merge (both create a human TaskComment).
                await service.replyToQuestion(task.id, pendingQuestion.id, text);
            } else {
                // No explicit question — a plain comment resumes the parked merge.
                await service.addComment(task.id, text, authorEmail);
            }
            setReplyText('');
            onRefresh?.(task.id);
        } catch (e) {
            console.error('Failed to send reply:', e);
        } finally {
            setSending(false);
        }
    };

    const handleStatus = async (status: string) => {
        if (statusBusy) return;
        setStatusBusy(true);
        try {
            await onUpdateTask(task.id, { status });
            onRefresh?.(task.id);
        } finally {
            setStatusBusy(false);
        }
    };

    return (
        <div data-testid="action-hub" className="mb-6 space-y-3">
            {isParked && (
                <div
                    data-testid="parked-callout"
                    className="rounded-lg border-2 border-amber-500/50 bg-amber-500/10 p-4"
                >
                    <div className="flex items-center gap-2 mb-2">
                        <HelpCircle className="size-4 text-amber-500 motion-safe:animate-pulse" />
                        <span className="text-sm font-bold text-amber-600 dark:text-amber-400">
                            {task.needsHumanReason || 'Waiting for your reply'}
                        </span>
                        <span className="ml-auto text-[10px] uppercase tracking-wider font-semibold text-amber-600/70 dark:text-amber-400/70">
                            Blocked on you
                        </span>
                    </div>
                    {pendingQuestion && (
                        <div
                            data-testid="parked-question-text"
                            className="mb-3 rounded-md bg-background/60 border border-amber-500/20 px-3 py-2 text-sm text-foreground/90 whitespace-pre-wrap"
                        >
                            {pendingQuestion.content}
                        </div>
                    )}
                    <div className="flex gap-2">
                        <Input
                            data-testid="parked-reply-input"
                            placeholder="Type your answer to resume…"
                            value={replyText}
                            onChange={e => setReplyText(e.target.value)}
                            onKeyDown={e => e.key === 'Enter' && !e.shiftKey && handleSendReply()}
                            className="text-sm border-amber-500/30 bg-background"
                            autoFocus
                        />
                        <Button
                            data-testid="parked-reply-send"
                            size="sm"
                            onClick={handleSendReply}
                            disabled={!replyText.trim() || sending}
                            className="gap-1 bg-amber-600 hover:bg-amber-700 text-white shrink-0"
                        >
                            {sending ? <Loader2 className="size-3 motion-safe:animate-spin" /> : <Send className="size-3" />}
                            Reply &amp; resume
                        </Button>
                    </div>
                </div>
            )}

            {isTesting && (
                <div className="flex items-center gap-3 rounded-lg border border-emerald-500/30 bg-emerald-500/5 px-4 py-3">
                    <CheckCircle2 className="size-4 text-emerald-500 shrink-0" />
                    <div className="flex-1 min-w-0">
                        <div className="text-sm font-semibold text-emerald-600 dark:text-emerald-400">Merged — on the testing shelf</div>
                        <div className="text-xs text-muted-foreground">Verify it, then flip to Done.</div>
                    </div>
                    <Button
                        data-testid="mark-done-btn"
                        size="sm"
                        onClick={() => handleStatus('DONE')}
                        disabled={statusBusy}
                        className="gap-1.5 bg-emerald-600 hover:bg-emerald-700 text-white shrink-0"
                    >
                        {statusBusy ? <Loader2 className="size-3 motion-safe:animate-spin" /> : <CheckCircle2 className="size-3.5" />}
                        Mark Done
                    </Button>
                </div>
            )}

            {isFailed && (
                <div className="flex items-center gap-3 rounded-lg border border-red-500/30 bg-red-500/5 px-4 py-3">
                    <RotateCcw className="size-4 text-red-500 shrink-0" />
                    <div className="flex-1 min-w-0">
                        <div className="text-sm font-semibold text-red-600 dark:text-red-400">Task failed</div>
                        <div className="text-xs text-muted-foreground">
                            {hasAssignee ? 'Requeue to re-dispatch the same agent.' : 'Assign an agent to requeue.'}
                        </div>
                    </div>
                    <Button
                        data-testid="requeue-btn"
                        size="sm"
                        variant="outline"
                        onClick={() => handleStatus('IN_PROGRESS')}
                        disabled={statusBusy || !hasAssignee}
                        className="gap-1.5 border-red-500/40 text-red-600 hover:bg-red-500/10 hover:text-red-700 shrink-0"
                    >
                        {statusBusy ? <Loader2 className="size-3 motion-safe:animate-spin" /> : <RotateCcw className="size-3.5" />}
                        Requeue
                    </Button>
                </div>
            )}
        </div>
    );
}
