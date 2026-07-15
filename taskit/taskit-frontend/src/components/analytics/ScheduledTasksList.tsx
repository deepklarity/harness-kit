import type { AnalyticsScheduledTaskRow } from '../../types';

interface ScheduledTasksListProps {
    rows: AnalyticsScheduledTaskRow[];
}

function statusBadgeClasses(status: string): string {
    switch (status) {
        case 'ACTIVE':
            return 'bg-green-600/15 text-green-700 dark:text-green-400 border-green-600/30';
        case 'PAUSED':
            return 'bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-500/30';
        case 'COMPLETED':
            return 'bg-sky-500/15 text-sky-700 dark:text-sky-400 border-sky-500/30';
        case 'CANCELED':
            return 'bg-muted text-muted-foreground border-border';
        default:
            return 'bg-muted text-muted-foreground border-border';
    }
}

export function ScheduledTasksList({ rows }: ScheduledTasksListProps) {
    if (!rows || rows.length === 0) {
        return (
            <div className="rounded-md border border-border px-3 py-4 text-center text-sm text-muted-foreground">
                No scheduled tasks
            </div>
        );
    }
    return (
        <div className="overflow-x-auto rounded-md border border-border">
            <table className="w-full text-sm">
                <thead>
                    <tr className="border-b border-border bg-muted/30 text-xs uppercase tracking-wide text-muted-foreground">
                        <th className="py-2 px-3 text-left font-medium">Schedule</th>
                        <th className="py-2 px-3 text-left font-medium">Board</th>
                        <th className="py-2 px-3 text-left font-medium">Status</th>
                        <th className="py-2 px-3 text-right font-medium">Runs</th>
                        <th className="py-2 px-3 text-right font-medium">Success</th>
                        <th className="py-2 px-3 text-right font-medium">Failure</th>
                    </tr>
                </thead>
                <tbody>
                    {rows.map(row => (
                        <tr key={row.id} className="border-b border-border/50 hover:bg-muted/30">
                            <td className="py-2 px-3">
                                <div className="font-medium">{row.template_title}</div>
                                <div className="text-xs text-muted-foreground tabular-nums">{row.template_kind}</div>
                            </td>
                            <td className="py-2 px-3 text-muted-foreground">{row.board_name ?? '—'}</td>
                            <td className="py-2 px-3">
                                <span className={`inline-flex items-center rounded-md border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${statusBadgeClasses(row.status)}`}>
                                    {row.status}
                                </span>
                            </td>
                            <td className="py-2 px-3 text-right tabular-nums">{row.run_count}</td>
                            <td className="py-2 px-3 text-right tabular-nums text-green-700 dark:text-green-400">
                                {row.success_count}
                            </td>
                            <td className="py-2 px-3 text-right tabular-nums">
                                {row.failure_count > 0 ? (
                                    <span className="text-red-700 dark:text-red-400">{row.failure_count}</span>
                                ) : (
                                    <span className="text-muted-foreground">0</span>
                                )}
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}