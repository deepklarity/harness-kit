import { formatDistanceToNowStrict } from 'date-fns';
import type { FactoryRunningTask, FactoryQueues } from '@/types';

interface NowStripProps {
    running: FactoryRunningTask[];
    queues: FactoryQueues | null;
    onTaskClick: (taskId: string) => void;
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

// Compact operational-pulse strip (Factory's content, ported and condensed).
// Not a card grid — a single horizontal strip so it reads at a glance.
export function NowStrip({ running, queues, onTaskClick }: NowStripProps) {
    return (
        <div className="flex flex-wrap items-stretch gap-4 rounded-lg border border-border bg-card/40 px-4 py-3">
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
                            <span className="shrink-0 text-muted-foreground">{heartbeatLabel(task.seconds_since_heartbeat)}</span>
                        </button>
                    ))
                )}
            </div>
        </div>
    );
}
