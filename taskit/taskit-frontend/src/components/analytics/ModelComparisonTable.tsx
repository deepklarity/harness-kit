import { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ArrowUpDown, GitCompare } from 'lucide-react';
import { formatCost } from '../../utils/costEstimation';
import { formatDuration, formatTokens, shortModelName } from '../../utils/transformer';
import type { AnalyticsModelComparison } from '../../types';

interface ModelComparisonTableProps {
    data: AnalyticsModelComparison[];
}

type SortKey = 'model' | 'avg_cost' | 'total_cost' | 'avg_duration_ms' | 'avg_tokens' | 'success_rate' | 'task_count';

export function ModelComparisonTable({ data }: ModelComparisonTableProps) {
    const [sortKey, setSortKey] = useState<SortKey>('total_cost');
    const [sortDesc, setSortDesc] = useState(true);

    const toggleSort = (key: SortKey) => {
        if (sortKey === key) setSortDesc(!sortDesc);
        else { setSortKey(key); setSortDesc(true); }
    };

    const sorted = [...data].sort((a, b) => {
        const av = a[sortKey];
        const bv = b[sortKey];
        if (typeof av === 'string' && typeof bv === 'string') return sortDesc ? bv.localeCompare(av) : av.localeCompare(bv);
        return sortDesc ? (bv as number) - (av as number) : (av as number) - (bv as number);
    });

    const columns: { key: SortKey; label: string; align?: string }[] = [
        { key: 'model', label: 'Model' },
        { key: 'task_count', label: 'Tasks', align: 'right' },
        { key: 'total_cost', label: 'Total Cost', align: 'right' },
        { key: 'avg_cost', label: 'Avg Cost', align: 'right' },
        { key: 'avg_tokens', label: 'Avg Tokens', align: 'right' },
        { key: 'avg_duration_ms', label: 'Avg Duration', align: 'right' },
        { key: 'success_rate', label: 'Success Rate', align: 'right' },
    ];

    return (
        <Card className="border-border">
            <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium flex items-center gap-2 text-foreground">
                    <GitCompare className="size-4 text-muted-foreground" />
                    Model Comparison
                </CardTitle>
            </CardHeader>
            <CardContent>
                {data.length === 0 ? (
                    <div className="text-sm text-muted-foreground py-8 text-center">No model data</div>
                ) : (
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
                                {sorted.map(row => (
                                    <tr key={row.model} className="border-b border-border/50 hover:bg-muted/30 transition-colors">
                                        <td className="py-2 px-3 font-medium">{shortModelName(row.model)}</td>
                                        <td className="py-2 px-3 text-right tabular-nums">{row.task_count}</td>
                                        <td className="py-2 px-3 text-right tabular-nums">{formatCost(row.total_cost)}</td>
                                        <td className="py-2 px-3 text-right tabular-nums">{formatCost(row.avg_cost)}</td>
                                        <td className="py-2 px-3 text-right tabular-nums">{formatTokens(row.avg_tokens)}</td>
                                        <td className="py-2 px-3 text-right tabular-nums">{formatDuration(row.avg_duration_ms)}</td>
                                        <td className="py-2 px-3 text-right tabular-nums">
                                            <span className={row.success_rate >= 80 ? 'text-green-500' : row.success_rate >= 50 ? 'text-yellow-500' : 'text-red-500'}>
                                                {row.success_rate}%
                                            </span>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </CardContent>
        </Card>
    );
}
