import { useState, useEffect, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useService } from '../../contexts/ServiceContext';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Download } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { formatCost } from '../../utils/costEstimation';
import { formatTokens } from '../../utils/transformer';
import { BarChart3, Hash, Coins, FileSearch, ListChecks } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { AnalyticsCostSummary, Board } from '../../types';

import { DateRangePicker } from './DateRangePicker';
import { CostOverTimeChart } from './CostOverTimeChart';
import { CostByModelChart } from './CostByModelChart';
import { CostByBoardChart } from './CostByBoardChart';
import { CostByAgentChart } from './CostByAgentChart';
import { EfficiencyMetrics } from './EfficiencyMetrics';
import { ModelComparisonTable } from './ModelComparisonTable';
import { TopExpensiveTasksList } from './TopExpensiveTasksList';
import { exportAnalyticsCsv } from './csvExport';

const ALL_BOARDS = '__ALL__';

interface AnalyticsPageProps {
    boards: Board[];
    onTaskClick?: (taskId: string) => void;
}

export function AnalyticsPage({ boards, onTaskClick }: AnalyticsPageProps) {
    const service = useService();
    const [searchParams, setSearchParams] = useSearchParams();

    const dateFrom = searchParams.get('date_from') || '';
    const dateTo = searchParams.get('date_to') || '';
    const granularity = searchParams.get('granularity') || 'day';
    const selectedBoard = searchParams.get('analytics_board') || ALL_BOARDS;
    const boardFilter = selectedBoard === ALL_BOARDS ? undefined : selectedBoard;

    const updateParam = useCallback((key: string, value: string) => {
        setSearchParams(prev => {
            const next = new URLSearchParams(prev);
            if (!value) next.delete(key);
            else next.set(key, value);
            return next;
        }, { replace: true });
    }, [setSearchParams]);

    const updateParams = useCallback((updates: Record<string, string>) => {
        setSearchParams(prev => {
            const next = new URLSearchParams(prev);
            for (const [key, value] of Object.entries(updates)) {
                if (!value) next.delete(key);
                else next.set(key, value);
            }
            return next;
        }, { replace: true });
    }, [setSearchParams]);

    const [data, setData] = useState<AnalyticsCostSummary | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const loadData = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const result = await service.fetchAnalytics({
                board: boardFilter,
                date_from: dateFrom || undefined,
                date_to: dateTo || undefined,
                granularity,
            });
            setData(result);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to load analytics');
        } finally {
            setLoading(false);
        }
    }, [service, boardFilter, dateFrom, dateTo, granularity]);

    useEffect(() => {
        loadData();
    }, [loadData]);

    const kpis: { icon: LucideIcon; value: string; label: string; sub: string; color: string }[] = data ? [
        {
            icon: Coins,
            value: formatCost(data.summary_kpis.total_spend),
            label: 'Total Spend',
            sub: '',
            color: 'var(--chart-1)',
        },
        {
            icon: Hash,
            value: formatTokens(data.summary_kpis.total_tokens),
            label: 'Total Tokens',
            sub: '',
            color: 'var(--chart-2)',
        },
        {
            icon: ListChecks,
            value: String(data.summary_kpis.task_count),
            label: 'Tasks',
            sub: '',
            color: 'var(--chart-3)',
        },
        {
            icon: BarChart3,
            value: formatCost(data.summary_kpis.avg_cost_per_task),
            label: 'Avg Cost/Task',
            sub: '',
            color: 'var(--chart-4)',
        },
        {
            icon: FileSearch,
            value: formatCost(data.summary_kpis.reflection_cost),
            label: 'Reflection Cost',
            sub: '',
            color: 'var(--chart-5)',
        },
    ] : [];

    return (
        <div className="space-y-6">
            {/* Filters row: board selector + date range + CSV */}
            <div className="flex flex-wrap items-center gap-3">
                <Select
                    value={selectedBoard}
                    onValueChange={v => updateParam('analytics_board', v === ALL_BOARDS ? '' : v)}
                >
                    <SelectTrigger className="w-[180px] h-8 text-sm">
                        <SelectValue>
                            {selectedBoard === ALL_BOARDS
                                ? <span className="font-medium">All Boards</span>
                                : <span className="truncate">{boards.find(b => b.id === selectedBoard)?.name}</span>
                            }
                        </SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value={ALL_BOARDS}>
                            <span className="font-medium">All Boards</span>
                        </SelectItem>
                        {boards.map(b => (
                            <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>
                        ))}
                    </SelectContent>
                </Select>

                <DateRangePicker
                    dateFrom={dateFrom}
                    dateTo={dateTo}
                    granularity={granularity}
                    onDateFromChange={v => updateParam('date_from', v)}
                    onDateToChange={v => updateParam('date_to', v)}
                    onGranularityChange={v => updateParam('granularity', v)}
                    onDateRangeChange={(from, to) => updateParams({ date_from: from, date_to: to })}
                />

                <Button
                    variant="outline"
                    size="sm"
                    className="gap-1.5 h-8 shrink-0 ml-auto"
                    disabled={!data}
                    onClick={() => data && exportAnalyticsCsv(data)}
                >
                    <Download className="size-3.5" />
                    <span className="hidden sm:inline">CSV</span>
                </Button>
            </div>

            {error && (
                <div className="text-sm text-destructive">{error}</div>
            )}

            {loading && !data ? (
                <div className="text-sm text-muted-foreground py-8">Loading analytics...</div>
            ) : data ? (
                <>
                    {/* KPI Cards */}
                    <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-4">
                        {kpis.map((card, i) => {
                            const Icon = card.icon;
                            return (
                                <Card key={i} className="border-border">
                                    <CardContent className="p-5">
                                        <div className="size-9 rounded-lg flex items-center justify-center mb-3" style={{ background: `color-mix(in srgb, ${card.color}, transparent 85%)` }}>
                                            <Icon className="size-4" style={{ color: card.color }} />
                                        </div>
                                        <div className="text-2xl font-bold tracking-tight leading-none mb-1">
                                            {card.value}
                                        </div>
                                        <div className="text-sm text-muted-foreground">{card.label}</div>
                                        {card.sub && <div className="text-xs text-muted-foreground mt-1.5">{card.sub}</div>}
                                    </CardContent>
                                </Card>
                            );
                        })}
                    </div>

                    {/* Cost Over Time — full width */}
                    <CostOverTimeChart data={data.time_series} />

                    {/* Two-column grid */}
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                        <CostByModelChart data={data.cost_by_model} />
                        <CostByBoardChart data={data.cost_by_board} />
                    </div>

                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                        <CostByAgentChart data={data.cost_by_agent} />
                        <EfficiencyMetrics data={data.efficiency_metrics} />
                    </div>

                    {/* Full-width sections */}
                    <ModelComparisonTable data={data.model_comparison} />
                    <TopExpensiveTasksList data={data.top_expensive_tasks} onTaskClick={onTaskClick} />
                </>
            ) : null}
        </div>
    );
}
