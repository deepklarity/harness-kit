import type { AnalyticsPerSpecRow } from '../../types';
import { formatCost } from '../../utils/costEstimation';

interface PerSpecListProps {
    rows: AnalyticsPerSpecRow[];
    onSpecClick?: (odinId: string) => void;
}

export function PerSpecList({ rows, onSpecClick }: PerSpecListProps) {
    if (!rows || rows.length === 0) {
        return (
            <div className="rounded-md border border-border px-3 py-4 text-center text-sm text-muted-foreground">
                No specs yet
            </div>
        );
    }
    return (
        <div className="overflow-x-auto rounded-md border border-border">
            <table className="w-full text-sm">
                <thead>
                    <tr className="border-b border-border bg-muted/30 text-xs uppercase tracking-wide text-muted-foreground">
                        <th className="py-2 px-3 text-left font-medium">Spec</th>
                        <th className="py-2 px-3 text-left font-medium">Board</th>
                        <th className="py-2 px-3 text-right font-medium">Tasks</th>
                        <th className="py-2 px-3 text-right font-medium">Done</th>
                        <th className="py-2 px-3 text-right font-medium">Cost</th>
                    </tr>
                </thead>
                <tbody>
                    {rows.map(row => {
                        const interactive = !!onSpecClick;
                        const cls = interactive
                            ? "border-b border-border/50 hover:bg-muted/30 cursor-pointer focus:outline-none focus:ring-2 focus:ring-ring"
                            : "border-b border-border/50 hover:bg-muted/30";
                        const props = interactive
                            ? {
                                  role: "button" as const,
                                  tabIndex: 0,
                                  "aria-label": `Open spec ${row.title}`,
                                  onClick: () => onSpecClick?.(row.odin_id),
                                  onKeyDown: (e: React.KeyboardEvent) => {
                                      if (e.key === "Enter" || e.key === " ") {
                                          e.preventDefault();
                                          onSpecClick?.(row.odin_id);
                                      }
                                  },
                              }
                            : {};
                        return (
                            <tr key={row.odin_id} className={cls} {...props}>
                                <td className="py-2 px-3">
                                    <div className="font-medium">{row.title}</div>
                                    <div className="text-xs text-muted-foreground tabular-nums">{row.odin_id}</div>
                                </td>
                                <td className="py-2 px-3 text-muted-foreground">{row.board_name ?? '—'}</td>
                                <td className="py-2 px-3 text-right tabular-nums">{row.task_count}</td>
                                <td className="py-2 px-3 text-right tabular-nums">
                                    <span className="text-green-700 dark:text-green-400">{row.done_count}</span>
                                    {row.failed_count > 0 && (
                                        <span className="ml-1 text-red-700 dark:text-red-400">
                                            /{row.failed_count} failed
                                        </span>
                                    )}
                                </td>
                                <td className="py-2 px-3 text-right tabular-nums">
                                    {row.total_cost_usd > 0 ? formatCost(row.total_cost_usd) : '—'}
                                </td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );
}