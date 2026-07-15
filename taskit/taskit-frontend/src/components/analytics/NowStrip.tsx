import { formatDistanceToNowStrict } from 'date-fns';
import type { FactoryRunningTask, FactoryQueues, MemoryShareHolder, MemorySharesBlock } from '@/types';

interface NowStripProps {
    running: FactoryRunningTask[];
    queues: FactoryQueues | null;
    onTaskClick: (taskId: string) => void;
    memoryShares?: MemorySharesBlock | null;
}

const QUEUE_BUCKETS: Array<{ key: keyof FactoryQueues; label: string }> = [
    { key: 'executing', label: 'Executing' },
    { key: 'waiting', label: 'Queued' },
    { key: 'review', label: 'Review' },
    { key: 'shelf', label: 'Parked' },
];

function heartbeatLabel(secondsSinceHeartbeat: number): string {
    if (secondsSinceHeartbeat < 60) {
        return `${Math.round(secondsSinceHeartbeat)}s ago`;
    }
    const then = new Date(Date.now() - secondsSinceHeartbeat * 1000);
    return `${formatDistanceToNowStrict(then)} ago`;
}

/**
 * Compose the "3 executing + 1 review = 4/4 memory shares" line — task #353.
 * The split comes from the same backend primitives the dispatcher uses
 * (`memory_share_summary()`), so what the operator sees cannot drift from
 * the gate that actually blocks new spawns.
 */
function memorySharesLine(block: MemorySharesBlock | null | undefined): {
    text: string;
    holders: MemoryShareHolder[];
} | null {
    if (!block) return null;
    const { executing_count, reflecting_count, shares_in_use, max_shares, holders } = block;
    if (shares_in_use === 0 && executing_count === 0 && reflecting_count === 0) {
        return null;
    }
    const max = max_shares > 0 ? max_shares : '∞';
    const text = `${executing_count} executing + ${reflecting_count} review = ${shares_in_use}/${max} memory shares`;
    return { text, holders: holders ?? [] };
}

// Compact operational-pulse strip (Factory's content, ported and condensed).
// Not a card grid — a single horizontal strip so it reads at a glance.
export function NowStrip({ running, queues, onTaskClick, memoryShares }: NowStripProps) {
    const sharesLine = memorySharesLine(memoryShares);
    return (
        <div className="flex flex-col gap-3 rounded-lg border border-border bg-card/40 px-4 py-3">
            <div className="flex flex-wrap items-stretch gap-4">
                <div className="flex shrink-0 items-center gap-3 border-r border-border pr-4">
                    {QUEUE_BUCKETS.map(({ key, label }) => (
                        <div key={key} className="text-center">
                            <div className="text-lg font-semibold leading-none tabular-nums">
                                {queues == null ? '—' : queues[key]}
                            </div>
                            <div className="text-[11px] text-muted-foreground">{label}</div>
                        </div>
                    ))}
                </div>
                <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
                    {running.length === 0 ? (
                        <span className="text-sm text-muted-foreground">No tasks running</span>
                    ) : (
                        running.map(task => (
                            <button
                                key={task.task_id}
                                type="button"
                                onClick={() => onTaskClick(task.task_id)}
                                className="flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs transition-colors hover:bg-accent/50"
                                title={`${task.task_title} — ${task.state}`}
                            >
                                <span className="size-1.5 shrink-0 rounded-full bg-green-500 animate-pulse" />
                                <span className="max-w-[160px] truncate font-medium">{task.task_title}</span>
                                <span className="shrink-0 text-muted-foreground">
                                    {heartbeatLabel(task.seconds_since_heartbeat)}
                                </span>
                            </button>
                        ))
                    )}
                </div>
            </div>
            {sharesLine && (
                <div
                    className="flex flex-wrap items-center gap-2 border-t border-border pt-2 text-xs text-muted-foreground"
                    data-testid="memory-shares-line"
                >
                    <span className="font-mono tabular-nums font-medium text-foreground">
                        {sharesLine.text}
                    </span>
                    {sharesLine.holders.length > 0 && (
                        <span className="flex flex-wrap items-center gap-1.5">
                            <span aria-hidden="true">·</span>
                            {sharesLine.holders.slice(0, 6).map(h => (
                                <button
                                    key={`${h.kind}:${h.task_id}:${h.report_id ?? ''}`}
                                    type="button"
                                    onClick={() => onTaskClick(String(h.task_id))}
                                    className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 font-mono text-[10px] transition-colors hover:bg-accent/50 ${
                                        h.kind === 'reflection'
                                            ? 'border-purple-500/40 text-purple-700 dark:text-purple-300'
                                            : 'border-emerald-500/40 text-emerald-700 dark:text-emerald-300'
                                    }`}
                                    title={h.kind === 'reflection'
                                        ? `Reflection on task ${h.task_id} — ${h.task_title}`
                                        : `Task ${h.task_id} — ${h.task_title}`}
                                >
                                    <span
                                        className={`size-1.5 shrink-0 rounded-full ${
                                            h.kind === 'reflection'
                                                ? 'bg-purple-500'
                                                : 'bg-emerald-500'
                                        }`}
                                        aria-hidden="true"
                                    />
                                    {h.kind === 'reflection' ? 'reflection' : 'task'} {h.task_id}
                                </button>
                            ))}
                            {sharesLine.holders.length > 6 && (
                                <span className="text-[10px]">
                                    +{sharesLine.holders.length - 6} more
                                </span>
                            )}
                        </span>
                    )}
                </div>
            )}
        </div>
    );
}
