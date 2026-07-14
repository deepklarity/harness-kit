import type { InboxFailedTask, InboxSnapshot } from '@/types';

interface InboxCompactProps {
    inbox: InboxSnapshot | null;
    onTaskClick: (taskId: string) => void;
}

interface Row {
    key: string;
    kind: string;
    kindClass?: string;
    title: string;
    detail: string;
    taskId: string | null;
    failed?: InboxFailedTask;
}

const FAILED_KIND_CLASS = 'bg-red-500/15 text-red-400';
const DEFAULT_KIND_CLASS = 'bg-muted text-muted-foreground';

function formatFailedAge(isoTimestamp: string): string {
    if (!isoTimestamp) return 'failed recently';
    const ts = new Date(isoTimestamp).getTime();
    if (Number.isNaN(ts)) return 'failed recently';
    const diffMs = Date.now() - ts;
    const diffMin = Math.max(1, Math.round(diffMs / 60000));
    if (diffMin < 60) return `failed ${diffMin}m ago`;
    const diffH = Math.round(diffMin / 60);
    if (diffH < 24) return `failed ${diffH}h ago`;
    const diffD = Math.round(diffH / 24);
    return `failed ${diffD}d ago`;
}

// Compact one-line-per-item view of everything waiting on a human. This
// deliberately drops the old InboxPanel's inline reply/dispose/rework
// controls in favor of a deep link — the action still happens, just one
// click further away, in TaskDetailModal. See Stats-rebuild judgment calls.
export function InboxCompact({ inbox, onTaskClick }: InboxCompactProps) {
    const rows: Row[] = [];

    // FAILED tasks are pinned at the top — a board should never sit silent.
    for (const failed of inbox?.failed_tasks ?? []) {
        rows.push({
            key: `failed-${failed.task_id}`,
            kind: 'FAILED',
            kindClass: FAILED_KIND_CLASS,
            title: failed.task_title,
            detail: '',
            taskId: failed.task_id,
            failed,
        });
    }
    for (const merge of inbox?.parked_merges ?? []) {
        rows.push({ key: `merge-${merge.task_id}`, kind: 'Parked merge', title: merge.task_title, detail: merge.why || '', taskId: merge.task_id });
    }
    for (const park of inbox?.reversibility_parks ?? []) {
        rows.push({ key: `park-${park.task_id}`, kind: 'Reversibility park', title: park.task_title, detail: park.why || '', taskId: park.task_id });
    }
    for (const shelf of inbox?.testing_shelf ?? []) {
        rows.push({ key: `shelf-${shelf.task_id}`, kind: 'Testing shelf', title: shelf.task_title, detail: '', taskId: shelf.task_id });
    }
    for (const error of inbox?.open_errors ?? []) {
        rows.push({
            key: `error-${error.source}-${error.signature}`,
            kind: 'Open error',
            title: error.latest_symptom,
            detail: `×${error.count}`,
            taskId: error.task_id,
        });
    }

    return (
        <div>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Inbox</h3>
            {rows.length === 0 ? (
                <div className="rounded-md border border-border px-3 py-4 text-center text-sm text-muted-foreground">
                    Nothing waiting on a human
                </div>
            ) : (
                <ul className="divide-y divide-border rounded-md border border-border">
                    {rows.map(row => {
                        const badgeClass = row.kindClass ?? DEFAULT_KIND_CLASS;
                        const isFailed = !!row.failed;
                        return (
                            <li key={row.key} className="flex items-center justify-between gap-3 px-3 py-1.5 text-sm">
                                <div className="flex min-w-0 items-center gap-2">
                                    <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${badgeClass}`}>
                                        {row.kind}
                                    </span>
                                    <span className="min-w-0 truncate font-medium">{row.title}</span>
                                    {isFailed ? (
                                        <span className="min-w-0 truncate text-xs text-muted-foreground">
                                            {row.failed!.last_failure_reason || 'Failed'}
                                        </span>
                                    ) : row.detail ? (
                                        <span className="shrink-0 text-xs text-muted-foreground">{row.detail}</span>
                                    ) : null}
                                </div>
                                {isFailed ? (
                                    <div className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
                                        <span>{formatFailedAge(row.failed!.failed_at)}</span>
                                        {row.failed!.blocked_count > 0 && (
                                            <>
                                                <span aria-hidden="true">·</span>
                                                <span>blocks</span>
                                                <strong className="font-semibold text-foreground">{row.failed!.blocked_count}</strong>
                                            </>
                                        )}
                                        {row.taskId ? (
                                            <button
                                                type="button"
                                                onClick={() => onTaskClick(row.taskId!)}
                                                className="text-muted-foreground underline transition-colors hover:text-foreground"
                                            >
                                                Open
                                            </button>
                                        ) : (
                                            <span className="text-muted-foreground/50">—</span>
                                        )}
                                    </div>
                                ) : row.taskId ? (
                                    <button
                                        type="button"
                                        onClick={() => onTaskClick(row.taskId!)}
                                        className="shrink-0 text-xs text-muted-foreground underline transition-colors hover:text-foreground"
                                    >
                                        Open
                                    </button>
                                ) : (
                                    <span className="shrink-0 text-xs text-muted-foreground/50">—</span>
                                )}
                            </li>
                        );
                    })}
                </ul>
            )}
        </div>
    );
}
