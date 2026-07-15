import { useState, useEffect, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useService } from '../../contexts/ServiceContext';
import { usePolling } from '../../hooks/usePolling';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Download } from 'lucide-react';
import { formatCost } from '../../utils/costEstimation';
import { formatTokens } from '../../utils/transformer';
import type { AnalyticsCostSummary, Board, ProviderQuota, FactorySnapshot, InboxSnapshot } from '../../types';

import { DateRangePicker } from './DateRangePicker';
import { CostOverTimeChart } from './CostOverTimeChart';
import { CostByModelChart } from './CostByModelChart';
import { TasksLandedChart } from './TasksLandedChart';
import { NowStrip } from './NowStrip';
import { InboxCompact } from './InboxCompact';
import { LeagueTable } from './LeagueTable';
import { ThroughputFunnel } from './ThroughputFunnel';
import { PerSpecList } from './PerSpecList';
import { ScheduledTasksList } from './ScheduledTasksList';
import { AgentTasksDrilldown } from './AgentTasksDrilldown';
import { exportAnalyticsCsv } from './csvExport';
import { QuotaCards } from './QuotaCards';

const ALL_BOARDS = '__ALL__';

interface StatsPageProps {
    boards: Board[];
    onTaskClick?: (taskId: string) => void;
}

function ZoneHeading({ children }: { children: React.ReactNode }) {
    return (
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {children}
        </h2>
    );
}

function StatTile({ value, label }: { value: string; label: string }) {
    return (
        <div className="rounded-md border border-border px-3 py-2">
            <div className="text-xl font-semibold leading-none tabular-nums">{value}</div>
            <div className="mt-1 text-xs text-muted-foreground">{label}</div>
        </div>
    );
}

export function StatsPage({ boards, onTaskClick }: StatsPageProps) {
    const service = useService();
    const [searchParams, setSearchParams] = useSearchParams();

    const dateFrom = searchParams.get('date_from') || '';
    const dateTo = searchParams.get('date_to') || '';
    const granularity = searchParams.get('granularity') || 'day';
    // Reuse the standard 'board' URL param so links from the board selector
    // work as-is on this page (the board dropdown drives 'board', not
    // 'analytics_board').
    const selectedBoard = searchParams.get('board') || ALL_BOARDS;
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
    const [quotaData, setQuotaData] = useState<ProviderQuota[]>([]);
    const [quotaLoading, setQuotaLoading] = useState(true);

    const [factory, setFactory] = useState<FactorySnapshot | null>(null);
    const [inbox, setInbox] = useState<InboxSnapshot | null>(null);

    const handleTaskClick = useCallback((taskId: string) => {
        onTaskClick?.(taskId);
    }, [onTaskClick]);

    // W12.4: clicking an agent row opens the agent drilldown. The
    // drilldown shows every task the agent has touched, filterable by
    // board / status / spec, with each row linking to the task detail
    // modal via ?taskId=<id>.
    const handleAgentClick = useCallback((agent: string) => {
        if (!agent || agent === 'unknown') return;
        const params = new URLSearchParams(searchParams);
        params.set('view', 'agent-tasks');
        params.set('agent', agent);
        setSearchParams(params, { replace: true });
    }, [searchParams, setSearchParams]);

    const handleBackFromDrilldown = useCallback(() => {
        const params = new URLSearchParams(searchParams);
        params.delete('view');
        params.delete('agent');
        params.delete('status');
        params.delete('spec');
        setSearchParams(params, { replace: true });
    }, [searchParams, setSearchParams]);

    useEffect(() => {
        setQuotaLoading(true);
        service.fetchQuotaStatus()
            .then(setQuotaData)
            .catch(() => {})
            .finally(() => setQuotaLoading(false));
    }, [service]);

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

    // NOW zone is board-scoped (factory snapshot + inbox only make sense for
    // a single board) and polls for a live heartbeat, same as the old
    // Factory page.
    const loadNow = useCallback(async () => {
        if (!boardFilter) {
            setFactory(null);
            setInbox(null);
            return;
        }
        const [factoryResult, inboxResult] = await Promise.all([
            service.fetchFactorySnapshot(boardFilter),
            service.fetchInbox(boardFilter),
        ]);
        setFactory(factoryResult);
        setInbox(inboxResult);
    }, [service, boardFilter]);

    usePolling(loadNow, { enabled: !!boardFilter });

    // League + per-spec + scheduled-tasks are now bundled in the
    // cost-summary response (W12.4) — no separate fetch needed. The
    // board-scoped fetchLeague path is kept for callers that still use
    // it (the per-board /api/boards/<id>/league/ endpoint is the source
    // of truth for the existing single-board view), but the StatsPage
    // always renders the league rows from cost-summary so the
    // All-Boards view can show them.
    const leagueRows = data?.league?.rows ?? [];
    const leagueLoading = loading && !data;

    const reviewPassRate = data && data.review_health.total_reviews > 0
        ? `${Math.round(((data.review_health.by_verdict.find(b => b.verdict === 'PASS')?.count ?? 0) / data.review_health.total_reviews) * 100)}%`
        : '—';

    const reworkTotal = data ? data.rework_breakdown.reduce((sum, b) => sum + b.tasks, 0) : 0;
    const reworkTasks = data ? data.rework_breakdown.filter(b => b.rounds !== '0').reduce((sum, b) => sum + b.tasks, 0) : 0;
    const reworkRate = data && reworkTotal > 0 ? `${Math.round((reworkTasks / reworkTotal) * 100)}%` : '—';

    const drilldownAgent = searchParams.get('agent');
    const drilldownView = searchParams.get('view') === 'agent-tasks' && drilldownAgent ? drilldownAgent : null;

    return (
        <div className="mx-auto max-w-[1200px] space-y-8 px-6 py-6">
            <div className="flex flex-wrap items-center gap-3">
                <Select
                    value={selectedBoard}
                    onValueChange={v => updateParam('board', v === ALL_BOARDS ? '' : v)}
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

            {drilldownView ? (
                <AgentTasksDrilldown
                    agent={drilldownView}
                    boards={boards}
                    boardFilter={boardFilter}
                    searchParams={searchParams}
                    onParamsChange={params => setSearchParams(params, { replace: true })}
                    onTaskClick={handleTaskClick}
                    onBack={handleBackFromDrilldown}
                />
            ) : (
                <>
                    {/* NOW — operational pulse, board-scoped. */}
                    <div>
                        <ZoneHeading>Now</ZoneHeading>
                        {!boardFilter ? (
                            <div className="rounded-md border border-border px-3 py-4 text-center text-sm text-muted-foreground">
                                Select a board to view live activity
                            </div>
                        ) : (
                            <div className="space-y-3">
                                <NowStrip running={factory?.running ?? []} queues={factory?.queues ?? null} onTaskClick={handleTaskClick} memoryShares={factory?.memory_shares ?? null} />
                                <InboxCompact inbox={inbox} onTaskClick={handleTaskClick} />
                            </div>
                        )}
                    </div>

                    {/* THROUGHPUT — funnel + landed rate + quality signals. */}
                    <div>
                        <ZoneHeading>Throughput</ZoneHeading>
                        {loading && !data ? (
                            <div className="text-sm text-muted-foreground py-4">Loading…</div>
                        ) : (
                            <div className="space-y-4">
                                <ThroughputFunnel data={data?.throughput_funnel ?? { total: 0, buckets: [] }} />
                                <div className="grid grid-cols-1 gap-4 lg:grid-cols-[2fr_1fr]">
                                    <TasksLandedChart data={data?.time_series ?? []} />
                                    <div className="grid grid-cols-2 gap-3 content-start lg:grid-cols-1">
                                        <StatTile value={reviewPassRate} label="Review pass rate" />
                                        <StatTile value={reworkRate} label="Rework / redo rate" />
                                    </div>
                                </div>
                            </div>
                        )}
                    </div>

                    {/* COST & AGENTS. */}
                    <div>
                        <ZoneHeading>Cost &amp; Agents</ZoneHeading>
                        {loading && !data ? (
                            <div className="text-sm text-muted-foreground py-4">Loading…</div>
                        ) : (
                            <div className="space-y-4">
                                <div className="grid grid-cols-3 gap-3">
                                    <StatTile value={data ? formatCost(data.summary_kpis.total_spend) : '—'} label="Total spend" />
                                    <StatTile value={data ? formatCost(data.summary_kpis.avg_cost_per_task) : '—'} label="Avg cost / task" />
                                    <StatTile value={data ? formatTokens(data.summary_kpis.total_tokens) : '—'} label="Total tokens" />
                                </div>

                                <CostOverTimeChart data={data?.time_series ?? []} />

                                <CostByModelChart data={data?.cost_by_model ?? []} />

                                <div data-testid="agent-league-zone">
                                    <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                                        Agent league
                                        {data?.league?.meta?.aggregate && (
                                            <span className="ml-2 normal-case tracking-normal text-muted-foreground">
                                                (all boards)
                                            </span>
                                        )}
                                    </h3>
                                    <LeagueTable
                                        rows={leagueRows}
                                        loading={leagueLoading}
                                        hideTitle
                                        onAgentClick={handleAgentClick}
                                    />
                                </div>
                            </div>
                        )}
                    </div>

                    {/* PER SPEC — one row per spec with cost + outcome counts. */}
                    <div>
                        <ZoneHeading>Per spec</ZoneHeading>
                        {loading && !data ? (
                            <div className="text-sm text-muted-foreground py-4">Loading…</div>
                        ) : (
                            <PerSpecList rows={data?.per_spec ?? []} />
                        )}
                    </div>

                    {/* SCHEDULED — schedule runs with success/failure history. */}
                    <div>
                        <ZoneHeading>Scheduled</ZoneHeading>
                        {loading && !data ? (
                            <div className="text-sm text-muted-foreground py-4">Loading…</div>
                        ) : (
                            <ScheduledTasksList rows={data?.scheduled_tasks ?? []} />
                        )}
                    </div>

                    {/* HEALTH — provider quotas. */}
                    <div>
                        <ZoneHeading>Health</ZoneHeading>
                        <QuotaCards data={quotaData} loading={quotaLoading} />
                    </div>
                </>
            )}
        </div>
    );
}
