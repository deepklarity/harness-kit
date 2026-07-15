import { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ArrowUpDown, Trophy } from 'lucide-react';
import { formatCost } from '../../utils/costEstimation';
import { formatDuration, formatTokens } from '../../utils/transformer';
import type { LeagueRow } from '../../types';

interface LeagueTableProps {
    rows: LeagueRow[];
    loading?: boolean;
    // Hides the card's own title — used when the caller renders its own
    // zone heading above the table (Stats page's "Agent league" heading).
    hideTitle?: boolean;
    // Fires when an agent row is clicked — the stats page uses this to
    // open the agent's task drilldown. When omitted, rows render as
    // plain table rows (back-compat for non-stats callers).
    onAgentClick?: (agent: string) => void;
}

type SortKey =
    | 'agent'
    | 'model'
    | 'tasks_landed'
    | 'hands_free_pct'
    | 'redo_rounds_avg'
    | 'cost_usd_total'
    | 'avg_reflection_cost_usd';

function handsFreeColorClass(pct: number): string {
    if (pct >= 0.8) return 'text-green-600 dark:text-green-500';
    if (pct >= 0.5) return 'text-yellow-600 dark:text-yellow-500';
    return 'text-red-600 dark:text-red-500';
}

function handsFreeLabel(pct: number): string {
    return `${Math.round(pct * 100)}%`;
}

// Tooltip summarizing the metrics that no longer get their own column —
// tokens/duration/conflicts/reflection-stats are still in the data, just
// decluttered from the visible table per the Stats-rebuild spec.
function rowTitle(row: LeagueRow): string {
    return [
        `Tokens (median): ${formatTokens(row.tokens_median)}`,
        `Duration (median): ${formatDuration(row.duration_ms_median)}`,
        `Merge conflicts caused: ${row.merge_conflicts_caused}`,
        `Reflections: ${row.reflection_count} (total $${row.reflection_cost_usd_total.toFixed(4)})`,
    ].join('\n');
}

export function LeagueTable({ rows, loading, hideTitle, onAgentClick }: LeagueTableProps) {
    const [sortKey, setSortKey] = useState<SortKey>('tasks_landed');
    const [sortDesc, setSortDesc] = useState(true);

    const toggleSort = (key: SortKey) => {
        if (sortKey === key) setSortDesc(!sortDesc);
        else { setSortKey(key); setSortDesc(true); }
    };

    const sorted = [...rows].sort((a, b) => {
        const av = a[sortKey];
        const bv = b[sortKey];
        if (typeof av === 'string' && typeof bv === 'string') {
            return sortDesc ? bv.localeCompare(av) : av.localeCompare(bv);
        }
        const numA = av as number;
        const numB = bv as number;
        return sortDesc ? numB - numA : numA - numB;
    });

    const columns: { key: SortKey; label: string; align?: 'left' | 'right' }[] = [
        { key: 'agent', label: 'Agent' },
        { key: 'model', label: 'Model' },
        { key: 'tasks_landed', label: 'Landed', align: 'right' },
        { key: 'hands_free_pct', label: 'Hands-free %', align: 'right' },
        { key: 'redo_rounds_avg', label: 'Redo avg', align: 'right' },
        { key: 'cost_usd_total', label: 'Cost', align: 'right' },
        { key: 'avg_reflection_cost_usd', label: 'Avg refl cost', align: 'right' },
    ];

    const title = !hideTitle && (
        <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium flex items-center gap-2 text-foreground">
                <Trophy className="size-4 text-muted-foreground" />
                Agent + Model League
            </CardTitle>
        </CardHeader>
    );

    if (loading) {
        return (
            <Card className="border-border">
                {title}
                <CardContent>
                    <div className="text-sm text-muted-foreground py-8 text-center">Loading…</div>
                </CardContent>
            </Card>
        );
    }

    if (rows.length === 0) {
        return (
            <Card className="border-border">
                {title}
                <CardContent>
                    <div className="text-sm text-muted-foreground py-8 text-center">
                        No league data for this board
                    </div>
                </CardContent>
            </Card>
        );
    }

    return (
        <Card className="border-border">
            {title}
            <CardContent>
                <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="border-b border-border">
                                {columns.map(col => (
                                    <th
                                        key={col.key}
                                        className={`py-2 px-3 font-medium text-muted-foreground cursor-pointer hover:text-foreground transition-colors ${col.align === 'right' ? 'text-right' : 'text-left'}`}
                                        onClick={() => toggleSort(col.key)}
                                    >
                                        <span className="inline-flex items-center gap-1">
                                            {col.label}
                                            <ArrowUpDown className={`size-3 ${sortKey === col.key ? 'text-foreground' : 'text-muted-foreground/40'}`} />
                                        </span>
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {sorted.map(row => {
                                const key = `${row.agent}|${row.model}`;
                                const interactive = !!onAgentClick;
                                const rowClass = interactive
                                    ? "border-b border-border/50 hover:bg-muted/30 transition-colors cursor-pointer focus:outline-none focus:ring-2 focus:ring-ring"
                                    : "border-b border-border/50 hover:bg-muted/30 transition-colors";
                                const rowProps = interactive
                                    ? {
                                          role: "button" as const,
                                          tabIndex: 0,
                                          "aria-label": `View tasks for ${row.agent}`,
                                          onClick: () => onAgentClick(row.agent),
                                          onKeyDown: (e: React.KeyboardEvent) => {
                                              if (e.key === "Enter" || e.key === " ") {
                                                  e.preventDefault();
                                                  onAgentClick(row.agent);
                                              }
                                          },
                                      }
                                    : {};
                                return (
                                    <tr key={key} title={rowTitle(row)} className={rowClass} {...rowProps}>
                                        <td className="py-2 px-3 font-medium">{row.agent}</td>
                                        <td className="py-2 px-3 font-medium">{row.model}</td>
                                        <td className="py-2 px-3 text-right tabular-nums">{row.tasks_landed}</td>
                                        <td className={`py-2 px-3 text-right tabular-nums ${handsFreeColorClass(row.hands_free_pct)}`}>
                                            {handsFreeLabel(row.hands_free_pct)}
                                        </td>
                                        <td className="py-2 px-3 text-right tabular-nums">
                                            {row.redo_rounds_avg > 0 ? (
                                                <span className="text-amber-600 dark:text-amber-500">
                                                    {row.redo_rounds_avg}
                                                </span>
                                            ) : (
                                                <span className="text-muted-foreground">0</span>
                                            )}
                                        </td>
                                        <td className="py-2 px-3 text-right tabular-nums">
                                            {row.cost_usd_total > 0 ? formatCost(row.cost_usd_total) : '—'}
                                        </td>
                                        <td className="py-2 px-3 text-right tabular-nums">
                                            {row.avg_reflection_cost_usd > 0
                                                ? formatCost(row.avg_reflection_cost_usd)
                                                : row.reflection_count > 0
                                                    ? formatCost(row.avg_reflection_cost_usd)
                                                    : '—'}
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            </CardContent>
        </Card>
    );
}
